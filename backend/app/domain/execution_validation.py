"""Phase 4: immutable offline execution-validation contracts, never broker authority."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

class Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

class GateState(str, Enum):
    DISABLED = "disabled"
    VALIDATION_REQUIRED = "validation_required"
    APPROVED = "approved"
    BLOCKED = "blocked"
    PAUSED = "paused"
    EMERGENCY_STOP = "emergency_stop"

class Decision(str, Enum):
    ALLOW = "allow"  # simulation/paper preview ONLY; never broker admission
    DEFER = "defer"
    REJECT = "reject"
    PAUSE = "pause"
    EMERGENCY_STOP = "emergency_stop"

class ApprovalMode(str, Enum):
    AUTO = "auto"
    SEMI_AUTO = "semi_auto"
    MANUAL = "manual"
    BLOCKED = "blocked"

class ExecutionMode(str, Enum):
    SIMULATION = "simulation"
    PAPER = "paper"
    LIVE = "live"

ALLOWED_TRANSITIONS = {
    GateState.DISABLED: {GateState.VALIDATION_REQUIRED, GateState.BLOCKED, GateState.EMERGENCY_STOP},
    GateState.VALIDATION_REQUIRED: {GateState.APPROVED, GateState.BLOCKED, GateState.PAUSED, GateState.DISABLED, GateState.EMERGENCY_STOP},
    GateState.APPROVED: {GateState.PAUSED, GateState.BLOCKED, GateState.DISABLED, GateState.EMERGENCY_STOP},
    GateState.BLOCKED: {GateState.VALIDATION_REQUIRED, GateState.DISABLED, GateState.EMERGENCY_STOP},
    GateState.PAUSED: {GateState.VALIDATION_REQUIRED, GateState.BLOCKED, GateState.EMERGENCY_STOP},
    GateState.EMERGENCY_STOP: {GateState.VALIDATION_REQUIRED},
}

def transition_allowed(old: GateState, new: GateState, *, explicit_reset: bool = False) -> bool:
    return (new in ALLOWED_TRANSITIONS[old]
            and (old is not GateState.EMERGENCY_STOP or explicit_reset))

class RiskPolicy(Frozen):
    max_exposure_inr: Decimal = Field(gt=0)
    max_daily_loss_inr: Decimal = Field(gt=0)
    max_session_loss_inr: Decimal = Field(gt=0)
    max_order_notional_inr: Decimal = Field(gt=0)
    max_units: Decimal = Field(gt=0)
    permitted_regimes: frozenset[Literal["trending","ranging","volatile","low_liquidity"]] = frozenset({"trending","ranging"})
    min_quality_score: int = Field(default=85, ge=0, le=100)
    max_config_age_seconds: int = Field(default=86400, gt=0)
    min_recent_performance: Decimal = Decimal("-0.10")

class StrategyHealth(Frozen):
    valid_parameters: bool
    has_risk_profile: bool
    valid_market_access: bool
    recent_performance: Decimal
    configuration_at_utc: datetime
    operational: bool

    @field_validator("configuration_at_utc")
    @classmethod
    def utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timezone required")
        return value.astimezone(timezone.utc)

class RiskEvidence(Frozen):
    current_exposure_inr: Decimal = Field(ge=0)
    daily_loss_inr: Decimal = Field(ge=0)
    session_loss_inr: Decimal = Field(ge=0)
    provider_healthy: bool
    market_open: bool
    risk_profile_verified: bool
    symbol_valid: bool
    # Must originate from previously approved source; caller input is NOT
    # independently authenticated in this offline service.
    source_verified: bool = False

class SignalIntent(Frozen):
    signal_id: str = Field(min_length=1, max_length=128)
    order_intent_id: str = Field(min_length=1, max_length=128)
    strategy_id: str = Field(min_length=1, max_length=64)
    version_id: str = Field(min_length=1, max_length=64)
    run_id: str = Field(min_length=1, max_length=64)
    qualified_symbol: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,11}:[^: ]{1,128}$")
    mode: ExecutionMode
    units: Decimal = Field(gt=0)
    estimated_notional_inr: Decimal = Field(gt=0)

class GateInput(Frozen):
    signal: SignalIntent
    policy: RiskPolicy
    health: StrategyHealth
    exposure: RiskEvidence
    market_quality_status: Literal["good","degraded","missing"]
    market_quality_score: int = Field(ge=0, le=100)
    market_regime: Literal["trending","ranging","volatile","low_liquidity","unknown"]
    at_utc: datetime
    risk_state_label: str = Field(default="offline_unverified", min_length=1, max_length=64)

    @field_validator("at_utc")
    @classmethod
    def utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timezone required")
        return value.astimezone(timezone.utc)

class GateResult(Frozen):
    signal_id: str
    order_intent_id: str
    run_id: str
    strategy_id: str
    version_id: str
    qualified_symbol: str
    mode: ExecutionMode
    gate_state: GateState
    approval_mode: ApprovalMode
    decision: Decision
    codes: tuple[str, ...]
    decision_event_id: str
    approval_event_id: str
    # Not even "allow" can be consumed as live trade authority.
    execution_authorized: Literal[False] = False

def evaluate_offline(inputs: GateInput, *, state: GateState,
                     system_state: GateState, approval_mode: ApprovalMode,
                     human_approval: bool) -> tuple[Decision, tuple[str, ...]]:
    """Pure fail-closed evaluation; returns offline advisory only."""
    s, p, h, e = inputs.signal, inputs.policy, inputs.health, inputs.exposure
    if s.mode is ExecutionMode.LIVE:
        return Decision.REJECT, ("LIVE_DISABLED",)
    if state is GateState.EMERGENCY_STOP or system_state is GateState.EMERGENCY_STOP:
        return Decision.EMERGENCY_STOP, ("EMERGENCY_STOP_ACTIVE",)
    if state is GateState.PAUSED or system_state is GateState.PAUSED:
        return Decision.PAUSE, ("GATE_PAUSED",)
    if state is GateState.BLOCKED or system_state is GateState.BLOCKED:
        return Decision.REJECT, ("GATE_BLOCKED",)
    if state is not GateState.APPROVED or system_state is not GateState.APPROVED:
        return Decision.DEFER, ("VALIDATION_REQUIRED",)
    if approval_mode is ApprovalMode.BLOCKED:
        return Decision.REJECT, ("APPROVAL_MODE_BLOCKED",)
    if s.mode is ExecutionMode.PAPER and approval_mode is ApprovalMode.AUTO:
        return Decision.DEFER, ("PAPER_OPERATOR_APPROVAL_REQUIRED",)
    codes = []
    if not e.risk_profile_verified or not h.has_risk_profile: codes.append("RISK_PROFILE_MISSING")
    if not e.source_verified: codes.append("EVIDENCE_UNVERIFIED")
    if not h.valid_parameters or not h.operational: codes.append("STRATEGY_UNHEALTHY")
    if not h.valid_market_access or not e.symbol_valid: codes.append("INVALID_MARKET_ACCESS")
    if (inputs.at_utc - h.configuration_at_utc).total_seconds() > p.max_config_age_seconds:
        codes.append("CONFIG_STALE")
    if h.recent_performance < p.min_recent_performance: codes.append("PERFORMANCE_THRESHOLD")
    if not e.market_open: codes.append("MARKET_CLOSED")
    if not e.provider_healthy or inputs.market_quality_status != "good" or inputs.market_quality_score < p.min_quality_score:
        codes.append("MARKET_DATA_UNSAFE")
    if inputs.market_regime not in p.permitted_regimes: codes.append("REGIME_BLOCKED")
    if e.current_exposure_inr + s.estimated_notional_inr > p.max_exposure_inr: codes.append("EXPOSURE_CAP")
    if e.daily_loss_inr >= p.max_daily_loss_inr: codes.append("DAILY_LOSS_CAP")
    if e.session_loss_inr >= p.max_session_loss_inr: codes.append("SESSION_LOSS_CAP")
    if s.estimated_notional_inr > p.max_order_notional_inr or s.units > p.max_units: codes.append("SIZE_CAP")
    urgent = {"DAILY_LOSS_CAP","SESSION_LOSS_CAP","MARKET_DATA_UNSAFE","STRATEGY_UNHEALTHY"}
    if urgent.intersection(codes):
        return Decision.EMERGENCY_STOP, tuple(codes)
    if codes:
        return Decision.REJECT, tuple(codes)
    if approval_mode in (ApprovalMode.MANUAL, ApprovalMode.SEMI_AUTO) and not human_approval:
        return Decision.DEFER, ("OPERATOR_APPROVAL_REQUIRED",)
    return Decision.ALLOW, ("OFFLINE_VALIDATION_ONLY",)
