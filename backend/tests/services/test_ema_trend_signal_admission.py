from datetime import datetime
from zoneinfo import ZoneInfo
from app.services.ema_trend.signal_admission import current

IST=ZoneInfo("Asia/Kolkata")


def source():
    close=datetime(2026,10,16,10,15,tzinfo=IST)
    return {"mode":"intraday","symbol":"NSE:INFY","close_ms":int(close.timestamp()*1000),
            "status":"armed","config_revision":1,"activation_eligible":True}


def test_current_signal_requires_completed_bar_window_and_current_configuration():
    settings={"revision":1,"config":{"enabled":True},"paused":False}
    result=current(source(),settings,datetime(2026,10,16,10,16,tzinfo=IST))
    assert result["entry_window_open"] and result["runtime_blockers"]==[]
    assert result["signal_source"]=="BROKER_COMPLETED_CANDLES"
    expired=current(source(),settings,datetime(2026,10,16,11,0,tzinfo=IST))
    assert not expired["entry_window_open"] and "ENTRY_EXPIRED" in expired["runtime_blockers"]
    superseded=current(source(),{**settings,"revision":2},datetime(2026,10,16,10,16,tzinfo=IST))
    assert "SIGNAL_CONFIG_SUPERSEDED" in superseded["runtime_blockers"]
    future=current(source(),settings,datetime(2026,10,16,10,14,tzinfo=IST))
    assert not future["entry_window_open"] and "SIGNAL_NOT_CLOSED" in future["runtime_blockers"]


def test_weekend_or_disabled_entries_do_not_look_executable():
    settings={"revision":1,"config":{"enabled":True},"paused":False}
    result=current(source(),settings,datetime(2026,10,17,10,16,tzinfo=IST))
    assert not result["entry_window_open"]
    assert "OUTSIDE_PROFILE_ENTRY_WINDOW" in result["runtime_blockers"]
    assert "NEW_EXPOSURE_DISABLED_OR_PAUSED" in current(source(),{**settings,"paused":True},
        datetime(2026,10,16,10,16,tzinfo=IST))["runtime_blockers"]
