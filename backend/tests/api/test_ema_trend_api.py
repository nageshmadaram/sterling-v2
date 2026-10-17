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


def test_flatten_pauses_owner_and_reports_unresolved_without_touching_other_owner(client,monkeypatch):
    from app.services.ema_trend import execution,config_store
    calls=[]
    for owner,ident in (("alice","own-open"),("alice","own-unknown"),("bob","other-open")):
        repo.put(owner,"campaign",ident,{"state":"OPEN"},0)
    async def close(owner,ident,revision,key):
        assert config_store.get(owner)["paused"] is True
        calls.append((owner,ident))
        if ident=="own-unknown":
            raise ValueError("UNKNOWN settlement remains unresolved")
        return {"status":"ACKNOWLEDGED","campaign_id":ident}
    monkeypatch.setattr(execution,"close_campaign",close)
    response=client.post("/api/v1/ema-trend/operations/flatten")
    assert response.status_code==200 and response.json()["status"]=="UNRESOLVED"
    assert set(calls)=={("alice","own-open"),("alice","own-unknown")}
    assert config_store.get("alice")["paused"] is True
    assert config_store.get("bob")["paused"] is False


def test_dataset_inspection_is_readonly_and_owner_scoped(client):
    repo.put("alice","dataset","own-dataset",{"manifest":{"content_sha256":"test-hash"}},0)
    repo.put("bob","dataset","other-dataset",{"manifest":{"content_sha256":"private-hash"}},0)
    response=client.get("/api/v1/ema-trend/datasets/own-dataset")
    assert response.status_code==200 and response.json()["read_only"] is True
    assert response.json()["manifest"]["content_sha256"]=="test-hash"
    assert client.get("/api/v1/ema-trend/datasets/other-dataset").status_code==404
    assert client.post("/api/v1/ema-trend/datasets/own-dataset",json={"manifest":{}}).status_code==405


def test_instrument_search_is_owner_scoped_and_uses_server_capabilities(client,monkeypatch):
    from app.services.ema_trend import scanner
    calls=[]
    class Master:
        async def search_instruments(self,query,exchange,limit):
            calls.append((query,exchange,limit))
            return [{"exchange":"NFO","tradingsymbol":"NIFTY26NOV25000CE","instrument_token":123,
                "instrument_type":"CE","expiry":"2026-11-26","lot_size":25,"tick_size":.05},
                {"exchange":"MCX","tradingsymbol":"GOLD26NOVFUT","instrument_token":124,
                 "instrument_type":"FUT","expiry":"2026-11-26","lot_size":1,"tick_size":1}]
    async def market(owner):
        assert owner=="alice"
        return Master()
    monkeypatch.setattr(scanner,"market_client",market)
    response=client.get("/api/v1/ema-trend/instruments",params={"query":"NIFTY","mode":"swing","uid":"bob"})
    assert response.status_code==200
    rows=response.json()["items"]
    assert rows[0]["contract"]["product"]=="NRML" and rows[0]["execution_supported"] is True
    assert rows[1]["execution_supported"] is False and "MCX_MULTIPLIER" in rows[1]["blocker"]
    assert calls==[("NIFTY","",100)]


def test_campaign_evidence_preserves_canonical_unknown_and_owner_scope(client):
    from app.services.kite_engine import order_journal
    from app.services.simulation.identity import sim_uid,sim_account_id
    repo.put("alice","dataset","d",{"manifest":{"provider":"KITE","content_sha256":"hash"}},0)
    repo.put("alice","signal","s",{"dataset_id":"d"},0)
    repo.put("alice","plan","p",{"signal_id":"s"},0)
    repo.put("alice","campaign","c",{"plan_id":"p"},0)
    intent=order_journal.reserve(uid=sim_uid("alice"),account_id=sim_account_id("alice"),
        strategy_id="ema_trend",generation_id="generation",signal_id="a",exchange="NSE",
        symbol="INFY",side="BUY",quantity=4)
    repo.put("alice","outbox","a",{"campaign_id":"c","action":{"id":"a"},
        "intent_key":intent.intent_key,"projected_quantity":0,"status":"SUBMITTED"},0)
    path="/api/v1/ema-trend/campaigns/c/evidence"
    response=client.get(path)
    assert response.status_code==200
    result=response.json()
    assert result["status"]=="INCOMPLETE" and result["read_only"]
    assert "ORDER_SETTLEMENT_UNRESOLVED" in result["gaps"]
    assert result["execution_mode"]=="PAPER" and result["live_eligible"] is False
    assert result["actions"][0]["confirmed_quantity"]==0  # Submission is never a fill.
    assert result["actions"][0]["canonical_order"]["state"]=="RESERVED"
    assert result["dataset"]["manifest"]["content_sha256"]=="hash"
    assert client.post(path,json={}).status_code==405
    client.app.dependency_overrides[get_current_user]=lambda:SimpleNamespace(user_id="bob")
    assert client.get(path).status_code==404


def test_campaign_evidence_does_not_expose_foreign_canonical_intent(client):
    from app.services.kite_engine import order_journal
    from app.services.simulation.identity import sim_uid,sim_account_id
    intent=order_journal.reserve(uid=sim_uid("bob"),account_id=sim_account_id("bob"),
        strategy_id="ema_trend",generation_id="generation",signal_id="a",exchange="NSE",
        symbol="PRIVATE",side="BUY",quantity=4)
    repo.put("alice","campaign","c",{},0)
    repo.put("alice","outbox","a",{"campaign_id":"c","action":{"id":"a"},
        "intent_key":intent.intent_key},0)
    result=client.get("/api/v1/ema-trend/campaigns/c/evidence").json()
    assert result["status"]=="INCOMPLETE"
    assert "CANONICAL_IDENTITY_MISMATCH" in result["gaps"]
    assert result["actions"][0]["canonical_order"] is None
    assert result["actions"][0]["confirmed_quantity"] is None


def test_connected_readiness_is_revision_bound_and_records_missing_connection(client,monkeypatch):
    from app.services.exchanges.kite import accounts
    monkeypatch.setattr(accounts,"get_active",lambda owner:None)
    base="/api/v1/ema-trend"
    client.patch(base+"/config",json={"expected_revision":0,"changes":{"symbols":["NSE:INFY"]}})
    body={"symbol":"NSE:INFY","mode":"swing","expected_config_revision":1}
    result=client.post(base+"/readiness/check",json=body)
    assert result.status_code==200
    report=result.json()
    assert report["status"]=="BLOCKED" and not report["live_eligible"] and not report["broker_writes"]
    assert report["checks"][0]["name"]=="connected_acceptance"
    assert repo.get("alice","acceptance",report["id"])["config_revision"]==1
    assert client.post(base+"/readiness/check",json={**body,"expected_config_revision":0}).status_code==409
    assert client.post(base+"/readiness/check",json={**body,"symbol":"NSE:PRIVATE"}).status_code==422
    assert client.post(base+"/readiness/check",json={**body,"account_id":"foreign"}).status_code==422
    assert client.get(base+"/export").json()["acceptance"][0]["id"]==report["id"]
