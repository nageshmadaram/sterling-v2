"""Hermetic durable strategy integration; real virtual exchange, zero broker writes."""
from datetime import datetime,timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.engines.ema_trend.config import EmaTrendConfig,stable_id
from app.engines.ema_trend.contracts import Action,Bar,Campaign,Feature,Setup
from app.engines.ema_trend.state_machine import make_action
from app.services import db
from app.services.ema_trend import config_store,execution,repository as repo,scanner,snapshot
from app.services.kite_engine import fill_ledger,order_journal
from app.services.simulation.identity import sim_uid,sim_account_id
from app.services.simulation.ledger import SimLedger
from app.services.simulation.sim_client import SimulatedKiteClient
from app.services.simulation.quotes import QuoteSource
from tests.unit.simulation.sim_fakes import FakeReal,row,ist


@pytest.fixture(autouse=True)
def isolated(tmp_path,monkeypatch):
    monkeypatch.setattr(db,"_DB_PATH",str(tmp_path/"ema.db"))
    monkeypatch.setattr(db,"_available",False)
    assert db.init()
    from app.services import live_safety
    live_safety.reset_all_for_tests()
    from app.services.kite_engine import positions,state
    positions._positions.clear()
    state.reset()
    from app.services import gamma_move_positions
    gamma_move_positions.reset()
    yield


def test_config_defaults_and_cas_survive_module_state():
    initial=config_store.get("alice")
    assert initial["revision"]==0 and initial["config"]["enabled"] is False
    saved=config_store.update("alice",0,{"symbols":["NSE:INFY"],"allocation_inr":"100000"})
    assert saved["revision"]==1
    assert config_store.get("alice")["config"]["allocation_inr"]=="100000"
    assert config_store.get("bob")["revision"]==0
    with pytest.raises(repo.Conflict):
        config_store.update("alice",0,{"enabled":True})
    with pytest.raises(ValueError,match="Unknown config"):
        config_store.update("alice",1,{"is_paper":False})
    with pytest.raises(ValueError):
        config_store.update("alice",1,{"execution_mode":"LIVE"})


def test_invalid_store_is_disabled_but_not_silently_repaired():
    repo.put("alice","settings","config",{"config":{"unexpected":True},"activation_ms":1},0)
    result=config_store.response("alice")
    assert result["config"]["enabled"] is False and result["invalid"]
    assert "INVALID_PERSISTED_CONFIG" in result["blockers"]


def test_tape_does_not_renumber_or_silently_accept_restatement():
    def bar(i):
        return Bar(open_ms=1000+i*60000,close_ms=61000+i*60000,available_ms=61000+i*60000,
            open=100,high=101,low=99,close=100,volume=100)
    old=scanner.anchored_tape("alice","NSE:INFY:ultra_scalping:signal",[bar(1),bar(2)])
    new=scanner.anchored_tape("alice","NSE:INFY:ultra_scalping:signal",[bar(0),bar(2),bar(3)])
    assert new[0]==old[0] and [b.open_ms for b in new]==[bar(i).open_ms for i in (1,2,3)]
    with pytest.raises(ValueError,match="RESTATED"):
        scanner.anchored_tape("alice","NSE:INFY:ultra_scalping:signal",[bar(2).model_copy(update={"close":100.5})])


def test_store_rollback_and_tenant_document_access():
    repo.put("alice","plan","p",{"status":"READY"},0)
    with pytest.raises(repo.NotFound):
        repo.get("bob","plan","p")
    with pytest.raises(RuntimeError):
        with repo.transaction() as conn:
            repo._put(conn,"alice","plan","uncommitted",{"status":"READY"},0)
            raise RuntimeError("crash")
    with pytest.raises(repo.NotFound):
        repo.get("alice","plan","uncommitted")


def test_transport_cannot_be_spoofed_by_paper_attribute():
    with pytest.raises(PermissionError,match="CONCRETE_VIRTUAL"):
        execution.require_virtual(SimpleNamespace(_is_paper=True,_account_id=sim_account_id("alice")),"alice")


def test_live_release_scope_stays_closed():
    from app.core.release_scope import may_originate,RELEASE_ORIGINATORS
    assert not may_originate("ema_trend","alice")
    assert may_originate("ema_trend",sim_uid("alice"))
    assert "ema_trend" not in RELEASE_ORIGINATORS


