"""Price arithmetic invariants for the Gamma-Unwind engine."""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.engines.gamma_move import InstrumentRef, PositionState, align_to_tick
from app.engines.gamma_move.exit import tick_action


@pytest.mark.parametrize("tick", [0.05, 0.01])
def test_tick_aligned_prices_are_returned_unchanged(tick):
    step = Decimal(str(tick))
    for n in range(1, 4000):
        price = float(step * n)
        assert align_to_tick(price, tick, side="buy") == pytest.approx(price, abs=1e-9)
        assert align_to_tick(price, tick, side="sell") == pytest.approx(price, abs=1e-9)


def test_entry_limit_never_drifts_a_tick_below_the_signal_print():
    # 102.15 / 0.05 is 2042.9999999999998 in binary floating point; a naive
    # floor() put the BUY limit one tick below the E1 print.
    assert align_to_tick(102.15, 0.05, side="buy") == pytest.approx(102.15)
    assert align_to_tick(0.15, 0.05, side="buy") == pytest.approx(0.15)


def test_off_tick_prices_round_in_the_conservative_direction():
    assert align_to_tick(100.02, 0.05, side="buy") == pytest.approx(100.00)
    assert align_to_tick(100.02, 0.05, side="sell") == pytest.approx(100.05)
    assert align_to_tick(100.049, 0.01, side="buy") == pytest.approx(100.04)
    assert align_to_tick(100.041, 0.01, side="sell") == pytest.approx(100.05)


def _pos(*, hold: str = "intraday", stop: float = 90.0, entry: float = 100.0) -> PositionState:
    inst = InstrumentRef("1", "ABC26OCT100CE", "CE", 100, "2026-10-29", 10, 0.05)
    return PositionState(
        signal_id="s", instrument=inst, list_name="BREAKOUT-CE", tier="ENTRY",
        underlying="ABC", underlying_token=1, entry=entry, quantity=10, lots=1,
        entered_ms=0, entry_day="2026-10-05", hold=hold, zone_edge=100.0,
        base_low=90.0, t1=120.0, t2=140.0, stop=stop, status="open",
    )


def test_t1_never_loosens_an_existing_stop():
    # Fill slipped below the structural base-low stop: T1 must not drag the
    # stop DOWN to the (lower) effective entry.
    pos = _pos(stop=95.0, entry=92.0)
    pos.fill_price = 92.0
    event, _ = tick_action(pos, 121.0)
    assert event == "T1"
    assert pos.trade_state == "T1_TRAIL"
    assert pos.stop == pytest.approx(95.0)
