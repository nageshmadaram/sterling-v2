"""Phase 5 offline scheduling, backlog, and run state contracts.

These are observations for validation-only runs, NOT broker orders, workers,
live-ready state, or permission for existing Sterling order routers.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

class FrozenRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)


def utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("schedule time must have an explicit timezone")
    return value.astimezone(timezone.utc)


class ScheduleKind(str, Enum):
    MANUAL = "manual"
    SESSION = "session"
    RECURRING = "recurring"
    DELAYED = "delayed"


class BacklogState(str, Enum):
    PENDING = "pending"
    QUEUED = "queued"
    EVALUATED = "evaluated"
    APPROVED = "approved"
    REJECTED = "rejected"
    PAUSED = "paused"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"
    EMERGENCY_STOPPED = "emergency_stopped"


class RunState(str, Enum):
    IDLE = "idle"
    QUEUED = "queued"
    EVALUATING = "evaluating"
    RISK_GATE = "risk_gate"
    APPROVED = "approved"
    REJECTED = "rejected"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    EMERGENCY_STOPPED = "emergency_stopped"


RUN_TRANSITIONS: dict[RunState, frozenset[RunState]] = {
    RunState.IDLE: frozenset({RunState.QUEUED, RunState.PAUSED, RunState.EMERGENCY_STOPPED}),
    RunState.QUEUED: frozenset({RunState.EVALUATING, RunState.PAUSED, RunState.EMERGENCY_STOPPED}),
    RunState.EVALUATING: frozenset({RunState.RISK_GATE, RunState.FAILED, RunState.EMERGENCY_STOPPED}),
    RunState.RISK_GATE: frozenset({RunState.APPROVED, RunState.REJECTED, RunState.PAUSED, RunState.EMERGENCY_STOPPED}),
    RunState.APPROVED: frozenset({RunState.COMPLETED, RunState.FAILED, RunState.EMERGENCY_STOPPED}),
    RunState.REJECTED: frozenset({RunState.QUEUED}),
    RunState.PAUSED: frozenset({RunState.QUEUED, RunState.EMERGENCY_STOPPED}),
    RunState.COMPLETED: frozenset(),
    RunState.FAILED: frozenset(),
    RunState.EMERGENCY_STOPPED: frozenset(),
}

BACKLOG_TRANSITIONS: dict[BacklogState, frozenset[BacklogState]] = {
    BacklogState.PENDING: frozenset({BacklogState.QUEUED, BacklogState.SKIPPED, BacklogState.CANCELLED, BacklogState.PAUSED, BacklogState.EMERGENCY_STOPPED}),
    BacklogState.QUEUED: frozenset({BacklogState.EVALUATED, BacklogState.PAUSED, BacklogState.CANCELLED, BacklogState.EMERGENCY_STOPPED}),
    BacklogState.EVALUATED: frozenset({BacklogState.APPROVED, BacklogState.REJECTED, BacklogState.PAUSED, BacklogState.EMERGENCY_STOPPED}),
    BacklogState.APPROVED: frozenset({BacklogState.CANCELLED, BacklogState.EMERGENCY_STOPPED}),
    BacklogState.REJECTED: frozenset({BacklogState.QUEUED, BacklogState.CANCELLED}),
    BacklogState.PAUSED: frozenset({BacklogState.QUEUED, BacklogState.CANCELLED, BacklogState.EMERGENCY_STOPPED}),
    BacklogState.SKIPPED: frozenset({BacklogState.QUEUED, BacklogState.CANCELLED}),
    BacklogState.CANCELLED: frozenset(),
    BacklogState.EMERGENCY_STOPPED: frozenset(),
}


def require_pair(old_backlog: BacklogState, new_backlog: BacklogState,
                 old_run: RunState, new_run: RunState) -> None:
    if new_backlog != old_backlog and new_backlog not in BACKLOG_TRANSITIONS[old_backlog]:
        raise PermissionError("invalid offline backlog transition")
    if new_backlog == old_backlog and not (
        (old_backlog is BacklogState.EVALUATED and old_run is RunState.EVALUATING
         and new_run is RunState.RISK_GATE)
        or (old_backlog is BacklogState.APPROVED and old_run is RunState.APPROVED
            and new_run in (RunState.COMPLETED, RunState.FAILED))
    ):
        raise PermissionError("no-op backlog transitions are not accepted")
    if new_run not in RUN_TRANSITIONS[old_run]:
        raise PermissionError("invalid offline orchestration transition")
    if new_backlog is BacklogState.APPROVED and new_run is not RunState.APPROVED:
        raise PermissionError("approval cannot skip risk state")
    if new_run is RunState.APPROVED and new_backlog is not BacklogState.APPROVED:
        raise PermissionError("approval requires backlog approval")
    if new_backlog is BacklogState.EVALUATED and new_run not in (RunState.EVALUATING, RunState.RISK_GATE):
        raise PermissionError("evaluated backlog requires gate progression")


class ScheduleSpec(FrozenRecord):
    schedule_id: str = Field(min_length=8, max_length=64)
    kind: ScheduleKind
    strategy_id: str = Field(min_length=1, max_length=64)
    version_id: str = Field(min_length=1, max_length=64)
    run_id: str = Field(min_length=1, max_length=64)
    batch_id: str = Field(min_length=1, max_length=128)
    qualified_symbol: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,11}:[^: ]{1,128}$")
    start_at_utc: datetime
    end_at_utc: datetime | None = None
    cadence_seconds: int | None = Field(default=None, ge=60, le=86400)
    max_occurrences: int = Field(default=1, ge=1, le=500)
    priority: int = Field(default=5, ge=0, le=9)
    max_queue_age_seconds: int = Field(default=300, ge=1, le=86400)
    dry_run: Literal[True] = True
    execution_authorized: Literal[False] = False

    @field_validator("start_at_utc", "end_at_utc")
    @classmethod
    def normalize_time(cls, val: datetime | None) -> datetime | None:
        return utc(val) if val else None

    @model_validator(mode="after")
    def valid_schedule(self):
        if self.end_at_utc is not None and self.end_at_utc <= self.start_at_utc:
            raise ValueError("schedule window ends before it starts")
        if self.kind is ScheduleKind.RECURRING:
            if self.cadence_seconds is None:
                raise ValueError("recurring schedule requires cadence")
        elif self.cadence_seconds is not None or self.max_occurrences != 1:
            raise ValueError("one-shot schedule cannot recur")
        if self.kind is ScheduleKind.SESSION and self.end_at_utc is None:
            raise ValueError("session schedule requires an explicit close")
        if self.kind is ScheduleKind.DELAYED and self.end_at_utc is not None:
            raise ValueError("delayed schedule does not use session window")
        return self

    def occurrence_at(self, index: int) -> datetime:
        if not 0 <= index < self.max_occurrences:
            raise IndexError("unbounded schedule occurrence")
        if self.kind is ScheduleKind.RECURRING:
            return self.start_at_utc + timedelta(seconds=self.cadence_seconds * index)
        return self.start_at_utc

    def lineage_digest(self, version_sha256: str, risk_sha256: str) -> str:
        payload = self.model_dump(mode="json")
        payload["version_sha256"] = version_sha256
        payload["risk_policy_sha256"] = risk_sha256
        return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class QueueIdentity(FrozenRecord):
    item_id: str = Field(min_length=8, max_length=64)
    schedule_id: str
    occurrence: int = Field(ge=0)
    due_at_utc: datetime
    strategy_id: str
    version_id: str
    run_id: str
    batch_id: str
    qualified_symbol: str
    version_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    risk_policy_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    lineage_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    priority: int = Field(ge=0, le=9)
    dry_run: Literal[True] = True
    execution_authorized: Literal[False] = False

    @field_validator("due_at_utc")
    @classmethod
    def aware(cls, value: datetime) -> datetime:
        return utc(value)


class QueueView(FrozenRecord):
    identity: QueueIdentity
    backlog: BacklogState
    run_state: RunState
    last_decision: str | None
    risk_event_id: str | None
    approval_event_id: str | None
    queue_event_id: str
    execution_authorized: Literal[False] = False


class SchedulingAlert(FrozenRecord):
    event_id: str
    kind: Literal["queue_growth","stale_item","validation_failure","rejected","paused","emergency_stop"]
    reason_code: str
    strategy_id: str
    item_id: str | None
    delivery: Literal["pending"] = "pending"
    execution_authorized: Literal[False] = False
