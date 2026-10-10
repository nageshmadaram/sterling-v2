"""Exactly-once additive realized-PnL events used by Gamma-Unwind.

Gamma cannot safely enter the shared symbol cost-basis ledger because another
release strategy can own the same option contract. These tests pin the separate
additive event authority that feeds the same account daily-loss breaker.
"""
from __future__ import annotations

import pytest

from app.services import db, live_safety
from app.services.kite_engine import fill_ledger, state

UID = "realized-pnl-event-u"


@pytest.fixture(autouse=True)
def isolated_events():
    prior = db._available
    db.init()
    with db.connection() as conn:
        conn.execute("DELETE FROM kite_realized_pnl_events WHERE uid=?", (UID,))
    fill_ledger.clear_for_tests(UID)
    state._daily_pnl.pop(UID, None)
    db.set_config(f"kite_engine_daily_pnl_{UID}", "")
    yield
    with db.connection() as conn:
        conn.execute("DELETE FROM kite_realized_pnl_events WHERE uid=?", (UID,))
    fill_ledger.clear_for_tests(UID)
    state._daily_pnl.pop(UID, None)
    db.set_config(f"kite_engine_daily_pnl_{UID}", "")
    db._available = prior


def test_same_broker_exit_replayed_after_crash_books_once():
    assert state.record_realized_pnl_once(
        UID, "gamma-unwind:acct:EXIT-1", -600.0,
        day_iso="2026-10-10", source="gamma_unwind:exit",
    ) is True

    # No position marker is involved. A process restart can replay the same exit,
    # and the durable event identity is sufficient to make it a no-op.
    state._daily_pnl.pop(UID, None)
    assert state.record_realized_pnl_once(
        UID, "gamma-unwind:acct:EXIT-1", -600.0,
        day_iso="2026-10-10", source="gamma_unwind:exit",
    ) is False

    assert state.daily_realized_pnl_strict(
        UID, day_iso="2026-10-10"
    ) == pytest.approx(-600.0)


def test_distinct_partial_exit_orders_each_book_once():
    state.record_realized_pnl_once(
        UID, "gamma-unwind:acct:EXIT-1", -200.0, day_iso="2026-10-10"
    )
    state.record_realized_pnl_once(
        UID, "gamma-unwind:acct:EXIT-2", 75.0, day_iso="2026-10-10"
    )

    assert state.daily_realized_pnl_strict(
        UID, day_iso="2026-10-10"
    ) == pytest.approx(-125.0)


def test_event_key_reuse_with_changed_pnl_fails_closed(monkeypatch):
    halts = []
    monkeypatch.setattr(
        live_safety, "set_kill_switch",
        lambda enabled, reason="", **_kwargs: halts.append((enabled, reason)),
    )
    state.record_realized_pnl_once(
        UID, "gamma-unwind:acct:EXIT-1", 500.0, day_iso="2026-10-10"
    )

    with pytest.raises(RuntimeError, match="realized_pnl_event_conflict"):
        state.record_realized_pnl_once(
            UID, "gamma-unwind:acct:EXIT-1", 900.0, day_iso="2026-10-10"
        )

    assert state.daily_realized_pnl_strict(
        UID, day_iso="2026-10-10"
    ) == pytest.approx(500.0)
    assert halts and halts[-1][0] is True


def test_event_is_counted_only_on_its_trading_day():
    state.record_realized_pnl_once(
        UID, "gamma-unwind:acct:EXIT-OLD", 700.0, day_iso="2026-10-09"
    )

    assert state.daily_realized_pnl_strict(
        UID, day_iso="2026-10-09"
    ) == pytest.approx(700.0)
    assert state.daily_realized_pnl_strict(
        UID, day_iso="2026-10-10"
    ) == pytest.approx(0.0)
