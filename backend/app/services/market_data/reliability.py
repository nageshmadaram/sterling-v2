"""Standalone fail-closed market observation resolver; no routes or execution calls.

Source hierarchy is ordered and explicit: broker -> alternate -> lake -> previous.
An unavailable/malformed source never becomes an invented or executable quote.
"""
from __future__ import annotations

import logging
from datetime import datetime, time, timezone
from decimal import Decimal
from typing import Callable, Protocol

from app.schemas.market_reliability import (
    Candle, InstrumentKey, MarketSnapshot, OptionChain, OptionContract,
    Quality, QualityEvent, Quote, SourceTier, as_utc,
)

log = logging.getLogger(__name__)

ORDER: tuple[SourceTier, ...] = ("broker", "alternate", "lake", "previous")
GOOD_MIN_SCORE = 85


class ReadOnlyQuoteSource(Protocol):
    async def read_quote(self, key: InstrumentKey) -> Quote | None: ...


def assess(quote: Quote, *, clock: datetime, stale_after_ms: int,
           require_depth: bool = False) -> Quality:
    if stale_after_ms <= 0:
        raise ValueError("stale_after_ms must be positive")
    now = as_utc(clock)
    issues: list[str] = []
    score = 100
    if quote.observed_at_utc is None:
        issues.append("TIMESTAMP_MISSING")
        score -= 70
        age_ms = None
    else:
        delta = int((now - quote.observed_at_utc).total_seconds() * 1000)
        age_ms = max(0, delta)
        if delta < -1000:
            issues.append("FUTURE_TIMESTAMP")
            score -= 70
        elif delta > stale_after_ms:
            issues.append("STALE")
            score -= 60
    if quote.source == "alternate":
        issues.append("ALTERNATE_SOURCE")
        score -= 10
    elif quote.source == "lake":
        issues.append("HISTORICAL_NOT_LIVE")
        score -= 40
    elif quote.source == "previous":
        issues.append("PREVIOUS_SNAPSHOT_NOT_LIVE")
        score -= 50
    if quote.depth is None and require_depth:
        issues.append("DEPTH_MISSING")
        score -= 15
    if quote.depth is not None:
        if not quote.depth.bids or not quote.depth.asks:
            issues.append("DEPTH_PARTIAL")
            if require_depth:
                score -= 20
    score = max(0, score)
    # Lake / previous data NEVER count as live-quality, even when time-close.
    realtime = quote.source in ("broker", "alternate")
    status = ("good" if realtime and not any(x in issues for x in
              ("STALE", "TIMESTAMP_MISSING", "FUTURE_TIMESTAMP", "DEPTH_MISSING", "DEPTH_PARTIAL"))
              and score >= GOOD_MIN_SCORE else "degraded")
    return Quality(status=status, score=score, issues=tuple(issues),
                   source=quote.source, age_ms=age_ms,
                   fallback_used=quote.source != "broker")


def classify_regime(candles: tuple[Candle, ...], *, spread_bps: Decimal | None = None) -> str:
    """Deterministic observational tags, NOT trading signals or market forecasts."""
    if len(candles) < 8:
        return "unknown"
    recent = candles[-8:]
    if spread_bps is not None and spread_bps >= 30:
        return "low_liquidity"
    if sum((c.volume for c in recent), Decimal(0)) == 0:
        return "low_liquidity"
    prices = [c.close for c in recent]
    mean_price = sum(prices) / Decimal(len(prices))
    if mean_price <= 0:
        return "unknown"
    span_pct = (max(c.high for c in recent) - min(c.low for c in recent)) / mean_price
    change_pct = abs(prices[-1] - prices[0]) / mean_price
    if span_pct >= Decimal("0.10"):
        return "volatile"
    if change_pct >= Decimal("0.015") and change_pct >= span_pct * Decimal("0.45"):
        return "trending"
    return "ranging"