@pytest.fixture
def virtual(monkeypatch):
    real=FakeReal(owner="alice")
    real._instruments.rows.append(row("INFY",1234,exchange="NSE",itype="EQ",lot=1,name="INFY",expiry=""))
    real.set_quote("NSE:INFY",100,bid=99.95,ask=100,qty=100000,age_s=0)
    ledger=SimLedger(db.connection)
    source=QuoteSource(lambda:real,owner="alice",use_websocket=False,ttl_s=0)
    client=SimulatedKiteClient("alice",lambda:real,ledger=ledger,quotes=source,clock=lambda:ist(10,16,day=(2026,10,16)))
    db.set_execution_control(uid=sim_uid("alice"),account_id=sim_account_id("alice"),
        operator_state="RUNNING",recovery_state="CLEAN",reason="test virtual tenant",actor="pytest")
    async def paper(_):return client
    async def market(_):return real
    monkeypatch.setattr(execution,"paper_client",paper)
    monkeypatch.setattr(scanner,"market_client",market)
    monkeypatch.setattr(execution,"_session_check",lambda *_args,**_kw:None)
    # This integration exercises canonical reservations, broker transport,
    # journal and ledger, while the independent global gate has its own tests.
    monkeypatch.setattr("app.services.execution_service.CanonicalExecutionService.is_trading_allowed",lambda *a,**kw:(True,"test virtual admission"))
    return real,client


def ready_signal():
    stamp=repo.now_ms()-10000
    bar=Bar(open_ms=stamp-60000,close_ms=stamp,available_ms=stamp,open=99,high=100,low=98,close=99.95,volume=1000000)
    f=Feature(index=300,bar=bar,ema10=99,ema21=98.5,dema50=97,atr10=2,
        st_line=97,st_direction="UP",context_ema10=99,context_ema21=98,context_st="UP",context_count=200,ready=True)
    return Setup(id="fresh-signal",symbol="NSE:INFY",mode="ultra_scalping",index=300,close_ms=stamp,
        cross_index=295,status="armed",reason="RETEST_CONFIRMED",path="retest",stop=Decimal("98"),
        ceiling=Decimal("100.30"),feature=f,config_revision=1)


def configured_signal():
    cfg=config_store.update("alice",0,{"enabled":True,"symbols":["NSE:INFY"],"modes":["ultra_scalping"],
        "allocation_inr":"100000","fee_bps":"1","slippage_bps":"1","gap_reserve_atr":"0"})
    with repo.transaction() as conn:
        stored=repo._load(conn,"alice","settings","config")
        stored["activation_ms"]=repo.now_ms()-120000
        conn.execute("UPDATE ema_trend_documents SET data_json=? WHERE owner_uid='alice' AND kind='settings'",(repo.encode(stored),))
    signal=ready_signal()
    repo.put("alice","signal",signal.id,{**signal.model_dump(mode="json"),"activation_eligible":True},0)
    return cfg,signal


