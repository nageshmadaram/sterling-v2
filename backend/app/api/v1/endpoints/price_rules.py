"""Authenticated strategy commands; never a raw order ticket."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, ConfigDict
from typing import Literal
from app.core.auth import UserContext, get_current_user
from app.services import price_rules as runtime, price_rules_store as store
router=APIRouter(prefix="/price-rules",tags=["Price Rules"])

class Draft(BaseModel):
    model_config=ConfigDict(extra="forbid")
    exchange: Literal["NFO","BFO"]="NFO"
    symbol: str=Field(min_length=3,max_length=80)
    lots: int=Field(ge=1,le=30,strict=True)
    stop_points: float=Field(gt=0,allow_inf_nan=False)
    target_points: float=Field(default=0,ge=0,allow_inf_nan=False)
    watch: str=""
    comparison: Literal["MANUAL",">=","<=",">","<"]="MANUAL"
    level: float=Field(default=0,ge=0,allow_inf_nan=False)
    valid_minutes: int=Field(default=60,ge=1,le=480)

class Command(BaseModel):
    revision: int=Field(ge=1)
    action: Literal["arm","pause","cancel"]

class Exit(BaseModel):
    percent: Literal[25,50,75,100]=100

class EnabledCommand(BaseModel):
    model_config=ConfigDict(extra="forbid")
    enabled: bool=Field(strict=True)
    expected_enabled: bool=Field(strict=True)

def uid(user):
    value=getattr(user,"user_id",None) or getattr(user,"uid",None)
    if not value:raise HTTPException(401,"authenticated user required")
    return value

def fail(exc):
    if isinstance(exc,KeyError):return HTTPException(404,"rule not found")
    if isinstance(exc,PermissionError):return HTTPException(403,str(exc))
    if isinstance(exc,ValueError):return HTTPException(409,str(exc))
    return HTTPException(503,str(exc))

@router.get("")
async def snapshot(user:UserContext=Depends(get_current_user)):
    try:
        owner=uid(user);doc=store.load(owner)
        from app.services.safety_supervisor import SafetySupervisor
        account=runtime.accounts.get_active(owner)
        verdict=SafetySupervisor().authorize(uid=owner,account_id=account.id if account else "",strategy_id=runtime.STRATEGY_ID)
        blocker = verdict.reason if not verdict.allowed else ""
        if doc.get("enabled") is not True:
            blocker = "price_rules_disabled"
        if not blocker:
            from app.core.lane_registry import LIVE_EXECUTION_ENABLED
            from app.services.continuity_doctor import egress_status
            from app.services.account_binding_service import active_binding, verification_is_current
            if not account or account.is_paper or owner.startswith("sim:"):
                blocker = "bound_live_account_required"
            elif not LIVE_EXECUTION_ENABLED:
                blocker = "LIVE_EXECUTION_DISABLED"
            elif egress_status().verified is not True:
                blocker = "verified_static_egress_required"
            else:
                binding = active_binding()
                if not binding or not binding.live_ready or not verification_is_current(binding):
                    blocker = "current_live_broker_binding_required"
        return dict(strategy_id=runtime.STRATEGY_ID,name="Price Rules",mode="REAL",naked_shorts_supported=False,
                    enabled=doc.get("enabled") is True,entry_allowed=not blocker,entry_blocker=blocker,rules=list(doc["rules"].values()),revision=doc["revision"])
    except Exception as exc:raise fail(exc) from exc

@router.get("/enabled")
async def get_enabled(user:UserContext=Depends(get_current_user)):
    try:
        doc=store.load(uid(user))
        return {"enabled": doc.get("enabled") is True, "revision": doc["revision"]}
    except Exception as exc:raise fail(exc) from exc

@router.post("/enabled")
async def set_enabled(body:EnabledCommand,user:UserContext=Depends(get_current_user)):
    try:return await runtime.set_enabled(uid(user),body.enabled,body.expected_enabled)
    except Exception as exc:raise fail(exc) from exc

@router.post("/plans")
async def create(body:Draft,user:UserContext=Depends(get_current_user)):
    try:return await runtime.create(uid(user),body.model_dump())
    except Exception as exc:raise fail(exc) from exc

@router.post("/plans/{rule_id}/command")
async def command(rule_id:str,body:Command,user:UserContext=Depends(get_current_user)):
    try:return runtime.command(uid(user),rule_id,body.revision,body.action)
    except Exception as exc:raise fail(exc) from exc

@router.post("/positions/{rule_id}/exit")
async def exit(rule_id:str,body:Exit,user:UserContext=Depends(get_current_user)):
    try:return await runtime.exit_position(uid(user),rule_id,body.percent)
    except Exception as exc:raise fail(exc) from exc
