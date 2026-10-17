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



def test_batched_snapshot_limits_each_mode_and_reads_shared_state_once(client,monkeypatch):
    from app.services.ema_trend import snapshot
    called=[]
    original=snapshot.config_store.response
    read_count=[]
    def response(uid):
        read_count.append(uid)
        return original(uid)
    monkeypatch.setattr(snapshot.config_store,"response",response)
    def by_mode(uid,mode,*,limit,cursor):
        called.append((uid,mode,limit,cursor))
        return [{"id":f"{mode}-{i}","mode":mode,"close_ms":1700000000000}
                for i in range(3)]
    monkeypatch.setattr(snapshot.repo,"list_signals_by_mode",by_mode)
    monkeypatch.setattr(snapshot,"signal_admission",lambda row,current,now:{"activation_eligible":False})
    original_list=snapshot.repo.list_documents
    def documents(uid,kind,**kwargs):
        if kind=="campaign":
            return [{"id":"unresolved","mode":"ultra_scalping","state":"RECOVERY_REQUIRED"}]
        return original_list(uid,kind,**kwargs)
    monkeypatch.setattr(snapshot.repo,"list_documents",documents)
    monkeypatch.setattr(snapshot,"public_campaign",lambda row:row)
    monkeypatch.setattr(snapshot.health,"summary",lambda uid:{"status":"RUNNING"})
    answer=client.get("/api/v1/ema-trend/snapshot",params={"modes":"swing,intraday","limit":2})
    assert answer.status_code==200,answer.text
    body=answer.json()
    assert len(body["rows"])==4
    assert {row["mode"] for row in body["rows"]}=={"swing","intraday"}
    assert body["truncatedModes"]==["swing","intraday"]
    assert body["next_cursor"] is None
    assert body["campaigns"][0]["id"]=="unresolved"
    assert read_count==["alice"]
    assert called==[("alice","swing",3,0),("alice","intraday",3,0)]


def test_batched_snapshot_rejects_invalid_modes_and_limits(client):
    url="/api/v1/ema-trend/snapshot"
    for params in ({"modes":"swing,swing"},{"modes":"swing,unsupported"},
                   {"mode":"swing","modes":"intraday"},{"modes":""},
                   {"modes":"swing","limit":1001}):
        assert client.get(url,params=params).status_code==422

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
    from app.services.kite_engine import order_journal
    monkeypatch.setattr(order_journal,"unresolved",lambda uid,account_id: [])
    monkeypatch.setattr(order_journal,"pending_projection",lambda uid,account_id: [])
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
        async def get_orders(self):
            reads.append("orders")
            return []
        async def get_gtts(self):
            reads.append("gtts")
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
    assert reads==["positions","holdings","orders","gtts","margins"]
    assert body["checks"]["broker_orders_readable"] and body["checks"]["broker_gtts_readable"]
    assert not body["checks"]["registered_static_egress_verified"]
    assert "BROKER_STATIC_EGRESS_WHITELIST_UNVERIFIED" in body["blockers"]
    assert not body["live_orders_enabled"] and not body["modes"]["swing"]["eligible"]
    assert "CNC_HOLDINGS_AUTHORIZATION_NOT_VERIFIED" in body["modes"]["swing"]["blockers"]
    assert "MIS_BROKER_PROTECTIVE_ORDER_PATH_NOT_WIRED" in body["modes"]["intraday"]["blockers"]


def test_live_readiness_detects_existing_external_equity_holdings(client,monkeypatch):
    from app.services.exchanges.kite import accounts,auth
    from app.services.kite_engine import order_journal
    monkeypatch.setattr(order_journal,"unresolved",lambda uid,account_id: [])
    monkeypatch.setattr(order_journal,"pending_projection",lambda uid,account_id: [])
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
        async def get_orders(self):
            return []
        async def get_gtts(self):
            return []
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