@pytest.mark.asyncio
async def test_real_virtual_exchange_canonical_fill_ledger_and_quarter_exit(virtual):
    real,client=virtual
    cfg,signal=configured_signal()
    plan=await execution.build_plan("alice",signal.id,1)
    assert plan["status"]=="READY",plan
    result=await execution.arm("alice",plan["id"],plan["revision"],"test-arm")
    c=result["campaign"]
    assert c["q0"]>=4 and c["qty"]==c["q0"] and c["entry_terminal"]
    assert c["protection"]=="PAPER_MONITOR_ONLY"
    holdings=fill_ledger.holdings(sim_uid("alice"),sim_account_id("alice"))
    assert len(holdings)==1 and holdings[0].net_quantity==c["qty"]
    assert holdings[0].fees_complete
    trades=client.ledger.trades("alice")
    expected_fees=sum(Decimal(str(t["_sim"]["charges"]["total"])) for t in trades)
    assert expected_fees>0 and Decimal(c["total_fees"])==expected_fees
    assert c["first_fill_ms"]==min(t["_sim"]["fill_ms"] for t in trades)
    duplicate=await execution.arm("alice",plan["id"],plan["revision"],"test-arm")
    assert duplicate["status"]=="ALREADY_ARMED"
    assert len(await client.get_orders())==1
    closed=await execution.close_campaign("alice",c["id"],c["revision"],"reduce-quarter",25)
    after=closed["campaign"]
    assert after["qty"]==c["q0"]-c["q0"]//4
    assert len(repo.events("alice",c["id"]))>=4
    again=await execution.close_campaign("alice",c["id"],c["revision"],"reduce-quarter",25)
    assert again["status"]=="ALREADY_REQUESTED" and again["campaign"]["qty"]==after["qty"]
    assert len(await client.get_orders())==2
    action=repo.list_documents("alice","outbox")[0]
    # Replayed cumulative evidence has no additional inventory or accounting effect.
    await execution.reconcile_action("alice",action["id"],client=client)
    assert execution.public_campaign(repo.get("alice","campaign",c["id"]))["qty"]==after["qty"]
    from app.services.ema_trend.evidence import campaign_trace
    trace=campaign_trace("alice",c["id"])
    assert len(trace["actions"])==2
    buys=sum(a["confirmed_quantity"] for a in trace["actions"] if a["canonical_order"]["side"]=="BUY")
    sells=sum(a["confirmed_quantity"] for a in trace["actions"] if a["canonical_order"]["side"]=="SELL")
    assert buys-sells==after["qty"]
    assert all(a["canonical_order"]["state"]=="FILLED" for a in trace["actions"])
    assert "FILL_PROJECTION_MISMATCH" not in trace["gaps"]
    # This integration fixture has no broker dataset; evidence must say so.
    assert "DATASET_LINK_MISSING" in trace["gaps"]
    assert real.writes==[]


@pytest.mark.asyncio
async def test_manual_arming_does_not_require_auto_and_live_is_denied(virtual):
    _,signal=configured_signal()
    assert config_store.config("alice").auto_execute is False
    plan=await execution.build_plan("alice",signal.id,1)
    with pytest.raises(PermissionError,match="LIVE_EXECUTION_DISABLED"):
        await execution.arm("alice",plan["id"],1,"live-try","LIVE")
    config_store.pause("alice",True)
    with pytest.raises(repo.Conflict):
        await execution.arm("alice",plan["id"],1,"paper-paused")


def test_historical_snapshot_keeps_held_campaigns_visible():
    c=Campaign(id="c",uid=sim_uid("alice"),account_id=sim_account_id("alice"),symbol="NSE:INFY",
        mode="swing",product="CNC",config_hash="x",config_revision=1,config_snapshot={},budget=100,
        initial_stop=95,stop=95,entry_index=1)
    repo.put("alice","campaign",c.id,c.model_dump(mode="json"),0)
    view=snapshot.snapshot("alice",day="2025-01-01")
    assert len(view["campaigns"])==1 and view["historical_view"]


def test_disabled_scanning_never_hides_order_recovery_state():
    config_store.update("alice",0,{"enabled":False})
    repo.put("alice","scan","failed",{"status":"FAILED","errors":[{"reason":"PROVIDER_DOWN"}]},0)
    view=snapshot.snapshot("alice")
    assert view["scan"]["status"]=="FAILED" and view["capabilities"]["live"] is False


@pytest.mark.asyncio
async def test_disabled_scanner_and_auto_do_not_stop_owned_position_exit(virtual):
    from app.services.ema_trend import runner
    real,client=virtual
    _,signal=configured_signal()
    plan=await execution.build_plan("alice",signal.id,1)
    opened=await execution.arm("alice",plan["id"],plan["revision"],"entry")
    c=opened["campaign"]
    config_store.update("alice",1,{"enabled":False,"auto_execute":False})
    real.set_quote("NSE:INFY",97,bid=96.95,ask=97,qty=100000,age_s=0)
    await runner.manage_campaign("alice",repo.get("alice","campaign",c["id"]),client)
    closed=execution.public_campaign(repo.get("alice","campaign",c["id"]))
    assert closed["qty"]==0 and closed["state"]=="CLOSED" and closed["reason"]=="STOP"
    assert not fill_ledger.holdings(sim_uid("alice"),sim_account_id("alice"))
    assert real.writes==[]


