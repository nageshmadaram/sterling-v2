"""Gamma-Unwind V1 client 0.8.2 (strategy spec S1.6.0).

This package keeps the historic Sterling engine id "gamma_move" for stable
routing/persistence, but the strategy implementation is now the user-supplied
Gamma-Unwind client strategy end to end.
"""
from __future__ import annotations

from .config import (
    CLIENT_TOP_N_OI, DIRECTION_MODES, GammaMoveConfig, REFERENCE_VERSION,
    SPEC_VERSION, STOP_MODES,
)
from .models import (
    Candle, Evaluation, GammaSignal, GateContext, InstrumentRef, PositionState,
    T1_PCT, T2_PCT, TIER_ENTRY, TIER_STRONG_WATCHLIST, TIER_WATCHLIST,
    TradeRecord, WatchContract, align_to_tick, q2, target_price,
)
from .levels import Levels, Zone, ZoneState, compute_zones, daily_atr_pct, pick_levels
from .selection import ListSpec, lists_for, select_strikes, trading_days_between
from .trigger import (
    ContractEngine, ContractState, a_holds, evaluate, oi_drop, vol_sma,
    zero_volume_slots,
)
from .regime import build_gates, direction, signal_warnings, zone_gap_pct, zone_limit
from .exit import (
    d68_action, exit_order_price, hold_at_entry, realised_inr,
    session_end_action, tick_action,
)

STRATEGY_ID = "gamma_move"
STRATEGY_NAME = "Gamma Unwind V1"
CONTRACT_VERSION = f"GU-{REFERENCE_VERSION}-{SPEC_VERSION}"

__all__ = [
    "STRATEGY_ID", "STRATEGY_NAME", "CONTRACT_VERSION", "REFERENCE_VERSION",
    "SPEC_VERSION", "CLIENT_TOP_N_OI", "STOP_MODES", "DIRECTION_MODES",
    "GammaMoveConfig", "Candle", "InstrumentRef", "WatchContract", "Evaluation",
    "GateContext", "GammaSignal", "PositionState", "TradeRecord", "TIER_ENTRY",
    "TIER_STRONG_WATCHLIST", "TIER_WATCHLIST", "T1_PCT", "T2_PCT",
    "align_to_tick", "q2", "target_price", "Zone", "ZoneState", "Levels",
    "compute_zones", "pick_levels", "daily_atr_pct", "ListSpec", "lists_for",
    "select_strikes", "trading_days_between", "ContractEngine", "ContractState",
    "oi_drop", "a_holds", "evaluate", "vol_sma", "zero_volume_slots",
    "build_gates", "direction", "zone_gap_pct", "zone_limit", "signal_warnings",
    "hold_at_entry", "d68_action", "tick_action", "session_end_action",
    "exit_order_price", "realised_inr",
]