def test_pending_amo_and_active_gtt_block_ema_ownership_even_when_flat():
    from app.services.ema_trend.live_preflight import _order_exposure_unresolved, _gtt_exposure_unresolved
    selected={"NSE:INFY"}
    amo={"exchange":"NSE","tradingsymbol":"INFY","variety":"amo",
         "transaction_type":"BUY","quantity":1,"status":"AMO REQ RECEIVED"}
    assert _order_exposure_unresolved(amo, selected)
    assert not _order_exposure_unresolved({**amo,"status":"CANCELLED","filled_quantity":0},selected)
    assert not _order_exposure_unresolved({**amo,"tradingsymbol":"TCS"},selected)
    gtt={"condition":{"exchange":"NSE","tradingsymbol":"INFY"},"status":"active"}
    assert _gtt_exposure_unresolved(gtt,selected)
    assert not _gtt_exposure_unresolved({**gtt,"status":"cancelled"},selected)
    assert _gtt_exposure_unresolved({"condition":"unreadable"},selected)


def test_terminal_broker_order_with_partial_fill_needs_reconciliation():
    from app.services.ema_trend.live_preflight import _order_exposure_unresolved
    symbols={"NSE:INFY"}
    order={"exchange":"NSE","tradingsymbol":"INFY","side":"BUY",
           "quantity":10,"filled_quantity":4,"status":"CANCELLED"}
    assert _order_exposure_unresolved(order, symbols)
    assert _order_exposure_unresolved({**order,"status":"REJECTED"}, symbols)
    assert _order_exposure_unresolved({**order,"status":"COMPLETE","filled_quantity":10}, symbols)
    assert _order_exposure_unresolved({**order,"status":"COMPLETE","filled_quantity":0}, symbols)
    assert _order_exposure_unresolved({**order,"status":"OPEN","filled_quantity":0}, symbols)
    assert _order_exposure_unresolved({**order,"filled_quantity":-1}, symbols)
    assert _order_exposure_unresolved({**order,"filled_quantity":11}, symbols)
    assert _order_exposure_unresolved({**order,"quantity":"NaN"}, symbols)
    assert _order_exposure_unresolved({**order,"filled_quantity":None}, symbols)
    assert _order_exposure_unresolved({"status":"CANCELLED"}, symbols)
    assert not _order_exposure_unresolved({**order,"status":"CANCELLED","filled_quantity":0}, symbols)
    assert not _order_exposure_unresolved({**order,"tradingsymbol":"TCS"}, symbols)


def test_live_readiness_parses_json_gtt_conditions_and_rejects_unknown_identity():
    from app.services.ema_trend.live_preflight import _gtt_exposure_unresolved
    symbols={"NSE:INFY"}
    active={"condition":'{"exchange":"NSE","tradingsymbol":"INFY"}',"status":"active"}
    assert _gtt_exposure_unresolved(active, symbols)
    assert _gtt_exposure_unresolved({**active,"status":"triggered"},symbols)
    assert not _gtt_exposure_unresolved({**active,"status":"cancelled"},symbols)
    assert not _gtt_exposure_unresolved({**active,"condition":'{"exchange":"NSE","tradingsymbol":"TCS"}'},symbols)
    assert _gtt_exposure_unresolved({**active,"condition":'{"tradingsymbol":"INFY"}'},symbols)
    assert _gtt_exposure_unresolved({**active,"condition":'not-json'},symbols)


def test_canonical_unknown_broker_intent_blocks_live_symbol_clearance(client,monkeypatch):
    from app.services.exchanges.kite import accounts,auth
    from app.services.kite_engine import order_journal
    from app.services.ema_trend import config_store
    config_store.update("alice",0,{"symbols":["NSE:INFY"]})
    acct=SimpleNamespace(id="KITE-JOURNAL",is_paper=False,
        has_credentials=True,connected=True)
    monkeypatch.setattr(accounts,"get_active",lambda uid:acct)
    monkeypatch.setattr(accounts,"is_sim_account",lambda x:False)
    async def session(*args,**kwargs):
        return SimpleNamespace(connected=True,validated=True,transient=False,kite_user_id="KITE-USER")
    monkeypatch.setattr(auth,"ensure_session",session)
    monkeypatch.setattr(order_journal,"unresolved",lambda owner, account_id:
        [SimpleNamespace(state="UNKNOWN",symbol="INFY")])
    monkeypatch.setattr(order_journal,"pending_projection",lambda owner, account_id:[])
    class ReadOnly:
        _is_paper=False
        _account_id="KITE-JOURNAL"
        async def get_positions_raw(self):
            return {"net":[]}
        async def get_holdings(self):
            return []
        async def get_orders(self):
            return []
        async def get_gtts(self):
            return []
        async def get_margins(self):
            return {"equity":{"available":{"live_balance":100000}}}
    async def acquire(account):
        return ReadOnly()
    monkeypatch.setattr(accounts,"acquire_client",acquire)
    result=client.get("/api/v1/ema-trend/live-readiness")
    assert result.status_code==200
    payload=result.json()
    assert payload["checks"]["broker_orders_readable"]
    assert payload["checks"]["broker_gtts_readable"]
    assert not payload["checks"]["canonical_journal_reconciled"]
    assert not payload["checks"]["configured_symbols_unencumbered"]
    assert "CANONICAL_BROKER_ORDER_RECONCILIATION_PENDING" in payload["blockers"]


