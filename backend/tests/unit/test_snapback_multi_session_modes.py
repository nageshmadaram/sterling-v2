"""Snapback intraday and overnight modes: multi-session signal history and overnight carry."""
from datetime import datetime

from app.engines.snapback.config import validate
from app.engines.snapback.intraday import (OVERNIGHT_MAX_SESSIONS, PremiumTradeState, _multi_session,
                                           advance_trade)
from app.engines.snapback.models import IST


def ts(day: int, hour: int, minute: int = 15) -> float:
    return datetime(2026, 10, day, hour, minute, tzinfo=IST).timestamp()


def trade(entry_time: float) -> PremiumTradeState:
    return PremiumTradeState(entry=100.0, entry_time=entry_time, stop=60.0, target=400.0, profit_lock=101.0,
                             quantity=50, variable_cost_points=1.0, fixed_cost_inr=40.0)


def candle(state, cfg, at):
    return advance_trade(state, timestamp=at, open=100.0, high=101.0, low=99.0, close=100.0, cfg=cfg)


def test_only_intraday_15m_and_overnight_use_earlier_sessions():
    assert _multi_session(validate({"trading_mode": "intraday", "scalp_timeframe_minutes": 15}))
    assert _multi_session(validate({"trading_mode": "overnight"}))
    assert not _multi_session(validate({"trading_mode": "scalp"}))
    assert not _multi_session(validate({"trading_mode": "intraday", "scalp_timeframe_minutes": 5}))


def test_overnight_holds_across_sessions_and_squares_off_on_the_last_one():
    cfg = validate({"trading_mode": "overnight"})
    # Monday 19 Oct 2026 entry, hourly bars; it must still be open on Tuesday and Wednesday morning.
    s = candle(trade(ts(19, 14)), cfg, ts(19, 14))
    s = candle(s, cfg, ts(20, 9))
    assert not s.exited and s.sessions == 2
    s = candle(s, cfg, ts(21, 9))
    assert not s.exited and s.sessions == OVERNIGHT_MAX_SESSIONS
    s = candle(s, cfg, ts(21, 15))
    assert s.exited and s.exit_reason == "session_exit"


def test_overnight_never_carries_into_a_weekend():
    cfg = validate({"trading_mode": "overnight"})
    s = candle(trade(ts(23, 13)), cfg, ts(23, 13))  # Friday 23 Oct 2026
    s = candle(s, cfg, ts(23, 15))
    assert s.exited and s.exit_reason == "session_exit"


def test_scalp_still_exits_at_the_session_change():
    cfg = validate({"trading_mode": "scalp"})
    s = candle(trade(ts(19, 15, 15)), cfg, ts(19, 15, 15))  # 15:15 is the square-off minute
    assert s.exited and s.exit_reason == "session_exit"
