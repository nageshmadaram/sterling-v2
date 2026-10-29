"""Authenticated, transactional, append-only OFFLINE risk gate.

No broker, API routes, order dispatch, live execution, or risk-gate integration.
'allow' is strictly a dry-run result: execution_authorized=False.
"""
from __future__ import annotations
import hashlib
import json
import time
from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from app.domain.execution_validation import (
    ApprovalMode, Decision, ExecutionMode, GateInput, GateResult, GateState,
    evaluate_offline, transition_allowed,
)
from app.persistence.execution_validation_models import PlatformGateEventRow
from app.persistence.session import session_scope
from app.persistence.strategy_registry_models import PlatformStrategyRunRow
from app.persistence import strategy_registry_service as registry

ZERO = "0" * 64
SYSTEM = "__SYSTEM__"
ALERT_CODES = {"EMERGENCY_STOP", "MARKET_DATA_UNSAFE", "DAILY_LOSS_CAP",
               "SESSION_LOSS_CAP", "STRATEGY_UNHEALTHY", "UNAUTHORIZED_TRANSITION"}


def _json(document) -> str:
    return json.dumps(document, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _reason(reason: str) -> str:
    return registry._reason(reason)


def _stream(strategy_id: str | None) -> str:
    return f"strategy:{strategy_id}" if strategy_id else SYSTEM


def _lock(db: Session) -> None:
    # Serialize gate decisions and system kill switches in PostgreSQL.
    # SQLite is supported for disposable tests only: no concurrent approval guarantee.
    if db.bind.dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(786434021)"))


def _verify_chain(rows: list[PlatformGateEventRow]) -> None:
    prior = ZERO
    for number, row in enumerate(rows, start=1):
        if row.sequence != number or row.previous_sha256 != prior:
            raise PermissionError("offline gate audit chain broken")
        doc = {
            "stream": row.stream_id, "sequence": row.sequence, "id": row.id,
            "strategy_id": row.strategy_id, "version_id": row.version_id,
            "run_id": row.run_id, "actor": row.actor_id, "role": row.actor_role,
            "reason": row.reason, "kind": row.kind,
            "payload": json.loads(row.payload_json),
            "at_ms": row.occurred_at_ms, "previous": prior,
        }
        digest = hashlib.sha256(_json(doc).encode()).hexdigest()
        if digest != row.content_sha256:
            raise PermissionError("offline gate audit content altered")
        prior = digest


def _read(db: Session, strategy_id: str | None) -> list[PlatformGateEventRow]:
    rows = list(db.scalars(select(PlatformGateEventRow).where(
        PlatformGateEventRow.stream_id == _stream(strategy_id)
    ).order_by(PlatformGateEventRow.sequence)))
    _verify_chain(rows)
    return rows


def _state(rows: list[PlatformGateEventRow]) -> tuple[GateState, ApprovalMode]:
    state, mode = GateState.DISABLED, ApprovalMode.BLOCKED
    for row in rows:
        if row.kind == "state_transition":
            payload = json.loads(row.payload_json)
            state = GateState(payload["state"])
            mode = ApprovalMode(payload["approval_mode"])
    return state, mode


def _append(db: Session, rows: list[PlatformGateEventRow], *,
            actor, strategy_id: str | None, version_id: str | None,
            run_id: str | None, reason: str, kind: str, payload: dict) -> str:
    entry_id, number = str(uuid4()), len(rows) + 1
    before = rows[-1].content_sha256 if rows else ZERO
    at_ms = int(time.time() * 1000)
    event = {
        "stream": _stream(strategy_id), "sequence": number, "id": entry_id,
        "strategy_id": strategy_id, "version_id": version_id,
        "run_id": run_id, "actor": actor.user_id, "role": actor.role,
        "reason": reason, "kind": kind, "payload": payload,
        "at_ms": at_ms, "previous": before,
    }
    digest = hashlib.sha256(_json(event).encode()).hexdigest()
    row = PlatformGateEventRow(
        id=entry_id, stream_id=_stream(strategy_id), sequence=number,
        strategy_id=strategy_id, version_id=version_id, run_id=run_id,
        actor_id=actor.user_id, actor_role=actor.role,
        reason=reason, kind=kind, payload_json=_json(payload),
        previous_sha256=before, content_sha256=digest, occurred_at_ms=at_ms,
    )
    db.add(row)
    db.flush()
    rows.append(row)
    return entry_id


class OfflineExecutionGate:
    """Opt-in SQLAlchemy service; NEVER a trading authority or order dispatcher."""
    def __init__(self, session_factory: sessionmaker):
        self._sessions = session_factory

    def transition(self, *, access_token: str, strategy_id: str | None,
                   new_state: GateState, approval_mode: ApprovalMode,
                   reason: str, explicit_emergency_reset: bool = False) -> str:
        actor = registry._identity(access_token)
        reason = _reason(reason)
        if not isinstance(new_state, GateState) or not isinstance(approval_mode, ApprovalMode):
            raise TypeError("typed state and approval mode required")
        if strategy_id is None and actor.role != "admin":
            raise PermissionError("system state requires verified admin")
        if new_state is GateState.APPROVED and approval_mode is ApprovalMode.BLOCKED:
            raise ValueError("offline approval requires an explicit mode")
        if new_state is GateState.APPROVED and approval_mode is ApprovalMode.AUTO:
            # Restrict auto to simulation only by evaluation; paper requires operator.
            pass
        with session_scope(self._sessions) as db:
            _lock(db)
            if strategy_id:
                strategy = registry._strategy(db, actor, strategy_id)
                if strategy.is_locked or strategy.state == "retired":
                    raise PermissionError("locked/retired strategy cannot transition")
            rows = _read(db, strategy_id)
            old, _ = _state(rows)
            if not transition_allowed(old, new_state, explicit_reset=explicit_emergency_reset):
                raise PermissionError("forbidden offline authorization transition")
            if old is GateState.EMERGENCY_STOP:
                if actor.role != "admin" or not reason.startswith("incident-reviewed:"):
                    raise PermissionError("emergency reset requires admin and incident review reason")
                # Cannot jump from emergency stop straight to approved.
                if new_state is not GateState.VALIDATION_REQUIRED:
                    raise PermissionError("incident reset must reenter validation")
            return _append(db, rows, actor=actor, strategy_id=strategy_id,
                           version_id=None, run_id=None, reason=reason,
                           kind="state_transition", payload={
                               "previous_state": old.value, "state": new_state.value,
                               "approval_mode": approval_mode.value,
                               "alert_required": new_state is GateState.EMERGENCY_STOP,
                               "alert_delivery": "pending" if new_state is GateState.EMERGENCY_STOP else "not_required",
                           })

    def pending_alerts(self, *, access_token: str,
                       strategy_id: str | None = None) -> tuple[dict, ...]:
        """Authenticated, read-only outbox view; no alert is sent or acknowledged."""
        actor = registry._identity(access_token)
        if strategy_id is None and actor.role != "admin":
            raise PermissionError("system alert view requires verified admin")
        with session_scope(self._sessions) as db:
            if strategy_id:
                registry._strategy(db, actor, strategy_id)
            rows = _read(db, strategy_id)
            return tuple({
                "event_id": row.id, "strategy_id": row.strategy_id,
                "run_id": row.run_id, "at_ms": row.occurred_at_ms,
                "alert_delivery": "pending", "alert_required": True,
                "execution_authorized": False,
            } for row in rows
                if row.kind == "state_transition"
                and json.loads(row.payload_json).get("alert_required") is True
                and json.loads(row.payload_json).get("alert_delivery") == "pending")

    def approve_offline_signal(self, *, access_token: str, inputs: GateInput,
                               reason: str) -> str:
        actor, reason = registry._identity(access_token), _reason(reason)
        if inputs.signal.mode is ExecutionMode.LIVE:
            raise PermissionError("live approval prohibited")
        with session_scope(self._sessions) as db:
            _lock(db)
            strategy = registry._strategy(db, actor, inputs.signal.strategy_id)
            version = registry._version(db, inputs.signal.version_id)
            run = db.get(PlatformStrategyRunRow, inputs.signal.run_id)
            if (strategy.is_locked or strategy.state == "retired"
                    or version.strategy_id != strategy.id or run is None
                    or run.version_id != version.id or run.mode not in ("validation", "simulation")):
                raise PermissionError("cannot approve unverified offline version/run")
            rows = _read(db, inputs.signal.strategy_id)
            state, mode = _state(rows)
            system, _ = _state(_read(db, None))
            if (state is not GateState.APPROVED or system is not GateState.APPROVED
                    or mode not in (ApprovalMode.SEMI_AUTO, ApprovalMode.MANUAL)):
                raise PermissionError("operator approval requires an approved offline gate")
            digest = hashlib.sha256(inputs.model_dump_json().encode()).hexdigest()
            return _append(db, rows, actor=actor, strategy_id=inputs.signal.strategy_id,
                           version_id=inputs.signal.version_id, run_id=inputs.signal.run_id,
                           reason=reason, kind="operator_approval",
                           payload={"intent_hash": digest, "signal_id": inputs.signal.signal_id,
                                    "order_intent_id": inputs.signal.order_intent_id,
                                    "expiry_ms": int(time.time() * 1000) + 300_000,
                                    "offline_only": True})

    def dry_run(self, *, access_token: str, inputs: GateInput,
                reason: str) -> GateResult:
        actor, reason = registry._identity(access_token), _reason(reason)
        signal = inputs.signal
        with session_scope(self._sessions) as db:
            _lock(db)
            strategy = registry._strategy(db, actor, signal.strategy_id)
            # Reject before writing an apparently successful decision if any
            # identity, version hash or offline run relationship is suspect.
            version = registry._version(db, signal.version_id)
            run = db.get(PlatformStrategyRunRow, signal.run_id)
            if (version.strategy_id != strategy.id or run is None
                    or run.version_id != version.id or run.mode not in ("validation","simulation")):
                raise PermissionError("unverified strategy version/run binding")
            rows = _read(db, signal.strategy_id)
            system_rows = _read(db, None)
            state, approval_mode = _state(rows)
            system, _ = _state(system_rows)
            granted = []
            if strategy.is_locked or strategy.state == "retired":
                decision, codes = Decision.REJECT, ("STRATEGY_LOCKED_OR_RETIRED",)
            else:
                intent_hash = hashlib.sha256(inputs.model_dump_json().encode()).hexdigest()
                now = int(time.time() * 1000)
                used = {
                    json.loads(row.payload_json).get("approval_event_id")
                    for row in rows if row.kind == "approval_decision"
                }
                granted = [
                    row for row in rows if row.kind == "operator_approval"
                    and (payload := json.loads(row.payload_json))["intent_hash"] == intent_hash
                    and payload["expiry_ms"] >= now and row.id not in used
                ]
                decision, codes = evaluate_offline(
                    inputs, state=state, system_state=system,
                    approval_mode=approval_mode, human_approval=bool(granted),
                )
            ids = {}
            metadata = {"signal_id": signal.signal_id,
                        "order_intent_id": signal.order_intent_id,
                        "version_sha256": version.content_sha256,
                        "qualified_symbol": signal.qualified_symbol,
                        "mode": signal.mode.value,
                        "risk_state": inputs.risk_state_label,
                        "offline_only": True, "execution_authorized": False}
            for kind, extra in [
                ("signal_received", {}),
                ("gate_evaluated", {"gate_state": state.value, "system_state": system.value,
                                    "quality": inputs.market_quality_status, "score": inputs.market_quality_score}),
                ("risk_decision", {"decision": decision.value, "codes": codes}),
                ("approval_decision", {"decision": decision.value, "approval_mode": approval_mode.value,
                                       "approval_event_id": granted[-1].id if decision is Decision.ALLOW and
                                       approval_mode in (ApprovalMode.MANUAL, ApprovalMode.SEMI_AUTO)
                                       and not (strategy.is_locked or strategy.state == "retired") else None})
            ]:
                ids[kind] = _append(db, rows, actor=actor, strategy_id=strategy.id,
                                    version_id=version.id, run_id=run.id,
                                    reason=reason, kind=kind,
                                    payload={**metadata, **extra})
            if decision is Decision.EMERGENCY_STOP and state is not GateState.EMERGENCY_STOP:
                _append(db, rows, actor=actor, strategy_id=strategy.id,
                        version_id=version.id, run_id=run.id, reason="risk-threshold: " + ",".join(codes),
                        kind="state_transition", payload={
                            "previous_state": state.value, "state": GateState.EMERGENCY_STOP.value,
                            "approval_mode": ApprovalMode.BLOCKED.value,
                            "alert_required": True, "alert_delivery": "pending",
                            "codes": codes,
                        })
            return GateResult(signal_id=signal.signal_id,
                              order_intent_id=signal.order_intent_id,
                              run_id=run.id, strategy_id=strategy.id,
                              version_id=version.id, qualified_symbol=signal.qualified_symbol,
                              mode=signal.mode, gate_state=state,
                              approval_mode=approval_mode, decision=decision,
                              codes=codes, decision_event_id=ids["risk_decision"],
                              approval_event_id=ids["approval_decision"])
