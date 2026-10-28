"""Pure, broker-independent strategy lifecycle contracts (foundation slice)."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrategyState(str, Enum):
    DRAFT = "draft"
    VALIDATED = "validated"
    ARMED = "armed"
    ACTIVE = "active"
    PAUSED = "paused"
    FAILED = "failed"
    DISABLED = "disabled"
    RETIRED = "retired"


class RunState(str, Enum):
    IDLE = "idle"
    QUEUED = "queued"
    ACTIVE = "active"
    PAUSED = "paused"
    FAILED = "failed"
    STOPPED = "stopped"


STRATEGY_TRANSITIONS: dict[StrategyState, frozenset[StrategyState]] = {
    StrategyState.DRAFT: frozenset({StrategyState.VALIDATED, StrategyState.DISABLED}),
    StrategyState.VALIDATED: frozenset({StrategyState.ARMED, StrategyState.DISABLED}),
    StrategyState.ARMED: frozenset({StrategyState.ACTIVE, StrategyState.DISABLED}),
    StrategyState.ACTIVE: frozenset({StrategyState.PAUSED, StrategyState.FAILED, StrategyState.DISABLED}),
    StrategyState.PAUSED: frozenset({StrategyState.ACTIVE, StrategyState.DISABLED}),
    StrategyState.FAILED: frozenset({StrategyState.DISABLED}),
    StrategyState.DISABLED: frozenset({StrategyState.DRAFT, StrategyState.RETIRED}),
    StrategyState.RETIRED: frozenset(),
}

RUN_TRANSITIONS: dict[RunState, frozenset[RunState]] = {
    RunState.IDLE: frozenset({RunState.QUEUED}),
    RunState.QUEUED: frozenset({RunState.ACTIVE, RunState.FAILED, RunState.STOPPED}),
    RunState.ACTIVE: frozenset({RunState.PAUSED, RunState.FAILED, RunState.STOPPED}),
    RunState.PAUSED: frozenset({RunState.ACTIVE, RunState.FAILED, RunState.STOPPED}),
    RunState.FAILED: frozenset(),
    RunState.STOPPED: frozenset(),
}


def require_transition(current: StrategyState | RunState, next_state: StrategyState | RunState) -> None:
    """Reject ambiguous or invalid state transitions. Never grants live authority."""
    transitions = STRATEGY_TRANSITIONS if isinstance(current, StrategyState) else RUN_TRANSITIONS
    if type(current) is not type(next_state) or next_state not in transitions[current]:
        raise ValueError(f"invalid lifecycle transition: {current.value} -> {next_state.value}")


def _no_secrets(node: Any) -> None:
    """Configurations are metadata, not credential stores."""
    if isinstance(node, dict):
        for key, value in node.items():
            normalized = str(key).lower().replace("-", "_")
            if any(part in normalized for part in ("password", "secret", "api_key", "access_token", "refresh_token", "private_key")):
                raise ValueError("strategy configuration must not contain credential fields")
            _no_secrets(value)
    elif isinstance(node, list):
        for item in node:
            _no_secrets(item)


class StrategyVersionSnapshot(BaseModel):
    """Frozen, canonical configuration payload with a stable content digest."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    strategy_id: str = Field(min_length=1, max_length=64)
    version: str = Field(min_length=1, max_length=80)
    parameters_json: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @classmethod
    def from_parameters(cls, *, strategy_id: str, version: str, parameters: dict[str, Any]):
        return cls(strategy_id=strategy_id, version=version, parameters_json=json.dumps(parameters, sort_keys=True, separators=(",", ":"), allow_nan=False))

    @field_validator("created_at")
    @classmethod
    def aware_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("created_at must be timezone-aware")
        return value.astimezone(timezone.utc)

    @field_validator("parameters_json")
    @classmethod
    def safe_parameters(cls, value: str) -> str:
        try:
            parsed = json.loads(value, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("invalid non-finite number")))
            if not isinstance(parsed, dict):
                raise ValueError("parameters must be a JSON object")
            _no_secrets(parsed)
            return json.dumps(parsed, sort_keys=True, separators=(",", ":"), allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("parameters must be finite JSON object without credentials") from exc

    def canonical_json(self) -> str:
        return self.parameters_json

    def sha256(self) -> str:
        return hashlib.sha256(self.parameters_json.encode("utf-8")).hexdigest()


class StrategyRunIntent(BaseModel):
    """Stage-1 run admission permits offline validation/simulation only."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    version_id: str = Field(min_length=1, max_length=64)
    requested_by: str = Field(min_length=1, max_length=128)
    mode: Literal["validation", "simulation"] = "validation"
