"""Pure calendar acceleration must retain the original Gamma outcomes."""
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
import pickle

import pytest

from app.engines.gamma_move import trigger
from tests.unit.test_gamma_unwind_reference import (
    BASE, armed_bars, bar, contract, rising_underlying,
)


@pytest.fixture(autouse=True)
def clean_clock_cache():
    functions = [trigger.ist_date, trigger._session_open_ms]
    for function in functions:
        if hasattr(function, 'cache_clear'):
            function.cache_clear()
    yield
    for function in functions:
        if hasattr(function, 'cache_clear'):
            function.cache_clear()


def test_repeated_replay_windows_convert_each_clock_input_once(monkeypatch):
    calls = {'date': 0, 'open': 0}

    class Clock:
        @classmethod
        def fromtimestamp(cls, *args, **kwargs):
            calls['date'] += 1
            return datetime.fromtimestamp(*args, **kwargs)

        def __new__(cls, *args, **kwargs):
            calls['open'] += 1
            return datetime(*args, **kwargs)

    monkeypatch.setattr(trigger, 'datetime', Clock)
    bars = [bar(i) for i in range(70)]
    for _ in range(3):
        for end in range(20, len(bars) + 1):
            assert trigger.zero_volume_slots(bars[:end], 20) == 0
    assert calls['date'] <= len(bars)
    assert calls['open'] <= 1


@pytest.mark.parametrize('kind', ['timestamp', 'session'])
def test_calendar_cache_has_a_finite_memory_bound(kind):
    if kind == 'timestamp':
        function, limit = trigger.ist_date, 8192
        values = [int(BASE.timestamp() * 1000) + i * 300_000 for i in range(limit + 20)]
    else:
        function, limit = trigger._session_open_ms, 512
        values = [BASE.date() + timedelta(days=i) for i in range(limit + 20)]
    for value in values:
        function(value)
    info = function.cache_info()
    assert 0 < info.maxsize <= limit
    assert info.currsize <= limit
    # Eviction recomputes the same answer; no market/account identity is needed.
    assert function(values[0]) == function(values[0])


@pytest.mark.parametrize('stamp', [
    -1, 0, 1, 1.5,
    int(datetime(2026, 10, 25, 18, 29, 59, tzinfo=timezone.utc).timestamp() * 1000),
    int(datetime(2026, 10, 25, 18, 30, tzinfo=timezone.utc).timestamp() * 1000),
    int(datetime(2026, 12, 31, 18, 30, tzinfo=timezone.utc).timestamp() * 1000),
])
def test_cached_ist_dates_equal_original_conversion(stamp):
    expected = datetime.fromtimestamp(stamp / 1000, trigger.IST).date()
    assert trigger.ist_date(stamp) == expected
    assert trigger.ist_date(stamp) == expected


@pytest.mark.parametrize('day', [date(1970, 1, 1), date(2026, 10, 26), date(2027, 1, 1)])
def test_cached_session_open_keeps_original_clock(day):
    expected = int(datetime(day.year, day.month, day.day, tzinfo=trigger.IST).timestamp() * 1000) + 555 * 60_000
    assert trigger._session_open_ms(day) == expected
    assert trigger._session_open_ms(day) == expected


@pytest.mark.parametrize('scenario', ['entry', 'missing', 'zero', 'new_day', 'rearm'])
def test_complete_engine_state_matches_uncached_original(monkeypatch, scenario):
    cached_date, cached_open = trigger.ist_date, trigger._session_open_ms

    def exercise():
        cfg = trigger.GammaMoveConfig().validate()
        engine = trigger.ContractEngine(contract(), cfg)
        rows = armed_bars()
        if scenario == 'missing':
            rows = rows[:8] + rows[10:]
        elif scenario == 'zero':
            rows = [replace(b, volume=0) if i % 4 == 0 else b for i, b in enumerate(rows)]
        history = []
        for end in range(1, len(rows) + 1):
            engine.on_bar_close(rows[:end])
            history.append(pickle.dumps(engine))
        forming = replace(bar(20), open=100.5, high=102, low=100.5, close=102, volume=200, oi=900)
        signal = engine.breakout(rows, forming, 102, 102, rising_underlying(), tick_ts_ms=forming.ts_ms + 60_000)
        history.append(pickle.dumps((engine, signal)))
        if scenario in {'entry', 'rearm', 'new_day'}:
            assert signal is not None
            restored = trigger.ContractEngine(contract(), cfg)
            restored.on_bar_close(rows)
            restored.restore_recorded_e1(signal)
            history.append(pickle.dumps(restored))
            if scenario == 'rearm':
                restored.on_bar_close([*rows, replace(forming, oi=700)], oi_now=700, oi_peak_value=1000)
            if scenario == 'new_day':
                restored.reset_day()
                next_day = [replace(b, ts_ms=b.ts_ms + 86_400_000) for b in rows]
                restored.on_bar_close([*rows, *next_day])
            history.append(pickle.dumps(restored))
        return history

    optimized = exercise()
    monkeypatch.setattr(trigger, 'ist_date', lambda ts: datetime.fromtimestamp(ts / 1000, trigger.IST).date())
    monkeypatch.setattr(trigger, '_session_open_ms', lambda day: int(datetime(day.year, day.month, day.day, tzinfo=trigger.IST).timestamp() * 1000) + trigger.SESSION_OPEN_MIN * 60_000)
    reference = exercise()
    assert optimized == reference
    # Restore before the fixture clears the bounded caches.
    monkeypatch.setattr(trigger, 'ist_date', cached_date)
    monkeypatch.setattr(trigger, '_session_open_ms', cached_open)
