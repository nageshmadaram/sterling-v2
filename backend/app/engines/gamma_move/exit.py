"""Gamma-Unwind client trade follow-up rules.

Reference semantics:
- T1 +20%, T2 +40%, rounded UP to tick.
- DTE<=1 is intraday: base-low premium SL, 15:20 exit.
- Swing: no premium SL before T1; D68 underlying daily-close zone stop.
- At T1 stop moves to entry; T2 closes; otherwise swing day 5 / expiry closes.
The reference says "book part" at T1 but defines no fraction, so this module
never invents one.
"""
from __future__ import annotations
from datetime import date

from .models import (
    MIN_TICK, T1_PCT, T2_PCT, PositionState, target_price,
)
from .selection import trading_days_between

TICK_EPS = 1e-6


def hold_at_entry(dte: int | None) -> str:
    return "intraday" if dte is not None and dte <= 1 else "swing"


def px_ge(a: float, b: float, tick: float) -> bool:
    return (a - b) / float(tick or MIN_TICK) > -TICK_EPS


def px_le(a: float, b: float, tick: float) -> bool:
    return (a - b) / float(tick or MIN_TICK) < TICK_EPS


def d68_action(option_type: str, edge: float,
               close: float, armed: bool) -> str | None:
    pe = option_type == "PE"
    if not armed:
        return "arm" if ((close < edge) if pe else (close > edge)) else None
    return "stop" if ((close > edge) if pe else (close < edge)) else None


def tick_action(pos: PositionState, ltp: float) -> tuple[str | None, float | None]:
    """Pure reference tick transition; returns (event, grading exit level)."""
    if not pos.is_open:
        return None, None
    tick = float(pos.instrument.tick_size or MIN_TICK)
    if pos.trade_state == "OPEN":
        if pos.hold == "intraday" and pos.stop > 0:
            strict = px_ge(pos.stop, pos.entry, tick)
            hit = (not px_ge(ltp, pos.stop, tick)) if strict else px_le(ltp, pos.stop, tick)
            if hit:
                return "SL", pos.stop
        if px_ge(ltp, pos.t1, tick):
            pos.trade_state = "T1_TRAIL"
            pos.stop = pos.effective_entry
            if px_ge(ltp, pos.t2, tick):
                return "T2", ltp
            return "T1", None
    if pos.trade_state == "T1_TRAIL":
        if px_ge(ltp, pos.t2, tick):
            return "T2", ltp
        if px_le(ltp, pos.effective_entry, tick):
            return "TRAIL", pos.effective_entry
    return None, None


def session_end_action(pos: PositionState, today: date,
                       swing_days: int) -> str | None:
    if not pos.is_open:
        return None
    if pos.hold == "intraday":
        return "EOD"
    entry_day = date.fromisoformat(pos.entry_day[:10])
    expiry = date.fromisoformat(pos.instrument.expiry[:10])
    day_k = trading_days_between(entry_day, today)
    pos.sessions_held = day_k
    if today >= expiry and day_k < swing_days:
        return "EXPIRY"
    if day_k >= swing_days:
        return "DAY5"
    return None


def exit_order_price(ltp: float, tick: float) -> float:
    # Kept for compatibility with the hardened legacy runner helpers.
    from .models import align_to_tick
    return align_to_tick(max(float(ltp), float(tick or MIN_TICK)),
                         tick, side="sell")


def realised_inr(pos: PositionState, exit_price: float,
                 quantity: int | None = None) -> float:
    qty = int(pos.quantity if quantity is None else quantity)
    return round((float(exit_price) - float(pos.effective_entry)) * qty, 2)


__all__ = [
    "T1_PCT", "T2_PCT", "target_price", "hold_at_entry", "d68_action",
    "tick_action", "session_end_action", "exit_order_price", "realised_inr",
]
