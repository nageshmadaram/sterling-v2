"""Phase 9: append-only, operator-scoped, OFFLINE informational alerts.

No worker, broker, execution, direct network, provider credentials, webhook or
message dispatch configured by default. All delivery state transitions are
durable audit events; an adapter is opt-in and MUST be idempotent.
"""
from __future__ import annotations
import hashlib
import json
import time
from dataclasses import dataclass
from enum import Enum
from typing import Protocol
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.persistence.offline_alert_models import PlatformOfflineAlertEventRow
from app.persistence.execution_validation_service import _lock, _read as read_gate
from app.persistence.offline_scheduler_service import _read as read_queue
from app.persistence.offline_decision_journal_service import read_for_operator
from app.persistence.offline_performance_service import _read as read_synthetic
from app.persistence.session import session_scope
from app.persistence import strategy_registry_service as registry

ZERO = "0" * 64
MAX_EVENTS = 20_000
MAX_ATTEMPTS = 4
BASE_BACKOFF_MS = 60_000


class Priority(str,Enum):
    INFO="info"
    WARNING="warning"
    CRITICAL="critical"

class Category(str,Enum):
    RISK_THRESHOLD="risk_threshold"
    STALE_MARKET_DATA="stale_market_data"
    SCHEDULING_FAILURE="scheduling_failure"
    INVALID_QUEUE_ITEM="invalid_queue_item"
    INVALID_DECISION_TRAIL="invalid_decision_trail"
    APPROVAL_REJECTION="approval_rejection"
    EMERGENCY_STOP="emergency_stop"
    INVALID_STRATEGY_HEALTH="invalid_strategy_health"
    AUDIT_EXCEPTION="audit_exception"
    STALE_PROVIDER_OUTPUT="stale_provider_output"
    NEGATIVE_KPI_TREND="negative_kpi_trend"
    INFORMATIONAL_UPDATE="informational_update"

class Channel(str,Enum):
    DASHBOARD="dashboard"
    TELEGRAM="telegram"
    WHATSAPP="whatsapp"
    EMAIL="email"

CRITICAL = {Category.EMERGENCY_STOP,Category.INVALID_DECISION_TRAIL,Category.AUDIT_EXCEPTION}
WARNING = {Category.RISK_THRESHOLD,Category.STALE_MARKET_DATA,
           Category.SCHEDULING_FAILURE,Category.INVALID_QUEUE_ITEM,
           Category.APPROVAL_REJECTION,Category.INVALID_STRATEGY_HEALTH,
           Category.STALE_PROVIDER_OUTPUT,Category.NEGATIVE_KPI_TREND}


def _json(payload):
    return json.dumps(payload,sort_keys=True,separators=(",",":"),allow_nan=False)


def _digest(payload):
    return hashlib.sha256(_json(payload).encode()).hexdigest()


def _events(db,strategy_id):
    rows=list(db.scalars(select(PlatformOfflineAlertEventRow).where(
        PlatformOfflineAlertEventRow.strategy_id==strategy_id
    ).order_by(PlatformOfflineAlertEventRow.sequence)))
    if len(rows)>MAX_EVENTS:
        raise PermissionError("alert ledger exceeds bounded offline reader")
    prev=ZERO
    per_alert={}
    for index,row in enumerate(rows,1):
        sequence=per_alert.get(row.alert_id,0)+1
        if (row.sequence!=index or row.alert_sequence!=sequence
            or row.previous_sha256!=prev or row.execution_authorized is not False):
            raise PermissionError("offline alert audit sequence invalid")
        content={"id":row.id,"alert_id":row.alert_id,"strategy_id":strategy_id,
                 "sequence":row.sequence,"alert_sequence":row.alert_sequence,
                 "event_kind":row.event_kind,"actor_id":row.actor_id,
                 "actor_role":row.actor_role,"reason":row.reason,
                 "payload":json.loads(row.payload_json),
                 "occurred_at_ms":row.occurred_at_ms,
                 "execution_authorized":False,"previous_sha256":prev}
        if _digest(content)!=row.content_sha256:
            raise PermissionError("offline alert ledger tampered")
        per_alert[row.alert_id]=sequence
        prev=row.content_sha256
    return rows


