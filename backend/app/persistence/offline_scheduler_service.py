"""Deterministic, operator-invoked, OFFLINE orchestration. Never starts a worker.

All durable queue and run statuses are projections of immutable audit events.
Risk decisions are bound to Phase 4 recorded gate events, not a caller's 'allow'
claim. No broker import, order API, timer or live service integration.
"""
from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timedelta, timezone
from uuid import NAMESPACE_URL, uuid4, uuid5

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.domain.execution_validation import Decision, GateInput, GateState, RiskPolicy
from app.domain.offline_scheduler import (
    BacklogState, QueueIdentity, QueueView, RunState, ScheduleKind, ScheduleSpec,
    SchedulingAlert, require_pair, utc,
)
from app.persistence.offline_scheduler_models import PlatformOrchestrationEventRow
from app.persistence.execution_validation_service import (
    OfflineExecutionGate, _lock, _read as _read_gate, _state as _gate_state,
)
from app.persistence.session import session_scope
from app.persistence.strategy_registry_models import PlatformStrategyRunRow
from app.persistence import strategy_registry_service as registry

ZERO = "0" * 64
ACTIVE = (BacklogState.PENDING, BacklogState.QUEUED, BacklogState.EVALUATED, BacklogState.PAUSED)


def _canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(obj) -> str:
    return hashlib.sha256(_canonical(obj).encode()).hexdigest()


def _read(db: Session, strategy_id: str) -> list[PlatformOrchestrationEventRow]:
    stream = "strategy:" + strategy_id
    rows = list(db.scalars(select(PlatformOrchestrationEventRow)
                           .where(PlatformOrchestrationEventRow.stream_id == stream)
                           .order_by(PlatformOrchestrationEventRow.sequence)))
    previous = ZERO
    for number, row in enumerate(rows, start=1):
        if number != row.sequence or row.previous_sha256 != previous:
            raise PermissionError("offline backlog audit sequence invalid")
        data = {
            "id": row.id, "stream": row.stream_id, "sequence": row.sequence,
            "strategy_id": row.strategy_id, "version_id": row.version_id,
            "run_id": row.run_id, "schedule_id": row.schedule_id,
            "item_id": row.item_id, "actor": row.actor_id, "role": row.actor_role,
            "kind": row.kind, "reason": row.reason,
            "payload": json.loads(row.payload_json), "at_ms": row.occurred_at_ms,
            "previous": previous,
        }
        previous = _digest(data)
        if previous != row.content_sha256:
            raise PermissionError("offline backlog audit fingerprint invalid")
    return rows


def _append(db: Session, rows: list[PlatformOrchestrationEventRow], *,
            actor, schedule: ScheduleSpec, item_id: str | None,
            kind: str, reason: str, payload: dict) -> str:
    event_id = str(uuid4())
    number = len(rows) + 1
    stamp = int(time.time() * 1000)
    content = {
        "id": event_id, "stream": "strategy:" + schedule.strategy_id,
        "sequence": number, "strategy_id": schedule.strategy_id,
        "version_id": schedule.version_id, "run_id": schedule.run_id,
        "schedule_id": schedule.schedule_id, "item_id": item_id,
        "actor": actor.user_id, "role": actor.role, "kind": kind,
        "reason": reason, "payload": payload, "at_ms": stamp,
        "previous": rows[-1].content_sha256 if rows else ZERO,
    }
    digest = _digest(content)
    row = PlatformOrchestrationEventRow(
        id=event_id, stream_id=content["stream"], sequence=number,
        strategy_id=schedule.strategy_id, version_id=schedule.version_id,
        run_id=schedule.run_id, schedule_id=schedule.schedule_id, item_id=item_id,
        actor_id=actor.user_id, actor_role=actor.role, kind=kind, reason=reason,
        payload_json=_canonical(payload), previous_sha256=content["previous"],
        content_sha256=digest, occurred_at_ms=stamp,
    )
    db.add(row)
    db.flush()
    rows.append(row)
    # Phase 6: exact same SQL transaction as the queue-state mutation.
    # If the journal insert fails, session_scope rolls back both records.
    from app.persistence.offline_decision_journal_service import append_for_orchestration
    append_for_orchestration(db, row, rows)
    return event_id


