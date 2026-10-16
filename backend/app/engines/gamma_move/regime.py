"""GAMMA-UNWIND client four-gate tiering.

All four gates run on every list in S1.6.0. Missing input is not a pass.
4 passes => ENTRY, 3 => STRONG_WATCHLIST, 0-2 => WATCHLIST.
"""
from __future__ import annotations
import math
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

from .config import GammaMoveConfig
from .models import Candle, GateContext, WatchContract

IST = timezone(timedelta(hours=5, minutes=30))
TF_MS = 5 * 60_000
EPS = 1e-9
GATE_NAMES = ("zone", "liquidity", "direction", "unwind")


def zone_gap_pct(option_type: str, spot: float | None,
                 edge: float | None) -> float | None:
    if not spot or not edge or spot <= 0:
        return None
    if option_type.upper() == "PE":
        return (spot - edge) / spot * 100.0
    return (edge - spot) / spot * 100.0


def zone_limit(atr_pct: float | None, cfg: GammaMoveConfig) -> float:
    base = float(cfg.zone_max_gap_pct)
    if (cfg.v1_zone_atr_k <= 0 or atr_pct is None
            or math.isnan(float(atr_pct))):
        return base
    return max(
        base,
        min(cfg.v1_zone_atr_k * float(atr_pct), cfg.v1_zone_max_pct),
    )


def direction(underlying_bars: Sequence[Candle], signal_ts_ms: int,
              option_type: str, spot: float | None) -> tuple[bool, bool] | None:
    day = datetime.fromtimestamp(signal_ts_ms / 1000, IST).date()
    bars = [
        b for b in underlying_bars
        if b.ts_ms + TF_MS <= signal_ts_ms
        and datetime.fromtimestamp(b.ts_ms / 1000, IST).date() == day
    ]
    if len(bars) < 2:
        return None
    total_volume = sum(b.volume for b in bars)
    if total_volume <= 0:
        return None
    vwap = sum(
        ((b.high + b.low + b.close) / 3.0) * b.volume for b in bars
    ) / total_volume
    current_spot = spot if spot and spot > 0 else bars[-1].close
    drift_window = bars[-6:]
    drift = drift_window[-1].close - drift_window[0].close
    sign = -1.0 if option_type.upper() == "PE" else 1.0
    return (
        sign * (current_spot - vwap) > 0,
        sign * drift > 0,
    )


def _unwind(snapshot: Mapping[str, Any], tick: float,
            cfg: GammaMoveConfig) -> bool | None:
    try:
        e2_close = float(snapshot["cycle_e2_close"])
        peak_close = float(snapshot["oi_peak_bar_close"])
    except (KeyError, TypeError, ValueError):
        return None
    return (
        e2_close
        >= peak_close + cfg.unwind_min_ticks * float(tick) - EPS
    )


def _quadrant(snapshot: Mapping[str, Any], tick: float) -> str | None:
    try:
        peak_close = float(snapshot["oi_peak_bar_close"])
        peak_oi = float(snapshot["oi_peak_bar_oi"])
        price = float(snapshot["signal_price"])
        oi_now = float(snapshot["oi_now"])
    except (KeyError, TypeError, ValueError):
        return None
    dp = price - peak_close
    doi = oi_now - peak_oi
    price_dir = (
        1 if dp >= tick - EPS
        else (-1 if dp <= -tick + EPS else 0)
    )
    if price_dir == 0 or doi == 0:
        return "FLAT"
    if doi < 0:
        return "SC" if price_dir > 0 else "LU"
    return "LB" if price_dir > 0 else "SB"


def build_gates(contract: WatchContract, *, spot: float | None,
                option_bars: Sequence[Candle],
                underlying_bars: Sequence[Candle],
                snapshot: Mapping[str, Any],
                cfg: GammaMoveConfig) -> GateContext:
    failed: list[str] = []
    unknown: list[str] = []
    gap = zone_gap_pct(
        contract.instrument.option_type, spot, contract.zone_edge
    )
    limit = zone_limit(contract.atr_pct, cfg)

    if not cfg.gates_enabled:
        disabled = set(GATE_NAMES)
    else:
        disabled: set[str] = set()
        if not cfg.direction_enabled:
            disabled.add("direction")
        if not cfg.unwind_enabled:
            disabled.add("unwind")

    if "zone" not in disabled:
        if gap is None:
            unknown.append("zone")
        elif abs(gap) > limit + EPS:
            failed.append("zone")

    # Reference gates.liquidity(): use whatever closed bars exist inside
    # the last liq_sma_bars window. Fewer than 20 is NOT unknown; only an empty
    # window or unknown/invalid lot size is unknown.
    vols = [float(b.volume) for b in option_bars[-cfg.liq_sma_bars:]]
    if not vols or contract.instrument.lot_size <= 0:
        avg_lots = None
        zero_bars = None
    else:
        avg_lots = (
            sum(vols) / len(vols) / contract.instrument.lot_size
        )
        zero_bars = sum(1 for v in vols if v <= 0)
    if "liquidity" not in disabled:
        if avg_lots is None or zero_bars is None:
            unknown.append("liquidity")
        elif (avg_lots < cfg.liq_min_avg_lots - EPS
              or zero_bars > cfg.liq_max_zero_vol_bars):
            failed.append("liquidity")

    f1 = f2 = None
    if "direction" not in disabled:
        result = direction(
            underlying_bars,
            int(snapshot.get("signal_ts_ms") or 0),
            contract.instrument.option_type,
            spot,
        )
        if result is None:
            unknown.append("direction")
        else:
            f1, f2 = result
            # 0.8.6: If stock is moving in opposite direction, entry won't be triggered
            # to prevent SL hunting from big players.
            ok = (
                (f1 and f2)
                if cfg.v1_direction_mode == "all"
                else (f1 or f2)
            )
            if not ok:
                failed.append("direction")

    unwind = None
    if "unwind" not in disabled:
        unwind = _unwind(
            snapshot, contract.instrument.tick_size, cfg
        )
        if unwind is None:
            unknown.append("unwind")
        elif not unwind:
            failed.append("unwind")

    passes = tuple(
        gate for gate in GATE_NAMES
        if gate not in failed
        and gate not in unknown
        and gate not in disabled
    )
    return GateContext(
        gap, limit, avg_lots, zero_bars,
        f1, f2, unwind,
        tuple(failed), tuple(unknown), passes,
        _quadrant(snapshot, contract.instrument.tick_size),
    )


def signal_warnings(ts_ms: int, dte: int | None,
                    cfg: GammaMoveConfig) -> tuple[str, ...]:
    out: list[str] = []
    when = datetime.fromtimestamp(ts_ms / 1000, IST)
    if when.strftime("%H:%M") >= cfg.late_day_warn_ist:
        out.append("Day is closing - plan accordingly")
    if dte is not None and dte <= cfg.expiry_warn_dte:
        out.append(
            f"Expires in {dte} trading days - plan the hold accordingly"
        )
    return tuple(out)
