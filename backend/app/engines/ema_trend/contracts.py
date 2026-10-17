"""Serializable inputs and durable, fill-driven campaign artifacts."""
from __future__ import annotations

from decimal import Decimal
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from .config import Mode


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Bar(StrictModel):
    open_ms: int = Field(ge=0)
    close_ms: int = Field(gt=0)
    available_ms: int = Field(gt=0)
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    close: float = Field(gt=0)
    volume: float = Field(ge=0)
    provider: str = "recorded"
    adjustment_version: str = "raw"

    @model_validator(mode="after")
    def valid(self):
        if not self.open_ms < self.close_ms <= self.available_ms:
            raise ValueError("bar completion/availability timestamps are inconsistent")
        if self.low > min(self.open, self.close) or self.high < max(self.open, self.close) or self.low > self.high:
            raise ValueError("OHLC range is inconsistent")
        return self


class Pivot(StrictModel):
    side: Literal["HIGH", "LOW"]
    price: float
    occurred_index: int
    confirmed_index: int
    occurred_ms: int
    confirmed_ms: int
    previous_price: float | None = None


class Feature(StrictModel):
    index: int
    bar: Bar
    ema10: float | None = None
    ema21: float | None = None
    dema50: float | None = None
    atr10: float | None = None
    st_line: float | None = None
    st_direction: Literal["UP", "DOWN", "UNAVAILABLE"] = "UNAVAILABLE"
    context_ema10: float | None = None
    context_ema21: float | None = None
    context_st: str = "UNAVAILABLE"
    context_st_line: float | None = None
    context_close_ms: int | None = None
    context_count: int = 0
    ready: bool = False
    pivots: list[Pivot] = Field(default_factory=list)
    prior_high: Pivot | None = None
    prior_low: Pivot | None = None


class Setup(StrictModel):
    id: str
    symbol: str
    mode: Mode
    index: int
    close_ms: int
    cross_index: int | None = None
    status: Literal["watching", "armed", "error", "expired"]
    reason: str
    path: str | None = None
    stop: Decimal | None = None
    ceiling: Decimal | None = None
    feature: Feature
    config_revision: int = 0


class Lot(StrictModel):
    fill_id: str
    qty: int = Field(gt=0)
    price: Decimal = Field(gt=0)
    fee_per_share: Decimal = Field(default=Decimal(0), ge=0)


class Vacancy(StrictModel):
    component: str
    qty: int = Field(ge=0)
    index: int


class Candidate(StrictModel):
    kind: str
    index: int
    expires_index: int
    barrier: Decimal
    epoch: int


class Action(StrictModel):
    id: str
    campaign_id: str
    kind: str
    side: Literal["BUY", "SELL"]
    quantity: int = Field(gt=0)
    index: int
    at_ms: int
    limit: Decimal = Field(gt=0)
    components: dict[str, int] = Field(default_factory=dict)
    filled_quantity: int = 0
    source_episode: int | None = None
    status: Literal["RESERVED", "ACKNOWLEDGED", "PARTIAL", "FILLED", "CANCELLED", "UNKNOWN", "REJECTED"] = "RESERVED"


class Campaign(StrictModel):
    id: str
    uid: str
    account_id: str
    tenant_kind: Literal["SIMULATION", "REPLAY"] = "SIMULATION"
    symbol: str
    mode: Mode
    product: Literal["CNC", "MIS", "NRML"]
    instrument_token: int = Field(default=0,ge=0)
    lot_size: int = Field(default=1,ge=1)
    instrument_type: Literal["EQ", "FUT", "CE", "PE"] = "EQ"
    expiry: str = ""
    tick_size: Decimal = Field(default=Decimal(".05"), gt=0)
    state: Literal["ENTRY_PENDING", "OPEN", "ACTION_PENDING", "CLOSING", "CLOSED", "RECOVERY_REQUIRED"] = "ENTRY_PENDING"
    revision: int = 1
    config_hash: str
    config_revision: int
    config_snapshot: dict
    budget: Decimal = Field(gt=0)
    gap_reserve_per_share: Decimal = Field(default=Decimal(0), ge=0)
    initial_stop: Decimal = Field(gt=0)
    stop: Decimal = Field(gt=0)
    q0: int = 0
    p0: Decimal = Decimal(0)
    r_price: Decimal = Decimal(0)
    entry_terminal: bool = False
    entry_filled_qty: int = 0
    entry_notional: Decimal = Decimal(0)
    entry_index: int
    first_fill_ms: int | None = None
    last_index: int = -1
    last_close_ms: int = 0
    last_mark: Decimal = Decimal(0)
    below10: int = 0
    below21: int = 0
    allowance10: int = 0
    allowance21: int = 0
    took_4r: bool = False
    bullish_epoch: int = -1
    bearish_epoch: int = -1
    last_reduction_index: int = -1
    last_add_price: Decimal = Decimal(0)
    realized_net: Decimal = Decimal(0)
    realized_loss_debit: Decimal = Decimal(0)
    total_fees: Decimal = Decimal(0)
    lots: list[Lot] = Field(default_factory=list)
    vacancies: list[Vacancy] = Field(default_factory=list)
    candidates: list[Candidate] = Field(default_factory=list)
    used_catalysts: list[str] = Field(default_factory=list)
    seen_fill_ids: list[str] = Field(default_factory=list)
    pending: Action | None = None
    pending_close: Action | None = None
    reason: str = "ENTRY_PENDING"
    protection: str = "PAPER_MONITOR_ONLY"
    pivot_high: Pivot | None = None
    pivot_low: Pivot | None = None

    @property
    def qty(self) -> int:
        return sum(l.qty for l in self.lots)

    @property
    def vwap(self) -> Decimal:
        return sum((l.price*l.qty for l in self.lots), Decimal(0))/self.qty if self.qty else Decimal(0)

    @property
    def quarter(self) -> int:
        return (self.q0//self.lot_size//4)*self.lot_size
