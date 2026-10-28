"""Phase 3 observational market-data contracts; never an order authorization.

Timestamps are timezone-aware UTC and price/volume fields use finite Decimal.
Exchange and symbol are separate to prevent cross-exchange cache collisions.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SourceTier = Literal["broker", "alternate", "lake", "previous"]
QualityStatus = Literal["good", "degraded", "missing"]
MarketRegime = Literal["trending", "ranging", "volatile", "low_liquidity", "unknown"]


def as_utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("market timestamp must have an explicit timezone")
    return value.astimezone(timezone.utc)


class FrozenRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)


class InstrumentKey(FrozenRecord):
    exchange: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,11}$")
    symbol: str = Field(min_length=1, max_length=128)

    @field_validator("symbol")
    @classmethod
    def clean_symbol(cls, value: str) -> str:
        if value != value.strip() or ":" in value or not value.isprintable():
            raise ValueError("symbol must be normalized without exchange prefix")
        return value

    @property
    def qualified(self) -> str:
        return f"{self.exchange}:{self.symbol}"


class Level(FrozenRecord):
    price: Decimal = Field(gt=0)
    quantity: Decimal = Field(ge=0)
    orders: int | None = Field(default=None, ge=0, strict=True)


class Depth(FrozenRecord):
    instrument: InstrumentKey
    bids: tuple[Level, ...] = ()
    asks: tuple[Level, ...] = ()
    observed_at_utc: datetime

    @field_validator("observed_at_utc")
    @classmethod
    def timestamp_utc(cls, value: datetime) -> datetime:
        return as_utc(value)


class Quote(FrozenRecord):
    instrument: InstrumentKey
    last_price: Decimal = Field(gt=0)
    observed_at_utc: datetime | None
    fetched_at_utc: datetime
    depth: Depth | None = None
    source: SourceTier

    @field_validator("observed_at_utc", "fetched_at_utc")
    @classmethod
    def timestamp_utc(cls, value: datetime | None) -> datetime | None:
        return as_utc(value) if value is not None else None

    @model_validator(mode="after")
    def consistent_depth(self):
        if self.depth and self.depth.instrument != self.instrument:
            raise ValueError("depth instrument differs from quote instrument")
        return self


class Candle(FrozenRecord):
    instrument: InstrumentKey
    timeframe: str = Field(pattern=r"^[1-9][0-9]*(?:m|h|d)$")
    timestamp_utc: datetime
    open: Decimal = Field(gt=0)
    high: Decimal = Field(gt=0)
    low: Decimal = Field(gt=0)
    close: Decimal = Field(gt=0)
    volume: Decimal = Field(ge=0)
    source: SourceTier

    @field_validator("timestamp_utc")
    @classmethod
    def timestamp_must_be_utc(cls, value: datetime) -> datetime:
        return as_utc(value)

    @model_validator(mode="after")
    def valid_ohlc(self):
        if self.low > min(self.open, self.close) or self.high < max(self.open, self.close) or self.low > self.high:
            raise ValueError("OHLC values inconsistent")
        return self


class HistoryQuery(FrozenRecord):
    instrument: InstrumentKey
    timeframe: str = Field(pattern=r"^[1-9][0-9]*(?:m|h|d)$")
    start_utc: datetime
    end_utc: datetime

    @field_validator("start_utc", "end_utc")
    @classmethod
    def require_utc(cls, value: datetime) -> datetime:
        return as_utc(value)

    @model_validator(mode="after")
    def valid_window(self):
        if self.end_utc <= self.start_utc:
            raise ValueError("history end must follow start")
        if (self.end_utc - self.start_utc).days > 366:
            raise ValueError("history window exceeds bounded retrieval limit")
        return self


class HistoryWindow(FrozenRecord):
    query: HistoryQuery
    candles: tuple[Candle, ...] = ()
    gap_starts_utc: tuple[datetime, ...] = ()
    coverage_pct: int = Field(ge=0, le=100)
    quality_status: QualityStatus
    source: SourceTier | None

    @field_validator("gap_starts_utc")
    @classmethod
    def utc_gaps(cls, values: tuple[datetime, ...]) -> tuple[datetime, ...]:
        return tuple(as_utc(value) for value in values)


class OptionContract(FrozenRecord):
    instrument: InstrumentKey
    expiry_utc: datetime
    strike: Decimal = Field(gt=0)
    right: Literal["CE", "PE"]
    bid: Decimal | None = Field(default=None, ge=0)
    ask: Decimal | None = Field(default=None, ge=0)
    open_interest: Decimal | None = Field(default=None, ge=0)
    observed_at_utc: datetime | None = None
    source: SourceTier

    @field_validator("expiry_utc", "observed_at_utc")
    @classmethod
    def utc_values(cls, value: datetime | None) -> datetime | None:
        return as_utc(value) if value is not None else None


class OptionChain(FrozenRecord):
    underlying: InstrumentKey
    contracts: tuple[OptionContract, ...]
    observed_at_utc: datetime | None = None
    source: SourceTier
    status: QualityStatus

    @field_validator("observed_at_utc")
    @classmethod
    def utc_observation(cls, value: datetime | None) -> datetime | None:
        return as_utc(value) if value is not None else None

    @model_validator(mode="after")
    def match_underlying(self):
        if any(c.instrument != self.underlying or c.source != self.source for c in self.contracts):
            raise ValueError("option-chain instrument/source mismatch")
        if self.status == "good" and (not self.contracts or self.observed_at_utc is None):
            raise ValueError("unverified option-chain cannot be good")
        return self


class Quality(FrozenRecord):
    status: QualityStatus
    score: int = Field(ge=0, le=100)
    issues: tuple[str, ...] = ()
    source: SourceTier | None = None
    age_ms: int | None = Field(default=None, ge=0)
    fallback_used: bool = False
    # Admission is deliberately always false; it is not a strategy risk gate.
    execution_authorized: Literal[False] = False


class QualityEvent(FrozenRecord):
    at_utc: datetime
    instrument: InstrumentKey
    kind: Literal["missing", "degraded", "fallback", "recovered"]
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,63}$")
    source: SourceTier | None
    score: int = Field(ge=0, le=100)

    @field_validator("at_utc")
    @classmethod
    def utc_time(cls, value: datetime) -> datetime:
        return as_utc(value)


class MarketSnapshot(FrozenRecord):
    instrument: InstrumentKey
    observed_at_utc: datetime
    quote: Quote | None
    options: tuple[OptionContract, ...] = ()
    regime: MarketRegime = "unknown"
    quality: Quality
    attempted_sources: tuple[SourceTier, ...] = ()
    # Read-only observation, *never* an execution-capable signal.
    execution_authorized: Literal[False] = False

    @field_validator("observed_at_utc")
    @classmethod
    def utc_time(cls, value: datetime) -> datetime:
        return as_utc(value)

    @model_validator(mode="after")
    def no_false_quality(self):
        if (self.quote is None) != (self.quality.status == "missing"):
            raise ValueError("missing result must have missing quality state")
        if self.quote is not None and self.quote.instrument != self.instrument:
            raise ValueError("snapshot instrument mismatch")
        return self
