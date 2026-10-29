"""Phase 6 read-only reconciliation plus audited stale-state quarantine.

No worker, broker, live authority, or automated execution. Reads never fix
broken evidence; only a valid stale queued preview can be safely marked skipped.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from sqlalchemy.orm import sessionmaker

from app.domain.offline_scheduler import BacklogState, RunState, utc
from app.persistence.session import session_scope
from app.persistence import strategy_registry_service as registry
from app.persistence.execution_validation_service import _lock
from app.persistence.offline_decision_journal_service import read_for_operator
from app.persistence.offline_scheduler_service import _read, _projection, _append, OfflineScheduler

FINAL = frozenset({
    "rejected","cancelled","expired","failed","completed","emergency_stopped"
})


def _outcome(item, rows):
    if item.run_state is RunState.COMPLETED:
        return "completed"
    if item.run_state is RunState.FAILED:
        return "failed"
    if item.backlog is BacklogState.EMERGENCY_STOPPED:
        return "emergency_stopped"
    if item.backlog is BacklogState.CANCELLED:
        return "cancelled"
    if item.backlog is BacklogState.REJECTED:
        return "rejected"
    if item.backlog is BacklogState.SKIPPED:
        # Expired is a reconciliation outcome, not a mutable queue state.
        for row in reversed(rows):
            if row.item_id == item.identity.item_id and row.kind == "queue_transition":
                payload=json.loads(row.payload_json)
                return "expired" if payload.get("code") in (
                    "STALE_QUEUE_OCCURRENCE", "STALE_BEFORE_EVALUATION"
                ) else "skipped"
        return "skipped"
    return item.backlog.value


class OfflineReconciler:
    def __init__(self,session_factory:sessionmaker):
        self._sessions=session_factory

    def inspect(self,*,access_token:str,strategy_id:str,
                at:datetime,limit:int=500) -> dict:
        now=utc(at)
        if not 1 <= limit <= 500:
            raise ValueError("bounded reconciliation limit required")
        actor=registry._identity(access_token)
        with session_scope(self._sessions) as db:
            registry._strategy(db,actor,strategy_id)
            try:
                source=_read(db,strategy_id)
                schedules,items,_=_projection(source)
                journal=read_for_operator(db,access_token=access_token,
                                          strategy_id=strategy_id,limit=None)
                # read_for_operator verifies full original/journal chain,
                # regardless of output page size.
            except (PermissionError, ValueError, KeyError, TypeError) as exc:
                # Do not expose raw payload or corrupted private ledger rows.
                return {"strategy_id":strategy_id,"status":"untrusted",
                        "issues":("AUDIT_OR_LINEAGE_INVALID",),
                        "items":(),"execution_authorized":False}
            results=[]
            for item in sorted(items.values(),key=lambda i: (
                    i.identity.due_at_utc,i.identity.priority,i.identity.item_id))[:limit]:
                spec=schedules[item.identity.schedule_id][0]
                problems=[]
                outcome=_outcome(item,source)
                age=(now-item.identity.due_at_utc).total_seconds()
                if outcome not in FINAL and age > spec.max_queue_age_seconds:
                    problems.append("STALE_NON_TERMINAL")
                trace=[row for row in journal
                       if row["item_id"]==item.identity.item_id]
                if not trace:
                    problems.append("MISSING_JOURNAL_LINEAGE")
                if item.backlog is BacklogState.APPROVED or item.run_state in (
                        RunState.COMPLETED,RunState.FAILED):
                    if not item.risk_event_id or not item.approval_event_id:
                        problems.append("MISSING_RISK_APPROVAL")
                    elif not any(r["risk_event_id"]==item.risk_event_id
                                 and r["approval_event_id"]==item.approval_event_id
                                 for r in trace):
                        problems.append("BROKEN_APPROVAL_LINK")
                results.append({
                    "item_id":item.identity.item_id,"run_id":item.identity.run_id,
                    "strategy_id":strategy_id,"version_id":item.identity.version_id,
                    "signal_id":item.identity.batch_id,"queue_state":item.backlog.value,
                    "run_state":item.run_state.value,"reconciled_outcome":outcome,
                    "terminal":outcome in FINAL,"issues":tuple(problems),
                    "risk_event_id":item.risk_event_id,
                    "approval_event_id":item.approval_event_id,
                    "lineage_sha256":item.identity.lineage_sha256,
                    "journal_reference":trace[-1]["audit_ref"] if trace else None,
                    "execution_authorized":False,
                })
            problems=tuple(sorted({x for item in results for x in item["issues"]}))
            return {
                "strategy_id":strategy_id,
                "status":"degraded" if problems else "verified_offline",
                "issues":problems,"items":tuple(results),
                "event_count":len(source),
                "journal_count":len(journal),
                "execution_authorized":False,
            }

    def mark_stale(self,*,access_token:str,strategy_id:str,
                   item_id:str,at:datetime,reason:str) -> str:
        """Append atomic stale -> skipped and alert; no automatic requeue."""
        now=utc(at)
        actor=registry._identity(access_token)
        reason=registry._reason(reason)
        with session_scope(self._sessions) as db:
            _lock(db)
            registry._strategy(db,actor,strategy_id)
            rows=_read(db,strategy_id)
            schedules,items,_=_projection(rows)
            item=items.get(item_id)
            if item is None or item.backlog is not BacklogState.QUEUED:
                raise PermissionError("stale quarantine requires a queued validation item")
            spec=schedules[item.identity.schedule_id][0]
            if (now-item.identity.due_at_utc).total_seconds() <= spec.max_queue_age_seconds:
                raise PermissionError("queue item is not stale")
            if item.execution_authorized:
                raise PermissionError("offline queue record unexpectedly carries authority")
            event=_append(db,rows,actor=actor,schedule=spec,item_id=item_id,
                          kind="queue_transition",reason=reason,payload={
                              "backlog":"skipped","run_state":"paused",
                              "code":"STALE_RECONCILIATION_QUARANTINE",
                              "dry_run":True,"execution_authorized":False,
                          })
            OfflineScheduler._alert(db,rows,actor=actor,spec=spec,item_id=item_id,
                                    kind="stale_item",code="STALE_RECONCILIATION_QUARANTINE",
                                    reason=reason)
            return event