def _projection(rows):
    schedules: dict[str, tuple[ScheduleSpec, str, str, dict]] = {}
    items: dict[str, QueueView] = {}
    schedule_paused: dict[str, bool] = {}
    for row in rows:
        data = json.loads(row.payload_json)
        if row.kind == "schedule_created":
            if row.schedule_id in schedules:
                raise PermissionError("duplicate schedule audit identity")
            spec = ScheduleSpec.model_validate(data["spec"])
            if spec.schedule_id != row.schedule_id or spec.strategy_id != row.strategy_id:
                raise PermissionError("schedule audit identity mismatch")
            schedules[row.schedule_id] = (spec, data["version_sha256"],
                                         data["risk_policy_sha256"], data["risk_policy"])
        elif row.kind == "schedule_paused":
            if row.schedule_id not in schedules:
                raise PermissionError("schedule control without creation")
            schedule_paused[row.schedule_id] = data["paused"]
        elif row.kind == "schedule_cancelled":
            if row.schedule_id not in schedules:
                raise PermissionError("schedule cancellation without creation")
            schedule_paused[row.schedule_id] = True
        elif row.kind in ("queue_transition", "run_transition"):
            if row.schedule_id not in schedules or row.item_id is None:
                raise PermissionError("queue event without registered schedule")
            backlog = BacklogState(data["backlog"])
            run_state = RunState(data["run_state"])
            if row.item_id not in items:
                if backlog is not BacklogState.PENDING or run_state is not RunState.IDLE:
                    raise PermissionError("queue creation must begin pending/idle")
                identity = QueueIdentity.model_validate(data["identity"])
                spec, version_hash, risk_hash, _ = schedules[row.schedule_id]
                expected = _item_identity(spec, identity.occurrence, version_hash, risk_hash)
                if identity != expected or identity.item_id != row.item_id:
                    raise PermissionError("queued lineage differs from schedule")
                items[row.item_id] = QueueView(
                    identity=identity, backlog=backlog, run_state=run_state,
                    last_decision=None, risk_event_id=None,
                    approval_event_id=None, queue_event_id=row.id,
                )
            else:
                previous = items[row.item_id]
                if row.schedule_id != previous.identity.schedule_id:
                    raise PermissionError("cross-schedule queue replay")
                require_pair(previous.backlog, backlog, previous.run_state, run_state)
                # Approval must be preceded by a recorded risk decision binding.
                if backlog is BacklogState.APPROVED and previous.risk_event_id is None:
                    raise PermissionError("queue approved without risk evidence")
                items[row.item_id] = previous.model_copy(update={
                    "backlog": backlog, "run_state": run_state,
                    "queue_event_id": row.id,
                })
        elif row.kind == "gate_binding":
            previous = items.get(row.item_id)
            if previous is None:
                raise PermissionError("risk binding without queued item")
            items[row.item_id] = previous.model_copy(update={
                "last_decision": data["decision"],
                "risk_event_id": data["risk_event_id"],
                "approval_event_id": data["approval_event_id"],
            })
    return schedules, items, schedule_paused


def _item_identity(spec, index, version_sha256, risk_sha256):
    stamp = spec.occurrence_at(index)
    key = str(uuid5(NAMESPACE_URL, f"sterling-v2/offline/{spec.schedule_id}/{index}"))
    return QueueIdentity(
        item_id=key, schedule_id=spec.schedule_id, occurrence=index,
        due_at_utc=stamp, strategy_id=spec.strategy_id,
        version_id=spec.version_id, run_id=spec.run_id,
        batch_id=spec.batch_id, qualified_symbol=spec.qualified_symbol,
        version_sha256=version_sha256, risk_policy_sha256=risk_sha256,
        lineage_sha256=spec.lineage_digest(version_sha256, risk_sha256),
        priority=spec.priority,
    )


def _registered(db, actor, strategy_id, version_id, run_id):
    strategy = registry._strategy(db, actor, strategy_id)
    version = registry._version(db, version_id)
    run = db.get(PlatformStrategyRunRow, run_id)
    if (version.strategy_id != strategy.id or run is None or
            run.version_id != version.id or run.mode not in ("simulation", "validation")):
        raise PermissionError("offline run identity/version mismatch")
    return strategy, version, run


