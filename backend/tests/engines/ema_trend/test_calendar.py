"""Calendar provenance cases and synthetic warmup, never performance evidence.

The price bars below are modelled fixtures. They establish session/availability
coverage and indicator readiness; they contain no actual market observations.
"""
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.core.session_calendar import CalendarUnavailable, is_trading_session
from app.engines.ema_trend.calendar import historical_regular_session, next_regular_session
from app.engines.ema_trend.features import aggregate_completed_bars, build_features
from .helpers import bar

IST = ZoneInfo("Asia/Kolkata")


def modelled_daily(day, close=100, volume=100_000):
    opening = int(datetime.combine(day, time(9, 15), IST).timestamp()*1000)
    closing = int(datetime.combine(day, time(15, 30), IST).timestamp()*1000)
    return bar(0, close).model_copy(update={
        "open_ms": opening, "close_ms": closing, "available_ms": closing,
        "provider": "MODELLED_TEST_FIXTURE_NOT_MARKET_DATA", "volume": volume,
    })


def test_election_closure_and_muhurat_are_not_regular_nse_sessions():
    assert historical_regular_session(date(2024, 11, 19))
    assert not historical_regular_session(date(2024, 11, 20))
    assert historical_regular_session(date(2024, 11, 21))
    assert not historical_regular_session(date(2024, 11, 1))
    assert next_regular_session(date(2024, 11, 19)) == date(2024, 11, 21)


def test_budget_saturday_is_full_regular_session_but_ordinary_weekends_are_not():
    assert historical_regular_session(date(2025, 2, 1))
    assert not historical_regular_session(date(2025, 2, 2))
    assert not historical_regular_session(date(2025, 2, 8))
    assert next_regular_session(date(2025, 1, 31)) == date(2025, 2, 1)
    assert next_regular_session(date(2025, 2, 1)) == date(2025, 2, 3)


def test_2025_diwali_regular_closures_do_not_become_weekday_sessions():
    assert historical_regular_session(date(2025, 10, 20))
    assert not historical_regular_session(date(2025, 10, 21))
    assert not historical_regular_session(date(2025, 10, 22))
    assert historical_regular_session(date(2025, 10, 23))
    assert next_regular_session(date(2025, 10, 20)) == date(2025, 10, 23)


@pytest.mark.parametrize("day,exchange", [
    (date(2023, 12, 29), "NSE"),
    (date(2024, 10, 31), "NSE"),
    (date(2025, 2, 1), "BSE"),
    (date(2027, 1, 1), "NSE"),
])
def test_unverified_year_exchange_and_precoverage_date_remain_unknown(day, exchange):
    with pytest.raises(CalendarUnavailable):
        historical_regular_session(day, exchange)


@pytest.mark.parametrize("day", [date(2026, 1, 1), date(2026, 10, 2), date(2026, 10, 5)])
def test_2026_adapter_retains_shared_calendar_authority(day):
    assert historical_regular_session(day) == is_trading_session(day, "NSE")


def test_week_containing_budget_saturday_requires_saturday_data_and_completion():
    monday = date(2025, 1, 27)
    days = [monday+timedelta(days=i) for i in range(6)]
    fixture = [modelled_daily(day, 100+i*10) for i, day in enumerate(days)]
    friday_close, saturday_close = fixture[4].close_ms, fixture[5].close_ms
    assert aggregate_completed_bars(fixture[:5], 10080, saturday_close) == []
    assert aggregate_completed_bars(fixture, 10080, friday_close) == []
    assert aggregate_completed_bars(fixture, 10080, saturday_close-1) == []
    weekly = aggregate_completed_bars(fixture, 10080, saturday_close)
    assert len(weekly) == 1
    assert weekly[0].close_ms == saturday_close
    assert weekly[0].close == 150 and weekly[0].high == 151
    assert weekly[0].volume == 600_000


def test_modelled_covered_history_supports_100_closed_weeks_without_forming_week():
    start, end = date(2024, 11, 4), date(2026, 10, 15)
    days = [start+timedelta(days=i) for i in range((end-start).days+1)]
    sessions = [day for day in days if historical_regular_session(day)]
    fixture = [modelled_daily(day, 100+i*.1) for i, day in enumerate(sessions)]
    asof = fixture[-1].available_ms
    weekly = aggregate_completed_bars(fixture, 10080, asof)
    assert len(weekly) >= 100
    assert datetime.fromtimestamp(weekly[-1].close_ms/1000, IST).date() == date(2026, 10, 9)
    assert all(w.available_ms <= asof for w in weekly)
    features = build_features(fixture, weekly)
    assert features[-1].ready
    assert features[-1].context_count >= 100
    assert features[-1].context_close_ms == weekly[-1].close_ms
