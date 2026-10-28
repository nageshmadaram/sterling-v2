"""Phase 3 deterministic observational quality, fallback and regime tests."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.schemas.broker_read import OptionChainObservation
from app.schemas.market_reliability import (
    Candle, Depth, HistoryQuery, InstrumentKey, Level, MarketSnapshot, Quality,
    Quote,
)
from app.services.market_data.reliability import (
    BrokerQuoteSource, MarketDataResolver, PreviousQuoteSource, QualityMonitor,
    assess, classify_regime, normalize_option_chain,
)

UTC = timezone.utc
NOW = datetime(2026, 10, 29, 4, 0, tzinfo=UTC)
KEY = InstrumentKey(exchange="NSE", symbol="INFY")


def q(source, *, timestamp=NOW, price="100.25", depth=None):
    return Quote(instrument=KEY, last_price=price,
                 observed_at_utc=timestamp, fetched_at_utc=NOW,
                 source=source, depth=depth)


class Source:
    def __init__(self, value=None, exc=None):
        self.value, self.exc, self.calls = value, exc, 0
    async def read_quote(self, key):
        self.calls += 1
        if self.exc:
            raise self.exc
        return self.value


@pytest.mark.asyncio
async def test_fallback_order_prefers_first_good_quote_and_stops():
    broker, alternate, lake, prev = (
        Source(q("broker", timestamp=NOW-timedelta(minutes=5))),
        Source(q("alternate")), Source(q("lake")), Source(q("previous")),
    )
    resolver = MarketDataResolver(dict(broker=broker, alternate=alternate,
                                        lake=lake, previous=prev))
    snap = await resolver.snapshot(KEY, clock=NOW)
    assert snap.quote.source == "alternate"
    assert snap.quality.status == "good"
    assert snap.quality.fallback_used
    assert snap.quality.execution_authorized is False
    assert snap.execution_authorized is False
    assert snap.attempted_sources == ("broker", "alternate")
    assert [p.calls for p in (broker, alternate, lake, prev)] == [1, 1, 0, 0]


@pytest.mark.asyncio
async def test_stale_missing_and_provider_error_never_become_trusted():
    resolver = MarketDataResolver({
        "broker": Source(exc=RuntimeError("access_token=secret")),
        "alternate": Source(None),
        "lake": Source(q("lake", timestamp=NOW-timedelta(days=3))),
        "previous": Source(q("previous", timestamp=NOW-timedelta(days=1))),
    })
    snap = await resolver.snapshot(KEY, clock=NOW)
    assert snap.quality.status == "degraded"
    assert snap.quality.execution_authorized is False
    assert snap.quote is not None
    assert snap.quote.source in ("lake", "previous")
    assert "STALE" in snap.quality.issues
    assert resolver.monitor.health()["fallback_active"] == 1
    empty = await MarketDataResolver({}).snapshot(KEY, clock=NOW)
    assert empty.quote is None and empty.quality.status == "missing"
    assert empty.quality.score == 0 and empty.regime == "unknown"


@pytest.mark.asyncio
async def test_contract_mismatch_and_partial_source_are_rejected():
    wrong = InstrumentKey(exchange="BSE", symbol="INFY")
    invalid = Source(Quote(instrument=wrong, last_price=100, observed_at_utc=NOW,
                           fetched_at_utc=NOW, source="broker"))
    snap = await MarketDataResolver({"broker": invalid}).snapshot(KEY, clock=NOW)
    assert snap.quality.status == "missing"
    invalid_source = Source(q("alternate"))
    assert (await MarketDataResolver({"broker": invalid_source}).snapshot(
        KEY, clock=NOW)).quote is None


def test_quality_scoring_stale_timestamp_missing_depth_and_fallback():
    depth = Depth(instrument=KEY, observed_at_utc=NOW,
                  bids=(Level(price=100, quantity=50),),
                  asks=(Level(price=101, quantity=50),))
    assert assess(q("broker", depth=depth), clock=NOW,
                  stale_after_ms=5000, require_depth=True).status == "good"
    unknown = assess(q("broker", timestamp=None), clock=NOW, stale_after_ms=5000)
    assert unknown.status == "degraded" and "TIMESTAMP_MISSING" in unknown.issues
    alt = assess(q("alternate"), clock=NOW, stale_after_ms=5000)
    assert alt.score == 90 and alt.fallback_used and alt.status == "good"
    no_depth = assess(q("broker"), clock=NOW, stale_after_ms=5000,
                      require_depth=True)
    assert no_depth.status == "degraded" and "DEPTH_MISSING" in no_depth.issues
    future = assess(q("broker", timestamp=NOW+timedelta(hours=1)),
                    clock=NOW, stale_after_ms=5000)
    assert "FUTURE_TIMESTAMP" in future.issues
    historical = assess(q("lake"), clock=NOW, stale_after_ms=5000)
    assert historical.status == "degraded"
    with pytest.raises(ValueError):
        assess(q("broker"), clock=NOW, stale_after_ms=0)


def test_canonical_quote_depth_candle_and_timestamps():
    ts = datetime(2026, 10, 29, 9, 30, tzinfo=timezone(timedelta(hours=5,minutes=30)))
    quote = q("broker", timestamp=ts)
    assert quote.observed_at_utc == NOW
    with pytest.raises(ValidationError):
        q("broker", timestamp=datetime(2026, 10, 29, 9, 30))
    with pytest.raises(ValidationError):
        Quote(instrument=KEY, last_price="NaN", observed_at_utc=NOW,
              fetched_at_utc=NOW, source="broker")
    with pytest.raises(ValidationError):
        Depth(instrument=KEY, observed_at_utc=NOW,
              bids=(Level(price=0, quantity=5),))
    with pytest.raises(ValidationError):
        Candle(instrument=KEY, timeframe="1m", timestamp_utc=NOW,
               open=100, high=99, low=95, close=102, volume=100, source="lake")


def test_market_regime_rules_observation_only():
    base = NOW-timedelta(minutes=8)
    def series(prices, volume=100):
        return tuple(Candle(instrument=KEY, timeframe="1m",
                            timestamp_utc=base+timedelta(minutes=i),
                            open=p, high=p+Decimal("0.2"),
                            low=p-Decimal("0.2"), close=p,
                            volume=volume, source="lake")
                     for i,p in enumerate(map(Decimal, map(str, prices))))
    trending = series([100, 101, 102, 103, 104, 105, 106, 107])
    assert classify_regime(trending) == "trending"
    assert classify_regime(series([100,100.1,99.9,100,100.05,100,99.95,100])) == "ranging"
    assert classify_regime(series([100,105,99,110,100,94,108,100])) == "volatile"
    assert classify_regime(trending, spread_bps=Decimal(35)) == "low_liquidity"
    assert classify_regime(series([100]*8, volume=0)) == "low_liquidity"
    assert classify_regime(trending[:3]) == "unknown"


@pytest.mark.asyncio
async def test_health_and_structured_events_are_read_only():
    observed=[]
    monitor=QualityMonitor(sink=observed.append)
    resolver=MarketDataResolver({"broker": Source(q("broker"))}, monitor=monitor)
    result=await resolver.snapshot(KEY, clock=NOW)
    assert result.quality.status == "good"
    health=monitor.health()
    assert health["good"] == 1 and health["execution_authorized"] is False
    assert observed[0].kind == "recovered"
    missing=await MarketDataResolver({}, monitor=monitor).snapshot(KEY, clock=NOW)
    assert observed[-1].kind == "missing"
    assert monitor.health()["missing"] == 1
    with pytest.raises(ValidationError):
        MarketSnapshot(instrument=KEY, observed_at_utc=NOW, quote=None,
                       quality=Quality(status="good", score=100))


@pytest.mark.asyncio
async def test_normalized_broker_reader_adapter_and_previous_snapshot():
    class Reader:
        async def quotes(self, keys):
            return [SimpleNamespace(
                symbol=keys[0], last_price=Decimal("101.5"),
                observed_at_utc=NOW, fetched_at_utc=NOW, depth=None)]
    val=await BrokerQuoteSource(Reader()).read_quote(KEY)
    assert val.instrument == KEY and val.source == "broker"
    saved=PreviousQuoteSource()
    saved.remember(val)
    previous=await saved.read_quote(KEY)
    assert previous.source == "previous"
    result=await MarketDataResolver({"previous": saved}).snapshot(KEY, clock=NOW)
    assert result.quality.status == "degraded"
    assert result.quality.execution_authorized is False


def test_option_chain_canonicalization_is_not_a_trading_signal():
    option=OptionChainObservation(underlying="INFY", strike=1500,
                                  expiry_date="2026-11-05", right="CE",
                                  bid=12, ask=13, open_interest=1000)
    chain=normalize_option_chain(KEY, [option], source="broker")
    assert chain.status == "degraded"  # no freshness receipt
    assert chain.contracts[0].expiry_utc.tzinfo == UTC
    assert chain.contracts[0].instrument == KEY
    with pytest.raises(ValueError):
        normalize_option_chain(KEY, [option.model_copy(update={"underlying":"RELIANCE"})],
                               source="broker")


@pytest.mark.asyncio
async def test_provider_health_is_explicit_fail_closed_and_non_authorizing():
    class Healthy:
        async def read_health(self):
            return SimpleNamespace(status="healthy")
        async def read_quote(self, key):
            return None
    class Unchecked:
        async def read_quote(self, key):
            return None
    class Broken:
        async def read_health(self):
            raise RuntimeError("access_token=secret")
        async def read_quote(self, key):
            return None
    resolver = MarketDataResolver({
        "broker": Healthy(), "alternate": Unchecked(), "lake": Broken(),
    })
    status=await resolver.provider_health()
    assert status["providers"] == {
        "broker": "healthy", "alternate": "unknown",
        "lake": "unavailable", "previous": "not_configured",
    }
    assert status["execution_authorized"] is False