def _append(db,rows,*,actor,alert_id,strategy_id,kind,reason,payload):
    if len(rows)>=MAX_EVENTS: raise PermissionError("alert ledger limit reached")
    now=int(time.time()*1000)
    content={"id":str(uuid4()),"alert_id":alert_id,"strategy_id":strategy_id,
             "sequence":len(rows)+1,
             "alert_sequence":sum(r.alert_id==alert_id for r in rows)+1,
             "event_kind":kind,"actor_id":actor.user_id,"actor_role":actor.role,
             "reason":registry._reason(reason),"payload":payload,
             "occurred_at_ms":now,"execution_authorized":False,
             "previous_sha256":rows[-1].content_sha256 if rows else ZERO}
    row=PlatformOfflineAlertEventRow(id=content["id"],alert_id=alert_id,
        strategy_id=strategy_id,sequence=content["sequence"],
        alert_sequence=content["alert_sequence"],event_kind=kind,
        actor_id=actor.user_id,actor_role=actor.role,reason=content["reason"],
        payload_json=_json(payload),occurred_at_ms=now,execution_authorized=False,
        previous_sha256=content["previous_sha256"],content_sha256=_digest(content))
    db.add(row)
    db.flush() # every transition is atomic with its audit insert
    rows.append(row)
    return row.id


def _source(db,strategy_id,source_type,source_ref,access_token):
    # Never permit an unverified "live" trade/broker reference.
    if source_type=="decision":
        for row in read_for_operator(db,access_token=access_token,strategy_id=strategy_id,limit=None):
            if row["journal_event_id"]==source_ref:
                return row["audit_ref"],row["run_id"],row["journal_event_id"]
    elif source_type=="queue":
        for row in read_queue(db,strategy_id):
            if row.id==source_ref:
                return row.content_sha256,row.run_id,None
    elif source_type=="gate":
        for row in read_gate(db,strategy_id):
            if row.id==source_ref:
                return row.content_sha256,row.run_id,None
    elif source_type=="hypothetical_trade":
        for row in read_synthetic(db,strategy_id):
            if row.id==source_ref:
                return row.content_sha256,row.entry_run_id,None
    raise PermissionError("source alert event absent from verified offline evidence")


