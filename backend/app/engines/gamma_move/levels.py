"""GAMMA-UNWIND daily supply/demand zones (client 0.8.2, S1.6.0).

Exact strategy semantics: Pine-style Wilder ATR, 10/10 pivots with the client
left_strict tie rule, guard-band de-duplication, close/wick retirement, newest
zones first, and nearest support/resistance selection.
"""
from __future__ import annotations
import math
from dataclasses import dataclass, field
from typing import Literal, Sequence

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

from .config import GammaMoveConfig
from .models import Candle


@dataclass(frozen=True)
class Zone:
    top: float
    bottom: float
    mid: float
    formed_bar_index: int
    confirmed_bar_index: int
    side: Literal["sell", "buy"]


@dataclass
class ZoneState:
    live_sell: list[Zone] = field(default_factory=list)
    live_buy: list[Zone] = field(default_factory=list)
    n_bars: int = 0
    atr_last: float = math.nan


@dataclass(frozen=True)
class Levels:
    ref_price: float
    support: Zone | None
    resistance: Zone | None
    dist_support_pct: float | None
    dist_resistance_pct: float | None


def true_range(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> np.ndarray:
    tr = high - low
    if len(tr) > 1:
        pc = close[:-1]
        tr[1:] = np.maximum(
            np.maximum(high[1:] - low[1:], np.abs(high[1:] - pc)),
            np.abs(low[1:] - pc),
        )
    return tr


def rma(src: np.ndarray, length: int) -> np.ndarray:
    out = np.full(len(src), np.nan)
    if length < 1 or len(src) < length:
        return out
    alpha = 1.0 / length
    prev = float(np.mean(src[:length]))
    out[length - 1] = prev
    for i in range(length, len(src)):
        prev = alpha * float(src[i]) + (1.0 - alpha) * prev
        out[i] = prev
    return out


def atr_series(high: np.ndarray, low: np.ndarray, close: np.ndarray,
               length: int) -> np.ndarray:
    return rma(true_range(high, low, close), length)


def _pivots(src: np.ndarray, length: int, high_side: bool) -> np.ndarray:
    """Pivot value on its confirmation bar.

    Client reference tie rule is left_strict: center > every left value and
    >= every right value for highs; lows mirror it.
    """
    n = len(src)
    out = np.full(n, np.nan)
    width = 2 * length + 1
    if n < width:
        return out
    win = sliding_window_view(src, width)
    center = win[:, length]
    left = win[:, :length]
    right = win[:, length + 1:]
    if high_side:
        ok = (center > left.max(axis=1)) & (center >= right.max(axis=1))
    else:
        ok = (center < left.min(axis=1)) & (center <= right.min(axis=1))
    ok &= ~np.isnan(center)
    out[width - 1:] = np.where(ok, center, np.nan)
    return out


def _is_clear(zones: list[Zone], mid: float, band: float) -> bool:
    # NaN comparisons are False, matching Pine na semantics.
    return all(not (abs(mid - z.mid) <= band) for z in zones)


def compute_zones(candles: Sequence[Candle], cfg: GammaMoveConfig) -> ZoneState:
    if not candles:
        return ZoneState()
    high = np.asarray([c.high for c in candles], dtype=float)
    low = np.asarray([c.low for c in candles], dtype=float)
    close = np.asarray([c.close for c in candles], dtype=float)
    atr_v = atr_series(high, low, close, cfg.zone_atr_len)
    ph = _pivots(high, cfg.zone_pivot_len, True)
    pl = _pivots(low, cfg.zone_pivot_len, False)
    sell_probe = high if cfg.zone_break_on_wick else close
    buy_probe = low if cfg.zone_break_on_wick else close
    depth = cfg.zone_depth / 10.0
    sells: list[Zone] = []
    buys: list[Zone] = []
    for i in range(len(candles)):
        sells = [z for z in sells if not (sell_probe[i] > z.top)]
        buys = [z for z in buys if not (buy_probe[i] < z.bottom)]
        a = float(atr_v[i])
        buffer = a * depth
        band = a * cfg.zone_guard_mult
        formed = i - cfg.zone_pivot_len
        if not math.isnan(ph[i]):
            top = float(ph[i])
            bottom = top - buffer
            mid = (top + bottom) / 2.0
            if _is_clear(sells, mid, band):
                sells.insert(0, Zone(top, bottom, mid, formed, i, "sell"))
                del sells[cfg.zone_memory:]
        if not math.isnan(pl[i]):
            bottom = float(pl[i])
            top = bottom + buffer
            mid = (top + bottom) / 2.0
            if _is_clear(buys, mid, band):
                buys.insert(0, Zone(top, bottom, mid, formed, i, "buy"))
                del buys[cfg.zone_memory:]
    return ZoneState(
        live_sell=sells,
        live_buy=buys,
        n_bars=len(candles),
        atr_last=float(atr_v[-1]),
    )


def pick_levels(state: ZoneState, ref_price: float) -> Levels:
    support = next(
        (z for z in sorted(state.live_buy,
                           key=lambda z: (-z.top, -z.confirmed_bar_index))
         if math.isfinite(z.top) and z.top < ref_price),
        None,
    )
    resistance = next(
        (z for z in sorted(state.live_sell,
                           key=lambda z: (z.bottom, -z.confirmed_bar_index))
         if math.isfinite(z.bottom) and z.bottom > ref_price),
        None,
    )
    ds = ((ref_price - support.top) / ref_price * 100.0) if support else None
    dr = ((resistance.bottom - ref_price) / ref_price * 100.0) if resistance else None
    return Levels(ref_price, support, resistance, ds, dr)


def daily_atr_pct(candles: Sequence[Candle], n: int = 14,
                  window: int = 40) -> float | None:
    cs = list(candles)[-window:]
    if len(cs) < n + 1:
        return None
    trs = [
        max(c.high - c.low, abs(c.high - p.close), abs(c.low - p.close))
        for p, c in zip(cs, cs[1:])
    ]
    atr = sum(trs[:n]) / n
    for tr in trs[n:]:
        atr = (atr * (n - 1) + tr) / n
    last = cs[-1].close
    return round(atr / last * 100.0, 4) if last > 0 and math.isfinite(atr) else None
