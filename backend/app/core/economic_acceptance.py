"""Validate an independently reviewed economic report, without tuning its limits.

This checks the report contract and refusal rules. It cannot establish that
market data or human attestations are true; immutable datasets, reproducible
analysis and independent review remain required acceptance work.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Mapping

from app.core.acceptance_evidence import utc_timestamp


def _number(value: Any, *, positive: bool = False) -> float:
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
        raise ValueError("economic metrics must be finite numbers")
    if positive and value <= 0:
        raise ValueError("after-cost expectancy and its lower confidence bound must be positive")
    return float(value)


def validate_economic_report(report: Mapping[str, Any], identity: Mapping[str, str]) -> None:
    for key in ("dataset_id", "strategy_id", "mode", "strategy_version", "config_hash", "runtime_release_sha"):
        if not identity.get(key) or report.get(key) != identity[key]:
            raise ValueError(f"economic report identity mismatch: {key}")
    if report.get("schema_version") != 1:
        raise ValueError("unsupported economic report schema")
    for key in ("data_integrity", "sessions_complete", "no_lookahead", "purge_and_embargo",
                "multiple_testing_recorded", "holdout_unused", "sample_adequacy_reviewed",
                "forward_quote_reconciled", "exceptions_and_missed_fills_counted"):
        if report.get(key) is not True:
            raise ValueError(f"economic requirement unverified: {key}")
    if report.get("forward_evidence_type") not in {"REAL_MARKET_SIM", "REAL_BROKER"}:
        raise ValueError("forward evidence must use observed real market data")
    registered = utc_timestamp(report["hypothesis_registered_at"])
    windows = report.get("oos_windows")
    if not isinstance(windows, list) or len(windows) < 2:
        raise ValueError("multiple non-overlapping OOS windows are required")
    previous_end = registered
    for window in windows:
        start, end = utc_timestamp(window["start"]), utc_timestamp(window["end"])
        if not registered < start < end or start <= previous_end:
            raise ValueError("OOS windows overlap or precede hypothesis registration")
        previous_end = end
        _number(window["net_expectancy"], positive=True)
    observed_at = utc_timestamp(identity["observed_at"]) if identity.get("observed_at") else datetime.now(timezone.utc)
    forward_start, forward_end = utc_timestamp(report["forward_start"]), utc_timestamp(report["forward_end"])
    if not previous_end < forward_start < forward_end <= observed_at:
        raise ValueError("forward window must follow OOS and finish before evidence observation")
    if report.get("confidence_method") != "session_cluster_bootstrap":
        raise ValueError("a dependence-aware session-clustered confidence estimate is required")
    confidence = _number(report.get("confidence_level"))
    if not 0 < confidence < 1:
        raise ValueError("confidence_level must be between zero and one")
    _number(report.get("net_expectancy"), positive=True)
    _number(report.get("expectancy_lower_bound"), positive=True)
    # Limits are preregistered inputs. No default trade count or post-hoc
    # drawdown allowance is chosen by this validator.
    for metric in ("sessions", "trades"):
        required, observed = report.get(f"required_{metric}"), report.get(f"observed_{metric}")
        if type(required) is not int or required <= 0 or type(observed) is not int or observed < required:
            raise ValueError(f"pre-registered {metric} sample is incomplete")
    for metric in ("drawdown", "expected_shortfall", "turnover", "capital_use"):
        observed = _number(report.get(metric))
        limit = _number(report.get(f"max_{metric}"), positive=True)
        if observed < 0 or observed > limit:
            raise ValueError(f"economic risk budget exceeded: {metric}")
    stress = report.get("stress_expectancy", {})
    for scenario in ("fees", "latency", "stop_slippage", "liquidity", "adverse_regime", "best_trade_removed", "best_symbol_removed"):
        _number(stress.get(scenario), positive=True)