@pytest.mark.asyncio
async def test_stop_reduces_confirmed_shares_while_unknown_buy_is_retained(virtual):
    from app.services.ema_trend import runner
    from app.engines.ema_trend.contracts import Vacancy
    real,client=virtual
    _,signal=configured_signal()
    plan=await execution.build_plan("alice",signal.id,1)
    opened=await execution.arm("alice",plan["id"],plan["revision"],"entry")
    c=opened["campaign"]
    reduced=await execution.close_campaign("alice",c["id"],c["revision"],"quarter",25)
    doc=repo.get("alice","campaign",c["id"])
    model=execution.campaign_model(doc)
    model.vacancies=[Vacancy(component="WEAK_10",qty=model.quarter,index=model.last_index)]
    pending=make_action(model,"RECLAIM_10","BUY",model.quarter,model.last_index,repo.now_ms(),Decimal("100"))
    repo.save_campaign_action("alice",execution.campaign_document(model),doc["revision"],pending.model_dump(mode="json"))
    outbox=repo.get("alice","outbox",pending.id)
    repo.put("alice","outbox",pending.id,{**outbox,"status":"UNKNOWN"},outbox["revision"])
    real.set_quote("NSE:INFY",97,bid=96.95,ask=97,qty=100000,age_s=0)
    await runner.manage_campaign("alice",repo.get("alice","campaign",c["id"]),client)
    result=execution.campaign_model(repo.get("alice","campaign",c["id"]))
    assert result.qty==0
    assert result.pending and result.pending.id==pending.id
    assert result.state=="CLOSING"
    assert repo.get("alice","outbox",pending.id)["status"]=="UNKNOWN"
    assert real.writes==[]


@pytest.mark.asyncio
async def test_external_virtual_sale_blocks_attribution_oversell(virtual):
    _,client=virtual
    _,signal=configured_signal()
    plan=await execution.build_plan("alice",signal.id,1)
    opened=await execution.arm("alice",plan["id"],plan["revision"],"entry")
    c=opened["campaign"]
    # A separate manual strategy sold shares. Its virtual broker fact is newer
    # than this campaign projection and must not authorize an ownership guess.
    with client.ledger._cx() as conn:
        conn.execute("UPDATE sim_positions SET quantity=1 WHERE owner='alice'")
    with pytest.raises(repo.Conflict,match="OWNED_VIRTUAL_INVENTORY"):
        await execution.close_campaign("alice",c["id"],c["revision"],"close")


def test_mark_risk_includes_open_losses_and_latches_peak_drawdown():
    from app.engines.ema_trend.contracts import Lot
    cfg=EmaTrendConfig(allocation_inr=100000,fee_bps=0,slippage_bps=0)
    c=Campaign(id="risk-c",uid=sim_uid("alice"),account_id=sim_account_id("alice"),symbol="NSE:INFY",
        mode="swing",product="CNC",config_hash="h",config_revision=1,config_snapshot=cfg.model_dump(mode="json"),
        budget=250,initial_stop=90,stop=90,entry_index=1,state="OPEN",q0=1000,p0=100,r_price=10,
        entry_terminal=True,lots=[Lot(fill_id="f",qty=1000,price=100)])
    repo.put("alice","campaign",c.id,c.model_dump(mode="json"),0)
    assert "OPEN_EXPOSURE_MARK_UNKNOWN" in execution._risk_blockers("alice",cfg)
    execution.record_mark("alice",c.id,110)
    assert execution._risk_blockers("alice",cfg)==[]
    # Still above initial capital, but >5% below the observed equity peak.
    execution.record_mark("alice",c.id,104)
    assert "ALLOCATION_DRAWDOWN_BREAKER" in execution._risk_blockers("alice",cfg)
    execution.record_mark("alice",c.id,111)
    assert "ALLOCATION_DRAWDOWN_BREAKER" in execution._risk_blockers("alice",cfg)


