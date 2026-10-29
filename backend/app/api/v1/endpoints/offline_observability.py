"""Phase 8 read-only offline strategy catalogue and risk-state evidence.

Not a trading readiness endpoint: no green operational status, no broker
account access, no order actions. Signed Sterling session and owner scoping.
"""
from __future__ import annotations

from typing import Annotated
from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select

from app.api.v1.endpoints.offline_validation_journal import _signed_token
from app.persistence.base import make_engine
from app.persistence.session import make_session_factory, session_scope
from app.persistence.strategy_registry_models import PlatformStrategyRow, PlatformStrategyVersionRow
from app.persistence import strategy_registry_service as registry
from app.persistence.execution_validation_service import _read as read_gate, _state as gate_state
from app.persistence.offline_scheduler_service import _read as read_schedule_events, _projection as schedules_projection

router=APIRouter(prefix="/offline-validation",tags=["offline-validation-read-only"])


def _read_view(fn, token):
    engine=make_engine()
    try:
        with session_scope(make_session_factory(engine)) as db:
            actor=registry._identity(token)
            return fn(db,actor)
    except (PermissionError,LookupError,ValueError,KeyError) as exc:
        raise HTTPException(status_code=403,detail="Offline observability access denied") from exc
    finally:
        engine.dispose()


@router.get("/strategies")
def strategy_catalog(token:str=Depends(_signed_token)):
    def read(db,actor):
        # Intentional least-privilege: even admins see only their own
        # catalogue here; explicit per-strategy ownership is still checked.
        rows=list(db.scalars(select(PlatformStrategyRow)
            .where(PlatformStrategyRow.owner_id==actor.user_id)
            .order_by(PlatformStrategyRow.display_name,PlatformStrategyRow.id)
            .limit(200)))
        return {"read_only":True,"execution_authorized":False,
                "source":"offline_versioned_registry",
                "strategies":[{"id":row.id,"name":row.display_name,
                    "state":row.state,"locked":bool(row.is_locked),
                    "owner_scoped":True} for row in rows]}
    return _read_view(read,token)


@router.get("/strategies/{strategy_id}/status")
def offline_strategy_status(strategy_id:str,token:str=Depends(_signed_token)):
    def read(db,actor):
        strategy=registry._strategy_readonly(db,actor,strategy_id)
        # No historical 'success' or simulation decision implies broker
        # permission; both independent Phase 4 gate streams are verified.
        global_state,global_mode=gate_state(read_gate(db,None))
        strategy_state,strategy_mode=gate_state(read_gate(db,strategy_id))
        schedule_events=read_schedule_events(db,strategy_id)
        schedules,items,paused_flags=schedules_projection(schedule_events)
        cancelled={row.schedule_id for row in schedule_events if row.kind=="schedule_cancelled"}
        schedule_views=[{
            "id":spec.schedule_id,"kind":spec.kind.value,
            "start_at_utc":spec.start_at_utc.isoformat(),
            "end_at_utc":spec.end_at_utc.isoformat() if spec.end_at_utc else None,
            "max_occurrences":spec.max_occurrences,"cadence_seconds":spec.cadence_seconds,
            "priority":spec.priority,
            "state":"cancelled" if spec.schedule_id in cancelled else
                    "paused" if paused_flags.get(spec.schedule_id,False) else "configured",
            "materialized_occurrences":sum(
                1 for item in items.values() if item.identity.schedule_id==spec.schedule_id),
            "execution_authorized":False,
        } for spec,_,_,_ in schedules.values()]
        versions=list(db.scalars(select(PlatformStrategyVersionRow)
            .where(PlatformStrategyVersionRow.strategy_id==strategy_id)
            .order_by(PlatformStrategyVersionRow.created_at_ms.desc()).limit(100)))
        for version in versions:
            registry._version(db,version.id)  # verifies immutable SHA256
        return {"read_only":True,"execution_authorized":False,
            "registry":{"id":strategy.id,"name":strategy.display_name,
                        "state":strategy.state,"locked":bool(strategy.is_locked)},
            "offline_gate":{"system_state":global_state.value,
                "system_approval_mode":global_mode.value,
                "strategy_state":strategy_state.value,
                "strategy_approval_mode":strategy_mode.value,
                "live_execution_enabled":False,
                "scope":"offline_validation_only"},
            "strategy_health":{"score":None,
                 "status":"not_independently_attested",
                 "reason":"No authenticated live strategy-health score in this offline slice"},
            "versions":[{"id":v.id,"label":v.version,"sha256":v.content_sha256}
                        for v in versions],
            "schedules":schedule_views}
    return _read_view(read,token)
