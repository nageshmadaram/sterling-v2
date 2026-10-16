from types import SimpleNamespace
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.auth import get_current_user
from app.api.v1.endpoints.ema_trend import router
from app.services import db
from app.services.ema_trend import repository as repo


@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setattr(db,"_DB_PATH",str(tmp_path/"api.db"))
    assert db.init()
    app=FastAPI()
    app.include_router(router,prefix="/api/v1")
    app.dependency_overrides[get_current_user]=lambda:SimpleNamespace(user_id="alice")
    with TestClient(app) as client:
        yield client


def test_default_off_strict_cas_and_invalid_mode(client):
    base="/api/v1/ema-trend"
    initial=client.get(base+"/config")
    assert initial.status_code==200
    assert initial.json()["config"]["execution_mode"]=="PAPER"
    assert initial.json()["capabilities"]["live"] is False
    saved=client.patch(base+"/config",json={"expected_revision":0,"changes":{"symbols":["NSE:INFY"]}})
    assert saved.status_code==200 and saved.json()["revision"]==1
    assert client.patch(base+"/config",json={"expected_revision":0,"changes":{"enabled":True}}).status_code==409
    assert client.patch(base+"/config",json={"expected_revision":1,"changes":{"unknown":True}}).status_code==422
    assert client.get(base+"/snapshot?mode=positional").status_code==422
    assert client.post(base+"/plans/x/arm",json={"expected_revision":1,"idempotency_key":"x","execution_mode":"LIVE"}).status_code==422


def test_plan_and_campaign_access_is_owner_scoped(client):
    repo.put("bob","plan","bob-plan",{"status":"READY"},0)
    repo.put("bob","campaign","bob-campaign",{"status":"OPEN"},0)
    assert client.post("/api/v1/ema-trend/plans/bob-plan/arm",json={"expected_revision":1,"idempotency_key":"x"}).status_code==404
    assert client.get("/api/v1/ema-trend/campaigns/bob-campaign").status_code==404


def test_readonly_history_chart_validation_and_export(client):
    base="/api/v1/ema-trend"
    assert client.get(base+"/history").json()["read_only"] is True
    chart=client.get(base+"/chart",params={"symbol":"NSE:INFY","mode":"swing"})
    assert chart.status_code==200 and chart.json()["blockers"]==["SCAN_TAPE_UNAVAILABLE"]
    validation=client.get(base+"/validation").json()
    assert validation["status"]=="INCONCLUSIVE" and not validation["live_capable"]
    assert client.get(base+"/export").json()["schema_version"]==1


def test_pause_resume_persists_and_scan_needs_selected_equities(client):
    base="/api/v1/ema-trend"
    assert client.post(base+"/operations/pause").json()["paused"]
    assert not client.post(base+"/operations/resume").json()["paused"]
    revision=client.get(base+"/config").json()["revision"]
    assert client.post(base+"/scan",json={"expected_revision":revision}).status_code==422


def test_missing_auth_uid_cannot_fall_back_to_default(client):
    client.app.dependency_overrides[get_current_user]=lambda:SimpleNamespace()
    assert client.get("/api/v1/ema-trend/config").status_code==401


def test_global_flatten_keeps_closed_projection_with_unresolved_buy(client,monkeypatch):
    from app.services.ema_trend import execution
    calls=[]
    async def fake_close(owner,campaign_id,revision,key):
        calls.append((owner,campaign_id,revision))
        return {"status":"UNRESOLVED","campaign_id":campaign_id}
    monkeypatch.setattr(execution,"close_campaign",fake_close)
    # A zero-share projection may be CLOSED before a disputed BUY is observed.
    repo.put("alice","campaign","disputed",{"state":"CLOSED","pending":{"id":"pending-buy"}},0)
    result=client.post("/api/v1/ema-trend/operations/flatten")
    assert result.status_code==200
    assert result.json()["status"]=="UNRESOLVED"
    assert calls==[("alice","disputed",1)]
    assert client.get("/api/v1/ema-trend/config").json()["paused"] is True


def test_resume_blocked_until_flatten_exposure_and_order_facts_settle(client):
    base="/api/v1/ema-trend"
    assert client.post(base+"/operations/pause").status_code==200
    row=repo.put("alice","campaign","awaiting-reconciliation",{
        "state":"CLOSING","flatten_requested":True,"lots":[],
        "pending":{"id":"disputed-buy","side":"BUY"}},0)
    denied=client.post(base+"/operations/resume")
    assert denied.status_code==409
    assert "FLATTEN_RECONCILIATION_PENDING" in denied.text
    assert client.get(base+"/config").json()["paused"] is True
    updated=repo.put("alice","campaign","awaiting-reconciliation",{
        **row,"state":"CLOSED","flatten_requested":True,"lots":[],
        "pending":None,"pending_close":None},row["revision"])
    assert updated["state"]=="CLOSED"
    result=client.post(base+"/operations/resume")
    assert result.status_code==200 and result.json()["paused"] is False


