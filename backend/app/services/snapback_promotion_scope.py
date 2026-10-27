"""Fail-closed provenance check for Snapback's prospective promotion evidence.

A session count scoped to experiment/build is not enough: an outcome produced
by an older experiment might still share a runtime SHA and inflate the number
of completed trades. The immutable Day-T opportunity's signal timestamp must
belong to a complete session in the current experiment and frozen build.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Mapping, Sequence


def _session_date(value: Any) -> str:
    if not isinstance(value, str) or len(value) < 10:
        return ""
    try:
        return date.fromisoformat(value[:10]).isoformat()
    except ValueError:
        return ""


def out_of_scope_trade_errors(
    *,
    sessions: Sequence[Mapping[str, Any]],
    opportunities: Sequence[Mapping[str, Any]],
    outcomes: Sequence[Mapping[str, Any]],
    experiment_id: str,
    runtime_build_sha: str,
) -> list[str]:
    """Report missing/foreign Day-T session provenance; never trim bad trades.

    Returns errors to the single promotion authority. It does not select an
    outcome, calculate P&L, or issue a passing release decision.
    """
    if not experiment_id or not runtime_build_sha:
        return ["promotion experiment or runtime build identity unavailable"]

    admissible_days = {
        _session_date(s.get("session_date"))
        for s in sessions
        if (s.get("experiment_id") == experiment_id
            and s.get("runtime_build_sha") == runtime_build_sha)
    } - {""}

    by_opportunity: dict[str, list[Mapping[str, Any]]] = {}
    for opp in opportunities:
        oid = str(opp.get("opportunity_id") or "")
        if oid:
            by_opportunity.setdefault(oid, []).append(opp)

    errors: list[str] = []
    for index, outcome in enumerate(outcomes):
        oid = str(outcome.get("opportunity_id") or "")
        label = oid or f"outcome_row_{index}"
        candidates = by_opportunity.get(oid, []) if oid else []
        if len(candidates) != 1:
            errors.append(
                f"{label}: cannot link exactly one Day-T opportunity for this outcome"
            )
            continue
        signal_day = _session_date(candidates[0].get("signal_timestamp"))
        if not signal_day or signal_day not in admissible_days:
            errors.append(
                f"{label}: signal day {signal_day or 'UNKNOWN'} is not a session "
                "owned by the current experiment and runtime build"
            )
    return errors
