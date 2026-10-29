"""Strictly read-only Phase 6 decision evidence API for operator/admin UI.

Requires a verified Sterling access JWT even in development; does not use the
historical anonymous development identity. No POST, no order or scheduling API.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated
from fastapi import APIRouter, Depends, Header, HTTPException, Query

from app.persistence.base import make_engine
from app.persistence.session import make_session_factory
from app.persistence.offline_decision_journal_service import OperatorDecisionJournal
from app.persistence.offline_reconciliation import OfflineReconciler

router=APIRouter(prefix="/offline-validation", tags=["offline-validation-read-only"])


def _signed_token(authorization: Annotated[str | None, Header()] = None) -> str:
    if not authorization or not authorization.startswith("Bearer ") or not authorization[7:].strip():
        raise HTTPException(status_code=401,detail="Signed Sterling bearer token required")
    return authorization[7:].strip()


def _read_service(operation,*,token,strategy_id,**args):
    engine=make_engine()
    try:
        factory=make_session_factory(engine)
        return operation(factory,token,strategy_id,**args)
    except (PermissionError,LookupError) as exc:
        # Deliberately avoid leaking tenant existence or internal ledger paths.
        raise HTTPException(status_code=403,detail="Offline evidence access refused") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422,detail="Invalid evidence query or record") from exc
    finally:
        engine.dispose()


@router.get("/strategies/{strategy_id}/decisions")
def decisions(strategy_id:str,limit:int=Query(default=100,ge=1,le=500),
              token:str=Depends(_signed_token)):
    def get(factory,token,strategy_id,**kw):
        return OperatorDecisionJournal(factory).decisions(
            access_token=token,strategy_id=strategy_id,limit=kw["limit"])
    return {"strategy_id":strategy_id,"read_only":True,"execution_authorized":False,
            "items":_read_service(get,token=token,strategy_id=strategy_id,limit=limit)}


@router.get("/strategies/{strategy_id}/reconciliation")
def reconciliation(strategy_id:str,limit:int=Query(default=100,ge=1,le=500),
                   token:str=Depends(_signed_token)):
    def get(factory,token,strategy_id,**kw):
        return OfflineReconciler(factory).inspect(
            access_token=token,strategy_id=strategy_id,
            at=datetime.now(timezone.utc),limit=kw["limit"])
    return _read_service(get,token=token,strategy_id=strategy_id,limit=limit)
