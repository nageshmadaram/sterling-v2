"""Verified regular-session history for EMA features, never a live entry gate.

Coverage starts November 2024 intentionally: the earlier 2024 short disaster
recovery sessions have not been modeled. The global live calendar is unchanged.
Muhurat (non-regular hours) is excluded; February 1 2025 was a full regular
Saturday and is included. Sources are recorded in CALENDAR_SOURCES.md.
"""
from datetime import date, timedelta
from app.core.session_calendar import CalendarUnavailable

HISTORY_START = date(2024, 11, 1)
HISTORY_POLICY_VERSION = "ema-nse-regular-20241101-2025-v1"
_CLOSED = frozenset(date.fromisoformat(s) for s in (
    "2024-11-01", "2024-11-15", "2024-11-20", "2024-12-25",
    "2025-02-26", "2025-03-14", "2025-03-31", "2025-04-10",
    "2025-04-14", "2025-04-18", "2025-05-01", "2025-08-15",
    "2025-08-27", "2025-10-02", "2025-10-21", "2025-10-22",
    "2025-11-05", "2025-12-25",
))
_EXTRA_REGULAR = frozenset({date(2025, 2, 1)})


def historical_regular_session(day: date, exchange: str = "NSE") -> bool:
    from app.core.session_calendar import is_trading_session
    if day.year >= 2026:
        return is_trading_session(day, exchange)
    if exchange == "NSE" and HISTORY_START <= day <= date(2025, 12, 31):
        return day in _EXTRA_REGULAR or (day.weekday() < 5 and day not in _CLOSED)
    raise CalendarUnavailable(f"CALENDAR_HISTORY_UNVERIFIED: {exchange} {day.isoformat()}")


def next_regular_session(day: date, exchange: str = "NSE") -> date:
    for offset in range(1, 32):
        candidate = day+timedelta(days=offset)
        if historical_regular_session(candidate, exchange):
            return candidate
    raise CalendarUnavailable("CALENDAR_HISTORY_UNVERIFIED: no regular session within 31 days")
