"""Read-only SuperTrend current-day signal projection preserves active positions."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

from app.api.v1.endpoints.kite_engine import _today_signal_rows


IST = timezone(timedelta(hours=5, minutes=30))


def _stamp(day_offset: int) -> int:
    now = datetime.now(IST)
    local_noon = now.replace(hour=12, minute=0, second=0, microsecond=0)
    return int((local_noon + timedelta(days=day_offset)).timestamp() * 1000)


def test_today_only_retains_same_ist_day():
    current = NS(timestamp_ms=_stamp(0), is_active=False)
    old = NS(timestamp_ms=_stamp(-1), is_active=False)
    assert _today_signal_rows([old, current]) == [current]


def test_ongoing_position_remains_visible_before_history_is_loaded():
    position = NS(timestamp_ms=_stamp(-3), is_active=True)
    assert _today_signal_rows([position]) == [position]


def test_future_or_unverifiable_broker_timestamps_are_not_relabelled_today():
    rows = [NS(timestamp_ms="", is_active=False),
            NS(timestamp_ms=float("inf"), is_active=False),
            NS(timestamp_ms=None, is_active=False)]
    assert _today_signal_rows(rows) == []


def test_original_rows_not_mutated_by_read_only_today_projection():
    old = NS(timestamp_ms=_stamp(-3), is_active=False)
    fresh = NS(timestamp_ms=_stamp(0), is_active=False)
    before = [old, fresh]
    assert _today_signal_rows(before) == [fresh]
    assert before == [old, fresh]
