"""Phase 11: one synthetic, non-authoritative end-to-end offline evidence chain.

All fixtures are disposable; no broker client, network transport, real
credentials, new execution authority, deployment or production database.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from tests.test_offline_scheduler_phase5 import lab, approved
from tests.test_offline_performance_phase7 import complete, make_fixture
from app.persistence.session import make_session_factory
from app.persistence.offline_decision_journal_service import OperatorDecisionJournal
from app.persistence.offline_reconciliation import OfflineReconciler
from app.persistence.offline_performance_service import OfflinePerformanceService
from app.persistence.offline_alert_service import OfflineAlertService, Category, Channel
from app.persistence.offline_health_service import OfflineHealthRecorder
from app.offline_security_profile import create_app


def test_phase11_single_synthetic_chain_registry_to_health_and_operator(lab):
    """Combined Phase 1/4/5/6/7/8/9/10 persistence, with zero broker effects."""
    approved(lab)
    entry = complete(lab, "PHASE11-ENTRY")
    exit_decision = complete(lab, "PHASE11-EXIT")

    sessions = make_session_factory(lab.engine)
    journal = OperatorDecisionJournal(sessions)
    decisions = journal.decisions(access_token="operator", strategy_id=lab.strategy, limit=500)
    assert {entry, exit_decision}.issubset({row["journal_event_id"] for row in decisions})
    assert all(row["execution_authorized"] is False for row in decisions)
    assert all(len(row["audit_ref"]) == 64 for row in decisions)

    recon = OfflineReconciler(sessions).inspect(
        access_token="operator", strategy_id=lab.strategy
    )
    assert recon["status"] == "verified_offline"
    assert len(recon["items"]) == 2
    assert all(row["terminal"] and row["execution_authorized"] is False
               for row in recon["items"])

    performance = OfflinePerformanceService(sessions)
    synthetic_trade = performance.record_hypothetical(
        access_token="admin", strategy_id=lab.strategy,
        fixture=make_fixture(entry, exit_decision),
    )
    report = performance.report(access_token="operator", strategy_id=lab.strategy)
    assert report["trade_count"] == 1
    assert report["execution_authorized"] is False
    assert report["observed_live_exposure_inr"] is None
    assert performance.trades(access_token="operator", strategy_id=lab.strategy)[0].id == synthetic_trade

    alerts = OfflineAlertService(sessions)
    alert = alerts.create(
        access_token="admin", strategy_id=lab.strategy,
        category=Category.INFORMATIONAL_UPDATE,
        source_type="decision", source_ref=entry,
        source_system="offline_decision_journal",
        message="Synthetic Phase 11 evidence review; not a market signal",
        reason="controlled non-authoritative certification sample",
    )
    assert alert["duplicate_suppressed"] is False
    assert alerts.create(
        access_token="admin", strategy_id=lab.strategy,
        category=Category.INFORMATIONAL_UPDATE,
        source_type="decision", source_ref=entry,
        source_system="offline_decision_journal",
        message="Synthetic Phase 11 evidence review; not a market signal",
        reason="idempotent replay",
    )["duplicate_suppressed"] is True
    aid = alert["alert_id"]
    assert alerts.delivery(access_token="admin", strategy_id=lab.strategy,
                           alert_id=aid, channel=Channel.DASHBOARD)["state"] == "sent"
    with pytest.raises(PermissionError):
        alerts.delivery(access_token="admin", strategy_id=lab.strategy,
                        alert_id=aid, channel=Channel.TELEGRAM)
    assert alerts.change_state(access_token="operator", strategy_id=lab.strategy,
                               alert_id=aid, action="acknowledged",
                               reason="Operator acknowledged synthetic evidence")["changed"]
    with pytest.raises(PermissionError):
        alerts.change_state(access_token="operator", strategy_id=lab.strategy,
                            alert_id=aid, action="resolved",
                            reason="Operator must not resolve")
    inbox = alerts.list(access_token="operator", strategy_id=lab.strategy)
    assert len(inbox) == 1
    assert inbox[0]["state"] == "acknowledged"
    assert inbox[0]["execution_authorized"] is False
    assert len(inbox[0]["audit_ref"]) == 64

    health = OfflineHealthRecorder(sessions)
    with pytest.raises(PermissionError):
        health.sample_once(access_token="operator", strategy_id=lab.strategy)
    event = health.sample_once(access_token="admin", strategy_id=lab.strategy)
    history = health.read(access_token="operator", strategy_id=lab.strategy)
    assert len(history["snapshots"]) == 1
    assert history["snapshots"][0]["event_id"] == event
    assert history["snapshots"][0]["status"]["queue"] == "verified_offline"
    assert history["snapshots"][0]["status"]["alerts"] == "verified_offline"
    assert history["snapshots"][0]["status"]["production_ready"] is False
    assert history["snapshots"][0]["status"]["broker_exposure"] == "unknown"
    assert history["execution_authorized"] is False

    for read in (
        lambda: journal.decisions(access_token="outside", strategy_id=lab.strategy),
        lambda: performance.report(access_token="outside", strategy_id=lab.strategy),
        lambda: alerts.list(access_token="outside", strategy_id=lab.strategy),
        lambda: health.read(access_token="outside", strategy_id=lab.strategy),
    ):
        with pytest.raises(PermissionError):
            read()


def test_phase11_isolated_api_runtime_never_mounts_broker_or_execution_routes(lab):
    app = create_app()
    paths = {getattr(route, "path", "") for route in app.routes}
    assert not any(
        any(marker in path for marker in
            ("/kite/", "/broker/", "/order", "/stream/", "/execution/", "/ops/"))
        for path in paths
    )
    client = TestClient(app)
    assert client.get("/api/v1/kite/orders").status_code == 404
    assert client.get("/api/v1/offline-validation/monitor").status_code == 403
    assert client.get("/api/v1/offline-validation/monitor",
                      headers={"Authorization": "Bearer admin"}).status_code == 403
    monitor = client.get("/api/v1/offline-validation/monitor",
                         headers={"Authorization": "Bearer operator"})
    assert monitor.status_code == 200
    state = monitor.json()
    assert state["execution_authorized"] is False and state["read_only"] is True
    assert state["broker"]["status"] == "not_connected"
    assert state["market_data"]["status"] == "not_independently_probed"
    assert state["worker"]["status"] == "disabled"
    assert client.post("/api/v1/offline-validation/monitor",
                       headers={"Authorization": "Bearer operator"}).status_code == 405
    assert client.get("/api/v1/offline-validation/strategies",
                      headers={"Authorization": "Bearer outside"}).status_code == 403


def test_phase11_http_write_surface_is_only_alert_metadata_and_mfa(lab):
    app = create_app()
    auth_writes = {
        "/api/v1/auth/login", "/api/v1/auth/refresh", "/api/v1/auth/logout",
        "/api/v1/offline-security/mfa/verify",
    }
    metadata_writes = {
        "/api/v1/offline-validation/strategies/{strategy_id}/alerts/{alert_id}/acknowledge",
        "/api/v1/offline-validation/strategies/{strategy_id}/alerts/{alert_id}/resolve",
    }
    writes = {
        route.path for route in app.routes
        if getattr(route, "methods", None)
        and set(route.methods) & {"POST", "PUT", "PATCH", "DELETE"}
    }
    assert writes == auth_writes | metadata_writes
    for route in app.routes:
        if route.path.startswith("/api/v1/offline-validation/"):
            assert set(route.methods) <= {"GET", "POST"}
            if "POST" in route.methods:
                assert route.path in metadata_writes
