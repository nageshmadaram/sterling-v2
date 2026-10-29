"""Phase 7 analytical P&L only; no actual fills and no trade authority.

The Phase 4-6 offline decision records do not contain broker fills. An
explicitly synthetic fill-pair is REQUIRED before P&L is computable. These
metrics quantify hypothetical outcomes, NOT observed or proven profitability.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)


class Side(str, Enum):
    LONG = "long"
    SHORT = "short"


class HypotheticalFillPair(Frozen):
    # Never call these broker fills. All prices, costs and risk units are
    # explicit operator-supplied, synthetic analytical assumptions.
    evidence_kind: Literal["offline_hypothetical"] = "offline_hypothetical"
    source_provenance: Literal["operator_supplied_synthetic"] = "operator_supplied_synthetic"
    entry_decision_id: str = Field(min_length=1, max_length=64)
    exit_decision_id: str = Field(min_length=1, max_length=64)
    entry_at_utc: datetime
    exit_at_utc: datetime
    entry_price: Decimal = Field(gt=0)
    exit_price: Decimal = Field(gt=0)
    units: Decimal = Field(gt=0)
    side: Side
    fees_inr: Decimal = Field(ge=0)
    slippage_inr: Decimal = Field(ge=0)
    risk_amount_inr: Decimal | None = Field(default=None, gt=0)
    market_regime: Literal["trending", "ranging", "volatile", "low_liquidity", "unknown"]
    entry_reason: str = Field(min_length=1, max_length=512)
    exit_reason: str = Field(min_length=1, max_length=512)
    operator_reason: str = Field(min_length=1, max_length=512)
    execution_authorized: Literal[False] = False

    @field_validator("entry_at_utc", "exit_at_utc")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("UTC-aware fill assumption time required")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def dates(self):
        if self.entry_at_utc >= self.exit_at_utc:
            raise ValueError("hypothetical exit must follow entry")
        if self.entry_decision_id == self.exit_decision_id:
            raise ValueError("entry and exit must be separate journal approvals")
        return self


class HypotheticalTrade(Frozen):
    id: str
    strategy_id: str
    version_id: str
    entry_run_id: str
    exit_run_id: str
    signal_id: str
    symbol: str
    exchange: str
    entry_decision_id: str
    exit_decision_id: str
    entry_at_utc: datetime
    exit_at_utc: datetime
    entry_reason: str
    exit_reason: str
    side: Side
    units: Decimal
    entry_price: Decimal
    exit_price: Decimal
    fees_inr: Decimal
    slippage_inr: Decimal
    gross_pnl_inr: Decimal
    net_pnl_inr: Decimal
    market_regime: str
    approval_mode: str
    risk_snapshot_ref: str
    risk_amount_inr: Decimal | None
    evidence_kind: Literal["offline_hypothetical"] = "offline_hypothetical"
    source_provenance: Literal["operator_supplied_synthetic"] = "operator_supplied_synthetic"
    execution_authorized: Literal[False] = False


def hypothetic_pnl(e: HypotheticalFillPair) -> tuple[Decimal, Decimal]:
    direction = Decimal(1) if e.side is Side.LONG else Decimal(-1)
    gross = (e.exit_price - e.entry_price) * e.units * direction
    return gross, gross - e.fees_inr - e.slippage_inr


def decimal_text(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None
