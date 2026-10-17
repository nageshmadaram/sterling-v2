"""Real-money EMA campaign state; deliberately distinct from virtual Campaign.

These contracts model proven broker facts, not a permission to place orders.
Only canonical Kite intents and verified broker fills may advance these records.
A protected position needs a verified broker-side stop; an application loop's
local stop calculation never satisfies that requirement.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .config import Mode

LivePhase = Literal[
    "PREPARED", "ENTRY_PENDING", "PROTECTION_REQUIRED", "PROTECTION_PENDING",
    "PROTECTED", "EXIT_PENDING", "RECOVERY_REQUIRED", "CLOSED",
]
OrderPhase = Literal[
    "RESERVED", "SUBMITTING", "SUBMITTED", "ACKNOWLEDGED", "PARTIAL",
    "COMPLETE", "CANCELLED", "REJECTED", "UNKNOWN",
]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class BrokerIntent(Strict):
    intent_key: str = Field(min_length=1)
    side: Literal["BUY", "SELL"]
    requested_qty: int = Field(gt=0)
    order_id: str = ""
    confirmed_qty: int = Field(default=0, ge=0)
    cumulative_value: Decimal = Field(default=Decimal(0), ge=0)
    status: OrderPhase = "RESERVED"

    @model_validator(mode="after")
    def check(self):
        if self.confirmed_qty > self.requested_qty:
            raise ValueError("LIVE_ORDER_OVERFILLED")
        if bool(self.confirmed_qty) != bool(self.cumulative_value):
            raise ValueError("LIVE_ORDER_FILL_VALUE_MISMATCH")
        if self.status == "COMPLETE" and self.confirmed_qty != self.requested_qty:
            raise ValueError("LIVE_ORDER_COMPLETE_WITHOUT_FILL")
        if self.status in {"SUBMITTED", "ACKNOWLEDGED", "PARTIAL", "COMPLETE"} and not self.order_id:
            raise ValueError("LIVE_ORDER_STATUS_REQUIRES_BROKER_ID")
        return self


class BrokerStop(Strict):
    trigger_id: str = Field(min_length=1)
    exchange: Literal["NSE", "BSE"]
    symbol: str = Field(min_length=1)
    product: Literal["CNC", "MIS"]
    qty: int = Field(gt=0)
    trigger: Decimal = Field(gt=0)
    verified_active: bool = False
    # A fired GTT can have a resting or rejected LIMIT child.
    child_order_unresolved: bool = False


class LiveCampaign(Strict):
    id: str = Field(min_length=1)
    owner_uid: str = Field(min_length=1)
    account_id: str = Field(min_length=1)
    exchange: Literal["NSE", "BSE"]
    symbol: str = Field(min_length=1)
    mode: Mode
    product: Literal["CNC", "MIS"]
    strategy_version: str = Field(min_length=1)
    config_hash: str = Field(min_length=1)
    signal_id: str = Field(min_length=1)
    phase: LivePhase = "PREPARED"
    entries: list[BrokerIntent] = Field(default_factory=list)
    exits: list[BrokerIntent] = Field(default_factory=list)
    confirmed_bought: int = Field(default=0, ge=0)
    confirmed_sold: int = Field(default=0, ge=0)
    stop: Decimal = Field(gt=0)
    protection: BrokerStop | None = None
    protection_submission_unknown: bool = False
    flatten_requested: bool = False
    reason: str = ""
    revision: int = Field(default=0, ge=0)

    @property
    def held_qty(self) -> int:
        return self.confirmed_bought - self.confirmed_sold

    @model_validator(mode="after")
    def invariant(self):
        if self.owner_uid.startswith("sim:") or self.account_id.startswith("SIM-"):
            raise ValueError("REAL_CAMPAIGN_CANNOT_BIND_SIMULATION_ACCOUNT")
        if self.confirmed_sold > self.confirmed_bought:
            raise ValueError("LIVE_CAMPAIGN_OVERSOLD")
        if any(entry.side != "BUY" for entry in self.entries):
            raise ValueError("LIVE_ENTRY_MUST_BUY")
        if any(exit_order.side != "SELL" for exit_order in self.exits):
            raise ValueError("LIVE_EXIT_MUST_SELL")
        if len({o.intent_key for o in self.entries + self.exits}) != len(self.entries + self.exits):
            raise ValueError("LIVE_ORDER_INTENT_DUPLICATED")
        if self.confirmed_bought != sum(o.confirmed_qty for o in self.entries):
            raise ValueError("LIVE_ENTRY_ACCOUNTING_DIVERGENCE")
        if self.confirmed_sold != sum(o.confirmed_qty for o in self.exits):
            raise ValueError("LIVE_EXIT_ACCOUNTING_DIVERGENCE")
        if self.protection is not None:
            stop = self.protection
            if (stop.exchange, stop.symbol, stop.product) != (self.exchange, self.symbol, self.product):
                raise ValueError("LIVE_STOP_CONTRACT_MISMATCH")
            if stop.trigger < self.stop:
                raise ValueError("LIVE_STOP_WEAKER_THAN_CAMPAIGN")
            if stop.qty > self.held_qty:
                raise ValueError("LIVE_STOP_QUANTITY_EXCEEDS_OWNED_SHARES")
        if self.phase == "PROTECTED":
            if (not self.held_qty or self.protection is None
                    or not self.protection.verified_active
                    or self.protection.child_order_unresolved
                    or self.protection.qty != self.held_qty
                    or self.protection_submission_unknown):
                raise ValueError("LIVE_PROTECTED_REQUIRES_EXACT_VERIFIED_BROKER_COVERAGE")
        if self.phase == "CLOSED":
            if (self.held_qty or self.protection is not None
                    or self.protection_submission_unknown
                    or any(o.status not in {"COMPLETE", "CANCELLED", "REJECTED"} for o in self.entries + self.exits)):
                raise ValueError("LIVE_CLOSED_REQUIRES_TERMINAL_ORDERS_AND_ZERO_INVENTORY")
        return self