def _project(rows):
    result={}
    for r in rows:
        payload=json.loads(r.payload_json)
        if r.event_kind=="created":
            if r.alert_id in result: raise PermissionError("duplicate alert creation")
            result[r.alert_id]={
                "alert_id":r.alert_id,"strategy_id":r.strategy_id,
                "created_at_ms":r.occurred_at_ms,"last_event_at_ms":r.occurred_at_ms,
                "priority":payload["priority"],"category":payload["category"],
                "source_system":payload["source_system"],
                "source_type":payload["source_type"],"source_ref":payload["source_ref"],
                "source_sha256":payload["source_sha256"],
                "run_id":payload.get("run_id"),"decision_id":payload.get("decision_id"),
                "message":payload["message"],"reason":r.reason,
                "expires_at_ms":payload["expires_at_ms"],
                "state":"active","delivery":{},
                "audit_ref":r.content_sha256,"history":[],
                "execution_authorized":False,
            }
        item=result.get(r.alert_id)
        if item is None: raise PermissionError("orphan alert transition")
        old=item["state"]
        if r.event_kind=="acknowledged":
            if old!="active": raise PermissionError("invalid alert ack transition")
            item["state"]="acknowledged"
        elif r.event_kind=="resolved":
            if old not in ("active","acknowledged"):
                raise PermissionError("invalid alert resolution")
            item["state"]="resolved"
        elif r.event_kind=="expired":
            if old not in ("active","acknowledged"):
                raise PermissionError("invalid alert expiry")
            item["state"]="expired"
        elif r.event_kind=="escalated":
            if old not in ("active","acknowledged") or item.get("escalated"):
                raise PermissionError("invalid informational escalation")
            item["escalated"]=True
        elif r.event_kind.startswith("delivery_"):
            if old in ("resolved","expired"):
                raise PermissionError("delivery after alert terminal")
            channel=payload.get("channel")
            if channel not in Channel._value2member_map_:
                raise PermissionError("invalid channel audit")
            before=item["delivery"].get(channel)
            if r.event_kind=="delivery_pending":
                if before and before["state"] not in ("failed",):
                    raise PermissionError("duplicate pending/sent delivery")
                attempt=payload["attempt"]
                if attempt != (before["attempt"]+1 if before else 1) or attempt>MAX_ATTEMPTS:
                    raise PermissionError("invalid delivery attempt")
                item["delivery"][channel]={
                    "state":"pending","attempt":attempt,
                    "last_attempt_at_ms":r.occurred_at_ms,"retry_after_ms":None,
                    "receipt":None,"reason":r.reason,
                }
            else:
                if before is None: raise PermissionError("delivery without attempt")
                kind=r.event_kind.split("_",1)[1]
                if kind in ("sent","failed") and before["state"]!="pending":
                    raise PermissionError("delivery completion without pending attempt")
                if kind=="acknowledged" and before["state"]!="sent":
                    raise PermissionError("delivery receipt acknowledgement requires sent")
                if kind=="expired" and before["state"] not in ("failed","pending"):
                    raise PermissionError("invalid delivery expiry")
                before["state"]=kind
                before["reason"]=r.reason
                before["receipt"]=payload.get("receipt")
                before["retry_after_ms"]=payload.get("retry_after_ms")
        elif r.event_kind!="created":
            raise PermissionError("unknown alert ledger event")
        item["audit_ref"]=r.content_sha256
        item["last_event_at_ms"]=r.occurred_at_ms
        item["history"].append({"event_id":r.id,"kind":r.event_kind,
                 "at_ms":r.occurred_at_ms,"actor":r.actor_id,
                 "role":r.actor_role,"reason":r.reason,
                 "source_sha256":r.content_sha256})
    return result


@dataclass(frozen=True)
class Transport:
    """Injected by independently reviewed deployment; no provider network in Phase 9."""
    channel: Channel
    destination_verified: bool
    enabled: bool
    idempotent: bool
    def send(self, *, alert_id: str, message: str, idempotency_key: str) -> str:
        raise NotImplementedError("transport must be explicitly implemented and approved")


