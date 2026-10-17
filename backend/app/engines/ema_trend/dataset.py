"""Deterministic recorded-data identity and bounded, explicit quality audit.

A clean manifest is not execution or economic approval. Missing periods and
calendar uncertainty remain visible instead of being inferred as zero volume.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from datetime import datetime, time, timedelta
import hashlib
import json

from .calendar import HISTORY_POLICY_VERSION, historical_regular_session, regular_close
from app.core.session_calendar import CalendarUnavailable
from .contracts import Bar
from .features import IST, validate_bars


def manifest(symbol: str, minutes: int, bars: Sequence[Bar], *, asof_ms: int | None = None) -> dict:
    if minutes not in {1, 3, 5, 15, 60, 1440, 10080}:
        raise ValueError("UNSUPPORTED_DATASET_INTERVAL")
    if symbol.count(":") != 1 or symbol.split(":")[0] not in {"NSE", "BSE", "NFO", "BFO"}:
        raise ValueError("EXPLICIT_EQUITY_EXCHANGE_REQUIRED")
    validate_bars(bars)
    content = [b.model_dump(mode="json") for b in bars]
    encoded = json.dumps({"schema_version":1,"symbol":symbol,"minutes":minutes,"bars":content},
                         sort_keys=True,separators=(",",":"),allow_nan=False).encode()
    counts = Counter()
    missing_samples = []
    expected_count = 0
    exchange = symbol.split(":")[0]
    actual = {(b.open_ms,b.close_ms) for b in bars}
    for b in bars:
        if asof_ms is not None and b.available_ms > asof_ms:
            counts["NOT_AVAILABLE_AT_ASOF"] += 1
        if not b.volume:
            counts["ZERO_VOLUME_BAR"] += 1
    if len({b.provider for b in bars})>1:
        counts["MIXED_PROVIDER_LINEAGE"]+=1
    if bars and minutes != 10080:
        first,last = bars[0].open_ms,bars[-1].close_ms
        start = datetime.fromtimestamp(first/1000,IST).date()
        end = datetime.fromtimestamp(last/1000,IST).date()
        if (end-start).days > 1500:
            raise ValueError("DATASET_AUDIT_RANGE_TOO_LARGE")
        day = start
        expected = set()
        while day <= end:
            try:
                regular = historical_regular_session(day,exchange)
            except CalendarUnavailable:
                counts["UNVERIFIED_CALENDAR_DAY"] += 1
                day += timedelta(days=1)
                continue
            if regular:
                opening = datetime.combine(day,time(9,15),IST)
                closing = datetime.combine(day,regular_close(day,exchange),IST)
                point = opening
                while point < closing:
                    finish = closing if minutes==1440 else min(point+timedelta(minutes=minutes),closing)
                    key = (int(point.timestamp()*1000),int(finish.timestamp()*1000))
                    if key[0]>=first and key[1]<=last:
                        expected.add(key)
                    point = finish
            day += timedelta(days=1)
        expected_count = len(expected)
        missing = sorted(expected-actual)
        counts["MISSING_EXPECTED_BAR"] = len(missing)
        counts["OFF_GRID_OR_UNVERIFIED_BAR"] = len(actual-expected)
        missing_samples = [{"open_ms":a,"close_ms":b} for a,b in missing[:20]]
    issues = [{"code":code,"count":count} for code,count in sorted(counts.items()) if count]
    return {"schema_version":1,"symbol":symbol,"interval_minutes":minutes,"bar_count":len(bars),
            "content_sha256":hashlib.sha256(encoded).hexdigest(),
            "first_open_ms":bars[0].open_ms if bars else None,"last_close_ms":bars[-1].close_ms if bars else None,
            "last_available_ms":bars[-1].available_ms if bars else None,"asof_ms":asof_ms,
            "providers":sorted({b.provider for b in bars}),
            "adjustment_versions":sorted({b.adjustment_version for b in bars}),
            "calendar_policy":HISTORY_POLICY_VERSION+"+SHARED_CURRENT_CALENDAR",
            "expected_bars_within_observed_bounds":expected_count if minutes!=10080 else None,
            "quality_status":"EMPTY" if not bars else "ISSUES_FOUND" if issues else "PARTIAL_CHECKS" if minutes==10080 else "CHECKS_PASSED",
            "issues":issues,"missing_samples":missing_samples,
            "limitations":["OBSERVED_BOUNDS_ONLY_NOT_FULL_REQUEST_RANGE","CORPORATE_ACTIONS_NOT_CERTIFIED",
                           "WEEKLY_CONSTITUENT_COVERAGE_REQUIRES_UPSTREAM_EVIDENCE" if minutes==10080 else "NO_TICK_OR_DEPTH_EVIDENCE"],
            "live_eligible":False}


def pair_manifest(symbol: str, signal_minutes: int, context_minutes: int,
                  bars: Sequence[Bar], context: Sequence[Bar], *, asof_ms: int | None = None) -> dict:
    signal = manifest(symbol,signal_minutes,bars,asof_ms=asof_ms)
    higher = manifest(symbol,context_minutes,context,asof_ms=asof_ms)
    digest=hashlib.sha256((signal["content_sha256"]+higher["content_sha256"]).encode()).hexdigest()
    return {"schema_version":1,"content_sha256":digest,"signal":signal,"context":higher,"live_eligible":False}
