"""Research-only Snapback challenger: causal premium move and stressed-cost gate.

This is a NEW strategy identity. It cannot authorize broker orders or inherit
the baseline's historical returns. Prior observed option candles are merely a
plausibility screen, not a forecast or an executable quote.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

IST = timezone(timedelta(hours=5, minutes=30))
CHALLENGER_ID = "snapback_intraday_cost_feasible_v1"
EXTRA_ROUNDTRIP_COST_POINTS = 0.5
MIN_PRIOR_WINDOWS = 12


def _session(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, IST).date().isoformat()


def _ceil_tick(points: float, tick: float = 0.05) -> float:
    return round(math.ceil((points - 1e-10) / tick) * tick, 2)


def _valid_price(x: Any) -> bool:
    return type(x) in (int, float) and math.isfinite(x) and x > 0


def prior_premium_excursions(
    times: Sequence[float], opens: Sequence[float], highs: Sequence[float],
    closes: Sequence[float], *, entry_time: float, bar_seconds: int,
    horizon_bars: int, min_windows: int = MIN_PRIOR_WINDOWS,
) -> tuple[float | None, int]:
    """75th percentile of earlier same-session favorable option excursions.

    Every window ends BEFORE the proposed entry. This is deliberately an
    optimistic OHLC upper bound, not a fillable target or return estimate.
    """
    n = len(times)
    if (len(opens) != n or len(highs) != n or len(closes) != n
            or bar_seconds <= 0 or horizon_bars < 2 or min_windows < 1
            or not _valid_price(entry_time)):
        return None, 0
    if n < horizon_bars + min_windows - 1:
        return None, 0
    # Bound memory and work regardless of historical tape size.
    start = max(0, n - 512)
    observations: list[float] = []
    for i in range(start, n):
        if (not all(_valid_price(float(x)) for x in
                    (times[i], opens[i], highs[i], closes[i]))
                or float(highs[i]) < max(float(opens[i]), float(closes[i]))
                or float(times[i]) + bar_seconds > entry_time):
            return None, 0
        if i > start and float(times[i]) <= float(times[i - 1]):
            return None, 0
    for j in range(start, n - horizon_bars + 1):
        last = j + horizon_bars
        if _session(float(times[j])) != _session(float(times[last - 1])):
            continue
        if any(abs(float(times[k + 1]) - float(times[k]) - bar_seconds) > 0.01
               for k in range(j, last - 1)):
            continue
        excursion = max(float(x) for x in highs[j:last]) - float(opens[j])
        observations.append(max(0.0, excursion))
    if len(observations) < min_windows:
        return None, len(observations)
    observations.sort()
    idx = math.floor(0.75 * (len(observations) - 1))
    return round(observations[idx], 6), len(observations)


def assess_cost_feasible_plan(
    baseline: Mapping[str, Any], *, times: Sequence[float],
    opens: Sequence[float], highs: Sequence[float], closes: Sequence[float],
    entry_time: float, bar_seconds: int, horizon_bars: int,
    min_rr: float = 1.0, lock_points: float = 1.0,
    extra_cost_points: float = EXTRA_ROUNDTRIP_COST_POINTS,
) -> dict[str, Any]:
    """Return a new independent candidate plan, NEVER a LIVE authorization."""
    plan = dict(baseline)
    plan["candidate_policy"] = CHALLENGER_ID
    plan["research_only"] = True

    def reject(reason: str, **facts: Any) -> dict[str, Any]:
        return {
            **plan, **facts, "accepted": False, "lots": 0, "quantity": 0,
            "rejection_reasons": [*plan.get("rejection_reasons", []), reason],
        }

    if baseline.get("accepted") is not True:
        return reject("baseline_plan_ineligible")
    try:
        qty = baseline["quantity"]
        entry = float(baseline["entry"])
        stop = float(baseline["stop_points"])
        var_cost = float(baseline["estimated_variable_cost_points"])
        fixed_cost = float(baseline["estimated_fixed_cost_inr"])
        risk_budget = float(baseline["risk_budget_inr"])
        original_target = float(baseline["effective_target_points"])
        if (type(qty) is not int or qty <= 0
                or not all(math.isfinite(x) for x in
                           (entry, stop, var_cost, fixed_cost, risk_budget,
                            original_target, min_rr, lock_points, extra_cost_points))
                or min(entry, stop, risk_budget) <= 0
                or min(var_cost, fixed_cost, original_target, min_rr,
                       lock_points, extra_cost_points) < 0):
            raise ValueError("invalid cost/quantity or risk inputs")
    except (KeyError, TypeError, ValueError, OverflowError):
        return reject("invalid_challenger_economic_inputs")

    unit_cost = var_cost + fixed_cost / qty + extra_cost_points
    required = unit_cost + min_rr * (stop + unit_cost)
    target_points = _ceil_tick(max(original_target, required))
    stressed_risk = (stop + var_cost + extra_cost_points) * qty + fixed_cost
    facts = {
        "additional_cost_stress_points": extra_cost_points,
        "stressed_minimum_target_points": target_points,
        "stressed_stop_risk_inr": round(stressed_risk, 6),
    }
    if stressed_risk > risk_budget + 1e-8:
        return reject("additional_cost_exceeds_risk_budget", **facts)

    ceiling, observed = prior_premium_excursions(
        times, opens, highs, closes, entry_time=entry_time,
        bar_seconds=bar_seconds, horizon_bars=horizon_bars,
    )
    facts.update({
        "prior_excursion_windows": observed,
        "prior_excursion_p75_points": ceiling,
        "excursion_basis": "causal_prior_option_ohlc_highs_not_executable_fills",
    })
    if ceiling is None:
        return reject("insufficient_causal_option_premium_history", **facts)
    if target_points > ceiling + 1e-8:
        return reject("cost_stressed_target_exceeds_prior_excursion_p75", **facts)

    breakeven = _ceil_tick(entry + unit_cost)
    profit_lock = _ceil_tick(entry + unit_cost + lock_points)
    stressed_target_net = (target_points - var_cost - extra_cost_points) * qty - fixed_cost
    return {
        **plan, **facts, "accepted": True, "rejection_reasons": [],
        "target": _ceil_tick(entry + target_points),
        "runner_trigger": _ceil_tick(entry + target_points),
        "effective_target_points": target_points,
        # The replay deducts variable cost from each closed trade. A higher
        # target without this field would fake challenger net returns.
        "estimated_variable_cost_points": var_cost + extra_cost_points,
        "estimated_total_cost_inr": round(
            (var_cost + extra_cost_points) * qty + fixed_cost, 6),
        "breakeven": breakeven, "profit_lock": profit_lock,
        "estimated_net_target_inr": round(stressed_target_net, 6),
        "estimated_stop_loss_inr": round(stressed_risk, 6),
        "net_reward_risk": round(stressed_target_net / stressed_risk, 8),
    }
