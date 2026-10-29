"""Phase 6 atomic append-only journal and read-only operator evidence.

Journal rows are written in the SAME transaction as Phase 5 orchestration
events. Nothing here can approve or submit a broker order.
"""
from __future__ import annotations

import hashlib
import json
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.persistence.offline_decision_journal_models import PlatformDecisionJournalRow
from app.persistence.offline_scheduler_models import PlatformOrchestrationEventRow
from app.persistence.execution_validation_service import _read as _read_phase4
from app.persistence.session import session_scope
from app.persistence import strategy_registry_service as registry

ZERO = "0" * 64


def _json(doc) -> str:
    return json.dumps(doc, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha(doc) -> str:
    return hashlib.sha256(_json(doc).encode()).hexdigest()


def _fields(row):
    return {
        "id": row.id, "stream": row.stream_id, "sequence": row.sequence,
        "orchestration_event_id": row.orchestration_event_id,
        "strategy_id": row.strategy_id, "version_id": row.version_id,
        "run_id": row.run_id, "item_id": row.item_id, "signal_id": row.signal_id,
        "decision_type": row.decision_type, "actor_id": row.actor_id,
        "actor_role": row.actor_role, "reason": row.reason,
        "risk_state": row.risk_state, "approval_mode": row.approval_mode,
        "approval_status": row.approval_status, "queue_state": row.queue_state,
        "run_state": row.run_state, "data_quality": json.loads(row.data_quality_json),
        "risk_event_id": row.risk_event_id,
        "approval_event_id": row.approval_event_id,
        "risk_policy_sha256": row.risk_policy_sha256,
        "version_sha256": row.version_sha256,
        "lineage_sha256": row.lineage_sha256,
        "payload": json.loads(row.payload_json),
        "execution_authorized": row.execution_authorized,
        "at_ms": row.occurred_at_ms,
        "previous": row.previous_sha256,
    }


def _verified(db: Session, strategy_id: str) -> list[PlatformDecisionJournalRow]:
    rows=list(db.scalars(select(PlatformDecisionJournalRow)
                         .where(PlatformDecisionJournalRow.stream_id == "strategy:" + strategy_id)
                         .order_by(PlatformDecisionJournalRow.sequence)))
    before=ZERO
    for count, row in enumerate(rows,1):
        if row.sequence != count or row.previous_sha256 != before:
            raise PermissionError("journal sequence broken")
        content=_fields(row)
        if content["execution_authorized"] is not False:
            raise PermissionError("journal live authority forbidden")
        calculated=_sha(content)
        if calculated != row.content_sha256:
            raise PermissionError("journal content hash mismatch")
        event=db.get(PlatformOrchestrationEventRow,row.orchestration_event_id)
        if (event is None or event.stream_id != row.stream_id
                or event.strategy_id != row.strategy_id
                or event.run_id != row.run_id or event.version_id != row.version_id
                or event.kind != row.decision_type
                or event.content_sha256 != content["payload"].get("orchestration_sha256")):
            raise PermissionError("orphan or tampered journal/orchestration evidence")
        before=calculated
    return rows


def _schedule_source(events, schedule_id):
    for event in events:
        if event.kind == "schedule_created" and event.schedule_id == schedule_id:
            return json.loads(event.payload_json)
    raise PermissionError("journal cannot find immutable schedule lineage")


def _risk_receipt(db, strategy_id, risk_id, approval_id):
    rows=_read_phase4(db,strategy_id)
    risk=next((i for i,r in enumerate(rows) if r.id == risk_id and r.kind == "risk_decision"),None)
    approval=next((i for i,r in enumerate(rows) if r.id == approval_id and r.kind == "approval_decision"),None)
    if risk is None or approval is None or approval != risk+1:
        raise PermissionError("journal risk/approval receipt missing or reordered")
    risk_row, approval_row=rows[risk],rows[approval]
    if (risk_row.version_id != approval_row.version_id
            or risk_row.run_id != approval_row.run_id
            or risk_row.strategy_id != approval_row.strategy_id):
        raise PermissionError("journal risk run binding mismatch")
    risk_data=json.loads(risk_row.payload_json)
    approval_data=json.loads(approval_row.payload_json)
    quality={}
    if risk >= 1 and rows[risk-1].kind == "gate_evaluated":
        gate_data=json.loads(rows[risk-1].payload_json)
        quality={"status":gate_data.get("quality","unknown"),
                 "score":gate_data.get("score"), "verified_live":False}
    if risk_data.get("execution_authorized") is not False or approval_data.get("execution_authorized") is not False:
        raise PermissionError("broker-authorizing gate record forbidden")
    return risk_data,approval_data,quality


def append_for_orchestration(db: Session, event: PlatformOrchestrationEventRow,
                             events: list[PlatformOrchestrationEventRow]) -> str:
    """Called immediately after Phase 5 _append flush, in its same SQL transaction."""
    if event.kind not in (
        "schedule_created","schedule_paused","schedule_cancelled",
        "queue_transition","run_transition","gate_binding",
        "requeue_denied","operator_alert",
    ):
        raise PermissionError("unknown journal event kind")
    history=_verified(db,event.strategy_id)
    if any(x.orchestration_event_id == event.id for x in history):
        raise PermissionError("orchestration event already journaled")
    spec=_schedule_source(events,event.schedule_id)
    schedule=spec["spec"]
    payload=json.loads(event.payload_json)
    signal_id=schedule["batch_id"]
    risk_id=payload.get("risk_event_id")
    approval_id=payload.get("approval_event_id")
    # Phase 5 has already committed a gate binding into the in-memory event
    # list before the next queue transition. Later outcomes inherit that link.
    for old in reversed(events):
        if old.item_id == event.item_id and old.kind == "gate_binding":
            data=json.loads(old.payload_json)
            risk_id = risk_id or data.get("risk_event_id")
            approval_id = approval_id or data.get("approval_event_id")
            break
    risk_state="offline_unverified"
    approval_mode="blocked"
    approval_status="not_evaluated"
    quality={"status":"unknown","score":None,"verified_live":False}
    if risk_id or approval_id:
        if not risk_id or not approval_id:
            raise PermissionError("incomplete risk/approval provenance")
        risk, approval, quality = _risk_receipt(db,event.strategy_id,risk_id,approval_id)
        if (risk.get("signal_id") != signal_id or
            risk.get("version_sha256") != spec["version_sha256"] or
            risk.get("order_intent_id") != event.item_id or
            approval.get("decision") != risk.get("decision")):
            raise PermissionError("risk snapshot not bound to queue lineage")
        risk_state=risk.get("risk_state","offline_unverified")
        approval_mode=approval.get("approval_mode","blocked")
        approval_status=approval.get("decision","unknown")
    identity=payload.get("identity") or {}
    lineage=identity.get("lineage_sha256") if isinstance(identity,dict) else None
    if not lineage and event.item_id:
        for old in events:
            if old.item_id == event.item_id:
                data=json.loads(old.payload_json)
                if isinstance(data.get("identity"),dict):
                    lineage=data["identity"].get("lineage_sha256")
                    break
    if not lineage:
        lineage=_sha({"schedule_id":event.schedule_id,
                      "version_sha256":spec["version_sha256"],
                      "risk_policy_sha256":spec["risk_policy_sha256"],
                      "batch_id":signal_id})
    journal_id=str(uuid4())
    before=history[-1].content_sha256 if history else ZERO
    properties={
        "id":journal_id,"stream":"strategy:"+event.strategy_id,
        "sequence":len(history)+1,"orchestration_event_id":event.id,
        "strategy_id":event.strategy_id,"version_id":event.version_id,
        "run_id":event.run_id,"item_id":event.item_id,"signal_id":signal_id,
        "decision_type":event.kind,"actor_id":event.actor_id,
        "actor_role":event.actor_role,"reason":event.reason,
        "risk_state":risk_state,"approval_mode":approval_mode,
        "approval_status":approval_status,
        "queue_state":payload.get("backlog","unchanged"),
        "run_state":payload.get("run_state","unchanged"),
        "data_quality":quality,"risk_event_id":risk_id,
        "approval_event_id":approval_id,
        "risk_policy_sha256":spec["risk_policy_sha256"],
        "version_sha256":spec["version_sha256"],
        "lineage_sha256":lineage,
        "payload":{"event":payload,"orchestration_sha256":event.content_sha256},
        "execution_authorized":False,"at_ms":event.occurred_at_ms,
        "previous":before,
    }
    fields={k:v for k,v in properties.items() if k not in ("data_quality","payload","stream","previous","at_ms")}
    db.add(PlatformDecisionJournalRow(
        **fields,stream_id=properties["stream"],
        data_quality_json=_json(quality),payload_json=_json(properties["payload"]),
        previous_sha256=before,content_sha256=_sha(properties),
        occurred_at_ms=event.occurred_at_ms,
    ))
    db.flush()  # FAIL => Phase 5 queue event and journal BOTH roll back
    return journal_id


def read_for_operator(db: Session, *, access_token: str,
                      strategy_id: str, limit: int = 100) -> tuple[dict,...]:
    """Read-only strategy-scoped view; verifies signed JWT and both ledger chains."""
    if limit is not None and not 1 <= limit <= 500:
        raise ValueError("operator journal page limit exceeded")
    actor=registry._identity(access_token)
    registry._strategy_readonly(db,actor,strategy_id)
    # Verify original source chain as well as mirrored journal chain.
    from app.persistence.offline_scheduler_service import _read as _read_source
    source=_read_source(db,strategy_id)
    journal=_verified(db,strategy_id)
    if len(source) != len(journal):
        raise PermissionError("partial offline decision journal; fail closed")
    if tuple(x.orchestration_event_id for x in journal) != tuple(x.id for x in source):
        raise PermissionError("decision journal and queue event ordering differ")
    out=[]
    for row in journal[-limit:] if limit is not None else journal:
        data=_fields(row)
        data["audit_ref"]=row.content_sha256
        data["journal_event_id"]=row.id
        data["execution_authorized"]=False
        out.append(data)
    return tuple(out)


class OperatorDecisionJournal:
    """User-facing READ ONLY evidence service, no state-changing method."""
    def __init__(self,session_factory:sessionmaker):
        self._sessions=session_factory

    def decisions(self,*,access_token:str,strategy_id:str,limit:int=100) -> tuple[dict,...]:
        with session_scope(self._sessions) as db:
            return read_for_operator(db,access_token=access_token,
                                     strategy_id=strategy_id,limit=limit)