def _gate_status(db, strategy_id):
    system, _ = _gate_state(_read_gate(db, None))
    strategy, _ = _gate_state(_read_gate(db, strategy_id))
    return system, strategy


def _stop_state(states):
    return GateState.EMERGENCY_STOP in states


def _blocked_strategy(row):
    return row.state in ("disabled", "retired", "failed") or bool(row.is_locked)


def _gate_result(db, item: QueueView, inputs: GateInput, result):
    if result.execution_authorized or result.mode.value == "live":
        raise PermissionError("broker-authorizing result not allowed in offline scheduler")
    if (result.order_intent_id != item.identity.item_id or
        result.signal_id != item.identity.batch_id or
        result.strategy_id != item.identity.strategy_id or
        result.version_id != item.identity.version_id or
        result.run_id != item.identity.run_id or
        result.qualified_symbol != item.identity.qualified_symbol):
        raise PermissionError("risk result has mismatched queue identity")
    events = _read_gate(db, item.identity.strategy_id)
    risk = next((row for row in events if row.id == result.decision_event_id), None)
    approval = next((row for row in events if row.id == result.approval_event_id), None)
    if risk is None or approval is None or risk.kind != "risk_decision" or approval.kind != "approval_decision":
        raise PermissionError("risk/approval evidence not recorded")
    risk_payload = json.loads(risk.payload_json)
    approval_payload = json.loads(approval.payload_json)
    if (risk_payload.get("decision") != result.decision.value
        or risk_payload.get("signal_id") != item.identity.batch_id
        or risk_payload.get("order_intent_id") != item.identity.item_id
        or risk_payload.get("version_sha256") != item.identity.version_sha256
        or approval_payload.get("decision") != result.decision.value
        or approval_payload.get("execution_authorized") is not False
        or risk_payload.get("execution_authorized") is not False):
        raise PermissionError("risk/approval audit payload mismatch")
    if risk.version_id != item.identity.version_id or risk.run_id != item.identity.run_id:
        raise PermissionError("risk audit version/run mismatch")
    return risk.content_sha256, approval.content_sha256


