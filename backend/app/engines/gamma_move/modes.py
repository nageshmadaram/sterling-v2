"""Gamma-Unwind trading modes: one entry signal, five sets of exits.

The OI-unwind ENTRY signal is the same in every mode. A mode only changes how
the trade is held and closed:

- ultra_scalping: base-low stop, full exit at T1 (+20%), 20-minute limit.
- scalping: base-low stop, T1 moves stop to entry, T2 closes, 60-minute limit.
- intraday: base-low stop, T1/T2, out by 15:20 (the reference DTE<=1 rule).
- overnight: swing-class (D68 daily-close stop), at most 2 sessions, and never
  held into a weekend or holiday gap.
- swing: swing-class, the reference day-5 / expiry exits.

"auto" keeps the reference behaviour: expiry within 1 trading day is intraday,
otherwise swing. Ultra scalp, scalp and overnight are not tested with real
money, so live entries in those modes are refused (practice only).
"""
from __future__ import annotations

from dataclasses import dataclass

TRADE_MODES = ("ultra_scalping", "scalping", "intraday", "overnight", "swing")
#: Modes allowed to place real orders. The rest are practice only until tested.
LIVE_TESTED_MODES = frozenset({"intraday", "swing"})


@dataclass(frozen=True)
class ModeRules:
    mode: str
    hold: str             # protection class: "intraday" or "swing"
    time_limit_min: int   # 0 = none
    max_sessions: int     # 0 = use swing_days
    full_exit_at_t1: bool
    no_gap_carry: bool


RULES = {
    "ultra_scalping": ModeRules("ultra_scalping", "intraday", 20, 0, True, False),
    "scalping": ModeRules("scalping", "intraday", 60, 0, False, False),
    "intraday": ModeRules("intraday", "intraday", 0, 0, False, False),
    "overnight": ModeRules("overnight", "swing", 0, 2, False, True),
    "swing": ModeRules("swing", "swing", 0, 0, False, False),
}


def resolve_mode(trade_mode: str | None, dte: int | None) -> ModeRules:
    """The rules a new entry gets. "auto" (or unset) is the reference rule."""
    mode = str(trade_mode or "auto")
    if mode == "auto":
        mode = "intraday" if dte is not None and dte <= 1 else "swing"
    if mode not in RULES:
        raise ValueError(f"unknown gamma trade mode: {mode}")
    return RULES[mode]
