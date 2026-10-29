"""Strict signed-JWT GET-only offline hypothetical KPI surface.

Never reads live trade journals or broker orders, never creates evidence,
never returns an execution-admission signal. This is operator display data.
"""
from __future__ import annotations
from decimal import Decimal
from typing import Annotated, Literal
from fastapi import APIRouter, Depends, Header, HTTPException, Query
from app.api.v1.endpoints.offline_validation_journal import _signed_token
from app.persistence.base import make_engine
from app.persistence.session import make_session_factory
from app.persistence.offline_performance_service import OfflinePerformanceService

router=APIRouter(prefix="/offline-validation/analytics",
                 tags=["offline-hypothetical-analytics-read-only"])


def _read(operation,token):
    engine=make_engine()
    try:
        svc=OfflinePerformanceService(make_session_factory(engine))
        return operation(svc,token)
    except (PermissionError,LookupError) as exc:
        raise HTTPException(status_code=403,detail="Offline analytics access denied") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422,detail="Invalid analytical record or filter") from exc
    finally:
        engine.dispose()


def _filters(version_id,symbol,regime,approval_mode,date_from,date_to):
    if date_from and date_to and date_from>date_to:
        raise ValueError("date_from must not exceed date_to")
    return dict(version_id=version_id,symbol=symbol,regime=regime,
                approval_mode=approval_mode,date_from=date_from,date_to=date_to)


def _query(strategy_id, token, version_id, symbol, regime, approval_mode,
           date_from, date_to, starting_equity_inr, rolling_window,
           group_by=None, details=False):
    filters=_filters(version_id,symbol,regime,approval_mode,date_from,date_to)
    def run(svc,token):
        if details:
            rows=svc.trades(access_token=token,strategy_id=strategy_id,**filters)
            return {"strategy_id":strategy_id,"records":[r.model_dump(mode="json") for r in rows],
                    "record_count":len(rows),"read_only":True,
                    "metric_status":"hypothetical_not_verified_live_profitability",
                    "execution_authorized":False}
        if group_by is not None:
            return svc.comparison(access_token=token,strategy_id=strategy_id,
                                  group_by=group_by,starting_equity_inr=starting_equity_inr,
                                  rolling_window=rolling_window,**filters)
        return svc.report(access_token=token,strategy_id=strategy_id,
                          starting_equity_inr=starting_equity_inr,
                          rolling_window=rolling_window,**filters)
    return _read(run,token)


# All filters are *offline* evidence filters; no live order/performance endpoint.
class _Unused:  # avoid Pydantic DTO mutation or HTTP commands
    pass


@router.get("/strategies/{strategy_id}/dashboard")
@router.get("/strategies/{strategy_id}/summary")
@router.get("/strategies/{strategy_id}/timeline")
@router.get("/strategies/{strategy_id}/risk")
def summary(
    strategy_id: str,
    token: str=Depends(_signed_token),
    version_id: str|None=None,
    symbol: str|None=None,
    regime: Literal["trending","ranging","volatile","low_liquidity","unknown"]|None=None,
    approval_mode: Literal["auto","semi_auto","manual","blocked"]|None=None,
    date_from: str|None=Query(default=None,pattern=r"^\d{4}-\d{2}-\d{2}$"),
    date_to: str|None=Query(default=None,pattern=r"^\d{4}-\d{2}-\d{2}$"),
    starting_equity_inr: Decimal|None=Query(default=None,gt=0),
    rolling_window: int=Query(default=5,ge=2,le=200),
):
    return _query(strategy_id,token,version_id,symbol,regime,approval_mode,
                  date_from,date_to,starting_equity_inr,rolling_window)


@router.get("/strategies/{strategy_id}/comparison")
@router.get("/strategies/{strategy_id}/symbols")
def breakdown(
    strategy_id: str,
    token: str=Depends(_signed_token),
    group_by: Literal["strategy","version","symbol","regime","approval_mode"]="symbol",
    version_id: str|None=None,symbol: str|None=None,
    regime: Literal["trending","ranging","volatile","low_liquidity","unknown"]|None=None,
    approval_mode: Literal["auto","semi_auto","manual","blocked"]|None=None,
    date_from: str|None=Query(default=None,pattern=r"^\d{4}-\d{2}-\d{2}$"),
    date_to: str|None=Query(default=None,pattern=r"^\d{4}-\d{2}-\d{2}$"),
    starting_equity_inr: Decimal|None=Query(default=None,gt=0),
    rolling_window: int=Query(default=5,ge=2,le=200),
):
    return _query(strategy_id,token,version_id,symbol,regime,approval_mode,
                  date_from,date_to,starting_equity_inr,rolling_window,group_by)


@router.get("/strategies/{strategy_id}/trades")
def synthetic_trades(strategy_id:str,token:str=Depends(_signed_token),
                     version_id:str|None=None,symbol:str|None=None,
                     regime:Literal["trending","ranging","volatile","low_liquidity","unknown"]|None=None,
                     approval_mode:Literal["auto","semi_auto","manual","blocked"]|None=None,
                     date_from:str|None=Query(default=None,pattern=r"^\d{4}-\d{2}-\d{2}$"),
                     date_to:str|None=Query(default=None,pattern=r"^\d{4}-\d{2}-\d{2}$")):
    return _query(strategy_id,token,version_id,symbol,regime,approval_mode,
                  date_from,date_to,None,5,details=True)


@router.get("/compare-strategies")
def compare_strategies(
    strategy_ids: list[str]=Query(min_length=1,max_length=10),
    token:str=Depends(_signed_token),
    starting_equity_inr:Decimal|None=Query(default=None,gt=0),
    rolling_window:int=Query(default=5,ge=2,le=200),
):
    if len(set(strategy_ids))!=len(strategy_ids):
        raise HTTPException(status_code=422,detail="duplicate strategies are not allowed")
    def run(svc,token):
        return {"read_only":True,"execution_authorized":False,
                "metric_status":"hypothetical_not_verified_live_profitability",
                "strategies":{sid:svc.report(
                    access_token=token,strategy_id=sid,
                    starting_equity_inr=starting_equity_inr,
                    rolling_window=rolling_window) for sid in sorted(strategy_ids)}}
    return _read(run,token)