def normalize_option_chain(underlying: InstrumentKey, rows, *,
                           source: SourceTier, observed_at_utc: datetime | None = None) -> OptionChain:
    """Read-only observation: expiry is a date, not a certified settlement clock."""
    from datetime import date
    observed = as_utc(observed_at_utc) if observed_at_utc else None
    contracts = []
    if not isinstance(rows, (tuple, list)):
        raise ValueError("option chain must be a list of normalized observations")
    for row in rows:
        if row.underlying != underlying.symbol or row.right not in ("CE", "PE"):
            raise ValueError("option contract identity mismatch")
        expiry = date.fromisoformat(row.expiry_date)
        # Midnight UTC is a date marker only, NEVER an order expiry timestamp.
        expiry_marker = datetime.combine(expiry, time.min, timezone.utc)
        contracts.append(OptionContract(
            instrument=underlying, expiry_utc=expiry_marker, strike=row.strike,
            right=row.right, bid=row.bid, ask=row.ask,
            open_interest=row.open_interest, observed_at_utc=observed,
            source=source,
        ))
    return OptionChain(underlying=underlying, contracts=tuple(contracts),
                       observed_at_utc=observed, source=source,
                       status="degraded" if contracts else "missing")


class QualityMonitor:
    """Local operator diagnostics and structured events, never a trading gate."""

    def __init__(self, sink: Callable[[QualityEvent], None] | None = None):
        self._sink = sink
        self._latest: dict[str, MarketSnapshot] = {}
        self._events: list[QualityEvent] = []

    def record(self, snapshot: MarketSnapshot, *, at: datetime) -> None:
        self._latest[snapshot.instrument.qualified] = snapshot
        event_type = ("missing" if snapshot.quality.status == "missing" else
                      "fallback" if snapshot.quality.fallback_used else
                      "degraded" if snapshot.quality.status != "good" else "recovered")
        event = QualityEvent(
            at_utc=at, instrument=snapshot.instrument, kind=event_type,
            code=("MARKET_DATA_MISSING" if event_type == "missing" else
                  "FALLBACK_ACTIVE" if event_type == "fallback" else
                  "DATA_DEGRADED" if event_type == "degraded" else "DATA_GOOD"),
            source=snapshot.quality.source, score=snapshot.quality.score,
        )
        self._events.append(event)
        # Bounded diagnostics, never an unbounded in-memory queue.
        if len(self._events) > 500:
            del self._events[:-500]
        log.info("market_data_quality_event %s",
                 event.model_dump_json(exclude={"instrument": {"symbol"}}))
        if self._sink:
            try:
                self._sink(event)
            except Exception:
                log.warning("market_data_quality_sink_failed")

    def health(self) -> dict:
        return {
            "observed_instruments": len(self._latest),
            "good": sum(s.quality.status == "good" for s in self._latest.values()),
            "degraded": sum(s.quality.status == "degraded" for s in self._latest.values()),
            "missing": sum(s.quality.status == "missing" for s in self._latest.values()),
            "fallback_active": sum(s.quality.fallback_used for s in self._latest.values()),
            "latest_events": tuple(self._events[-20:]),
            "execution_authorized": False,
        }