class OfflineAlertService:
    def __init__(self,sessions:sessionmaker):
        self.sessions=sessions

    def create(self,*,access_token:str,strategy_id:str,category:Category,
               source_type:str,source_ref:str,source_system:str,
               message:str,reason:str,run_id:str|None=None,
               decision_id:str|None=None,expires_at_ms:int|None=None):
        if not isinstance(category,Category): raise ValueError("typed alert category required")
        if not isinstance(message,str) or not 1<=len(message.strip())<=500:
            raise ValueError("bounded nonempty alert message required")
        if not isinstance(source_system,str) or not 1<=len(source_system)<=80:
            raise ValueError("source system required")
        if expires_at_ms is not None and (not isinstance(expires_at_ms,int)
            or expires_at_ms <= int(time.time()*1000)):
            raise ValueError("future alert expiry required")
        actor=registry._identity(access_token)
        aid=_digest({"strategy":strategy_id,"category":category.value,
                     "source_type":source_type,"source_ref":source_ref})
        with session_scope(self.sessions) as db:
            _lock(db)
            registry._strategy_readonly(db,actor,strategy_id)
            rows=_events(db,strategy_id)
            previous=_project(rows)
            if aid in previous:
                return {"alert_id":aid,"duplicate_suppressed":True}
            source_sha,source_run,source_decision=_source(
                db,strategy_id,source_type,source_ref,access_token)
            if (run_id is not None and run_id!=source_run or
                decision_id is not None and decision_id!=source_decision):
                raise PermissionError("unverified alert run or decision linkage")
            priority=(Priority.CRITICAL if category in CRITICAL
                      else Priority.WARNING if category in WARNING else Priority.INFO)
            payload={"priority":priority.value,"category":category.value,
                     "source_system":source_system.strip(),"source_type":source_type,
                     "source_ref":source_ref,"source_sha256":source_sha,
                     "run_id":source_run,"decision_id":source_decision,
                     "message":message.strip(),"expires_at_ms":expires_at_ms}
            _append(db,rows,actor=actor,alert_id=aid,strategy_id=strategy_id,
                    kind="created",reason=reason,payload=payload)
            return {"alert_id":aid,"duplicate_suppressed":False}

    def list(self,*,access_token:str,strategy_id:str,limit:int=100,
             unresolved_only:bool=False):
        if not 1<=limit<=500: raise ValueError("bounded replay limit required")
        actor=registry._identity(access_token)
        with session_scope(self.sessions) as db:
            registry._strategy_readonly(db,actor,strategy_id)
            records=_project(_events(db,strategy_id))
            # Fail closed if source provenance changes, even when alert ledger
            # itself has not been modified.
            for item in records.values():
                sha,linked_run,linked_decision=_source(
                    db,strategy_id,item["source_type"],item["source_ref"],access_token)
                if (sha!=item["source_sha256"] or
                    linked_run!=item["run_id"] or linked_decision!=item["decision_id"]):
                    raise PermissionError("alert source immutable fingerprint mismatch")
            replay=sorted(records.values(),key=lambda r:(-r["created_at_ms"],r["alert_id"]))
            if unresolved_only: replay=[r for r in replay if r["state"] in ("active","acknowledged")]
            return tuple(replay[:limit])

    def change_state(self,*,access_token:str,strategy_id:str,alert_id:str,
                     action:str,reason:str):
        if action not in ("acknowledged","resolved"):
            raise PermissionError("only informational ack/resolve supported")
        actor=registry._identity(access_token)
        if action=="resolved" and actor.role!="admin":
            raise PermissionError("resolution requires verified administrator")
        with session_scope(self.sessions) as db:
            _lock(db)
            registry._strategy_readonly(db,actor,strategy_id)
            rows=_events(db,strategy_id)
            item=_project(rows).get(alert_id)
            if item is None: raise LookupError("alert not found")
            if item["state"]==action:
                return {"changed":False,"state":action}
            if item["state"] not in ("active","acknowledged") or action=="acknowledged" and item["state"]!="active":
                raise PermissionError("terminal or duplicate alert state change")
            _source(db,strategy_id,item["source_type"],item["source_ref"],access_token)
            _append(db,rows,actor=actor,alert_id=alert_id,strategy_id=strategy_id,
                    kind=action,reason=reason,payload={})
            return {"changed":True,"state":action}

    def expire_due(self,*,access_token:str,strategy_id:str,at_ms:int):
        actor=registry._identity(access_token)
        if actor.role!="admin": raise PermissionError("expiry requires verified admin")
        changed=0
        with session_scope(self.sessions) as db:
            _lock(db)
            registry._strategy_readonly(db,actor,strategy_id)
            rows=_events(db,strategy_id)
            for alert_id,item in _project(rows).items():
                if item["state"] in ("active","acknowledged") and item["expires_at_ms"] is not None and at_ms>=item["expires_at_ms"]:
                    for channel,delivery in item["delivery"].items():
                        if delivery["state"] in ("pending","failed"):
                            _append(db,rows,actor=actor,alert_id=alert_id,strategy_id=strategy_id,
                                    kind="delivery_expired",reason="offline delivery retention expiry",
                                    payload={"channel":channel})
                    _append(db,rows,actor=actor,alert_id=alert_id,strategy_id=strategy_id,
                            kind="expired",reason="offline alert retention expiry",
                            payload={})
                    changed+=1
        return changed

    def ingest_verified_offline(self,*,access_token:str,strategy_id:str,max_sources:int=200):
        """Explicit bounded read+project of Phase 4-7 offline source alerts.

        This function has no HTTP endpoint, timer, background worker,
        external network, order action or independent trading permission.
        On replay, deterministic source IDs suppress duplicate alerts.
        """
        if not 1<=max_sources<=500:
            raise ValueError("bounded offline alert ingestion required")
        actor=registry._identity(access_token)
        if actor.role!="admin":
            raise PermissionError("offline source ingestion requires admin")
        with session_scope(self.sessions) as db:
            registry._strategy_readonly(db,actor,strategy_id)
            gate=read_gate(db,strategy_id)
            queue=read_queue(db,strategy_id)
            # Verify immutable chains before accepting any source.
            read_for_operator(db,access_token=access_token,strategy_id=strategy_id,limit=None)
            selected=[]
            for row in gate:
                if row.kind=="state_transition":
                    content=json.loads(row.payload_json)
                    if content.get("state")=="emergency_stop":
                        selected.append((Category.EMERGENCY_STOP,"gate",row.id,
                                         "offline_gate","Offline validation emergency stop"))
            kinds={"emergency_stop":Category.EMERGENCY_STOP,
                   "validation_failure":Category.APPROVAL_REJECTION,
                   "stale_item":Category.INVALID_QUEUE_ITEM,
                   "queue_growth":Category.SCHEDULING_FAILURE,
                   "paused":Category.INFORMATIONAL_UPDATE}
            for row in queue:
                if row.kind=="operator_alert":
                    payload=json.loads(row.payload_json)
                    category=kinds.get(payload.get("kind"),Category.SCHEDULING_FAILURE)
                    selected.append((category,"queue",row.id,
                                     "offline_scheduler","Offline queue: "+str(payload.get("code","alert"))[:128]))
            selected=selected[-max_sources:]
        # No SQL transaction is held while invoking ingestion operations;
        # each source is independently deduplicated and audited on commit.
        registered=0
        for category,typ,ref,system,message in selected:
            result=self.create(access_token=access_token,strategy_id=strategy_id,
                category=category,source_type=typ,source_ref=ref,
                source_system=system,message=message,
                reason="audited offline source ingestion")
            if not result["duplicate_suppressed"]: registered+=1
        return registered

    def escalate_due(self,*,access_token:str,strategy_id:str,at_ms:int,
                     critical_after_ms:int=300_000,warning_after_ms:int=900_000):
        """Only emits audited informational escalation, never performs actions."""
        if critical_after_ms < 0 or warning_after_ms < 0:
            raise ValueError("escalation delay cannot be negative")
        actor=registry._identity(access_token)
        if actor.role!="admin": raise PermissionError("escalation requires admin")
        count=0
        with session_scope(self.sessions) as db:
            _lock(db)
            registry._strategy_readonly(db,actor,strategy_id)
            rows=_events(db,strategy_id)
            for aid,item in _project(rows).items():
                if item["state"] not in ("active","acknowledged") or item.get("escalated"):
                    continue
                wait=critical_after_ms if item["priority"]=="critical" else warning_after_ms
                if item["priority"]!="info" and at_ms-item["created_at_ms"]>=wait:
                    _append(db,rows,actor=actor,alert_id=aid,strategy_id=strategy_id,
                            kind="escalated",reason="offline unresolved alert escalation",payload={})
                    count+=1
        return count

    def delivery(self,*,access_token:str,strategy_id:str,alert_id:str,
                 channel:Channel,transport:Transport|None=None):
        """Manual retryable outbox. No background run, no unapproved outbound send.

        Pending/sent unknown requires operator verification; never blind retry.
        """
        if not isinstance(channel,Channel): raise ValueError("supported channel required")
        actor=registry._identity(access_token)
        if actor.role!="admin": raise PermissionError("transport action requires admin")
        if channel != Channel.DASHBOARD and (not transport or
            transport.channel!=channel or not transport.enabled or
            not transport.destination_verified or not transport.idempotent):
            raise PermissionError("unverified external transport not allowed")
        with session_scope(self.sessions) as db:
            _lock(db)
            registry._strategy_readonly(db,actor,strategy_id)
            rows=_events(db,strategy_id)
            item=_project(rows).get(alert_id)
            if not item or item["state"] in ("resolved","expired"):
                raise PermissionError("unknown or terminal alert")
            before=item["delivery"].get(channel.value)
            if before:
                if before["state"]=="sent" or before["state"]=="acknowledged":
                    return {"changed":False,"state":before["state"]}
                if before["state"]!="failed" or before["attempt"]>=MAX_ATTEMPTS:
                    raise PermissionError("pending/ambiguous/exhausted delivery not retryable")
                if before["retry_after_ms"] is None or int(time.time()*1000)<before["retry_after_ms"]:
                    raise PermissionError("durable exponential retry not due")
            attempt=(before["attempt"]+1) if before else 1
            key=_digest({"alert":alert_id,"channel":channel.value})
            _append(db,rows,actor=actor,alert_id=alert_id,strategy_id=strategy_id,
                    kind="delivery_pending",reason="offline informational delivery reservation",
                    payload={"channel":channel.value,"attempt":attempt,"idempotency_key":key})
            message=item["message"]
        # Send happens strictly AFTER pending commit; state uncertainty is
        # retained as pending on crash, never automatically double sent.
        try:
            receipt=(key if channel==Channel.DASHBOARD else
                    transport.send(alert_id=alert_id,message=message,idempotency_key=key))
            if not isinstance(receipt,str) or not receipt.strip() or len(receipt)>256:
                raise ValueError("provider returned invalid delivery receipt")
            state,reason="delivery_sent","transport accepted informational message"
            details={"channel":channel.value,"receipt":receipt.strip()}
        except Exception as exc:
            state,reason="delivery_failed","offline transport delivery failed"
            details={"channel":channel.value,"retry_after_ms":int(time.time()*1000)+BASE_BACKOFF_MS*2**(attempt-1),
                     "error_type":type(exc).__name__}
        with session_scope(self.sessions) as db:
            _lock(db)
            registry._strategy_readonly(db,actor,strategy_id)
            rows=_events(db,strategy_id)
            current=_project(rows).get(alert_id)
            if not current or current["state"] in ("resolved","expired"):
                # Do not silently pretend a receipt was committed.
                raise PermissionError("alert terminal after attempt; manual verification required")
            delivery=current["delivery"].get(channel.value)
            if not delivery or delivery["state"]!="pending" or delivery["attempt"]!=attempt:
                raise PermissionError("delivery changed concurrently")
            _append(db,rows,actor=actor,alert_id=alert_id,strategy_id=strategy_id,
                    kind=state,reason=reason,payload=details)
        return {"changed":True,"state":"sent" if state=="delivery_sent" else "failed"}

    def confirm_receipt(self,*,access_token:str,strategy_id:str,alert_id:str,
                        channel:Channel,reason:str):
        actor=registry._identity(access_token)
        if actor.role!="admin": raise PermissionError("delivery confirmation requires admin")
        with session_scope(self.sessions) as db:
            _lock(db)
            registry._strategy_readonly(db,actor,strategy_id)
            rows=_events(db,strategy_id)
            item=_project(rows).get(alert_id)
            if not item or item["state"] in ("resolved","expired"):
                raise PermissionError("unknown/terminal alert")
            attempt=item["delivery"].get(channel.value)
            if not attempt or attempt["state"]!="sent":
                raise PermissionError("delivery not accepted")
            _append(db,rows,actor=actor,alert_id=alert_id,strategy_id=strategy_id,
                    kind="delivery_acknowledged",reason=reason,
                    payload={"channel":channel.value,"receipt":attempt["receipt"]})
        return True
