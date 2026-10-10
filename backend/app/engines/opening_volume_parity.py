"""Deterministic multi-session comparison of ORION observations and Sterling scans.

Only observable ORION fields are compared. Private scoring weights, hidden
predicates and unpublished thresholds are never inferred from matching outputs.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable


MIN_CERTIFICATION_SESSIONS = 20
PREFERRED_CERTIFICATION_SESSIONS = 30

# Keep these values aligned with docs/architecture/ORION_PARITY_CONTRACT.md.
ACCEPTANCE_THRESHOLDS: dict[str, tuple[str, float]] = {
    "direction_agreement_pct": (">=", 98.0),
    "event_precision_pct": (">=", 95.0),
    "event_recall_pct": (">=", 95.0),
    "event_f1_pct": (">=", 95.0),
    "signal_time_within_1m_pct": (">=", 95.0),
    "signal_time_within_2m_pct": (">=", 99.0),
    "orb_direction_event_agreement_pct": (">=", 98.0),
    "tier_agreement_pct": (">=", 90.0),
    "sessions_with_sterling_only_pct": ("<", 3.0),
    "sessions_with_orion_only_pct": ("<", 3.0),
    "observable_field_coverage_pct": (">=", 95.0),
}

_OBSERVABLE_FIELDS = (
    "direction",
    "tier",
    "actionable_signal_time",
    "orb_break_time",
    "combo",
    "rvol",
)


def _time(value: object) -> str | None:
    if value in (None, ""):
        return None
    text = str(value)
    if "T" in text:
        text = text.split("T", 1)[1]
    value = text[:5]
    if len(value) != 5 or value[2] != ":":
        return None
    try:
        hour, minute = (int(part) for part in value.split(":", 1))
    except ValueError:
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return value


def _minute_of_day(value: object) -> int | None:
    parsed = _time(value)
    if parsed is None:
        return None
    hour, minute = (int(part) for part in parsed.split(":", 1))
    return hour * 60 + minute


def _actionable_time(row: dict[str, Any]) -> str | None:
    raw = (
        row.get("actionable_signal_time")
        or row.get("signal_time")
        or row.get("orb_break_time")
    )
    return _time(raw)


def _observable_value(row: dict[str, Any], field: str) -> object:
    if field == "actionable_signal_time":
        return _actionable_time(row)
    if field.endswith("_time"):
        return _time(row.get(field))
    return row.get(field)


def _pct(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator * 100.0, 2)


def _f1(precision_pct: float | None, recall_pct: float | None) -> float | None:
    if precision_pct is None or recall_pct is None:
        return None
    if precision_pct + recall_pct == 0:
        return 0.0
    return round(
        2.0 * precision_pct * recall_pct / (precision_pct + recall_pct),
        2,
    )


def _normal(value: object) -> str:
    return str(value or "").strip().upper()


def _gate(metric: str, value: float | None) -> dict[str, Any]:
    operator, threshold = ACCEPTANCE_THRESHOLDS[metric]
    if value is None:
        passed = False
        status = "UNKNOWN"
    elif operator == ">=":
        passed = value >= threshold
        status = "PASS" if passed else "FAIL"
    elif operator == "<":
        passed = value < threshold
        status = "PASS" if passed else "FAIL"
    else:
        raise ValueError(f"unsupported acceptance operator: {operator}")
    return {
        "metric": metric,
        "value": value,
        "operator": operator,
        "threshold": threshold,
        "status": status,
        "passed": passed,
    }


def compare_opening_sessions(
    orion_rows: Iterable[dict[str, Any]],
    sterling_rows: Iterable[dict[str, Any]],
    *,
    rvol_tolerance: float = 0.05,
) -> dict[str, Any]:
    """Compare observable fields and evaluate the documented parity contract.

    Event detection is keyed by session date and symbol. Field agreement is a
    separate dimension: a symbol can be detected by both systems while direction,
    tier, time, COMBO label or RVOL still differs.
    """

    if rvol_tolerance < 0:
        raise ValueError("rvol_tolerance must be non-negative")

    exact_fields = (
        "direction",
        "tier",
        "actionable_signal_time",
        "orb_break_time",
        "combo",
    )

    def index(rows: Iterable[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
        out: dict[tuple[str, str], dict[str, Any]] = {}
        for row in rows:
            session = str(row.get("session_date") or "")[:10]
            symbol = str(row.get("symbol") or "").strip().upper()
            if not session or not symbol:
                raise ValueError("every comparison row requires session_date and symbol")
            key = (session, symbol)
            if key in out:
                raise ValueError(f"duplicate comparison row: {session}:{symbol}")
            out[key] = row
        return out

    expected = index(orion_rows)
    actual = index(sterling_rows)
    expected_keys = set(expected)
    actual_keys = set(actual)
    intersection = expected_keys & actual_keys
    keys = sorted(expected_keys | actual_keys)

    by_session: dict[str, dict[str, int]] = defaultdict(
        lambda: {"expected": 0, "matched": 0, "missing": 0, "extra": 0, "mismatched": 0}
    )
    comparisons: list[dict[str, Any]] = []
    for session, symbol in keys:
        left = expected.get((session, symbol))
        right = actual.get((session, symbol))
        metrics = by_session[session]
        if left is None:
            metrics["extra"] += 1
            comparisons.append(
                {
                    "session_date": session,
                    "symbol": symbol,
                    "status": "extra",
                    "mismatches": [],
                }
            )
            continue
        metrics["expected"] += 1
        if right is None:
            metrics["missing"] += 1
            comparisons.append(
                {
                    "session_date": session,
                    "symbol": symbol,
                    "status": "missing",
                    "mismatches": [],
                }
            )
            continue

        mismatches: list[str] = []
        for field in exact_fields:
            left_value = _observable_value(left, field)
            right_value = _observable_value(right, field)
            if left_value != right_value:
                mismatches.append(field)
        try:
            if abs(float(left.get("rvol")) - float(right.get("rvol"))) > rvol_tolerance:
                mismatches.append("rvol")
        except (TypeError, ValueError):
            mismatches.append("rvol")

        status = "match" if not mismatches else "mismatch"
        metrics["matched" if status == "match" else "mismatched"] += 1
        comparisons.append(
            {
                "session_date": session,
                "symbol": symbol,
                "status": status,
                "mismatches": mismatches,
            }
        )

    expected_count = len(expected)
    actual_count = len(actual)
    detected_both = len(intersection)
    matched_count = sum(row["status"] == "match" for row in comparisons)
    missing_count = sum(row["status"] == "missing" for row in comparisons)
    extra_count = sum(row["status"] == "extra" for row in comparisons)
    mismatch_count = sum(row["status"] == "mismatch" for row in comparisons)

    event_precision = _pct(detected_both, actual_count)
    event_recall = _pct(detected_both, expected_count)

    direction_den = direction_ok = 0
    tier_den = tier_ok = 0
    time_den = time_1m = time_2m = 0
    orb_den = orb_ok = 0
    coverage_den = coverage_num = 0

    for key, left in expected.items():
        right = actual.get(key)

        # Coverage denominator is every observable field ORION actually exposes.
        # A missing Sterling row contributes zero covered fields.
        for field in _OBSERVABLE_FIELDS:
            left_value = _observable_value(left, field)
            if left_value in (None, ""):
                continue
            coverage_den += 1
            if right is not None:
                right_value = _observable_value(right, field)
                if right_value not in (None, ""):
                    coverage_num += 1

        left_direction = left.get("direction")
        if left_direction not in (None, ""):
            direction_den += 1
            if right is not None and _normal(right.get("direction")) == _normal(left_direction):
                direction_ok += 1

        left_tier = left.get("tier")
        if left_tier not in (None, ""):
            tier_den += 1
            if right is not None and _normal(right.get("tier")) == _normal(left_tier):
                tier_ok += 1

        left_actionable = _minute_of_day(_actionable_time(left))
        if left_actionable is not None:
            time_den += 1
            right_actionable = (
                _minute_of_day(_actionable_time(right))
                if right is not None
                else None
            )
            if right_actionable is not None:
                delta = abs(right_actionable - left_actionable)
                if delta <= 1:
                    time_1m += 1
                if delta <= 2:
                    time_2m += 1

        left_orb = _time(left.get("orb_break_time"))
        if left_direction not in (None, "") and left_orb is not None:
            orb_den += 1
            if (
                right is not None
                and _normal(right.get("direction")) == _normal(left_direction)
                and _time(right.get("orb_break_time")) == left_orb
            ):
                orb_ok += 1

    session_count = len(by_session)
    sessions_with_extra = sum(1 for counts in by_session.values() if counts["extra"] > 0)
    sessions_with_missing = sum(1 for counts in by_session.values() if counts["missing"] > 0)

    observable_metrics = {
        "direction_agreement_pct": _pct(direction_ok, direction_den),
        "event_precision_pct": event_precision,
        "event_recall_pct": event_recall,
        "event_f1_pct": _f1(event_precision, event_recall),
        "signal_time_within_1m_pct": _pct(time_1m, time_den),
        "signal_time_within_2m_pct": _pct(time_2m, time_den),
        "orb_direction_event_agreement_pct": _pct(orb_ok, orb_den),
        "tier_agreement_pct": _pct(tier_ok, tier_den),
        "sessions_with_sterling_only_pct": _pct(sessions_with_extra, session_count),
        "sessions_with_orion_only_pct": _pct(sessions_with_missing, session_count),
        "observable_field_coverage_pct": _pct(coverage_num, coverage_den),
    }
    gates = [_gate(metric, observable_metrics[metric]) for metric in ACCEPTANCE_THRESHOLDS]
    sample_passed = session_count >= MIN_CERTIFICATION_SESSIONS
    observable_contract_passed = sample_passed and all(gate["passed"] for gate in gates)
    observable_contract_status = (
        "PASS"
        if observable_contract_passed
        else "INSUFFICIENT_SAMPLE"
        if not sample_passed
        else "FAIL"
    )

    return {
        "sessions": [
            {"session_date": session, **counts}
            for session, counts in sorted(by_session.items())
        ],
        "summary": {
            "session_count": session_count,
            "expected_count": expected_count,
            "matched_count": matched_count,
            "missing_count": missing_count,
            "extra_count": extra_count,
            "mismatched_count": mismatch_count,
            # Backwards-compatible exact-row recall. Event recall is separately
            # reported in observable_metrics and does not require every field to match.
            "recall_pct": round(matched_count / expected_count * 100.0, 2)
            if expected_count
            else None,
            "exact_match": bool(
                expected_count
                and matched_count == expected_count
                and not extra_count
            ),
        },
        "observable_metrics": observable_metrics,
        "certification": {
            # These rows are function arguments (and the HTTP endpoint accepts
            # caller-supplied rows). Passing metrics therefore proves only the
            # observable comparison contract. It is not independent evidence
            # that the rows came from ORION or from untouched Sterling output.
            "status": "UNVERIFIED_INPUT",
            "passed": False,
            "reason": (
                "comparison rows have no independently verified provenance; "
                "persisted source artifacts are required for certification"
            ),
            "observable_contract_status": observable_contract_status,
            "observable_contract_passed": observable_contract_passed,
            "session_count": session_count,
            "minimum_sessions": MIN_CERTIFICATION_SESSIONS,
            "preferred_sessions": PREFERRED_CERTIFICATION_SESSIONS,
            "sample_size_passed": sample_passed,
            "gates": gates,
            "proprietary_decision_parity": "UNKNOWN",
            "proprietary_unknowns": [
                "ORION numeric strength-score weights",
                "ORION private COMBO predicate",
                "ORION Momentum Lab server-side predicates",
                "ORION unpublished conviction thresholds",
            ],
        },
        "comparisons": comparisons,
        "scope": "observable fields only; proprietary score and Momentum Lab internals excluded",
    }