def test_daily_breaker_counts_open_mtm_and_unknown_mark_never_resets_latch():
    from app.engines.ema_trend.contracts import Lot
    cfg=EmaTrendConfig(allocation_inr=100000,fee_bps=0,slippage_bps=0)
    c=Campaign(id="daily-c",uid=sim_uid("alice"),account_id=sim_account_id("alice"),symbol="NSE:INFY",
        mode="ultra_scalping",product="MIS",config_hash="h",config_revision=1,config_snapshot=cfg.model_dump(mode="json"),
        budget=250,initial_stop=90,stop=90,entry_index=1,state="OPEN",q0=1000,p0=100,r_price=10,
        entry_terminal=True,lots=[Lot(fill_id="f",qty=1000,price=100)])
    repo.put("alice","campaign",c.id,c.model_dump(mode="json"),0)
    execution.record_mark("alice",c.id,100)
    assert execution._risk_blockers("alice",cfg)==[]
    execution.record_mark("alice",c.id,98.9)
    assert "DAILY_LOSS_BREAKER" in execution._risk_blockers("alice",cfg)
    execution.record_mark("alice",c.id,100,at_ms=repo.now_ms()-100000)
    assert "OPEN_EXPOSURE_MARK_STALE" in execution._risk_blockers("alice",cfg)
    execution.record_mark("alice",c.id,100)
    assert "DAILY_LOSS_BREAKER" in execution._risk_blockers("alice",cfg)


@pytest.mark.asyncio
async def test_historical_replay_is_durable_deterministic_and_never_reads_market(monkeypatch):
    from app.services.ema_trend import replay
    import asyncio
    config_store.update("alice",0,{"symbols":["NSE:INFY"],"modes":["ultra_scalping"],"allocation_inr":"100000"})
    def bars(n,start,step):
        return [Bar(open_ms=start+i*step,close_ms=start+(i+1)*step,available_ms=start+(i+1)*step,
            open=100,high=101,low=99,close=100,volume=10000) for i in range(n)]
    start=int(ist(9,15,day=(2026,10,16)).timestamp()*1000)
    signal=bars(300,start,60000)
    context=bars(110,start-110*300000,300000)
    async def forbidden(*a,**kw):raise AssertionError("historical replay read current market")
    monkeypatch.setattr(scanner,"market_client",forbidden)
    run=replay.start("alice","NSE:INFY","ultra_scalping",signal,context,1)
    await replay._tasks[run["id"]]
    result=repo.get("alice","run",run["id"])
    assert result["status"]=="COMPLETED",result
    assert result["result"]["evidence_status"]=="INCONCLUSIVE"
    assert result["result"]["live_eligible"] is False
    again=replay.start("alice","NSE:INFY","ultra_scalping",signal,context,1)
    assert again["id"]==run["id"]
    assert repo.list_documents("alice","campaign")==[]


def test_full_database_backup_restores_ema_documents_events_and_owner_isolation(tmp_path,monkeypatch):
    """Use the shipped full SQLite backup/restore, never the operator database."""
    from pathlib import Path
    from app.services.snapback_backup import create_backup,restore_backup
    config_store.update("alice",0,{"symbols":["NSE:TCS"],"allocation_inr":"100000"})
    repo.put("alice","campaign","restore-campaign",{"state":"OPEN","qty":17},0)
    repo.put("alice","outbox","restore-order",{"status":"UNKNOWN","order_id":"virtual-only"},0)
    repo.event("alice","restore-fill","restore-campaign","FILL",{"quantity":17})
    backup=create_backup(source_db=Path(db._DB_PATH),backup_root=tmp_path/"backup",freeze_record=None)
    restored=tmp_path/"restored.db"
    restore_backup(backup_db=backup.db_path,checksum_path=backup.checksum_path,destination_db=restored)
    monkeypatch.setattr(db,"_DB_PATH",str(restored))
    monkeypatch.setattr(db,"_available",False)
    assert db.init()
    assert config_store.get("alice")["config"]["symbols"]==["NSE:TCS"]
    assert repo.get("alice","campaign","restore-campaign")["qty"]==17
    assert repo.get("alice","outbox","restore-order")["status"]=="UNKNOWN"
    assert len(repo.events("alice","restore-campaign"))==1
    with pytest.raises(repo.NotFound):
        repo.get("bob","campaign","restore-campaign")


