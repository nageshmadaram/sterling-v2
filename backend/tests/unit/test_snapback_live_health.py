"""Live Snapback is gated on the live path's health, not the evidence runner's."""
from datetime import datetime, timedelta, timezone

from app.services import snapback_health as h


def _health(*errors, broker=True):
    return {"status": "HALTED", "broker_connected": broker, "unresolved_errors": list(errors)}


def _beat(seconds_ago):
    h.record_cycle("live_dispatcher", datetime.now(timezone.utc) - timedelta(seconds=seconds_ago))


def test_a_dead_evidence_runner_does_not_block_live_trading():
    _beat(5)
    out = h.live_health(_health("runner_stale", "manifest_mismatch", "market_data_stale",
                                "prospective_runner_unavailable", "startup_preflight_failed"))
    assert out == {"status": "HEALTHY", "errors": []}


def test_a_silent_dispatcher_is_degraded():
    _beat(h.DISPATCHER_STALE_AFTER_SECONDS + 30)
    out = h.live_health(_health())
    assert out["status"] == "DEGRADED" and out["errors"] == ["dispatcher_stale"]


def test_real_recovery_and_reconciliation_faults_still_halt():
    _beat(5)
    out = h.live_health(_health("recovery_required", "runner_stale"))
    assert out["status"] == "HALTED" and out["errors"] == ["recovery_required"]
    out = h.live_health(_health("position_reconciliation_mismatch"))
    assert "position_reconciliation_mismatch" in out["errors"] and out["status"] != "HEALTHY"


def test_readiness_uses_the_live_health(monkeypatch):
    from app.services import snapback_readiness as r

    _beat(5)
    monkeypatch.setattr(h, "get_prospective_health", lambda uid="default": _health("runner_stale"))
    monkeypatch.setattr(r, "_reconciliation_clean", lambda uid: True)
    monkeypatch.setattr(r, "_live_book_clean", lambda uid: True)
    monkeypatch.setattr("app.services.snapback_family_ops.new_trades_halted", lambda *a, **k: False)
    state = r.readiness_state("default")
    assert state.system_status == "HEALTHY" and state.broker_reconciled is True


def test_a_deliberately_absent_evidence_runner_raises_no_operational_alert():
    from app.services.snapback_alerts import derive_operational_alerts

    health = {"status": "HALTED", "runner_alive": False, "manifest_ok": False,
              "unresolved_errors": ["prospective_runner_unavailable", "runner_stale", "manifest_mismatch"]}
    codes = {a.code for a in derive_operational_alerts(health=health, backup_ok=True)}
    assert not codes & {"runner_stopped", "manifest_mismatch", "system_halted"}
