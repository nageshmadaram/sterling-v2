"""Fail-closed aggregation for Snapback observed-option research replay.

Historical realized candle P&L is not broker-verified P&L. A reported positive
sum must never silently discard failed, skipped, or unresolved contracts.
This module has no broker, runtime state, or persistence dependencies.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Any


def rejected_plan_count(value: Any) -> int:
    """The current replay returns a count; older replay contracts returned a list."""
    if type(value) is int and value >= 0:
        return value
    if isinstance(value, (list, tuple)):
        return len(value)
    raise ValueError("rejected_plans must be a nonnegative integer or list")


def closed_trade_audit(result: Mapping[str, Any]) -> dict[str, Any]:
    """Store independently re-summable, simulated CLOSED trade receipts only.

    A SHA-256 detects accidental changes; it is not an exchange signature or a
    broker affirmation. The full minimal records are needed for an outside
    analyst to recompute quantities, costs, drawdowns and chronology.
    """
    trades = result.get("trades")
    count = result.get("trade_count")
    if type(count) is not int or count < 0 or not isinstance(trades, list) or len(trades) != count:
        raise ValueError("closed-trade record count inconsistent")
    receipts = []
    for trade in trades:
        if not isinstance(trade, Mapping) or trade.get("exited") is not True:
            raise ValueError("research trade receipt must be fully closed")
        qty = trade.get("quantity")
        if type(qty) is not int or qty <= 0:
            raise ValueError("invalid simulated trade quantity")
        numeric = {}
        for key in ("entry_time", "exit_time", "entry", "exit_price",
                    "net_pnl_inr", "estimated_cost_inr"):
            value = trade.get(key)
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError(f"invalid simulated trade {key}")
            numeric[key] = float(value)
        if (numeric["exit_time"] < numeric["entry_time"]
                or numeric["entry"] <= 0 or numeric["exit_price"] <= 0
                or numeric["estimated_cost_inr"] < 0):
            raise ValueError("incoherent simulated trade chronology, price or cost")
        gross = (numeric["exit_price"] - numeric["entry"]) * qty
        if abs(gross - numeric["estimated_cost_inr"] - numeric["net_pnl_inr"]) > 0.01:
            raise ValueError("simulated trade net P&L does not reconcile")
        receipts.append({
            "quantity": qty, **numeric,
            "exit_reason": str(trade.get("exit_reason") or ""),
        })
    total_net = sum(r["net_pnl_inr"] for r in receipts)
    reported_net = result.get("net_pnl_inr")
    if (type(reported_net) not in (int, float)
            or not math.isfinite(reported_net)
            or abs(total_net - float(reported_net)) > 0.01):
        raise ValueError("simulated closed trade totals contradict replay P&L")
    blob = json.dumps(receipts, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")
    return {
        "evidence_type": "SIMULATED_CLOSED_OPTION_TRADES",
        "broker_verified": False,
        "records": receipts,
        "sha256": hashlib.sha256(blob).hexdigest(),
        "closed_trades": len(receipts),
        "net_pnl_inr_exact": round(sum(r["net_pnl_inr"] for r in receipts), 6),
    }


_STRESS_POINTS = (0.5, 1.0, 2.0)


def incremental_execution_cost_stress(result: Mapping[str, Any]) -> dict[str, Any]:
    """Conservative extra execution-cost sensitivity on actual replay trades.

    Each shock is EXTRA round-trip option premium points per unit, on top of
    the strategy's already deducted fee/spread/slippage assumptions. These
    are hypothetical stresses, never actual broker costs or a G7 receipt.
    Missing or inconsistent trade-level receipts fail closed: do not
    invent quantities from a count or manufacture a stressed P&L.
    """
    trades = result.get("trades")
    count = result.get("trade_count")
    if trades is None:
        return {"available": False, "reason": "TRADE_LEVEL_EVIDENCE_MISSING",
                "stressed_net_inr": None}
    if not isinstance(trades, list) or len(trades) != count:
        return {"available": False, "reason": "TRADE_LEVEL_COUNT_MISMATCH",
                "stressed_net_inr": None}
    if not trades:
        return {"available": False, "reason": "NO_CLOSED_TRADES",
                "stressed_net_inr": None}
    exact_net, total_units = 0.0, 0
    for trade in trades:
        if not isinstance(trade, Mapping):
            return {"available": False, "reason": "INVALID_TRADE_EVIDENCE",
                    "stressed_net_inr": None}
        qty, pnl = trade.get("quantity"), trade.get("net_pnl_inr")
        if (type(qty) is not int or qty <= 0 or type(pnl) not in (int, float)
                or not math.isfinite(pnl)):
            return {"available": False, "reason": "INVALID_TRADE_EVIDENCE",
                    "stressed_net_inr": None}
        total_units += qty
        exact_net += float(pnl)
    reported = result.get("net_pnl_inr")
    if (type(reported) not in (int, float) or not math.isfinite(reported)
            or abs(exact_net - float(reported)) > 0.01):
        return {"available": False, "reason": "TRADE_PNL_TOTAL_MISMATCH",
                "stressed_net_inr": None}
    return {
        "available": True,
        "basis": "additional_hypothetical_roundtrip_option_premium_points_per_unit",
        "total_option_units_traded": total_units,
        "closed_trades": count,
        "baseline_net_inr": round(exact_net, 6),
        "extra_cost_break_even_points": round(exact_net / total_units, 6),
        "stressed_net_inr": {
            format(shock, "g"): round(exact_net - shock * total_units, 6)
            for shock in _STRESS_POINTS
        },
    }


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
    stress = incremental_execution_cost_stress(result)
    complete = result.get("complete") is True and result.get("unresolved_position") is None
    return {
        "trades": trades, "win_rate_pct": round(float(wins), 1),
        "net_inr": round(float(net)), "net_inr_exact": float(net),
        "return_pct": round(float(ret), 2),
        "max_dd_inr": round(float(dd)),
        "rejected_plans": count,
        "replay_complete": complete,
        "additional_cost_stress": stress,
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
    # Never round EACH contract before aggregation. A handful of small
    # gains rounded to zero can flip a near-break-even mode's verdict.
    net_exact = sum(r.get("net_inr_exact", r["net_inr"]) for r in ok)
    profitable = sum(r.get("net_inr_exact", r["net_inr"]) > 0 for r in ok)
    stress_complete = bool(ok) and all(
        isinstance(r.get("additional_cost_stress"), Mapping)
        and r["additional_cost_stress"].get("available") is True for r in ok
    )
    stress_totals = ({
        format(shock, "g"): round(sum(
            r["additional_cost_stress"]["stressed_net_inr"][format(shock, "g")]
            for r in ok
        ), 6) for shock in _STRESS_POINTS
    } if stress_complete else None)
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
        "trades": n, "net_inr": round(net_exact),
        "net_inr_exact": round(net_exact, 6),
        "profitable_contracts": profitable,
        "additional_cost_stress_complete": stress_complete,
        "additional_cost_stress_net_inr": stress_totals,
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


def write_research_report_once(path: Path, report: Mapping[str, Any]) -> None:
    """Atomically create a dated diagnostic report, never overwrite evidence.

    Write and fsync to an exclusive temporary file in the same directory, then
    hardlink into its final name. The link operation is atomic and fails if
    another run already published that date. Disk-full and JSON failures leave
    no partial final JSON file. This is research, NOT an acceptance receipt.
    """
    if not isinstance(report, Mapping):
        raise ValueError("Snapback report must be an object")
    payload = (json.dumps(report, indent=2, allow_nan=False, default=str) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as out:
            out.write(payload)
            out.flush()
            os.fsync(out.fileno())
        # Unlike os.replace, link() cannot overwrite the historical report.
        os.link(temporary, path)
    finally:
        os.unlink(temporary)