@pytest.mark.asyncio
async def test_known_rejected_reduction_escalates_to_closing_without_duplicate_order(virtual,monkeypatch):
    _,client=virtual
    _,signal=configured_signal()
    plan=await execution.build_plan("alice",signal.id,1)
    armed=await execution.arm("alice",plan["id"],plan["revision"],"entry-reject-test")
    document=repo.get("alice","campaign",armed["campaign"]["id"])
    c=execution.campaign_model(document)
    action=make_action(c,"WEAK_10","SELL",c.quarter,c.last_index,repo.now_ms(),Decimal("99.95"))
    repo.save_campaign_action("alice",execution.campaign_document(c),document["revision"],action.model_dump(mode="json"))
    outbox=repo.get("alice","outbox",action.id)
    repo.put("alice","outbox",action.id,{**outbox,"status":"REJECTED"},outbox["revision"])
    result=await execution.reconcile_action("alice",action.id,client=client)
    assert result["state"]=="CLOSING" and result["reason"]=="RISK_EXIT"
    assert result["qty"]==armed["campaign"]["qty"] and not result["pending"]
    assert len(await client.get_orders())==1  # reconciliation did not retry the rejected reduction


def test_provenance_freezes_parent_config_and_ignores_supplied_identity():
    first=config_store.update("alice",0,{"symbols":["NSE:INFY"],"allocation_inr":"100000"})
    signal=repo.put("alice","signal","audit-signal",{"mode":"swing","config_revision":first["revision"],
        "provenance":{"owner_uid":"bob","live_eligible":True}},0)
    origin=signal["provenance"]["origin"]
    assert signal["provenance"]["owner_uid"]=="alice"
    assert signal["provenance"]["live_eligible"] is False
    assert origin["config_revision"]==1 and len(origin["runtime_source_sha256"])==64
    config_store.update("alice",1,{"symbols":["NSE:TCS"],"allocation_inr":"200000"})
    plan=repo.put("alice","plan","audit-plan",{"signal_id":"audit-signal","mode":"swing"},0)
    assert plan["provenance"]["origin"]==origin
    projected=repo.put("alice","signal","audit-signal",{"status":"EXPIRED"},signal["revision"])
    assert projected["provenance"]["origin"]==origin
    repo.put("alice","campaign","audit-campaign",{"plan_id":"audit-plan"},0)
    repo.event("alice","audit-event","audit-campaign","FILL",{"quantity":1})
    assert repo.events("alice","audit-campaign")[0]["data"]["provenance"]["origin"]==origin
    assert repo.get("alice","settings","config")["provenance"]["origin"]["config_revision"]==2


def test_legacy_artifacts_are_not_relabelled_as_new_origin():
    with repo.transaction() as conn:
        repo._put(conn,"alice","signal","legacy",{"mode":"swing"},0)
        raw=repo._load(conn,"alice","signal","legacy")
        raw.pop("provenance")
        conn.execute("UPDATE ema_trend_documents SET data_json=? WHERE id='legacy'",(repo.encode(raw),))
    saved=repo.put("alice","signal","legacy",{"status":"EXPIRED"},1)
    assert saved["provenance"]["origin"] is None
    assert saved["provenance"]["origin_status"]=="LEGACY_ORIGIN_UNVERIFIED"
    child=repo.put("alice","plan","legacy-plan",{"signal_id":"legacy"},0)
    assert child["provenance"]["origin"] is None
    assert child["provenance"]["origin_status"]=="PARENT_ORIGIN_UNVERIFIED"


@pytest.mark.asyncio
async def test_scan_persists_dataset_link_and_export_keeps_manifest(virtual,monkeypatch):
    from app.engines.ema_trend import features,setups
    real,_=virtual
    configured_signal()
    signal=ready_signal().model_copy(update={"id":"dataset-linked-signal"})
    async def history(*args,**kwargs):
        return [signal.feature.bar]
    monkeypatch.setattr(scanner,"fetch_bars",history)
    monkeypatch.setattr(features,"build_features",lambda *args:[signal.feature])
    monkeypatch.setattr(setups,"evaluate_setups",lambda *args,**kwargs:[signal])
    result=await scanner.scan_symbol("alice","NSE:INFY","ultra_scalping",client=real)
    dataset=repo.get("alice","dataset",result["dataset_id"])
    stored=repo.get("alice","signal",signal.id)
    assert stored["dataset_id"]==dataset["id"]
    assert stored["dataset_sha256"]==dataset["manifest"]["content_sha256"]
    assert stored["provenance"]["origin"]["dataset_sha256"]==stored["dataset_sha256"]
    assert snapshot.export("alice")["datasets"][0]["id"]==dataset["id"]


