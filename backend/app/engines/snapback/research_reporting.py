"""Fail-closed aggregation for Snapback observed-option research replay.

Historical realized candle P&L is not broker-verified P&L. A reported positive
sum must never silently discard failed, skipped, or unresolved contracts.
This module has no broker, runtime state, or persistence dependencies.
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any


def rejected_plan_count(value: Any) -> int:
    """The current replay returns a count; older replay contracts returned a list."""
    if type(value) is int and value >= 0:
        return value
    if isinstance(value, (list, tuple)):
        return len(value)
    raise ValueError("rejected_plans must be a nonnegative integer or list")


def contract_replay_summary(result: Mapping[str, Any]) -> dict[str, Any]:
    """Extract strictly typed, conservative metrics from one replay.

    An open/unresolved position never contributes a complete contract result,
    even if the research engine has already realized gains on earlier trades.
    """
    if not isinstance(result, Mapping):
        raise ValueError("Snapback replay result must be an object")
    if result.get("research_only") is not True:
        raise ValueError("Snapback result must be expressly research-only")
    trades = result.get("trade_count")
    if type(trades) is not int or trades < 0:
        raise ValueError("Snapback trade_count must be a nonnegative integer")
    wins = result.get("win_rate_pct")
    net = result.get("net_pnl_inr")
    ret = result.get("return_pct")
    dd = result.get("max_realized_drawdown_inr")
    for name, value in (("win_rate_pct", wins), ("net_pnl_inr", net),
                        ("return_pct", ret), ("max_realized_drawdown_inr", dd)):
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError(f"Snapback {name} must be finite")
    if not 0 <= wins <= 100 or dd < 0:
        raise ValueError("Snapback win rate or drawdown is invalid")
    count = rejected_plan_count(result.get("rejected_plans"))
    complete = result.get("complete") is True and result.get("unresolved_position") is None
    return {
        "trades": trades, "win_rate_pct": round(float(wins), 1),
        "net_inr": round(float(net)), "return_pct": round(float(ret), 2),
        "max_dd_inr": round(float(dd)),
        "rejected_plans": count,
        "replay_complete": complete,
        "unresolved_reason": (
            str(result.get("unresolved_reason") or "position_not_closed")[:160]
            if not complete else None
        ),
        "evidence_type": "REAL_MARKET_SIM",
    }


def mode_totals(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Preserve every failed contract in the denominator; never sum unsafe runs."""
    all_rows = list(rows)
    if any(not isinstance(r, Mapping) for r in all_rows):
        raise ValueError("all contract rows must be objects")
    ok = [r for r in all_rows if "trades" in r and r.get("replay_complete") is True
          and r.get("error") is None and r.get("skipped") is None]
    invalid = [r for r in all_rows if "trades" in r and (r.get("replay_complete") is not True
               or r.get("error") is not None or r.get("skipped") is not None)]
    errors = sum("error" in r for r in all_rows)
    skipped = sum("skipped" in r for r in all_rows)
    unresolved = sum(r.get("replay_complete") is False for r in invalid)
    incomplete_coverage = sum(
        not isinstance(r.get("coverage"), Mapping)
        or r["coverage"].get("all_sessions_complete") is not True
        for r in all_rows if "trades" in r
    )
    n = sum(r["trades"] for r in ok)
    net = sum(r["net_inr"] for r in ok)
    profitable = sum(r["net_inr"] > 0 for r in ok)
    completed = len(ok)
    # A positive in-sample subset or modelled tape is never a sufficient G7.
    return {
        "contracts": completed, "contracts_attempted": len(all_rows),
        "contract_errors": errors, "contracts_skipped": skipped,
        "contracts_unresolved": unresolved,
        "contracts_with_incomplete_coverage": incomplete_coverage,
        "research_complete": bool(all_rows) and errors == 0 and skipped == 0
                             and unresolved == 0 and incomplete_coverage == 0
                             and completed == len(all_rows),
        "trades": n, "net_inr": round(net),
        "profitable_contracts": profitable,
        "avg_win_rate_pct": round(sum(r["win_rate_pct"] * r["trades"] for r in ok) / n, 1) if n else None,
        "promotion_eligible": False, "broker_verified": False,
        "result_basis": "closed-contract real-option-candle simulation, not broker fills",
    }


def choose_option_contract(candidates: Sequence[Mapping[str, Any]],
                           spot: float) -> Mapping[str, Any] | None:
    """Deterministic nearest-strike choice among fully verified metadata rows.

    The caller must separately freeze a date-effective contract universe.
    Malformed strike/token/lot metadata must never crash the entire research
    run or be treated as a valid listed option.
    """
    if isinstance(spot, bool) or not isinstance(spot, (int, float)) or not math.isfinite(spot) or spot <= 0:
        return None
    valid = []
    for row in candidates:
        if not isinstance(row, Mapping):
            continue
        try:
            strike = float(row.get("strike"))
            token = int(row.get("instrument_token"))
            lot = int(row.get("lot_size"))
            symbol = str(row.get("tradingsymbol") or "").strip()
        except (ValueError, TypeError, OverflowError):
            continue
        if not (math.isfinite(strike) and strike > 0 and token > 0 and lot > 0 and symbol):
            continue
        # Truncating fractional exchange identities (e.g. lot_size=75.5)
        # would invent a different order unit.
        raw_token, raw_lot = row.get("instrument_token"), row.get("lot_size")
        if isinstance(raw_token, bool) or isinstance(raw_lot, bool):
            continue
        try:
            if float(raw_token) != token or float(raw_lot) != lot:
                continue
        except (ValueError, TypeError, OverflowError):
            continue
        valid.append((abs(strike - spot), strike, symbol, row))
    return min(valid, key=lambda x: (x[0], x[1], x[2]))[3] if valid else None


def eligible_expiries(contracts: Sequence[Mapping[str, Any]], today: date) -> list[str]:
    """Exchange-date-valid future expiries only; 'None' is not a future date."""
    future = set()
    for row in contracts:
        if not isinstance(row, Mapping):
            continue
        try:
            expiry = date.fromisoformat(str(row.get("expiry"))[:10])
        except (ValueError, TypeError):
            continue
        if expiry > today:
            future.add(expiry.isoformat())
    return sorted(future)
