"""Phase 7: synthetic P&L mathematics and immutable decision-bound trade evidence."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.test_offline_scheduler_phase5 import lab, approved, input_for, schedule, policy
from app.domain.offline_performance import HypotheticalFillPair, Side, hypothetic_pnl
from app.persistence.session import make_session_factory
from app.persistence.offline_decision_journal_service import OperatorDecisionJournal
from app.persistence.offline_performance_service import OfflinePerformanceService
from app.services.offline_performance_metrics import summarize, compare, threshold_alerts
from app.api.v1.endpoints import offline_performance as api

UTC=timezone.utc


@pytest.fixture
def service(lab):
    return OfflinePerformanceService(make_session_factory(lab.engine))


def complete(lab, batch):
    spec=schedule(lab,batch=batch)
    lab.service.schedule(access_token="admin",spec=spec,risk_policy=policy(),reason="synthetic fixture")
    item=lab.service.materialize_due(access_token="admin",strategy_id=lab.strategy,
                    clock=datetime.now(UTC),reason="offline queue")[0]
    outcome=lab.service.evaluate(access_token="admin",strategy_id=lab.strategy,
                                  item_id=item.identity.item_id,inputs=input_for(item),
                                  reason="offline risk validation")
    assert outcome.backlog.value=="approved" and not outcome.execution_authorized
    lab.service.complete_preview(access_token="admin",strategy_id=lab.strategy,
                                 item_id=item.identity.item_id,reason="offline complete")
    rows=OperatorDecisionJournal(make_session_factory(lab.engine)).decisions(
        access_token="admin",strategy_id=lab.strategy,limit=500)
    matches=[r for r in rows if r["item_id"]==item.identity.item_id
             and r["decision_type"]=="run_transition" and r["run_state"]=="completed"]
    assert len(matches)==1 and matches[0]["approval_status"]=="allow"
    return matches[0]["journal_event_id"]


def make_fixture(entry,exit,*,entry_at=None,exit_at=None,
                 entry_price=100,exit_price=110,units=10,side=Side.LONG,
                 fees=5,slippage=5,risk=100,regime="trending"):
    now=datetime.now(UTC)
    return HypotheticalFillPair(
        entry_decision_id=entry,exit_decision_id=exit,
        entry_at_utc=entry_at or now-timedelta(hours=3),
        exit_at_utc=exit_at or now-timedelta(hours=1),
        entry_price=Decimal(entry_price),exit_price=Decimal(exit_price),
        units=Decimal(units),side=side,
        fees_inr=Decimal(fees),slippage_inr=Decimal(slippage),
        risk_amount_inr=Decimal(risk),market_regime=regime,
        entry_reason="synthetic decision entry assumption",
        exit_reason="synthetic decision exit assumption",
        operator_reason="offline price/cost scenario assumption",
    )


def one_trade(lab,service,**args):
    entry=complete(lab,"ENTRY-"+str(uuid4())[:8])
    exit=complete(lab,"EXIT-"+str(uuid4())[:8])
    pair=make_fixture(entry,exit,**args)
    pk=service.record_hypothetical(access_token="admin",strategy_id=lab.strategy,fixture=pair)
    assert pk
    return pk,pair


def test_empty_approvals_cannot_create_pnl(lab,service):
    approved(lab)
    complete(lab,"ONE")
    report=service.report(access_token="admin",strategy_id=lab.strategy)
    assert report["trade_count"]==0
    assert report["net_pnl_inr"]=="0"
    assert report["profit_factor"] is None
    assert report["sharpe_active_utc_days"] is None
    assert report["max_drawdown_pct"] is None
    assert report["execution_authorized"] is False
    assert report["kpi_alerts"][0]["code"]=="INSUFFICIENT_SYNTHETIC_SAMPLE"


def test_hypothetical_journal_requires_two_distinct_completed_approved_decisions(lab,service):
    approved(lab)
    entry=complete(lab,"ENTRY")
    with pytest.raises(ValidationError):
        make_fixture(entry,entry)
    with pytest.raises(PermissionError):
        service.record_hypothetical(
            access_token="admin",strategy_id=lab.strategy,
            fixture=make_fixture(entry,str(uuid4())))
    assert service.trades(access_token="admin",strategy_id=lab.strategy)==()


def test_approved_synthetic_prices_fees_costs_and_linkage(lab,service):
    approved(lab)
    id,pair=one_trade(lab,service)
    rows=service.trades(access_token="admin",strategy_id=lab.strategy)
    assert len(rows)==1
    t=rows[0]
    assert t.id==id
    assert t.gross_pnl_inr==100 and t.net_pnl_inr==90
    assert t.fees_inr==5 and t.slippage_inr==5
    assert t.execution_authorized is False
    assert t.source_provenance=="operator_supplied_synthetic"
    assert t.entry_decision_id==pair.entry_decision_id
    assert t.exit_decision_id==pair.exit_decision_id
    assert t.risk_snapshot_ref
    with pytest.raises(PermissionError):
        service.record_hypothetical(access_token="admin",strategy_id=lab.strategy,fixture=pair)
    with pytest.raises(PermissionError):
        service.trades(access_token="outside",strategy_id=lab.strategy)


def test_net_profit_factor_expectancy_contribution_drawdown_and_returns(lab,service):
    approved(lab)
    d1=datetime(2026,9,30,10,tzinfo=UTC)
    d2=datetime(2026,10,1,10,tzinfo=UTC)
    one_trade(lab,service,entry_at=d1-timedelta(hours=2),exit_at=d1,
              entry_price=100,exit_price=110,fees=5,slippage=5)
    one_trade(lab,service,entry_at=d2-timedelta(hours=2),exit_at=d2,
              entry_price=100,exit_price=120,side=Side.SHORT,fees=5,slippage=5,
              regime="volatile")
    result=service.report(access_token="admin",strategy_id=lab.strategy,
                          starting_equity_inr=Decimal(1000),rolling_window=2)
    assert result["trade_count"]==2
    assert result["net_pnl_inr"]=="-120"
    assert result["gross_profit_inr"]=="90"
    assert result["gross_loss_inr"]=="210"
    assert Decimal(result["profit_factor"])==Decimal(90)/Decimal(210)
    assert result["win_rate_pct"]=="50"
    assert result["expectancy_inr"]=="-60"
    assert result["max_drawdown_inr"]=="210"
    assert Decimal(result["max_drawdown_pct"])>0
    assert result["daily_pnl_inr"]=={"2026-09-30":"90","2026-10-01":"-210"}
    assert result["monthly_pnl_inr"]=={"2026-09":"90","2026-10":"-210"}
    assert len(result["equity_curve"])==2
    assert result["equity_curve"][0]["rolling_profit_factor"] is None
    assert Decimal(result["equity_curve"][1]["rolling_profit_factor"])==Decimal(90)/Decimal(210)
    assert result["sharpe_active_utc_days"] is not None
    assert result["sortino_active_utc_days"] is not None
    assert result["mean_return_per_trade_pct"] is not None
    assert result["mean_assumed_entry_notional_inr"]=="1000"
    assert result["peak_assumed_entry_notional_inr"]=="1000"
    assert result["observed_live_exposure_inr"] is None
    assert result["average_r_multiple"]=="-0.6"
    assert result["execution_authorized"] is False
    by_regime=service.comparison(access_token="admin",strategy_id=lab.strategy,
                 group_by="regime")
    assert set(by_regime["groups"])=={"trending","volatile"}
    assert by_regime["groups"]["volatile"]["net_pnl_inr"]=="-210"


def test_financial_edge_cases_no_fabricated_infinite_scores(lab,service):
    approved(lab)
    one_trade(lab,service,fees=0,slippage=0)
    result=service.report(access_token="admin",strategy_id=lab.strategy,
                          starting_equity_inr=Decimal(1000))
    assert result["profit_factor"] is None  # no losing synthetic trades
    assert result["sharpe_active_utc_days"] is None  # only one UTC day
    assert result["sortino_active_utc_days"] is None
    assert result["gross_loss_inr"]=="0"
    assert result["win_rate_pct"]=="100"
    with pytest.raises(ValueError):
        summarize([],starting_equity_inr=Decimal(0))
    with pytest.raises(ValueError):
        compare([],group_by="unsupported")


def test_negative_thresholds_informational_only():
    summary={"execution_authorized":False,"trade_count":10,"profit_factor":"0.5",
             "expectancy_inr":"-2","win_rate_pct":"20",
             "max_drawdown_pct":"25","net_pnl_inr":"-20"}
    alerts=threshold_alerts(summary)
    assert {a["code"] for a in alerts}=={
        "PROFIT_FACTOR_BELOW_ONE","NEGATIVE_EXPECTANCY","LOW_WIN_RATE",
        "DRAWDOWN_THRESHOLD","HYPOTHETICAL_UNDERPERFORMANCE"}
    assert all(a["execution_authorized"] is False and a["delivery"]=="not_sent" for a in alerts)


def test_immutable_evidence_and_forced_insert_rollback(lab,service):
    approved(lab)
    entry=complete(lab,"ORIG")
    exit=complete(lab,"EXIT")
    fixture=make_fixture(entry,exit)
    with lab.engine.begin() as db:
        db.execute(text("CREATE TRIGGER reject_analytics BEFORE INSERT ON platform_hypothetical_trade_events "
                        "BEGIN SELECT RAISE(ABORT,'synthetic analytics outage'); END"))
    with pytest.raises(DBAPIError):
        service.record_hypothetical(access_token="admin",strategy_id=lab.strategy,
                                    fixture=fixture)
    assert service.trades(access_token="admin",strategy_id=lab.strategy)==()
    with lab.engine.begin() as db:
        db.execute(text("DROP TRIGGER reject_analytics"))
    service.record_hypothetical(access_token="admin",strategy_id=lab.strategy,fixture=fixture)
    for sql in ("UPDATE platform_hypothetical_trade_events SET reason='altered'",
                "DELETE FROM platform_hypothetical_trade_events",
                "UPDATE platform_hypothetical_trade_events SET execution_authorized=1"):
        with pytest.raises(DBAPIError):
            with lab.engine.begin() as db:
                db.execute(text(sql))


def test_source_tamper_or_missing_journal_fails_closed(lab,service):
    approved(lab)
    one_trade(lab,service)
    with lab.engine.begin() as db:
        db.execute(text("DROP TRIGGER trg_hyp_trade_immutable_update"))
        db.execute(text("UPDATE platform_hypothetical_trade_events SET payload_json='{}'"))
    with pytest.raises((PermissionError,KeyError)):
        service.trades(access_token="admin",strategy_id=lab.strategy)


def test_live_evidence_and_invalid_fields_rejected():
    now=datetime.now(UTC)
    payload={
        "entry_decision_id":"entry","exit_decision_id":"exit",
        "entry_at_utc":now-timedelta(hours=1),"exit_at_utc":now,
        "entry_price":100,"exit_price":105,"units":5,"side":"long",
        "fees_inr":0,"slippage_inr":0,"market_regime":"trending",
        "entry_reason":"e","exit_reason":"x","operator_reason":"synthetic"}
    with pytest.raises(ValidationError):
        HypotheticalFillPair(**payload,source_provenance="actual_broker")
    with pytest.raises(ValidationError):
        HypotheticalFillPair(**payload,execution_authorized=True)
    with pytest.raises(ValidationError):
        HypotheticalFillPair(**{**payload,"entry_price":float("nan")})
    with pytest.raises(ValidationError):
        HypotheticalFillPair(**{**payload,"exit_at_utc":now-timedelta(days=1)})


def test_readonly_api_synthetic_scoped_summary_compare_and_trades(lab,service,monkeypatch):
    approved(lab)
    one_trade(lab,service)
    def mock_read(operation,token):
        try:
            return operation(service,token)
        except PermissionError as exc:
            raise api.HTTPException(status_code=403,detail="scope denied") from exc
    monkeypatch.setattr(api,"_read",mock_read)
    app=FastAPI()
    app.include_router(api.router,prefix="/api/v1")
    client=TestClient(app)
    root=f"/api/v1/offline-validation/analytics/strategies/{lab.strategy}"
    assert client.get(root+"/dashboard").status_code==401
    assert client.post(root+"/dashboard",headers={"Authorization":"Bearer admin"}).status_code==405
    assert client.get(root+"/dashboard",headers={"Authorization":"Bearer outside"}).status_code==403
    ok=client.get(root+"/dashboard",headers={"Authorization":"Bearer admin"})
    assert ok.status_code==200,ok.text
    assert ok.json()["net_pnl_inr"]=="90"
    assert ok.json()["execution_authorized"] is False
    assert ok.json()["metric_status"]=="hypothetical_not_verified_live_profitability"
    trade=client.get(root+"/trades",headers={"Authorization":"Bearer admin"})
    assert trade.status_code==200
    assert trade.json()["records"][0]["source_provenance"]=="operator_supplied_synthetic"
    grouped=client.get(root+"/comparison?group_by=version",
                       headers={"Authorization":"Bearer admin"})
    assert grouped.status_code==200
    assert list(grouped.json()["groups"])==[lab.version]
    comparison=client.get("/api/v1/offline-validation/analytics/compare-strategies",
                          params={"strategy_ids":lab.strategy},
                          headers={"Authorization":"Bearer admin"})
    assert comparison.status_code==200,comparison.text
    assert comparison.json()["execution_authorized"] is False
    assert all(set(route.methods)=={"GET"} for route in api.router.routes)
