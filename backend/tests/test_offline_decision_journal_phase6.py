"""Phase 6 real DB tests: journal atomicity, chain, recon and GET-only operator view."""
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.test_offline_scheduler_phase5 import lab, schedule, input_for, approved, policy

from app.domain.offline_scheduler import BacklogState, ScheduleKind
from app.persistence.offline_decision_journal_service import OperatorDecisionJournal
from app.persistence.offline_reconciliation import OfflineReconciler
from app.persistence.session import make_session_factory
from app.api.v1.endpoints import offline_validation_journal as api

UTC=timezone.utc


def journal_count(lab):
    with lab.engine.connect() as db:
        return db.execute(text("SELECT COUNT(*) FROM platform_decision_journal")).scalar_one()


def event_count(lab):
    with lab.engine.connect() as db:
        return db.execute(text("SELECT COUNT(*) FROM platform_orchestration_events")).scalar_one()


def journal(lab, token="admin"):
    return OperatorDecisionJournal(make_session_factory(lab.engine)).decisions(
        access_token=token,strategy_id=lab.strategy,limit=500)


def test_every_schedule_queue_decision_has_atomic_non_authoritative_journal(lab):
    spec=schedule(lab)
    lab.service.schedule(access_token="admin",spec=spec,risk_policy=policy(),reason="register")
    one=lab.service.materialize_due(access_token="admin",strategy_id=lab.strategy,
                                    clock=datetime.now(UTC),reason="due")
    assert one[0].backlog is BacklogState.QUEUED
    entries=journal(lab)
    assert len(entries)==event_count(lab)==journal_count(lab)==3
    assert [row["sequence"] for row in entries]==[1,2,3]
    assert all(row["execution_authorized"] is False for row in entries)
    assert all(len(row["audit_ref"])==64 for row in entries)
    assert all(row["version_sha256"]==one[0].identity.version_sha256 for row in entries)
    assert all(row["risk_policy_sha256"]==one[0].identity.risk_policy_sha256 for row in entries)
    assert all(row["signal_id"]==one[0].identity.batch_id for row in entries)
    assert all(row["risk_state"]=="offline_unverified" for row in entries)
    with pytest.raises(PermissionError):
        journal(lab,"outside")


def test_journal_links_risk_approval_quality_and_completed_lineage(lab):
    approved(lab)
    spec=schedule(lab)
    lab.service.schedule(access_token="admin",spec=spec,risk_policy=policy(),reason="create")
    item=lab.service.materialize_due(access_token="admin",strategy_id=lab.strategy,
                                     clock=datetime.now(UTC),reason="queue")[0]
    out=lab.service.evaluate(access_token="admin",strategy_id=lab.strategy,
                             item_id=item.identity.item_id,inputs=input_for(item),
                             reason="offline evaluation")
    assert out.backlog is BacklogState.APPROVED and not out.execution_authorized
    completed=lab.service.complete_preview(access_token="admin",
                                           strategy_id=lab.strategy,item_id=item.identity.item_id,
                                           reason="offline complete")
    entries=journal(lab)
    assert len(entries)==event_count(lab)==journal_count(lab)
    gate=[e for e in entries if e["decision_type"]=="gate_binding"]
    assert len(gate)==1
    assert gate[0]["risk_event_id"]==out.risk_event_id
    assert gate[0]["approval_event_id"]==out.approval_event_id
    assert gate[0]["approval_status"]=="allow"
    assert gate[0]["approval_mode"]=="auto"
    assert gate[0]["data_quality"]=={"status":"good","score":98,"verified_live":False}
    assert entries[-1]["run_state"]=="completed"
    assert entries[-1]["risk_event_id"]==out.risk_event_id
    assert entries[-1]["lineage_sha256"]==item.identity.lineage_sha256
    reconciled=OfflineReconciler(make_session_factory(lab.engine)).inspect(
        access_token="admin",strategy_id=lab.strategy,at=datetime.now(UTC))
    assert reconciled["status"]=="verified_offline"
    assert reconciled["items"][0]["reconciled_outcome"]=="completed"
    assert reconciled["items"][0]["terminal"] is True


def test_failed_journal_insert_rolls_back_schedule_and_queue_state(lab):
    with lab.engine.begin() as db:
        db.execute(text("CREATE TRIGGER fail_journal_insert BEFORE INSERT ON platform_decision_journal "
                        "BEGIN SELECT RAISE(ABORT,'synthetic journal outage'); END"))
    with pytest.raises(DBAPIError):
        lab.service.schedule(access_token="admin",spec=schedule(lab),
                             risk_policy=policy(),reason="audit failure")
    assert journal_count(lab)==event_count(lab)==0
    with lab.engine.begin() as db:
        db.execute(text("DROP TRIGGER fail_journal_insert"))
    spec=schedule(lab)
    lab.service.schedule(access_token="admin",spec=spec,risk_policy=policy(),reason="success")
    with lab.engine.begin() as db:
        db.execute(text("CREATE TRIGGER fail_journal_insert BEFORE INSERT ON platform_decision_journal "
                        "WHEN NEW.decision_type='queue_transition' "
                        "BEGIN SELECT RAISE(ABORT,'synthetic queue journal outage'); END"))
    with pytest.raises(DBAPIError):
        lab.service.materialize_due(access_token="admin",strategy_id=lab.strategy,
                                     clock=datetime.now(UTC),reason="no partial queue")
    assert journal_count(lab)==event_count(lab)==1
    assert lab.service.queue(access_token="admin",strategy_id=lab.strategy)==()


