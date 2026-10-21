from datetime import datetime, timedelta, timezone

from app.engines.ema_trend.config import MODES, PROFILES, EmaTrendConfig, gap_close_due

IST = timezone(timedelta(hours=5, minutes=30))


def test_overnight_is_a_full_mode():
    assert "overnight" in MODES
    p = PROFILES["overnight"]
    assert (p.signal_minutes, p.context_minutes) == (60, 1440)
    assert p.force_close is None and p.no_gap_carry and p.max_hold_bars == 21
    assert EmaTrendConfig(modes=list(MODES)).modes == list(MODES)


def test_existing_profiles_are_unchanged_by_the_gap_rule():
    for mode in ("swing", "intraday", "scalping", "ultra_scalping"):
        assert PROFILES[mode].no_gap_carry is False


def test_overnight_closes_before_a_weekend_but_not_midweek():
    p = PROFILES["overnight"]
    friday = datetime(2026, 10, 23, 15, 20, tzinfo=IST)
    wednesday = datetime(2026, 10, 21, 15, 20, tzinfo=IST)
    assert gap_close_due(p, friday) is True
    assert gap_close_due(p, wednesday) is False
    assert gap_close_due(p, friday.replace(hour=14)) is False
    assert gap_close_due(PROFILES["swing"], friday) is False


def test_overnight_setups_continue_across_the_session_boundary():
    from app.engines.ema_trend.contracts import Bar
    from app.engines.ema_trend.features import continuous

    def bar(y, mo, d, h, mi, minutes=60):
        start = datetime(y, mo, d, h, mi, tzinfo=IST)
        end = start + timedelta(minutes=minutes)
        ms = lambda t: int(t.timestamp() * 1000)
        return Bar(open_ms=ms(start), close_ms=ms(end), available_ms=ms(end),
                   open=1, high=1, low=1, close=1, volume=1, provider="test",
                   adjustment_version="test")
    last = bar(2026, 10, 20, 15, 15, minutes=15)   # 15:15-15:30 Tuesday
    first = bar(2026, 10, 21, 9, 15)               # 09:15 Wednesday
    assert continuous(last, first, 60, across_sessions=True) is True
    assert continuous(last, first, 60) is False
    friday_last = bar(2026, 10, 23, 15, 15, minutes=15)
    tuesday = bar(2026, 10, 27, 9, 15)             # skips Monday: a real gap
    assert continuous(friday_last, tuesday, 60, across_sessions=True) is False