def test_live_readiness_requires_real_account_and_never_enables_orders(client,monkeypatch):
    from app.services.exchanges.kite import accounts
    monkeypatch.setattr(accounts,"get_active",lambda uid: None)
    response=client.get("/api/v1/ema-trend/live-readiness")
    assert response.status_code==200
    body=response.json()
    assert body["status"]=="LIVE_BLOCKED" and body["live_orders_enabled"] is False
    assert body["read_only"] is True and body["account_id"] is None
    assert "ACTIVE_REAL_KITE_ACCOUNT_REQUIRED" in body["blockers"]


def test_valid_real_login_is_necessary_but_never_sufficient_to_arm_ema(client,monkeypatch):
    from app.services.exchanges.kite import accounts,auth
    from app.services.ema_trend import live_preflight
    account=SimpleNamespace(id="KITE-TEST-1",is_paper=False,has_credentials=True,connected=True)
    monkeypatch.setattr(accounts,"get_active",lambda uid: account)
    monkeypatch.setattr(accounts,"is_sim_account",lambda account: False)
    async def proven_session(*args,**kwargs):
        return SimpleNamespace(connected=True,validated=True,transient=False,kite_user_id="real-test")
    monkeypatch.setattr(auth,"ensure_session",proven_session)
    reads=[]
    class ReadOnlyKite:
        _is_paper=False
        _account_id="KITE-TEST-1"
        async def get_positions_raw(self):
            reads.append("positions")
            return {"net":[]}
        async def get_holdings(self):
            reads.append("holdings")
            return []
        async def get_margins(self):
            reads.append("margins")
            return {"equity":{"available":{"live_balance":100000}}}
    async def acquire(account):
        return ReadOnlyKite()
    monkeypatch.setattr(accounts,"acquire_client",acquire)
    from app.services.ema_trend import config_store
    config_store.update("alice",0,{"symbols":["NSE:INFY"],"modes":["swing","intraday"]})
    response=client.get("/api/v1/ema-trend/live-readiness")
    assert response.status_code==200
    body=response.json()
    assert body["checks"]["kite_session_validated"]
    assert body["checks"]["live_client_identity"]
    assert body["checks"]["broker_inventory_readable"]
    assert body["checks"]["available_capital_readable"]
    assert body["checks"]["broker_holdings_readable"]
    assert body["checks"]["configured_symbols_unencumbered"]
    assert reads==["positions","holdings","margins"]
    assert not body["live_orders_enabled"] and not body["modes"]["swing"]["eligible"]
    assert "CNC_HOLDINGS_AUTHORIZATION_NOT_VERIFIED" in body["modes"]["swing"]["blockers"]
    assert "MIS_BROKER_PROTECTIVE_ORDER_PATH_NOT_WIRED" in body["modes"]["intraday"]["blockers"]


def test_live_readiness_detects_existing_external_equity_holdings(client,monkeypatch):
    from app.services.exchanges.kite import accounts,auth
    from app.services.ema_trend import config_store
    cfg=config_store.update("alice",0,{"symbols":["NSE:INFY"]})
    acct=SimpleNamespace(id="KITE-HOLDING-TEST",is_paper=False,
                         has_credentials=True,connected=True)
    monkeypatch.setattr(accounts,"get_active",lambda uid: acct)
    monkeypatch.setattr(accounts,"is_sim_account",lambda acct: False)
    async def session(*args,**kwargs):
        return SimpleNamespace(connected=True,validated=True,transient=False,kite_user_id="kite-test")
    monkeypatch.setattr(auth,"ensure_session",session)
    class BrokerReadOnly:
        _is_paper=False
        _account_id="KITE-HOLDING-TEST"
        async def get_positions_raw(self):
            return {"net":[]}
        async def get_holdings(self):
            return [{"exchange":"NSE","tradingsymbol":"INFY","quantity":10}]
        async def get_margins(self):
            return {"equity":{"available":{"live_balance":1000}}}
    async def acquire(account):
        return BrokerReadOnly()
    monkeypatch.setattr(accounts,"acquire_client",acquire)
    response=client.get("/api/v1/ema-trend/live-readiness")
    assert response.status_code==200
    result=response.json()
    assert result["checks"]["broker_inventory_readable"]
    assert result["checks"]["broker_holdings_readable"]
    assert not result["checks"]["configured_symbols_unencumbered"]
    assert "BROKER_SYMBOL_OWNERSHIP_RECONCILIATION_REQUIRED" in result["blockers"]
    assert not result["live_orders_enabled"]
