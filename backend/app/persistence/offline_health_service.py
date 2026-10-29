"""Operator-invoked immutable OFFLINE health snapshots.

Never probes broker API, creates orders, starts workers or concludes production
readiness. Evidence is independently labeled unverifiable when not sourced.
"""
import hashlib
import json
import time
from uuid import uuid4
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker
from app.persistence.offline_health_models import PlatformOfflineHealthEventRow
from app.persistence import strategy_registry_service as registry
from app.persistence.offline_scheduler_service import _read as read_queue
from app.persistence.offline_alert_service import _events as read_alerts
from app.persistence.execution_validation_service import _lock
from app.persistence.session import session_scope

ZERO="0"*64

def _canonical(payload): return json.dumps(payload,sort_keys=True,separators=(",",":"),allow_nan=False)
def _sha(payload): return hashlib.sha256(_canonical(payload).encode()).hexdigest()

def _read(db,strategy_id):
    rows=list(db.scalars(select(PlatformOfflineHealthEventRow).where(
        PlatformOfflineHealthEventRow.strategy_id==strategy_id
    ).order_by(PlatformOfflineHealthEventRow.sequence)))
    prev=ZERO
    for i,row in enumerate(rows,1):
        doc={"id":row.id,"strategy_id":row.strategy_id,"sequence":row.sequence,
             "actor_id":row.actor_id,"actor_role":row.actor_role,
             "report":json.loads(row.report_json),"at_ms":row.occurred_at_ms,
             "previous":prev,"execution_authorized":False}
        if row.sequence!=i or row.previous_sha256!=prev or row.execution_authorized is not False or _sha(doc)!=row.content_sha256:
            raise PermissionError("offline health audit chain invalid")
        prev=row.content_sha256
    return rows


class OfflineHealthRecorder:
    def __init__(self,sessions:sessionmaker): self.sessions=sessions

    def sample_once(self,*,access_token:str,strategy_id:str)->str:
        actor=registry._identity(access_token)
        if actor.role!="admin": raise PermissionError("health capture requires administrator")
        with session_scope(self.sessions) as db:
            _lock(db)
            registry._strategy_readonly(db,actor,strategy_id)
            old=_read(db,strategy_id)
            try:
                queue=read_queue(db,strategy_id)
                queue_status="verified_offline"
                queue_count=len(queue)
            except (PermissionError,ValueError):
                queue_status="untrusted_offline"
                queue_count=None
            try:
                alerts=read_alerts(db,strategy_id)
                alert_status="verified_offline"
                alert_count=len(alerts)
            except (PermissionError,ValueError):
                alert_status="untrusted_offline"
                alert_count=None
            data={"app":"offline_observation_only","database":"connected",
                  "broker":"not_connected","broker_exposure":"unknown",
                  "market_data":"not_independently_probed",
                  "worker":"disabled","queue":queue_status,
                  "queue_audited_event_count":queue_count,
                  "alerts":alert_status,"alert_audited_event_count":alert_count,
                  "telegram":"no_provider_receipt",
                  "execution_authorized":False,
                  "production_ready":False}
            number=len(old)+1
            stamp=int(time.time()*1000)
            doc={"id":str(uuid4()),"strategy_id":strategy_id,"sequence":number,
                 "actor_id":actor.user_id,"actor_role":actor.role,
                 "report":data,"at_ms":stamp,
                 "previous":old[-1].content_sha256 if old else ZERO,
                 "execution_authorized":False}
            db.add(PlatformOfflineHealthEventRow(
                id=doc["id"],strategy_id=strategy_id,sequence=number,
                actor_id=actor.user_id,actor_role=actor.role,
                report_json=_canonical(data),occurred_at_ms=stamp,
                previous_sha256=doc["previous"],content_sha256=_sha(doc),
                execution_authorized=False))
            db.flush()
            return doc["id"]

    def read(self,*,access_token:str,strategy_id:str,limit:int=50):
        if not 1<=limit<=100: raise ValueError("bounded audit history required")
        actor=registry._identity(access_token)
        with session_scope(self.sessions) as db:
            registry._strategy_readonly(db,actor,strategy_id)
            rows=_read(db,strategy_id)
            return {"read_only":True,"execution_authorized":False,
                    "source":"manually_sampled_offline_health",
                    "snapshots":[{"event_id":r.id,"at_ms":r.occurred_at_ms,
                         "actor_id":r.actor_id,"audit_ref":r.content_sha256,
                         "status":json.loads(r.report_json),
                         "execution_authorized":False} for r in rows[-limit:][::-1]]}