def test_live_order_observation_denies_other_owners_intents(client,monkeypatch):
    from app.services.kite_engine import order_journal
    monkeypatch.setattr(order_journal,"get",lambda key:
        SimpleNamespace(uid="bob",strategy_id="ema_trend"))
    res=client.get("/api/v1/ema-trend/live-order-observations/unknown")
    assert res.status_code==404


def test_live_order_observation_requires_exact_non_sim_account(client,monkeypatch):
    from app.services.kite_engine import order_journal
    from app.services.exchanges.kite import accounts
    fake=SimpleNamespace(uid="alice",strategy_id="ema_trend",
        account_id="KITE-LIVE",order_id="B-1")
    monkeypatch.setattr(order_journal,"get",lambda key:fake)
    monkeypatch.setattr(accounts,"get_active",lambda user:None)
    res=client.get("/api/v1/ema-trend/live-order-observations/own-intent")
    assert res.status_code==403
    assert "BOUND_LIVE_KITE_ACCOUNT_REQUIRED" in res.text


def test_live_order_observation_reads_exact_order_but_never_writes(client,monkeypatch):
    from app.services.kite_engine import order_journal
    from app.services.exchanges.kite import accounts,auth
    intent=SimpleNamespace(uid="alice",account_id="KITE-REAL-1",
        strategy_id="ema_trend",order_id="REAL-ORDER-1",state="SUBMITTED",
        exchange="NSE",symbol="INFY",side="BUY",quantity=4,
        payload={"product":"CNC"},filled_quantity=0,filled_value=0,
        reconciliation_required=False,projection_pending=False)
    monkeypatch.setattr(order_journal,"get",lambda key:intent)
    account=SimpleNamespace(id="KITE-REAL-1",is_paper=False,connected=True)
    monkeypatch.setattr(accounts,"get_active",lambda user:account)
    monkeypatch.setattr(accounts,"is_sim_account",lambda account:False)
    async def session(*args,**kwargs):
        return SimpleNamespace(connected=True,validated=True,transient=False,kite_user_id="BROKER-USER")
    monkeypatch.setattr(auth,"ensure_session",session)
    calls=[]
    class FakeReadOnlyBroker:
        _is_paper=False
        _account_id="KITE-REAL-1"
        async def get_order_history(self,order_id):
            calls.append(("history",order_id))
            return [
                {"order_id":order_id,"exchange":"NSE","tradingsymbol":"INFY",
                 "product":"CNC","transaction_type":"BUY","quantity":4,
                 "filled_quantity":0,"average_price":0,"status":"OPEN"},
                {"order_id":order_id,"exchange":"NSE","tradingsymbol":"INFY",
                 "product":"CNC","transaction_type":"BUY","quantity":4,
                 "filled_quantity":2,"average_price":100,"status":"OPEN"},
            ]
        async def place_order(self,*args,**kwargs):
            raise AssertionError("no live order submission permitted")
    async def acquire(account):
        return FakeReadOnlyBroker()
    monkeypatch.setattr(accounts,"acquire_client",acquire)
    response=client.get("/api/v1/ema-trend/live-order-observations/own-intent")
    assert response.status_code==200
    body=response.json()
    assert calls==[("history","REAL-ORDER-1")]
    assert body["status"]=="RECONCILIATION_REQUIRED"
    assert body["broker_confirmed_filled_quantity"]==2
    assert body["unprojected_quantity"]==2
    assert body["unprojected_value"]=="200"
    assert "UNPROJECTED_BROKER_FILL_PRESENT" in body["blockers"]
    assert body["live_orders_enabled"] is False and body["read_only"]
