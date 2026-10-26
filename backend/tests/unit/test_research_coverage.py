from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.core.research_coverage import paired_session_coverage

IST = ZoneInfo("Asia/Kolkata")
DAY = date(2026, 10, 21)


def tape(day=DAY):
    opening = datetime.combine(day, datetime.min.time(), IST).replace(hour=9, minute=15)
    return [[(opening + timedelta(minutes=i * 15)).timestamp(), 100, 101, 99, 100, 1] for i in range(25)]


def audit(underlying, option, **over):
    args = dict(start=DAY, end=DAY, interval_minutes=15,
                as_of=datetime(2026, 10, 22, 16, tzinfo=IST))
    args.update(over)
    return paired_session_coverage(underlying, option, **args)


def test_matching_truncated_tapes_are_not_complete():
    result = audit(tape()[:3], tape()[:3])
    assert result["accepted_timestamps"] == []
    assert result["complete_sessions"] == 0
    assert set(result["sessions"][0]["gap_codes"]) == {"UNDERLYING_MISSING_BARS", "OPTION_MISSING_BARS"}


def test_whole_missing_session_is_retained_in_the_denominator():
    result = audit(tape(), tape(), end=date(2026, 10, 22))
    assert result["requested_sessions"] == 2 and result["complete_sessions"] == 1
    assert not result["all_sessions_complete"]


def test_complete_session_yields_only_actual_timestamps():
    result = audit(tape(), tape())
    assert result["all_sessions_complete"]
    assert result["accepted_timestamps"] == [r[0] for r in tape()]


def test_forming_session_duplicate_and_bad_ohlc_are_not_eligible():
    current = audit(tape(), tape(), as_of=datetime(2026, 10, 21, 14, tzinfo=IST))
    assert "SESSION_IN_PROGRESS" in current["sessions"][0]["gap_codes"]
    duplicate = audit(tape(), tape() + tape()[:1])
    assert "OPTION_DUPLICATE_BARS" in duplicate["sessions"][0]["gap_codes"]
    bad = tape(); bad[0][2] = 98
    result = audit(tape(), bad)
    assert "OPTION_INVALID_OHLC" in result["sessions"][0]["gap_codes"]


def test_unknown_calendar_is_never_a_complete_session():
    result = audit([], [], start=date(2025, 10, 21), end=date(2025, 10, 21))
    assert result["sessions"][0]["gap_codes"] == ["CALENDAR_UNKNOWN"]
    assert not result["all_sessions_complete"]
