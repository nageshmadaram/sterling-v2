from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.engines.ema_trend.features import aggregate_completed_bars, build_features
from app.engines.ema_trend.pivots import confirmed_pivots
from .helpers import BASE_MS, bar


def test_pivot_is_unavailable_until_second_right_bar_closes():
    highs = [102, 103, 110, 108, 107, 105]
    lows = [98, 97, 94, 96, 97, 98]
    bars = [bar(i, 100, high=h, low=l) for i, (h, l) in enumerate(zip(highs, lows))]
    assert confirmed_pivots(bars[:4]) == []
    pivots = confirmed_pivots(bars[:5])
    assert {(p.side, p.occurred_index, p.confirmed_index) for p in pivots} == {
        ("HIGH", 2, 4), ("LOW", 2, 4),
    }
    assert all(p.confirmed_ms == bars[4].close_ms for p in pivots)
    full = build_features(bars, [])
    assert full[2].pivots == [] and full[3].pivots == []
    assert full[4].pivots == pivots


def test_equal_high_plateau_chooses_first_strict_left_pivot():
    highs = [102, 103, 110, 110, 107, 105]
    bars = [bar(i, 100, high=h, low=98) for i, h in enumerate(highs)]
    highs_found = [p for p in confirmed_pivots(bars) if p.side == "HIGH"]
    assert [(p.occurred_index, p.confirmed_index) for p in highs_found] == [(2, 4)]


def test_context_is_visible_on_availability_not_historical_close():
    context = [bar(0, 90, available_delay=5 * 60_000)]
    signal = [bar(i, 100 + i) for i in range(8)]
    features = build_features(signal, context)
    assert features[4].context_count == 0
    assert features[4].context_close_ms is None
    assert features[5].context_count == 1
    assert features[5].context_close_ms == context[0].close_ms


def test_ready_requires_250_signal_and_100_available_context_bars():
    signal = [bar(i, 100 + i * .1) for i in range(251)]
    context = [bar(i, 90 + i * .1).model_copy(update={
        "open_ms": BASE_MS - (101-i) * 60_000,
        "close_ms": BASE_MS - (100-i) * 60_000,
        "available_ms": BASE_MS - (100-i) * 60_000,
    }) for i in range(100)]
    context[-1] = context[-1].model_copy(update={"available_ms": signal[249].available_ms + 1})
    features = build_features(signal, context)
    assert not features[248].ready
    assert not features[249].ready
    assert features[249].context_count == 99
    assert features[250].ready


def test_future_signal_and_context_append_preserves_feature_prefix():
    bars = [bar(i, 100 + i * .1 + (i % 5)) for i in range(275)]
    context = [bar(i, 100 + i * .2) for i in range(275)]
    full = build_features(bars, context)
    for length in (10, 99, 249, 250):
        prefix = build_features(bars[:length], context[:length])
        assert [f.model_dump() for f in full[:length]] == [f.model_dump() for f in prefix]


@pytest.mark.parametrize("kind", ["duplicate", "reversed", "mixed_adjustment"])
def test_invalid_bar_lineage_rejected_before_indicator_assembly(kind):
    bars = [bar(0), bar(1)]
    if kind == "duplicate":
        bars[1] = bars[0]
    elif kind == "reversed":
        bars.reverse()
    else:
        bars[1] = bars[1].model_copy(update={"adjustment_version": "split-v2"})
    with pytest.raises(ValueError):
        build_features(bars, [])


def test_three_minute_aggregation_requires_complete_constituents(monkeypatch):
    from app.core import session_calendar
    monkeypatch.setattr(session_calendar, "is_trading_session", lambda *_: True)
    ist = ZoneInfo("Asia/Kolkata")
    start = int(datetime(2026, 9, 30, 9, 15, tzinfo=ist).timestamp() * 1000)
    bars = [bar(i, 100 + i).model_copy(update={
        "open_ms": start+i*60_000, "close_ms": start+(i+1)*60_000,
        "available_ms": start+(i+1)*60_000,
    }) for i in range(3)]
    assert aggregate_completed_bars(bars[:2], 3, start+180_000) == []
    assert aggregate_completed_bars([bars[0], bars[2]], 3, start+180_000) == []
    assert aggregate_completed_bars(bars, 3, start+179_999) == []
    result = aggregate_completed_bars(bars, 3, start+180_000)
    assert len(result) == 1
    assert (result[0].open, result[0].close, result[0].volume) == (100, 102, 300_000)


def test_holiday_friday_week_only_appears_after_thursday_close(monkeypatch):
    from app.core import session_calendar
    ist = ZoneInfo("Asia/Kolkata")
    monday = date(2026, 9, 28)
    days = [monday + timedelta(days=i) for i in range(4)]
    monkeypatch.setattr(session_calendar, "is_trading_session", lambda d, *_: d in days)
    bars = [bar(i, 100+i).model_copy(update={
        "open_ms": int(datetime.combine(d, time(9, 15), ist).timestamp()*1000),
        "close_ms": int(datetime.combine(d, time(15, 30), ist).timestamp()*1000),
        "available_ms": int(datetime.combine(d, time(15, 30), ist).timestamp()*1000),
    }) for i, d in enumerate(days)]
    assert aggregate_completed_bars(bars[:3], 10080, bars[-1].close_ms) == []
    assert aggregate_completed_bars(bars, 10080, bars[-1].close_ms-1) == []
    week = aggregate_completed_bars(bars, 10080, bars[-1].close_ms)
    assert len(week) == 1
    assert week[0].close_ms == bars[-1].close_ms
    assert week[0].volume == 400_000
