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
