from datetime import datetime
from zoneinfo import ZoneInfo
import pytest
from app.engines.ema_trend.dataset import manifest,pair_manifest
from app.engines.ema_trend.contracts import Bar


def candles():
    start=int(datetime(2026,10,16,9,15,tzinfo=ZoneInfo("Asia/Kolkata")).timestamp()*1000)
    return [Bar(open_ms=start+i*60000,close_ms=start+(i+1)*60000,available_ms=start+(i+1)*60000,
                open=100,high=102,low=99,close=101,volume=1000,provider="recorded-test") for i in range(3)]


def test_manifest_is_reproducible_and_detects_price_and_lineage_change():
    bars=candles()
    first=manifest("NSE:TCS",1,bars)
    assert first==manifest("NSE:TCS",1,bars)
    assert first["quality_status"]=="CHECKS_PASSED"
    assert len(first["content_sha256"])==64 and first["live_eligible"] is False
    changed=[*bars[:2],bars[2].model_copy(update={"close":100.5})]
    assert manifest("NSE:TCS",1,changed)["content_sha256"]!=first["content_sha256"]
    changed=[b.model_copy(update={"provider":"another-provider"}) for b in bars]
    assert manifest("NSE:TCS",1,changed)["content_sha256"]!=first["content_sha256"]


def test_missing_bar_and_delayed_availability_remain_visible():
    bars=candles()
    bars[2]=bars[2].model_copy(update={"available_ms":bars[2].available_ms+1000})
    result=manifest("NSE:TCS",1,[bars[0],bars[2]],asof_ms=bars[2].close_ms)
    issues={i["code"]:i["count"] for i in result["issues"]}
    assert issues["MISSING_EXPECTED_BAR"]==1 and issues["NOT_AVAILABLE_AT_ASOF"]==1
    assert result["missing_samples"]==[{"open_ms":bars[1].open_ms,"close_ms":bars[1].close_ms}]
    assert result["quality_status"]=="ISSUES_FOUND"


def test_unverified_calendar_is_reported_without_certifying_data():
    bars=[b.model_copy(update={"open_ms":b.open_ms-4*365*86400000,"close_ms":b.close_ms-4*365*86400000,
                             "available_ms":b.available_ms-4*365*86400000}) for b in candles()]
    result=manifest("NSE:TCS",1,bars)
    assert any(i["code"]=="UNVERIFIED_CALENDAR_DAY" for i in result["issues"])
    assert result["quality_status"]=="ISSUES_FOUND"


def test_bad_order_rejected_and_pair_identity_includes_context():
    bars=candles()
    with pytest.raises(ValueError,match="DUPLICATE_OR_OUT_OF_ORDER"):
        manifest("NSE:TCS",1,list(reversed(bars)))
    first=pair_manifest("NSE:TCS",1,5,bars,bars)
    second=pair_manifest("NSE:TCS",1,5,bars,bars[:-1])
    assert first["content_sha256"]!=second["content_sha256"]
    assert manifest("NSE:TCS",1,[])["quality_status"]=="EMPTY"