def test_supervision_health_tracks_errors_staleness_and_recovery(monkeypatch):
    from app.services.ema_trend import health
    now=100000
    monkeypatch.setattr(repo,"now_ms",lambda:now)
    assert health.summary("alice")["status"]=="UNKNOWN"
    initial=health.record("alice",{"errors":[]})
    assert health.summary("alice")["status"]=="RUNNING"
    now+=1000
    assert health.record("alice",{"errors":[]})["revision"]==initial["revision"]
    health.record("alice",{"errors":[{"reason":"QUOTE_UNAVAILABLE"}]})
    assert health.summary("alice")["status"]=="DEGRADED"
    assert health.summary("bob")["status"]=="UNKNOWN"
    now+=31000
    assert health.summary("alice")["status"]=="STALE"
    health.record("alice",{"errors":[]})
    assert health.summary("alice")["status"]=="RUNNING"


@pytest.mark.asyncio
async def test_changed_rule_signal_cannot_open_new_paper_exposure(virtual,monkeypatch):
    _,signal=configured_signal()
    monkeypatch.setattr(execution,"descriptor",lambda:{"rule_hash":"different-build-rules"})
    plan=await execution.build_plan("alice",signal.id,1)
    assert plan["status"]=="BLOCKED"
    assert "SIGNAL_RULE_IDENTITY_UNVERIFIED_OR_CHANGED" in plan["blockers"]
    assert len(await virtual[1].get_orders())==0


@pytest.mark.asyncio
async def test_changed_rule_campaign_still_allows_owned_sell_reduction(virtual,monkeypatch):
    _,signal=configured_signal()
    plan=await execution.build_plan("alice",signal.id,1)
    armed=await execution.arm("alice",plan["id"],plan["revision"],"rule-change-entry")
    c=armed["campaign"]
    monkeypatch.setattr(execution,"descriptor",lambda:{"rule_hash":"changed-after-entry"})
    closed=await execution.close_campaign("alice",c["id"],c["revision"],"rule-change-close")
    assert closed["campaign"]["qty"]==0 and closed["campaign"]["state"]=="CLOSED"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure",[False,True])
async def test_runner_persists_completed_or_failed_supervision_cycle(monkeypatch,failure):
    import asyncio
    from app.services.ema_trend import runner,health
    config_store.update("alice",0,{"enabled":False})
    async def run(owner):
        assert owner=="alice"
        if failure:
            raise ValueError("MARKET_FEED_UNAVAILABLE")
        return {"status":"PAPER_ONLY","errors":[]}
    async def finish(*args):
        raise asyncio.CancelledError()
    monkeypatch.setattr(runner,"run_user",run)
    monkeypatch.setattr(runner.asyncio,"sleep",finish)
    with pytest.raises(asyncio.CancelledError):
        await runner.run_loop()
    report=health.summary("alice")
    assert report["status"]==("DEGRADED" if failure else "RUNNING")
    if failure:
        assert report["errors"][0]["reason"]=="MARKET_FEED_UNAVAILABLE"


@pytest.mark.asyncio
async def test_changed_rule_campaign_cannot_submit_a_reserved_add(virtual,monkeypatch):
    _,signal=configured_signal()
    plan=await execution.build_plan("alice",signal.id,1)
    armed=await execution.arm("alice",plan["id"],plan["revision"],"change-add-entry")
    document=repo.get("alice","campaign",armed["campaign"]["id"])
    c=execution.campaign_model(document)
    action=make_action(c,"RECLAIM_10","BUY",1,c.last_index,repo.now_ms(),Decimal("100"))
    repo.save_campaign_action("alice",execution.campaign_document(c),document["revision"],action.model_dump(mode="json"))
    monkeypatch.setattr(execution,"descriptor",lambda:{"rule_hash":"different-after-reservation"})
    with pytest.raises(repo.Conflict,match="CAMPAIGN_RULE_IDENTITY"):
        await execution.submit_action("alice",c.id,action.id,client=virtual[1])
    assert len(await virtual[1].get_orders())==1
    assert repo.get("alice","outbox",action.id)["status"]=="RESERVED"


