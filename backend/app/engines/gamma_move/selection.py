"""GAMMA-UNWIND expiry/list and client HOI-1 strike selection."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable, Literal, Sequence

from .config import CLIENT_TOP_N_OI, GammaMoveConfig

EPS = 1e-9
Side = Literal["PE", "CE"]


@dataclass(frozen=True)
class ListSpec:
    list_name: str
    side: Side
    expiry_date: date
    series: Literal["CURRENT", "NEXT"]


def trading_days_between(start: date, end: date) -> int:
    """Reference DTE: trading days with start < d <= end."""
    if end <= start:
        return 0
    from app.services.kite_engine.market_hours import is_trading_day
    n = 0
    day = start + timedelta(days=1)
    while day <= end:
        if is_trading_day(day, "NFO"):
            n += 1
        day += timedelta(days=1)
    return n


def lists_for(trade_date: date, expiries: Iterable[str | date],
              cfg: GammaMoveConfig) -> list[ListSpec]:
    exps = sorted({
        e if isinstance(e, date) else date.fromisoformat(str(e)[:10])
        for e in expiries
    })
    current = next((e for e in exps if e >= trade_date), None)
    if current is None:
        return []
    dte = trading_days_between(trade_date, current)
    if dte > cfg.expiry_dte_threshold:
        return [
            ListSpec("BREAKDOWN-PE", "PE", current, "CURRENT"),
            ListSpec("BREAKOUT-CE", "CE", current, "CURRENT"),
        ]
    nxt = next((e for e in exps if e > current), None)
    if nxt is None:
        return []
    return [
        ListSpec("EXPIRY-PE", "PE", current, "CURRENT"),
        ListSpec("EXPIRY-CE", "CE", current, "CURRENT"),
        ListSpec("BREAKDOWN-PE", "PE", nxt, "NEXT"),
        ListSpec("BREAKOUT-CE", "CE", nxt, "NEXT"),
    ]


def in_band(strike: float, zone_edge: float, band_pct: float) -> bool:
    return abs(strike - zone_edge) * 100.0 <= band_pct * zone_edge + EPS


def select_strikes(rows: Sequence[dict], side: Side, ref_price: float,
                   zone_edge: float, lot_size: int,
                   cfg: GammaMoveConfig) -> list[dict]:
    """Client edition Step 2: one highest-OI OTM strike (HOI-1).

    Previous-session turnover is fail-open only when the broker cannot provide
    the previous volume/close values, matching the client reference.
    """
    candidates: list[dict] = []
    for row in rows:
        if str(row.get("option_type") or row.get("instrument_type") or "").upper() != side:
            continue
        strike = float(row.get("strike") or 0.0)
        oi = int(float(row.get("oi") or 0))
        if strike <= 0 or oi <= 0:
            continue
        if not (strike < ref_price if side == "PE" else strike > ref_price):
            continue
        if not in_band(strike, zone_edge, cfg.strike_band_pct):
            continue
        if lot_size > 0 and oi < cfg.min_oi_lots * lot_size - EPS:
            continue
        previous_volume = row.get("previous_volume")
        previous_close = row.get("previous_close")
        if (cfg.min_prev_turnover_rs > 0
                and previous_volume is not None
                and previous_close is not None
                and float(previous_volume) * float(previous_close)
                    < cfg.min_prev_turnover_rs - EPS):
            continue
        tick = float(row.get("tick_size") or 0.05)
        premium = row.get("last_price", row.get("ltp"))
        if (cfg.min_premium_ticks > 0
                and (premium is None
                     or float(premium) < cfg.min_premium_ticks * tick - EPS)):
            continue
        candidates.append(row)
    candidates.sort(key=lambda row: (
        -int(float(row.get("oi") or 0)),
        abs(float(row.get("strike") or 0) - zone_edge),
        float(row.get("strike") or 0),
    ))
    total = len(candidates)
    out: list[dict] = []
    # Client package is hard-pinned to one contract per stock/list.
    for idx, row in enumerate(candidates[:CLIENT_TOP_N_OI], 1):
        item = dict(row)
        item["tag"] = f"HOI-{idx}"
        item["candidates_n"] = total
        out.append(item)
    return out
