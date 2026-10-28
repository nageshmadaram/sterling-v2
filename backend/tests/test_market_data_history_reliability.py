"""Historical normalization, gaps, persistence and provider contract tests."""
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.schemas.market_reliability import Candle, HistoryQuery, InstrumentKey
from app.services.market_data.historical_reliability import (
    LakeQuoteSource, LocalHistoricalLake, TrueDataHistoricalSource,
    aggregate_candles, history_window, normalize_bars,
)

NOW = datetime(2026, 10, 29, 4, tzinfo=timezone.utc)
KEY = InstrumentKey(exchange="NSE", symbol="INFY")


def query(count=5, timeframe="1m"):
    return HistoryQuery(instrument=KEY, timeframe=timeframe,
                        start_utc=NOW, end_utc=NOW+timedelta(minutes=count))


def bars(count=5, *, missing=()):
    return [{"timestamp": (NOW+timedelta(minutes=i)).isoformat(),
             "open": str(100+i), "high": str(101+i),
             "low": str(99+i), "close": str(100+i),
             "volume": 10} for i in range(count) if i not in missing]


def test_history_normalization_preserves_utc_and_marks_gaps():
    q=query()
    normalized=normalize_bars(bars(5, missing=(2,)), q, source="alternate")
    window=history_window(q, normalized, source="alternate")
    assert len(window.candles) == 4 and window.coverage_pct == 80
    assert window.quality_status == "degraded"
    assert window.gap_starts_utc == (NOW+timedelta(minutes=2),)
    assert all(c.timestamp_utc.tzinfo == timezone.utc for c in normalized)
    assert history_window(q, (), source=None).quality_status == "missing"
    with pytest.raises(ValueError):
        normalize_bars(bars(2)+bars(2), q, source="alternate")


def test_malformed_ohlcv_and_candle_alignment_rejected():
    q=query()
    invalid=bars(1)
    invalid[0]["low"]="102"
    with pytest.raises(ValidationError):
        normalize_bars(invalid, q, source="alternate")
    off=bars(1)
    off[0]["timestamp"]=(NOW+timedelta(seconds=30)).isoformat()
    candles=normalize_bars(off, q, source="alternate")
    with pytest.raises(ValueError):
        history_window(q, candles, source="alternate")
    with pytest.raises(ValidationError):
        HistoryQuery(instrument=KEY, timeframe="1m",
                     start_utc=datetime(2026,10,29), end_utc=NOW)


def test_candle_aggregation_excludes_incomplete_periods():
    q=query(count=10)
    candles=normalize_bars(bars(10), q, source="alternate")
    result=aggregate_candles(candles, target_timeframe="5m")
    assert len(result)==2
    assert result[0].open == 100
    assert result[0].close == 104
    assert result[0].high == 105
    assert result[0].low == 99
    assert result[0].volume == 50
    incomplete=normalize_bars(bars(10, missing=(2,)), q, source="alternate")
    result=aggregate_candles(incomplete, target_timeframe="5m")
    assert len(result)==1 and result[0].timestamp_utc == NOW+timedelta(minutes=5)


@pytest.mark.asyncio
async def test_local_lake_is_opt_in_and_fallback_is_degraded(tmp_path):
    q=query()
    store=LocalHistoricalLake(tmp_path/"isolated_history")
    assert store.load(q) is None
    window=history_window(q, normalize_bars(bars(), q, source="alternate"),
                          source="alternate")
    store.save(window)
    loaded=store.load(q)
    assert loaded is not None
    assert loaded.source == "lake" and loaded.quality_status == "good"
    assert all(c.source == "lake" for c in loaded.candles)
    quote=await LakeQuoteSource(store,q).read_quote(KEY)
    assert quote is not None and quote.source == "lake"
    assert quote.last_price == 104
    from app.services.market_data.reliability import MarketDataResolver
    snap=await MarketDataResolver({"lake": LakeQuoteSource(store,q)}).snapshot(KEY,clock=NOW)
    assert snap.quality.status == "degraded"
    assert snap.execution_authorized is False


@pytest.mark.asyncio
async def test_truedata_adapter_uses_supported_interval_and_canonicalizes():
    class TruedataFixture:
        DOCUMENTED_BAR_INTERVALS={"1min","5min","60min"}
        def __init__(self):
            self.calls=[]
        async def get_bars(self,symbol,start,end,*,interval):
            self.calls.append((symbol,start,end,interval))
            return bars()
    provider=TruedataFixture()
    window=await TrueDataHistoricalSource(provider).history(query())
    assert window.coverage_pct==100
    assert window.quality_status=="good"
    assert provider.calls[0][0]=="INFY"
    assert provider.calls[0][3]=="1min"
    hour=HistoryQuery(instrument=KEY,timeframe="1h",start_utc=NOW,
                      end_utc=NOW+timedelta(hours=1))
    with pytest.raises(ValueError):
        await TrueDataHistoricalSource(
            type("OnlyOneMin",(),{"DOCUMENTED_BAR_INTERVALS":{"1min"}})()
        ).history(hour)


def test_lake_rejects_wrong_cache_identity_and_invalid_empty_save(tmp_path):
    q=query()
    store=LocalHistoricalLake(tmp_path/"isolate")
    empty=history_window(q, (), source=None)
    with pytest.raises(ValueError):
        store.save(empty)
    store.save(history_window(q, normalize_bars(bars(),q,source="lake"),source="lake"))
    p=next(store.root.glob("*.json"))
    data=json.loads(p.read_text())
    data["query"]["instrument"]["symbol"]="RELIANCE"
    p.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        store.load(q)


@pytest.mark.asyncio
async def test_truedata_alternate_quote_needs_explicit_symbol_mapping():
    from app.services.market_data.historical_reliability import TrueDataAlternateQuoteSource
    class TrueDataTicks:
        def __init__(self):
            self.calls=[]
        async def get_last_ticks(self,symbol,n):
            self.calls.append((symbol,n))
            return [{"timestamp":"2026-10-29 09:30:00","ltp":"1234.50"}]
    provider = TrueDataTicks()
    source=TrueDataAlternateQuoteSource(provider, {"NSE:INFY":"INFY"})
    found=await source.read_quote(KEY)
    assert found is not None and found.source=="alternate"
    assert found.last_price == Decimal("1234.50")
    assert found.observed_at_utc == NOW
    assert provider.calls == [("INFY",1)]
    unknown=await source.read_quote(InstrumentKey(exchange="BSE",symbol="INFY"))
    assert unknown is None
    assert provider.calls == [("INFY",1)]


@pytest.mark.asyncio
async def test_truedata_partial_last_tick_is_degraded_not_live_quality():
    from app.services.market_data.historical_reliability import TrueDataAlternateQuoteSource
    from app.services.market_data.reliability import MarketDataResolver
    class Partial:
        async def get_last_ticks(self,symbol,n):
            return [{"ltp":"1500"}]
    src=TrueDataAlternateQuoteSource(Partial(), {"NSE:INFY":"INFY"})
    outcome=await MarketDataResolver({"alternate":src}).snapshot(KEY,clock=NOW)
    assert outcome.quote is not None
    assert outcome.quality.status=="degraded"
    assert "TIMESTAMP_MISSING" in outcome.quality.issues
    assert not outcome.execution_authorized