class MarketDataResolver:
    """Explicit source injection. No automatic network fetch, order or deployment wiring."""

    def __init__(self, sources: dict[SourceTier, ReadOnlyQuoteSource], *,
                 monitor: QualityMonitor | None = None,
                 stale_after_ms: int = 5000):
        if stale_after_ms <= 0:
            raise ValueError("stale threshold must be positive")
        self._sources = dict(sources)
        self._monitor = monitor or QualityMonitor()
        self._stale_after_ms = stale_after_ms

    @property
    def monitor(self) -> QualityMonitor:
        return self._monitor

    async def provider_health(self) -> dict:
        """Explicit read-only health probe; unknown is never interpreted as up."""
        results = {}
        for tier in ORDER:
            source = self._sources.get(tier)
            if source is None:
                results[tier] = "not_configured"
                continue
            probe = getattr(source, "read_health", None)
            if not callable(probe):
                results[tier] = "unknown"
                continue
            try:
                value = await probe()
                status = value.get("status") if isinstance(value, dict) else getattr(value, "status", None)
                results[tier] = status if status in (
                    "healthy", "unavailable", "simulation", "unknown"
                ) else "unknown"
            except Exception:
                log.warning("market_data_provider_health_failed tier=%s", tier)
                results[tier] = "unavailable"
        return {"providers": results, "execution_authorized": False}

    async def snapshot(self, key: InstrumentKey, *, clock: datetime,
                       require_depth: bool = False,
                       candles: tuple[Candle, ...] = ()) -> MarketSnapshot:
        now = as_utc(clock)
        attempted: list[SourceTier] = []
        degraded: list[tuple[int, int, Quote, Quality]] = []
        chosen: tuple[Quote, Quality] | None = None
        for tier_index, tier in enumerate(ORDER):
            provider = self._sources.get(tier)
            if provider is None:
                continue
            attempted.append(tier)
            try:
                value = await provider.read_quote(key)
                if value is None:
                    continue
                if not isinstance(value, Quote) or value.instrument != key or value.source != tier:
                    raise ValueError("provider contract mismatch")
                q = assess(value, clock=now, stale_after_ms=self._stale_after_ms,
                           require_depth=require_depth)
            except Exception:
                # Never propagate raw provider errors (may contain credentials).
                log.warning("market_data_source_invalid tier=%s", tier)
                continue
            if q.status == "good":
                chosen = (value, q)
                break
            degraded.append((q.score, -tier_index, value, q))
        if chosen is None and degraded:
            _, _, quote, quality = max(degraded, key=lambda x: (x[0], x[1]))
            chosen = (quote, quality)
        if chosen is None:
            quality = Quality(status="missing", score=0, issues=("ALL_SOURCES_UNAVAILABLE",))
            snap = MarketSnapshot(instrument=key, observed_at_utc=now, quote=None,
                                  regime="unknown", quality=quality,
                                  attempted_sources=tuple(attempted))
        else:
            quote, quality = chosen
            regime = classify_regime(candles)
            snap = MarketSnapshot(instrument=key, observed_at_utc=now, quote=quote,
                                  regime=regime, quality=quality,
                                  attempted_sources=tuple(attempted))
        self._monitor.record(snap, at=now)
        return snap


class BrokerQuoteSource:
    """Optional wrapper for Phase 2A normalized read facade. No broker write calls."""
    def __init__(self, normalized_reader):
        self._reader = normalized_reader

    async def read_health(self):
        return await self._reader.health()

    async def read_quote(self, key: InstrumentKey) -> Quote | None:
        rows = await self._reader.quotes([key.qualified])
        if len(rows) != 1:
            return None
        row = rows[0]
        from app.schemas.market_reliability import Depth, Level
        depth = None
        if row.depth is not None and row.observed_at_utc is not None:
            depth = Depth(instrument=key, observed_at_utc=row.observed_at_utc,
                          bids=tuple(Level(price=v.price, quantity=v.quantity, orders=v.orders)
                                     for v in row.depth.bids),
                          asks=tuple(Level(price=v.price, quantity=v.quantity, orders=v.orders)
                                     for v in row.depth.asks))
        return Quote(instrument=key, last_price=row.last_price, depth=depth,
                     observed_at_utc=row.observed_at_utc,
                     fetched_at_utc=row.fetched_at_utc, source="broker")


class PreviousQuoteSource:
    """Memory-only previous observations. Never presents a fallback as live."""
    def __init__(self):
        self._quotes: dict[str, Quote] = {}

    def remember(self, value: Quote) -> None:
        self._quotes[value.instrument.qualified] = value

    async def read_quote(self, key: InstrumentKey) -> Quote | None:
        previous = self._quotes.get(key.qualified)
        return previous.model_copy(update={"source": "previous"}) if previous else None
