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


def _fresh_env(monkeypatch, *, clock_ok=True, free=10 * 1024**3, clock_raises=False):
    from app.services import snapback_preflight as pf

    h._env_cache["at"] = 0.0
    if clock_raises:
        monkeypatch.setattr(pf, "check_clock", lambda: (_ for _ in ()).throw(RuntimeError("no timedatectl")))
    else:
        monkeypatch.setattr(pf, "check_clock", lambda: (clock_ok, {"clock_sync_detail": "x"}))
    import shutil
    from collections import namedtuple

    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr(shutil, "disk_usage", lambda path: usage(100 * 1024**3, 0, free))


def test_an_undisciplined_clock_blocks_live_entries(monkeypatch):
    _beat(5)
    _fresh_env(monkeypatch, clock_ok=False)
    out = h.live_health(_health())
    assert out["status"] == "DEGRADED" and "clock_unsynchronised" in out["errors"]


def test_a_nearly_full_disk_blocks_live_entries_but_2gib_free_does_not(monkeypatch):
    _beat(5)
    _fresh_env(monkeypatch, free=100 * 1024**2)
    assert "disk_low" in h.live_health(_health())["errors"]
    _fresh_env(monkeypatch, free=2 * 1024**3)
    assert h.live_health(_health()) == {"status": "HEALTHY", "errors": []}


def test_an_unreadable_machine_probe_is_reported_not_assumed_fine(monkeypatch):
    _beat(5)
    _fresh_env(monkeypatch, clock_raises=True)
    assert "clock_unreadable" in h.live_health(_health())["errors"]


def test_gate_blockers_name_the_cause_not_a_generic_unhealthy(monkeypatch):
    from app.services import snapback_readiness as r
    from app.services.snapback_readiness import ReadinessState, reason_text

    monkeypatch.setattr(r, "readiness_state", lambda uid="default": ReadinessState(
        state="DEGRADED", system_status="DEGRADED", evidence_verdict="INCONCLUSIVE",
        broker_reconciled=False, live_errors=["broker_disconnected", "dispatcher_stale"],
        broker_connected=False))
    codes = r.gate_blockers("default")
    assert codes == ["no_broker_session", "dispatcher_not_running"]
    assert "Log in" in reason_text("no_broker_session")
    assert "system_not_healthy" not in codes and "broker_not_reconciled" not in codes

    monkeypatch.setattr(r, "readiness_state", lambda uid="default": ReadinessState(
        state="DEGRADED", system_status="HEALTHY", evidence_verdict="INCONCLUSIVE",
        broker_reconciled=False, live_errors=[], broker_connected=True))
    assert r.gate_blockers("default") == ["broker_not_reconciled"]
