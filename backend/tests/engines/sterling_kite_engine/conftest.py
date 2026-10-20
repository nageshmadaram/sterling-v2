import numpy as np
import pytest


@pytest.fixture
def execution_policy_open_session(monkeypatch):
    """Execution plumbing tests use an open calendar; policy tests use real dates.

    Keep the caller's wall clock for quote freshness and elapsed-time checks.
    Translate a real policy from a known session so its holding budget remains
    relative to that clock, including when CI runs on weekends or holidays.
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from app.core import mode_execution
    original = mode_execution.freeze_policy
    reference = datetime(2026, 10, 7, 10, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
    def policy(strategy, mode, now, **kwargs):
        result = original(strategy, mode, reference, **kwargs)
        delta = int(now.timestamp() * 1000) - result["entry_ms"]
        result["entry_ms"] += delta
        result["hard_exit_ms"] += delta
        return result
    monkeypatch.setattr(mode_execution, "freeze_policy", policy)

from tests.conftest import reset_global_stores as _shared_reset_global_stores


@pytest.fixture
def isolated_kite_database(tmp_path):
    """Exercise real persistence without reloading another test's positions.

    The global store fixture also writes SQLite, so this fixture must wrap its
    setup and teardown. Restoring the DB path only after cleanup keeps those
    writes away from any database configured by the caller.
    """
    from app.services import db, live_safety
    from app.services.kite_engine import monitor, positions, service, state

    def clear_caches():
        positions.reset()
        state.reset()
        live_safety._IDEMPOTENCY_CACHE.clear()
        monitor._exiting.clear()
        monitor._stop_probe.clear()
        monitor.forget_holdings()
        service._entry_locks.clear()
        service._red_stale_warned.clear()
        service._orphan_warned.clear()

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(db, "_DB_PATH", str(tmp_path / "kite-test.sqlite3"))
        patch.setattr(db, "_available", False)
        assert db.init(), "Kite tests require an isolated, working SQLite database"
        clear_caches()
        try:
            yield
        finally:
            clear_caches()


@pytest.fixture(autouse=True)
def _kill_switch_does_not_leak(isolated_kite_database):
    """A test that drives a real halt (e.g. unknown protection ownership) must not leave
    the process-wide kill switch engaged for the tests that run after it."""
    from app.services import live_safety
    yield
    if live_safety.kill_switch_state().get("enabled"):
        try:
            live_safety.set_kill_switch(False)
        except Exception:  # noqa: BLE001
            live_safety._KILL_SWITCH.update({"enabled": False, "reason": ""})


@pytest.fixture(autouse=True)
def reset_global_stores(isolated_kite_database):
    """Retain shared cleanup, with an explicit dependency on DB isolation."""
    yield from _shared_reset_global_stores.__wrapped__()


def series(values):
    """Build OHLC arrays from a close path; tight bars so HA tracks closely."""
    c = np.asarray(values, dtype=float)
    o = np.concatenate([[c[0]], c[:-1]])
    h = np.maximum(o, c) + 1.0
    l = np.minimum(o, c) - 1.0
    return o, h, l, c


@pytest.fixture
def uptrend():
    # long, smooth rise — drives all three SuperTrends bullish after warmup
    return series(list(np.linspace(100, 400, 120)))


@pytest.fixture
def down_then_up():
    # falling then rising — produces a bear→bull transition
    fall = list(np.linspace(300, 150, 60))
    rise = list(np.linspace(150, 450, 60))
    return series(fall + rise)