@pytest.mark.asyncio
async def test_operations_reads_only_owner_virtual_campaigns_without_changing_books(virtual):
    _,signal=configured_signal()
    plan=await execution.build_plan("alice",signal.id,1)
    opened=await execution.arm("alice",plan["id"],plan["revision"],"ops-paper-entry")
    before=await virtual[1].get_orders()
    paper=snapshot.operations("alice")
    assert paper["status"]=="AVAILABLE" and paper["execution_mode"]=="PAPER"
    assert paper["live_eligible"] is False
    assert paper["campaigns"][0]["qty"]==opened["campaign"]["qty"]
    assert snapshot.operations("bob")["campaigns"]==[]
    assert await virtual[1].get_orders()==before
    assert virtual[0].writes==[]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind",["FUT","CE","PE"])
@pytest.mark.parametrize("venue",["NFO","BFO"])
async def test_exact_derivative_contract_uses_canonical_whole_lot_orders(virtual,kind,venue):
    real,client=virtual
    name="TEST26NOVFUT" if kind=="FUT" else f"TEST26NOV100{kind}"
    key=f"{venue}:{name}"
    real._instruments.rows.append(row(name,987654,exchange=venue,itype=kind,lot=25,name="TEST",expiry="2026-11-26"))
    real.set_quote(key,100,bid=99.95,ask=100,qty=100000,age_s=0)
    configured_signal()
    current=config_store.get("alice")
    updated=config_store.update("alice",current["revision"],{"symbols":[key]})
    with repo.transaction() as conn:
        value=repo._load(conn,"alice","settings","config")
        value["activation_ms"]=repo.now_ms()-120000
        conn.execute("UPDATE ema_trend_documents SET data_json=? WHERE owner_uid='alice' AND kind='settings'",(repo.encode(value),))
    signal=ready_signal().model_copy(update={"id":f"derivative-{kind}","symbol":key,"config_revision":updated["revision"]})
    repo.put("alice","signal",signal.id,{**signal.model_dump(mode="json"),"activation_eligible":True},0)
    plan=await execution.build_plan("alice",signal.id,updated["revision"])
    assert plan["status"]=="READY",plan["blockers"]
    assert plan["quantity"]%25==0 and plan["quantity"]>=100
    opened=await execution.arm("alice",plan["id"],plan["revision"],f"{kind}-entry")
    c=opened["campaign"]
    assert c["lot_size"]==25 and c["instrument_type"]==kind and c["product"]=="MIS"
    reduced=await execution.close_campaign("alice",c["id"],c["revision"],f"{kind}-quarter",25)
    assert reduced["campaign"]["qty"]==c["q0"]-c["quarter"]
    after=reduced["campaign"]
    closed=await execution.close_campaign("alice",c["id"],after["revision"],f"{kind}-close")
    assert closed["campaign"]["qty"]==0
    assert all(o["quantity"]%25==0 for o in await client.get_orders())
    assert real.writes==[]


@pytest.mark.asyncio
async def test_derivative_contract_metadata_change_blocks_arm_without_an_order(virtual):
    real,client=virtual
    key="NFO:TEST26NOV100CE"
    instrument=row("TEST26NOV100CE",99123,exchange="NFO",itype="CE",lot=25,name="TEST",expiry="2026-11-26")
    real._instruments.rows.append(instrument)
    real.set_quote(key,100,bid=99.95,ask=100,qty=100000,age_s=0)
    configured_signal()
    config_store.update("alice",1,{"symbols":[key]})
    with repo.transaction() as conn:
        value=repo._load(conn,"alice","settings","config");value["activation_ms"]=repo.now_ms()-120000
        conn.execute("UPDATE ema_trend_documents SET data_json=? WHERE owner_uid='alice' AND kind='settings'",(repo.encode(value),))
    signal=ready_signal().model_copy(update={"id":"changing-contract","symbol":key,"config_revision":2})
    repo.put("alice","signal",signal.id,signal.model_dump(mode="json"),0)
    plan=await execution.build_plan("alice",signal.id,2)
    assert plan["status"]=="READY",plan["blockers"]
    instrument["tick_size"]=.1
    with pytest.raises(repo.Conflict,match="CONTRACT_METADATA_CHANGED"):
        await execution.arm("alice",plan["id"],plan["revision"],"no-stale-metadata")
    assert await client.get_orders()==[]