class OfflineScheduler:
    """No background timer, no HTTP route, no broker execution dependency."""

    def __init__(self, session_factory: sessionmaker, gate: OfflineExecutionGate):
        self._sessions = session_factory
        self._gate = gate

    def schedule(self, *, access_token: str, spec: ScheduleSpec,
                 risk_policy: RiskPolicy, reason: str) -> str:
        actor = registry._identity(access_token)
        reason = registry._reason(reason)
        with session_scope(self._sessions) as db:
            _lock(db)  # shared with Phase 4, including global emergency stop
            strategy, version, run = _registered(
                db, actor, spec.strategy_id, spec.version_id, spec.run_id
            )
            if _blocked_strategy(strategy):
                raise PermissionError("disabled/retired/locked strategy cannot schedule")
            if GateState.EMERGENCY_STOP in _gate_status(db, spec.strategy_id):
                raise PermissionError("offline emergency stop blocks scheduling")
            rows = _read(db, spec.strategy_id)
            schedules, _, _ = _projection(rows)
            if spec.schedule_id in schedules:
                raise PermissionError("schedule id is already registered")
            policy = risk_policy.model_dump(mode="json")
            return _append(db, rows, actor=actor, schedule=spec, item_id=None,
                           kind="schedule_created", reason=reason,
                           payload={"spec": spec.model_dump(mode="json"),
                                    "version_sha256": version.content_sha256,
                                    "risk_policy_sha256": _digest(policy),
                                    "risk_policy": policy, "dry_run": True,
                                    "execution_authorized": False})

    def set_pause(self, *, access_token: str, strategy_id: str,
                  schedule_id: str, paused: bool, reason: str) -> str:
        actor, reason = registry._identity(access_token), registry._reason(reason)
        with session_scope(self._sessions) as db:
            _lock(db)
            registry._strategy(db, actor, strategy_id)
            rows = _read(db, strategy_id)
            schedules, _, flags = _projection(rows)
            if any(r.kind == "schedule_cancelled" and r.schedule_id == schedule_id for r in rows):
                raise PermissionError("cancelled offline schedule is permanently closed")
            if schedule_id not in schedules or flags.get(schedule_id, False) == paused:
                raise PermissionError("schedule not found or already in requested state")
            if not paused and _stop_state(_gate_status(db, strategy_id)):
                raise PermissionError("emergency stopped schedule cannot resume")
            return _append(db, rows, actor=actor, schedule=schedules[schedule_id][0],
                           item_id=None, kind="schedule_paused", reason=reason,
                           payload={"paused": paused, "offline_only": True})

    def cancel_schedule(self, *, access_token: str, strategy_id: str,
                        schedule_id: str, reason: str) -> str:
        """Cancel future dry-run occurrences; cannot reopen the schedule."""
        actor, reason = registry._identity(access_token), registry._reason(reason)
        with session_scope(self._sessions) as db:
            _lock(db)
            registry._strategy(db, actor, strategy_id)
            rows = _read(db, strategy_id)
            schedules, _, _ = _projection(rows)
            if schedule_id not in schedules or any(
                r.kind == "schedule_cancelled" and r.schedule_id == schedule_id
                for r in rows
            ):
                raise PermissionError("schedule missing or already cancelled")
            return _append(db, rows, actor=actor, schedule=schedules[schedule_id][0],
                           item_id=None, kind="schedule_cancelled", reason=reason,
                           payload={"code": "OPERATOR_CANCELLED",
                                    "dry_run": True, "execution_authorized": False})

    def materialize_due(self, *, access_token: str, strategy_id: str,
                        clock: datetime, reason: str, limit: int = 50,
                        backlog_warn_at: int = 10) -> tuple[QueueView, ...]:
        actor, reason = registry._identity(access_token), registry._reason(reason)
        now = utc(clock)
        if not 1 <= limit <= 100 or not 1 <= backlog_warn_at <= 10000:
            raise ValueError("bounded queue poll required")
        with session_scope(self._sessions) as db:
            _lock(db)
            strategy = registry._strategy(db, actor, strategy_id)
            rows = _read(db, strategy_id)
            schedules, items, paused = _projection(rows)
            states = _gate_status(db, strategy_id)
            candidates = []
            for spec, vh, rh, _ in schedules.values():
                if paused.get(spec.schedule_id, False):
                    continue
                for index in range(spec.max_occurrences):
                    due = spec.occurrence_at(index)
                    if spec.end_at_utc and due >= spec.end_at_utc:
                        break
                    identity = _item_identity(spec, index, vh, rh)
                    if identity.item_id not in items and due <= now:
                        candidates.append((due, spec.priority, spec.schedule_id,
                                           index, spec, identity))
            candidates.sort(key=lambda c: (c[0], c[1], c[2], c[3]))
            emitted = []
            for due, _, _, _, spec, identity in candidates[:limit]:
                _append(db, rows, actor=actor, schedule=spec, item_id=identity.item_id,
                        kind="queue_transition", reason=reason,
                        payload={"backlog": "pending", "run_state": "idle",
                                 "identity": identity.model_dump(mode="json"),
                                 "dry_run": True, "execution_authorized": False})
                if _stop_state(states):
                    backlog, run_state, code = ("emergency_stopped", "emergency_stopped", "SYSTEM_OR_STRATEGY_STOP")
                elif _blocked_strategy(strategy):
                    backlog, run_state, code = ("skipped", "paused", "STRATEGY_DISABLED")
                elif GateState.PAUSED in states:
                    backlog, run_state, code = ("paused", "paused", "GATE_PAUSED")
                elif (now - due).total_seconds() > spec.max_queue_age_seconds or (
                        spec.kind is ScheduleKind.SESSION and now >= spec.end_at_utc):
                    backlog, run_state, code = ("skipped", "paused", "STALE_QUEUE_OCCURRENCE")
                else:
                    backlog, run_state, code = ("queued", "queued", "OFFLINE_QUEUE_ONLY")
                event_id = _append(db, rows, actor=actor, schedule=spec,
                                   item_id=identity.item_id, kind="queue_transition",
                                   reason=reason, payload={
                                       "backlog": backlog, "run_state": run_state,
                                       "code": code, "dry_run": True,
                                       "execution_authorized": False,
                                   })
                if backlog in ("skipped", "emergency_stopped"):
                    self._alert(db, rows, actor=actor, spec=spec,
                                item_id=identity.item_id,
                                kind="stale_item" if backlog == "skipped" else "emergency_stop",
                                code=code, reason=reason)
                emitted.append(QueueView(identity=identity,
                                         backlog=BacklogState(backlog),
                                         run_state=RunState(run_state),
                                         last_decision=None, risk_event_id=None,
                                         approval_event_id=None, queue_event_id=event_id))
            if emitted:
                latest = _projection(rows)[1]
                active = sum(x.backlog in ACTIVE for x in latest.values())
                if active >= backlog_warn_at:
                    self._alert(db, rows, actor=actor, spec=emitted[0].identity,
                                item_id=None, kind="queue_growth",
                                code="OFFLINE_BACKLOG_GROWTH", reason=reason)
            return tuple(emitted)

    @staticmethod
    def _alert(db, rows, *, actor, spec, item_id, kind, code, reason):
        # Source artifact is a durable pending alert; NO external send.
        schedule = spec if isinstance(spec, ScheduleSpec) else None
        if schedule is None:
            for row in rows:
                if row.kind == "schedule_created" and row.schedule_id == spec.schedule_id:
                    schedule = ScheduleSpec.model_validate(json.loads(row.payload_json)["spec"])
                    break
        if schedule is None:
            raise PermissionError("unknown schedule for alert")
        return _append(db, rows, actor=actor, schedule=schedule, item_id=item_id,
                       kind="operator_alert", reason=reason, payload={
                           "kind": kind, "code": code, "alert_delivery": "pending",
                           "dry_run": True, "execution_authorized": False,
                       })

    def queue(self, *, access_token: str, strategy_id: str) -> tuple[QueueView, ...]:
        actor = registry._identity(access_token)
        with session_scope(self._sessions) as db:
            registry._strategy(db, actor, strategy_id)
            items = _projection(_read(db, strategy_id))[1]
            return tuple(sorted(items.values(),
                                key=lambda x: (x.identity.due_at_utc, x.identity.priority,
                                               x.identity.schedule_id, x.identity.occurrence)))

    def alerts(self, *, access_token: str, strategy_id: str) -> tuple[SchedulingAlert, ...]:
        actor = registry._identity(access_token)
        with session_scope(self._sessions) as db:
            registry._strategy(db, actor, strategy_id)
            rows = _read(db, strategy_id)
            result = []
            for row in rows:
                if row.kind == "operator_alert":
                    payload = json.loads(row.payload_json)
                    result.append(SchedulingAlert(
                        event_id=row.id, kind=payload["kind"],
                        reason_code=payload["code"], strategy_id=strategy_id,
                        item_id=row.item_id,
                    ))
            return tuple(result)

    def _bind(self, db, rows, *, actor, spec, item, inputs, result, reason):
        risk_sha, approval_sha = _gate_result(db, item, inputs, result)
        # Once-only risk evidence binding, including across requeue cycles.
        if any(r.kind == "gate_binding" and
               json.loads(r.payload_json).get("risk_event_id") == result.decision_event_id
               for r in rows):
            raise PermissionError("risk record already bound to an offline queue event")
        return _append(db, rows, actor=actor, schedule=spec, item_id=item.identity.item_id,
                       kind="gate_binding", reason=reason, payload={
                           "decision": result.decision.value,
                           "risk_event_id": result.decision_event_id,
                           "approval_event_id": result.approval_event_id,
                           "risk_event_sha256": risk_sha,
                           "approval_event_sha256": approval_sha,
                           "inputs_sha256": _digest(inputs.model_dump(mode="json")),
                           "dry_run": True, "execution_authorized": False,
                       })

    @staticmethod
    def _validate_input(item: QueueView, inputs: GateInput):
        signal = inputs.signal
        identity = item.identity
        if (signal.order_intent_id != identity.item_id
            or signal.signal_id != identity.batch_id
            or signal.strategy_id != identity.strategy_id
            or signal.version_id != identity.version_id
            or signal.run_id != identity.run_id
            or signal.qualified_symbol != identity.qualified_symbol
            or _digest(inputs.policy.model_dump(mode="json")) != identity.risk_policy_sha256):
            raise PermissionError("risk input does not match immutable schedule/run lineage")
        if signal.mode.value != "simulation":
            raise PermissionError("offline scheduler accepts simulation previews only")
        age = (datetime.now(timezone.utc) - inputs.at_utc).total_seconds()
        if age > 300 or age < -2:
            raise PermissionError("stale or future-dated risk-evaluation evidence")

    def evaluate(self, *, access_token: str, strategy_id: str,
                 item_id: str, inputs: GateInput, reason: str) -> QueueView:
        actor, reason = registry._identity(access_token), registry._reason(reason)
        # First verify the immutable lineage and item state. Then ask Phase 4
        # to persist a REAL dry-run decision; never accept client-provided allow.
        with session_scope(self._sessions) as db:
            _lock(db)
            registry._strategy(db, actor, strategy_id)
            rows = _read(db, strategy_id)
            specs, items, _ = _projection(rows)
            item = items.get(item_id)
            if item is None or item.backlog is not BacklogState.QUEUED:
                raise PermissionError("offline item not queued")
            self._validate_input(item, inputs)
            spec = specs[item.identity.schedule_id][0]
            if (datetime.now(timezone.utc) - item.identity.due_at_utc).total_seconds() > spec.max_queue_age_seconds:
                event = _append(db, rows, actor=actor, schedule=spec, item_id=item_id,
                                kind="queue_transition", reason=reason,
                                payload={"backlog": "skipped", "run_state": "paused",
                                         "code": "STALE_BEFORE_EVALUATION",
                                         "dry_run": True, "execution_authorized": False})
                self._alert(db, rows, actor=actor, spec=spec,
                            item_id=item_id, kind="stale_item",
                            code="STALE_BEFORE_EVALUATION", reason=reason)
                return item.model_copy(update={"backlog": BacklogState.SKIPPED,
                                               "run_state": RunState.PAUSED,
                                               "queue_event_id": event})
        result = self._gate.dry_run(access_token=access_token, inputs=inputs,
                                    reason="phase5 evaluation: " + reason)
        with session_scope(self._sessions) as db:
            _lock(db)  # serializes with global kill and Phase 4 evaluation
            strategy, version, _ = _registered(db, actor, strategy_id,
                                               inputs.signal.version_id,
                                               inputs.signal.run_id)
            rows = _read(db, strategy_id)
            specs, items, _ = _projection(rows)
            item = items.get(item_id)
            if item is None or item.backlog is not BacklogState.QUEUED:
                raise PermissionError("offline queue modified during gate evaluation")
            self._validate_input(item, inputs)
            spec = specs[item.identity.schedule_id][0]
            for back, run_state in (("evaluated", "evaluating"), ("evaluated", "risk_gate")):
                _append(db, rows, actor=actor, schedule=spec, item_id=item_id,
                        kind="queue_transition" if run_state == "evaluating" else "run_transition",
                        reason=reason, payload={"backlog": back, "run_state": run_state,
                                               "dry_run": True, "execution_authorized": False})
            self._bind(db, rows, actor=actor, spec=spec, item=item, inputs=inputs,
                       result=result, reason=reason)
            state = _gate_status(db, strategy_id)
            if _stop_state(state) or result.decision is Decision.EMERGENCY_STOP:
                backlog, run_state, code = "emergency_stopped", "emergency_stopped", "GATE_EMERGENCY_STOP"
            elif _blocked_strategy(strategy):
                backlog, run_state, code = "rejected", "rejected", "STRATEGY_DISABLED"
            elif result.decision is Decision.ALLOW and all(x is GateState.APPROVED for x in state):
                backlog, run_state, code = "approved", "approved", "OFFLINE_ALLOW_ONLY"
            elif result.decision is Decision.REJECT:
                backlog, run_state, code = "rejected", "rejected", "RISK_REJECTED"
            else:
                backlog, run_state, code = "paused", "paused", "VALIDATION_DEFERRED"
            event_id = _append(db, rows, actor=actor, schedule=spec, item_id=item_id,
                               kind="queue_transition", reason=reason,
                               payload={"backlog": backlog, "run_state": run_state,
                                        "decision": result.decision.value,
                                        "decision_event_id": result.decision_event_id,
                                        "approval_event_id": result.approval_event_id,
                                        "code": code, "dry_run": True,
                                        "execution_authorized": False})
            if backlog in ("rejected", "paused", "emergency_stopped"):
                self._alert(db, rows, actor=actor, spec=spec, item_id=item_id,
                            kind=("emergency_stop" if backlog == "emergency_stopped"
                                  else "rejected" if backlog == "rejected" else "paused"),
                            code=code, reason=reason)
            return QueueView(identity=item.identity, backlog=BacklogState(backlog),
                             run_state=RunState(run_state),
                             last_decision=result.decision.value,
                             risk_event_id=result.decision_event_id,
                             approval_event_id=result.approval_event_id,
                             queue_event_id=event_id)

    def requeue(self, *, access_token: str, strategy_id: str,
                item_id: str, inputs: GateInput, reason: str) -> bool:
        actor, reason = registry._identity(access_token), registry._reason(reason)
        with session_scope(self._sessions) as db:
            _lock(db)
            strategy = registry._strategy(db, actor, strategy_id)
            rows = _read(db, strategy_id)
            schedules, items, _ = _projection(rows)
            item = items.get(item_id)
            if item is None or item.backlog not in (
                    BacklogState.REJECTED, BacklogState.PAUSED, BacklogState.SKIPPED):
                raise PermissionError("item cannot be requeued")
            self._validate_input(item, inputs)
            if _blocked_strategy(strategy) or _stop_state(_gate_status(db, strategy_id)):
                raise PermissionError("disabled, retired or emergency-stopped requeue")
            if (datetime.now(timezone.utc) - item.identity.due_at_utc).total_seconds() > schedules[item.identity.schedule_id][0].max_queue_age_seconds:
                _append(db, rows, actor=actor, schedule=schedules[item.identity.schedule_id][0],
                        item_id=item_id, kind="requeue_denied", reason=reason,
                        payload={"code": "REQUEUE_STALE", "execution_authorized": False})
                return False
        result = self._gate.dry_run(access_token=access_token, inputs=inputs,
                                    reason="phase5 requeue validation: " + reason)
        with session_scope(self._sessions) as db:
            _lock(db)
            strategy, _, _ = _registered(db, actor, strategy_id,
                                         inputs.signal.version_id, inputs.signal.run_id)
            rows = _read(db, strategy_id)
            schedules, items, _ = _projection(rows)
            item = items.get(item_id)
            if item is None or item.backlog not in (
                BacklogState.REJECTED, BacklogState.PAUSED, BacklogState.SKIPPED
            ):
                raise PermissionError("queue changed during requeue validation")
            self._validate_input(item, inputs)
            spec = schedules[item.identity.schedule_id][0]
            self._bind(db, rows, actor=actor, spec=spec, item=item,
                       inputs=inputs, result=result, reason=reason)
            states = _gate_status(db, strategy_id)
            safe = (not _blocked_strategy(strategy)
                    and all(s is GateState.APPROVED for s in states)
                    and result.decision is Decision.ALLOW
                    and inputs.market_quality_status == "good"
                    and inputs.health.valid_parameters
                    and inputs.health.operational)
            if not safe:
                _append(db, rows, actor=actor, schedule=spec, item_id=item_id,
                        kind="requeue_denied", reason=reason,
                        payload={"code": "REQUEUE_RISK_DENIED",
                                 "risk_event_id": result.decision_event_id,
                                 "execution_authorized": False})
                self._alert(db, rows, actor=actor, spec=spec, item_id=item_id,
                            kind="validation_failure",
                            code="REQUEUE_RISK_DENIED", reason=reason)
                return False
            _append(db, rows, actor=actor, schedule=spec, item_id=item_id,
                    kind="queue_transition", reason=reason, payload={
                        "backlog": "queued", "run_state": "queued",
                        "risk_event_id": result.decision_event_id,
                        "reason_code": "RISK_APPROVED_REQUEUE_OFFLINE",
                        "dry_run": True, "execution_authorized": False,
                    })
            return True

    def pause_item(self, *, access_token: str, strategy_id: str,
                   item_id: str, reason: str) -> QueueView:
        """Defensive offline pause; only approved Phase 4 requeue may resume."""
        actor, reason = registry._identity(access_token), registry._reason(reason)
        with session_scope(self._sessions) as db:
            _lock(db)
            registry._strategy(db, actor, strategy_id)
            rows = _read(db, strategy_id)
            schedules, items, _ = _projection(rows)
            item = items.get(item_id)
            if item is None or item.backlog is not BacklogState.QUEUED:
                raise PermissionError("only queued validation previews may pause")
            spec = schedules[item.identity.schedule_id][0]
            event = _append(db, rows, actor=actor, schedule=spec, item_id=item_id,
                            kind="queue_transition", reason=reason,
                            payload={"backlog": "paused", "run_state": "paused",
                                     "code": "OPERATOR_PAUSED", "dry_run": True,
                                     "execution_authorized": False})
            self._alert(db, rows, actor=actor, spec=spec, item_id=item_id,
                        kind="paused", code="OPERATOR_PAUSED", reason=reason)
            return item.model_copy(update={"backlog": BacklogState.PAUSED,
                                           "run_state": RunState.PAUSED,
                                           "queue_event_id": event})

    def complete_preview(self, *, access_token: str, strategy_id: str,
                         item_id: str, reason: str) -> QueueView:
        actor, reason = registry._identity(access_token), registry._reason(reason)
        with session_scope(self._sessions) as db:
            _lock(db)
            registry._strategy(db, actor, strategy_id)
            rows = _read(db, strategy_id)
            schedules, items, _ = _projection(rows)
            item = items.get(item_id)
            if item is None or item.backlog is not BacklogState.APPROVED or item.run_state is not RunState.APPROVED:
                raise PermissionError("cannot complete preview without evaluated approval")
            event = _append(db, rows, actor=actor,
                            schedule=schedules[item.identity.schedule_id][0],
                            item_id=item_id, kind="run_transition", reason=reason,
                            payload={"backlog": "approved", "run_state": "completed",
                                     "dry_run": True, "execution_authorized": False})
            return item.model_copy(update={"run_state": RunState.COMPLETED, "queue_event_id": event})

    def fail_preview(self, *, access_token: str, strategy_id: str,
                     item_id: str, reason: str) -> QueueView:
        """Terminal validation failure after offline approval; never retries."""
        actor, reason = registry._identity(access_token), registry._reason(reason)
        with session_scope(self._sessions) as db:
            _lock(db)
            registry._strategy(db, actor, strategy_id)
            rows = _read(db, strategy_id)
            schedules, items, _ = _projection(rows)
            item = items.get(item_id)
            if item is None or item.backlog is not BacklogState.APPROVED or item.run_state is not RunState.APPROVED:
                raise PermissionError("failed preview requires prior audited offline approval")
            spec = schedules[item.identity.schedule_id][0]
            event_id = _append(db, rows, actor=actor, schedule=spec, item_id=item_id,
                               kind="run_transition", reason=reason,
                               payload={"backlog": "cancelled", "run_state": "failed",
                                        "code": "OFFLINE_VALIDATION_FAILED",
                                        "dry_run": True, "execution_authorized": False})
            self._alert(db, rows, actor=actor, spec=spec, item_id=item_id,
                        kind="validation_failure", code="OFFLINE_VALIDATION_FAILED",
                        reason=reason)
            return item.model_copy(update={
                "backlog": BacklogState.CANCELLED, "run_state": RunState.FAILED,
                "queue_event_id": event_id,
            })

    def cancel(self, *, access_token: str, strategy_id: str,
               item_id: str, reason: str) -> str:
        actor, reason = registry._identity(access_token), registry._reason(reason)
        with session_scope(self._sessions) as db:
            _lock(db)
            registry._strategy(db, actor, strategy_id)
            rows = _read(db, strategy_id)
            schedules, items, _ = _projection(rows)
            item = items.get(item_id)
            if item is None or item.backlog is not BacklogState.QUEUED:
                raise PermissionError("only pending offline validation may be cancelled")
            return _append(db, rows, actor=actor,
                           schedule=schedules[item.identity.schedule_id][0],
                           item_id=item_id, kind="queue_transition", reason=reason,
                           payload={"backlog": "cancelled", "run_state": "paused",
                                    "dry_run": True, "execution_authorized": False})
