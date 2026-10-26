"""Session coverage for paired real tapes; equal truncated tapes are incomplete."""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime, time, timedelta
from math import isfinite
from typing import Sequence
from zoneinfo import ZoneInfo

from app.services.kite_engine.market_hours import continuous_close, is_trading_day

IST = ZoneInfo("Asia/Kolkata")


def paired_session_coverage(
    underlying: Sequence[Sequence[float]], options: Sequence[Sequence[float]], *,
    start: date, end: date, interval_minutes: int, as_of: datetime,
    underlying_exchange: str = "NSE", option_exchange: str = "NFO",
) -> dict:
    """Audit every requested day, including days absent from BOTH responses.

    Inputs use candle-open Unix seconds. The shared continuous session is
    bounded by the earlier exchange close. No timestamp is synthesized into
    either tape, no unknown year is assumed to be a trading year, and failures
    remain in the report even when complete sessions are selected for replay.
    """
    if type(interval_minutes) is not int or interval_minutes <= 0 or interval_minutes > 375:
        raise ValueError("interval_minutes must be a positive intraday interval")
    if as_of.tzinfo is None or end < start or (end - start).days > 3700:
        raise ValueError("bounded dates and a timezone-aware observation are required")
    tapes = []
    for rows in (underlying, options):
        timestamps = []
        invalid_days = set()
        for row in rows:
            if len(row) < 5 or not all(isfinite(float(value)) for value in row[:5]):
                raise ValueError("malformed research candle")
            stamp, op, high, low, close = map(float, row[:5])
            day = datetime.fromtimestamp(stamp, IST).date()
            if not low <= min(op, close) <= max(op, close) <= high or low <= 0:
                invalid_days.add(day)
            timestamps.append(stamp)
        by_day = {}
        for stamp in timestamps:
            by_day.setdefault(datetime.fromtimestamp(stamp, IST).date(), Counter())[stamp] += 1
        tapes.append((by_day, invalid_days))
    sessions = []
    accepted = []
    cursor = start
    while cursor <= end:
        reasons = []
        try:
            trading = is_trading_day(cursor, underlying_exchange) and is_trading_day(cursor, option_exchange)
        except (LookupError, ValueError):
            trading = None
            reasons.append("CALENDAR_UNKNOWN")
        if trading is False:
            cursor += timedelta(days=1)
            continue
        expected = set()
        if trading:
            opening = datetime.combine(cursor, time(9, 15), IST)
            closing = datetime.combine(cursor, min(continuous_close(cursor, underlying_exchange), continuous_close(cursor, option_exchange)), IST)
            if as_of < closing:
                reasons.append("SESSION_IN_PROGRESS")
            tick = opening
            while tick < closing:
                expected.add(tick.timestamp())
                tick += timedelta(minutes=interval_minutes)
        counts = []
        for label, (by_day, invalid_days) in zip(("UNDERLYING", "OPTION"), tapes):
            tape = by_day.get(cursor, {})
            actual = set(tape)
            missing = expected - actual
            counts.append(len(missing))
            if missing:
                reasons.append(f"{label}_MISSING_BARS")
            # A complete expected grid is necessary, not sufficient. Off-grid
            # candles can be future data, a different timeframe, or
            # pre/post-market prints; accepting them silently contaminates
            # no-lookahead and forward replay evidence.
            if actual - expected:
                reasons.append(f"{label}_UNEXPECTED_BARS")
            if any(tape[t] > 1 for t in actual):
                reasons.append(f"{label}_DUPLICATE_BARS")
            if cursor in invalid_days:
                reasons.append(f"{label}_INVALID_OHLC")
        complete = not reasons and bool(expected)
        sessions.append({"day": cursor.isoformat(), "complete": complete,
                         "gap_codes": reasons, "expected_bars": len(expected),
                         "missing_underlying": counts[0], "missing_option": counts[1]})
        if complete:
            accepted.extend(sorted(expected))
        cursor += timedelta(days=1)
    return {"schema_version": 1, "sessions": sessions, "accepted_timestamps": accepted,
            "complete_sessions": sum(s["complete"] for s in sessions),
            "requested_sessions": len(sessions),
            "all_sessions_complete": bool(sessions) and all(s["complete"] for s in sessions)}