def test_journal_append_only_and_database_offline_flag(lab):
    lab.service.schedule(access_token="admin",spec=schedule(lab),
                         risk_policy=policy(),reason="append only")
    for query in ("UPDATE platform_decision_journal SET reason='altered'",
                  "DELETE FROM platform_decision_journal",
                  "UPDATE platform_decision_journal SET execution_authorized=1"):
        with pytest.raises(DBAPIError):
            with lab.engine.begin() as db:
                db.execute(text(query))
    assert journal_count(lab)==1


def test_reconciliation_marks_stale_with_alert_and_both_ledger_receipts(lab):
    spec=schedule(lab,kind=ScheduleKind.DELAYED,
                  at=datetime.now(UTC)-timedelta(minutes=10),age=30)
    lab.service.schedule(access_token="admin",spec=spec,risk_policy=policy(),reason="create")
    item=lab.service.materialize_due(access_token="admin",strategy_id=lab.strategy,
                    clock=spec.start_at_utc+timedelta(seconds=1),reason="old due")[0]
    reconciler=OfflineReconciler(make_session_factory(lab.engine))
    report=reconciler.inspect(access_token="admin",strategy_id=lab.strategy,
                              at=datetime.now(UTC))
    assert report["status"]=="degraded"
    assert "STALE_NON_TERMINAL" in report["issues"]
    event=reconciler.mark_stale(access_token="admin",strategy_id=lab.strategy,
                               item_id=item.identity.item_id,at=datetime.now(UTC),
                               reason="operator quarantined stale")
    assert event
    assert lab.service.queue(access_token="admin",strategy_id=lab.strategy)[0].backlog is BacklogState.SKIPPED
    assert journal_count(lab)==event_count(lab)
    final=reconciler.inspect(access_token="admin",strategy_id=lab.strategy,
                             at=datetime.now(UTC))
    assert final["items"][0]["reconciled_outcome"]=="skipped"
    assert final["status"]=="verified_offline"
    assert any(a.kind=="stale_item" for a in
               lab.service.alerts(access_token="admin",strategy_id=lab.strategy))
    with pytest.raises(PermissionError):
        reconciler.mark_stale(access_token="admin",strategy_id=lab.strategy,
                              item_id=item.identity.item_id,at=datetime.now(UTC),
                              reason="cannot quarantine twice")


def test_missing_journal_row_is_flagged_not_implicitly_trusted(lab):
    spec=schedule(lab)
    lab.service.schedule(access_token="admin",spec=spec,risk_policy=policy(),reason="source")
    with lab.engine.begin() as db:
        # Only disposable database fixture. Simulates a privileged SQL bypass.
        db.execute(text("DROP TRIGGER trg_decision_journal_immutable_delete"))
        db.execute(text("DELETE FROM platform_decision_journal"))
    status=OfflineReconciler(make_session_factory(lab.engine)).inspect(
        access_token="admin",strategy_id=lab.strategy,at=datetime.now(UTC))
    assert status["status"]=="untrusted"
    assert status["issues"]==("AUDIT_OR_LINEAGE_INVALID",)
    with pytest.raises(PermissionError):
        journal(lab)


def test_operator_get_only_endpoints_and_strict_signed_auth(lab,monkeypatch):
    application=FastAPI()
    application.include_router(api.router,prefix="/api/v1")
    def fixture_read(operation,*,token,strategy_id,**kwargs):
        if token not in ("admin","outside"):
            raise api.HTTPException(status_code=403,detail="invalid")
        try:
            return operation(make_session_factory(lab.engine),token,strategy_id,**kwargs)
        except PermissionError as exc:
            raise api.HTTPException(status_code=403,detail="forbidden") from exc
    monkeypatch.setattr(api,"_read_service",fixture_read)
    spec=schedule(lab)
    lab.service.schedule(access_token="admin",spec=spec,risk_policy=policy(),reason="operator surface")
    client=TestClient(application)
    path=f"/api/v1/offline-validation/strategies/{lab.strategy}/decisions"
    assert client.get(path).status_code==401
    assert client.post(path,headers={"Authorization":"Bearer admin"}).status_code==405
    assert client.get(path,headers={"Authorization":"Bearer outside"}).status_code==403
    accepted=client.get(path,headers={"Authorization":"Bearer admin"})
    assert accepted.status_code==200
    payload=accepted.json()
    assert payload["read_only"] is True and payload["execution_authorized"] is False
    assert payload["items"][0]["actor_id"]=="owner"
    reconcile=client.get(path.replace("/decisions","/reconciliation"),
                         headers={"Authorization":"Bearer admin"})
    assert reconcile.status_code==200
    assert reconcile.json()["execution_authorized"] is False
    assert all({"GET","HEAD"} >= set(route.methods) for route in api.router.routes)
