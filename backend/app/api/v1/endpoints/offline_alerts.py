"""Phase 9: signed read endpoints + tightly limited metadata-only alert ack/resolve.

The only POSTs append audit acknowledgements or administrator resolution.
NO alert creation, transport send, scheduling, broker or trading HTTP routes.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from app.api.v1.endpoints.offline_validation_journal import _signed_token
from app.persistence.base import make_engine
from app.persistence.session import make_session_factory
from app.persistence.offline_alert_service import OfflineAlertService

router=APIRouter(prefix="/offline-validation",tags=["offline-alerts-informational"])


class MetadataReason(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    reason: str=Field(min_length=4,max_length=256)


def _invoke(operation,token):
    engine=make_engine()
    try:
        service=OfflineAlertService(make_session_factory(engine))
        return operation(service,token)
    except (PermissionError,LookupError) as exc:
        raise HTTPException(status_code=403,detail="Offline alert access denied") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422,detail="Invalid offline alert request") from exc
    finally:
        engine.dispose()


@router.get("/strategies/{strategy_id}/alerts")
def alerts(strategy_id:str,token:str=Depends(_signed_token),
           limit:int=Query(default=100,ge=1,le=500),
           unresolved_only:bool=False):
    items=_invoke(lambda svc,key:svc.list(access_token=key,strategy_id=strategy_id,
                 limit=limit,unresolved_only=unresolved_only),token)
    return {"read_only":True,"execution_authorized":False,"source":"offline_alert_ledger",
            "items":items}


@router.post("/strategies/{strategy_id}/alerts/{alert_id}/acknowledge")
def acknowledge(strategy_id:str,alert_id:str,body:MetadataReason,
                token:str=Depends(_signed_token)):
    result=_invoke(lambda svc,key:svc.change_state(
        access_token=key,strategy_id=strategy_id,alert_id=alert_id,
        action="acknowledged",reason=body.reason),token)
    return {**result,"execution_authorized":False,"mutation_scope":"alert_metadata_only"}


@router.post("/strategies/{strategy_id}/alerts/{alert_id}/resolve")
def resolve(strategy_id:str,alert_id:str,body:MetadataReason,
            token:str=Depends(_signed_token)):
    # Server-side admin-only policy enforced in OfflineAlertService.
    result=_invoke(lambda svc,key:svc.change_state(
        access_token=key,strategy_id=strategy_id,alert_id=alert_id,
        action="resolved",reason=body.reason),token)
    return {**result,"execution_authorized":False,"mutation_scope":"alert_metadata_only"}
