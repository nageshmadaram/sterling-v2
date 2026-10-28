"""Read-only, broker-independent observation records.

No type defined here represents execution authorization or order submission.
Amounts are Decimal; timestamps are UTC. Unknown exchange fields remain None.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Observation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)


class BrokerHealth(Observation):
    broker: Literal["zerodha"] = "zerodha"
    status: Literal["healthy", "unavailable", "simulation", "unknown"]
    authenticated: bool = False
    observed_at_utc: datetime


class AccountInfo(Observation):
    broker: Literal["zerodha"] = "zerodha"
    account_id: str = Field(min_length=1)
    user_name: str | None = None
    email: str | None = None


class Funds(Observation):
    segment: str = Field(min_length=1)
    available_inr: Decimal | None = None
    used_inr: Decimal | None = None
    net_inr: Decimal | None = None


class Position(Observation):
    exchange: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    product: str | None = None
    net_quantity: Decimal
    average_price: Decimal | None = None
    last_price: Decimal | None = None
    pnl_inr: Decimal | None = None


class Holding(Observation):
    exchange: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    quantity: Decimal
    average_price: Decimal | None = None
    last_price: Decimal | None = None


class OrderObservation(Observation):
    order_id: str = Field(min_length=1)
    exchange: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    status: str = Field(min_length=1)
    side: str | None = None
    product: str | None = None
    quantity: Decimal
    filled_quantity: Decimal | None = None
    price: Decimal | None = None
    observed_at_utc: datetime | None = None


class FillObservation(Observation):
    fill_id: str = Field(min_length=1)
    order_id: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    quantity: Decimal
    price: Decimal
    executed_at_utc: datetime | None = None


class DepthLevel(Observation):
    price: Decimal
    quantity: Decimal
    orders: int | None = Field(default=None, ge=0)


class MarketDepth(Observation):
    symbol: str = Field(min_length=1)
    exchange: str = Field(min_length=1)
    bids: tuple[DepthLevel, ...]
    asks: tuple[DepthLevel, ...]
    observed_at_utc: datetime | None = None


class Quote(Observation):
    symbol: str = Field(min_length=1)
    exchange: str = Field(min_length=1)
    last_price: Decimal
    observed_at_utc: datetime | None = None
    fetched_at_utc: datetime
    stale: bool
    source: Literal["kite"] = "kite"
    quality: Literal["fresh", "stale", "unknown"]
    depth: MarketDepth | None = None


class HistoricalCandle(Observation):
    symbol: str = Field(min_length=1)
    timeframe: str = Field(min_length=1)
    timestamp_ms: int = Field(ge=0)
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    source: Literal["existing_market_adapter"] = "existing_market_adapter"


class OptionChainObservation(Observation):
    underlying: str = Field(min_length=1)
    strike: Decimal
    expiry_date: str = Field(min_length=1)
    right: Literal["CE", "PE"]
    bid: Decimal | None = None
    ask: Decimal | None = None
    iv: Decimal | None = None
    open_interest: Decimal | None = None
    delta: Decimal | None = None
    gamma: Decimal | None = None
    theta: Decimal | None = None
    vega: Decimal | None = None
