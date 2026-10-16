"""Frozen mode contracts; deliberately independent of legacy hourly swing."""
from __future__ import annotations

import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Mode = Literal["swing", "intraday", "scalping", "ultra_scalping"]
MODES: tuple[Mode, ...] = ("swing", "intraday", "scalping", "ultra_scalping")


def canonical_mode(value: str) -> Mode:
    value = {"scalp": "scalping", "ultrascalp": "ultra_scalping",
             "ultra_scalp": "ultra_scalping"}.get(value, value)
    if value not in MODES:
        raise ValueError(f"unsupported EMA Trend mode: {value}")
    return value  # type: ignore[return-value]


def stable_id(*values: object) -> str:
    raw = json.dumps(values, sort_keys=True, separators=(",", ":"), default=str,
                     allow_nan=False)
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


class Profile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)
    mode: Mode
    signal_minutes: int
    context_minutes: int
    product: Literal["CNC", "MIS"]
    setup_expiry_bars: int
    cooldown_bars: int
    min_stop_atr: float
    max_stop_atr: float
    max_chase_atr: float
    max_quote_age_s: int
    max_hold_bars: int | None = None
    entry_start: str = "09:30"
    entry_end: str = "14:45"
    force_close: str | None = "15:15"
    require_green: bool = True
    chop_filter: bool = True
    horizon_policy: Literal["CONDITION_BASED", "TIME_OR_CONDITION"] = "TIME_OR_CONDITION"


PROFILES = {
    "swing": Profile(mode="swing", signal_minutes=1440, context_minutes=10080,
                     product="CNC", setup_expiry_bars=20, cooldown_bars=2,
                     min_stop_atr=.5, max_stop_atr=4, max_chase_atr=.5,
                     max_quote_age_s=30, entry_start="09:20", entry_end="15:00",
                     force_close=None, require_green=False, chop_filter=False,
                     horizon_policy="CONDITION_BASED"),
    "intraday": Profile(mode="intraday", signal_minutes=15, context_minutes=60,
                        product="MIS", setup_expiry_bars=12, cooldown_bars=3,
                        min_stop_atr=.5, max_stop_atr=3, max_chase_atr=.3,
                        max_quote_age_s=10),
    "scalping": Profile(mode="scalping", signal_minutes=3, context_minutes=15,
                        product="MIS", setup_expiry_bars=10, cooldown_bars=5,
                        min_stop_atr=.4, max_stop_atr=2.5, max_chase_atr=.2,
                        max_quote_age_s=5, max_hold_bars=20),
    "ultra_scalping": Profile(mode="ultra_scalping", signal_minutes=1, context_minutes=5,
                             product="MIS", setup_expiry_bars=8, cooldown_bars=10,
                             min_stop_atr=.4, max_stop_atr=2, max_chase_atr=.15,
                             max_quote_age_s=3, max_hold_bars=15),
}


class EmaTrendConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, validate_assignment=True)
    enabled: bool = False
    auto_execute: bool = False
    execution_mode: Literal["PAPER"] = "PAPER"
    modes: list[Mode] = Field(default_factory=lambda: ["swing"], min_length=1, max_length=4)
    symbols: list[str] = Field(default_factory=list, max_length=30)
    allocation_inr: Decimal = Field(default=Decimal("0"), ge=0, le=Decimal("100000000"))
    risk_fraction: Decimal = Field(default=Decimal("0.0025"), gt=0, le=Decimal("0.005"))
    strategy_risk_fraction: Decimal = Field(default=Decimal("0.0075"), gt=0, le=Decimal("0.0075"))
    daily_loss_fraction: Decimal = Field(default=Decimal("0.01"), gt=0, le=Decimal("0.01"))
    drawdown_fraction: Decimal = Field(default=Decimal("0.05"), gt=0, le=Decimal("0.05"))
    max_campaigns: int = Field(default=3, ge=1, le=3)
    max_sector_campaigns: int = Field(default=2, ge=1, le=2)
    # Explicit estimated PAPER schedule. Not represented as actual broker fees.
    fee_bps: Decimal = Field(default=Decimal("10"), ge=0, le=100)
    brokerage_per_order: Decimal = Field(default=Decimal("0"), ge=0, le=1000)
    slippage_bps: Decimal = Field(default=Decimal("5"), ge=0, le=100)
    gap_reserve_atr: Decimal = Field(default=Decimal("0.1"), ge=0, le=2)
    fee_schedule: str = Field(default="PAPER_ESTIMATE_V1_NOT_BROKER_VERIFIED", max_length=160)

    @field_validator("modes", mode="before")
    @classmethod
    def modes_valid(cls, values):
        if not isinstance(values, list):
            raise ValueError("modes must be a list")
        result = [canonical_mode(v) for v in values]
        if len(set(result)) != len(result):
            raise ValueError("duplicate modes")
        return result

    @field_validator("symbols")
    @classmethod
    def symbols_valid(cls, values):
        normalized = [v.strip().upper() for v in values]
        if any(not re.fullmatch(r"(?:NSE|BSE):[A-Z0-9&_.-]{1,40}", v) for v in normalized):
            raise ValueError("symbols must be explicit NSE:SYMBOL or BSE:SYMBOL equities")
        if len(set(normalized)) != len(normalized):
            raise ValueError("duplicate symbols")
        return normalized

    @property
    def config_hash(self) -> str:
        return stable_id(self.model_dump(mode="json"))


@lru_cache(maxsize=1)
def rule_hash() -> str:
    """Bind evidence to actual mathematical sources, not only a hand-edited tag."""
    digest = hashlib.sha256()
    directory = Path(__file__).parent
    for name in ("config.py", "contracts.py", "indicators.py", "pivots.py", "features.py",
                 "setups.py", "state_machine.py", "sizing.py", "calendar.py", "manifest.py"):
        digest.update(name.encode())
        digest.update((directory/name).read_bytes())
    return digest.hexdigest()[:24]


def descriptor() -> dict:
    return {"id": "ema_trend", "name": "10/21 EMA + Supertrend", "version": "1.0.0",
            "execution_vehicle": "EQUITY", "profiles": {k: v.model_dump() for k, v in PROFILES.items()},
            "indicators": {"ema_fast": 10, "ema_slow": 21, "dema": 50, "st_atr": 10, "st_factor": 3},
            "rule_hash": rule_hash(),
            "live_capable": False,
            "blockers": ["ECONOMIC_EVIDENCE_INCONCLUSIVE", "EQUITY_PROTECTION_UNVERIFIED",
                         "LIVE_EXECUTION_DISABLED", "FEE_SCHEDULE_NOT_BROKER_VERIFIED"]}
