"""Authenticated EMA trend paper/research surface. No live write route exists."""
from __future__ import annotations

from typing import Literal
from fastapi import APIRouter,Depends,HTTPException,Query
from pydantic import BaseModel,ConfigDict,Field

from app.core.auth import UserContext,get_current_user
from app.engines.ema_trend.config import MODES,PROFILES,canonical_mode,descriptor
from app.engines.ema_trend.contracts import Bar
from app.services.ema_trend import config_store,execution,repository as repo,scanner,snapshot,replay,live_preflight,live_observer

router=APIRouter(prefix="/ema-trend",tags=["ema-trend"])


class Strict(BaseModel):
    model_config=ConfigDict(extra="forbid",allow_inf_nan=False)


class ConfigPatch(Strict):
    expected_revision:int=Field(ge=0)
    changes:dict


class ScanRequest(Strict):
    expected_revision:int=Field(ge=0)
    modes:list[str]|None=None


class PlanRequest(Strict):
    signal_id:str=Field(min_length=1,max_length=160)
    expected_config_revision:int=Field(ge=0)


class ReadinessRequest(Strict):
    symbol:str=Field(min_length=3,max_length=100)
    mode:str
    expected_config_revision:int=Field(ge=0)


class ArmRequest(Strict):
    expected_revision:int=Field(ge=1)
    idempotency_key:str=Field(min_length=1,max_length=160)
    execution_mode:Literal["PAPER"]="PAPER"


class CampaignAction(Strict):
    action:Literal["close","reduce"]
    expected_revision:int=Field(ge=1)
    idempotency_key:str=Field(min_length=1,max_length=160)
    percent:Literal[25,50,100]=100


class ReplayRequest(Strict):
    symbol:str
    mode:str
    expected_config_revision:int=Field(ge=0)
    bars:list[Bar]|None=Field(default=None,max_length=20000)
    context_bars:list[Bar]|None=Field(default=None,max_length=10000)


def uid(user:UserContext):
    value=str(getattr(user,"user_id","") or "").strip()
    if not value:
        raise HTTPException(401,"authenticated owner is required")
    # The authentication dependency may resolve the selected virtual view.
    # Normalize this trusted identity only; payloads never accept uid/account.
    from app.services.simulation.identity import owner_uid
    return owner_uid(value)


def refusal(exc:Exception):
    if isinstance(exc,HTTPException):
        return exc
    if isinstance(exc,repo.NotFound):
        return HTTPException(404,str(exc))
    if isinstance(exc,repo.Conflict):
        return HTTPException(409,str(exc))
    if isinstance(exc,PermissionError):
        return HTTPException(403,str(exc))
    if isinstance(exc,(ValueError,TypeError,KeyError)):
        return HTTPException(422,str(exc))
    return HTTPException(503,f"EMA trend service unavailable: {type(exc).__name__}")


@router.get("/config")
async def get_config(user:UserContext=Depends(get_current_user)):
    try:
        return config_store.response(uid(user))
    except Exception as exc:
        raise refusal(exc) from exc


@router.patch("/config")
async def patch_config(body:ConfigPatch,user:UserContext=Depends(get_current_user)):
    try:
        config_store.update(uid(user),body.expected_revision,body.changes)
        return config_store.response(uid(user))
    except Exception as exc:
        raise refusal(exc) from exc


@router.get("/instruments")
async def instruments(query:str=Query(min_length=2,max_length=80),mode:str="swing",user:UserContext=Depends(get_current_user)):
    try:
        return await scanner.search_instruments(uid(user),query,canonical_mode(mode))
    except Exception as exc:
        raise refusal(exc) from exc


@router.get("/snapshot")
async def get_snapshot(mode:str|None=None,day:str|None=None,cursor:int=Query(0,ge=0),
                       user:UserContext=Depends(get_current_user)):
    try:
        return snapshot.snapshot(uid(user),mode,day,cursor)
    except Exception as exc:
        raise refusal(exc) from exc


@router.get("/datasets/{dataset_id}")
async def get_dataset(dataset_id:str,user:UserContext=Depends(get_current_user)):
    try:
        return {**repo.get(uid(user),"dataset",dataset_id),"read_only":True}
    except Exception as exc:
        raise refusal(exc) from exc


@router.post("/scan")
async def post_scan(body:ScanRequest,user:UserContext=Depends(get_current_user)):
    try:
        return scanner.start_scan(uid(user),body.modes,body.expected_revision)
    except Exception as exc:
        raise refusal(exc) from exc


@router.get("/scans/{job_id}")
async def get_scan(job_id:str,user:UserContext=Depends(get_current_user)):
    try:
        return repo.get(uid(user),"scan",job_id)
    except Exception as exc:
        raise refusal(exc) from exc


@router.post("/scans/{job_id}/cancel")
async def cancel_scan(job_id:str,user:UserContext=Depends(get_current_user)):
    try:
        return scanner.cancel_scan(uid(user),job_id)
    except Exception as exc:
        raise refusal(exc) from exc


@router.post("/plans")
async def post_plan(body:PlanRequest,user:UserContext=Depends(get_current_user)):
    try:
        return await execution.build_plan(uid(user),body.signal_id,body.expected_config_revision)
    except Exception as exc:
        raise refusal(exc) from exc


@router.post("/plans/{plan_id}/arm")
async def arm_plan(plan_id:str,body:ArmRequest,user:UserContext=Depends(get_current_user)):
    try:
        return await execution.arm(uid(user),plan_id,body.expected_revision,body.idempotency_key,body.execution_mode)
    except Exception as exc:
        raise refusal(exc) from exc


@router.get("/history")
async def history(cursor:int=Query(0,ge=0),limit:int=Query(100,ge=1,le=500),
                  user:UserContext=Depends(get_current_user)):
    try:
        owner=uid(user)
        items=repo.list_documents(owner,"signal",limit=limit,cursor=cursor)
        return {"items":items,"events":repo.events(owner,limit=limit,cursor=cursor),
                "campaigns":[execution.public_campaign(c) for c in repo.list_documents(owner,"campaign",limit=limit,cursor=cursor)],
                "next_cursor":cursor+limit if len(items)==limit else None,"read_only":True}
    except Exception as exc:
        raise refusal(exc) from exc


@router.get("/campaigns/{campaign_id}")
async def campaign(campaign_id:str,user:UserContext=Depends(get_current_user)):
    try:
        owner=uid(user)
        return {"campaign":execution.public_campaign(repo.get(owner,"campaign",campaign_id)),
                "events":repo.events(owner,campaign_id)}
    except Exception as exc:
        raise refusal(exc) from exc


@router.get("/campaigns/{campaign_id}/evidence")
async def campaign_evidence(campaign_id:str,user:UserContext=Depends(get_current_user)):
    from app.services.ema_trend.evidence import campaign_trace
    try:
        return campaign_trace(uid(user),campaign_id)
    except Exception as exc:
        raise refusal(exc) from exc


@router.post("/campaigns/{campaign_id}/actions")
async def campaign_action(campaign_id:str,body:CampaignAction,user:UserContext=Depends(get_current_user)):
    try:
        return await execution.close_campaign(uid(user),campaign_id,body.expected_revision,
              body.idempotency_key,100 if body.action=="close" else body.percent)
    except Exception as exc:
        raise refusal(exc) from exc


@router.post("/operations/pause")
async def pause(user:UserContext=Depends(get_current_user)):
    try:
        return config_store.pause(uid(user),True)
    except Exception as exc:
        raise refusal(exc) from exc


@router.post("/operations/resume")
async def resume(user:UserContext=Depends(get_current_user)):
    try:
        owner=uid(user)
        # A prior flatten is an emergency stop request. Resuming entries
        # while any attributable shares or disputed orders remain would allow
        # exposure to grow before the close has been verified.
        for campaign in repo.list_documents(owner,"campaign",limit=2000):
            if campaign.get("flatten_requested") and (
                sum(int(lot.get("qty",0)) for lot in campaign.get("lots",[]))>0
                or campaign.get("pending") or campaign.get("pending_close")
                or campaign.get("state")!="CLOSED"
            ):
                raise repo.Conflict("FLATTEN_RECONCILIATION_PENDING_RESUME_BLOCKED")
        return config_store.pause(owner,False)
    except Exception as exc:
        raise refusal(exc) from exc


@router.post("/operations/flatten")
async def flatten(user:UserContext=Depends(get_current_user)):
    try:
        owner=uid(user)
        config_store.pause(owner,True)
        results=[]
        for c in repo.list_documents(owner,"campaign",limit=2000):
            # A campaign can have zero confirmed shares but an outstanding
            # BUY with unknown final status. It is not safely flattened.
            if c["state"]=="CLOSED" and not (c.get("pending") or c.get("pending_close")):
                continue
            try:
                results.append(await execution.close_campaign(owner,c["id"],c["revision"],f"flatten:{c['id']}:{c['revision']}"))
            except Exception as exc:
                results.append({"campaign_id":c["id"],"status":"UNRESOLVED","reason":str(exc)})
        return {"status":"UNRESOLVED" if any(r.get("status")=="UNRESOLVED" for r in results) else "REQUESTED", "results":results}
    except Exception as exc:
        raise refusal(exc) from exc


@router.get("/live-readiness")
async def live_readiness(user:UserContext=Depends(get_current_user)):
    """Read only: verify active real Kite identity without granting order rights."""
    try:
        return await live_preflight.inspect(uid(user))
    except Exception as exc:
        raise refusal(exc) from exc


@router.get("/live-order-observations/{intent_key}")
async def live_order_observation(intent_key:str,user:UserContext=Depends(get_current_user)):
    """Observe only an owned canonical EMA live intent; never submit or retry."""
    try:
        return await live_observer.observe_live_ema_order(uid(user),intent_key)
    except Exception as exc:
        raise refusal(exc) from exc


@router.get("/validation")
async def validation(user:UserContext=Depends(get_current_user)):
    owner=uid(user)
    return {"status":"INCONCLUSIVE","paper_only":True,"live_capable":False,
            "profiles":{mode:{"status":"INCONCLUSIVE","live_capable":False} for mode in MODES},
            "blockers":descriptor()["blockers"]+["PAPER_MONITOR_ONLY","CORPORATE_ACTION_POLICY_UNVERIFIED"],
            "fee_schedule":config_store.config(owner).fee_schedule}


@router.post("/readiness/check")
async def check_readiness(body:ReadinessRequest,user:UserContext=Depends(get_current_user)):
    from app.services.ema_trend.readiness import check
    try:
        return await check(uid(user),body.symbol,canonical_mode(body.mode),body.expected_config_revision)
    except Exception as exc:
        raise refusal(exc) from exc


@router.get("/chart")
async def chart(symbol:str,mode:str,signal_id:str|None=None,expected_config_revision:int|None=None,
                user:UserContext=Depends(get_current_user)):
    from app.engines.ema_trend.features import aggregate_completed_bars,build_features
    try:
        owner=uid(user)
        selected=canonical_mode(mode)
        cutoff=None
        revision=config_store.get(owner)["revision"]
        if signal_id:
            source=repo.get(owner,"signal",signal_id)
            if source["symbol"]!=symbol or source["mode"]!=selected:
                raise repo.Conflict("CHART_SOURCE_IDENTITY_MISMATCH")
            if expected_config_revision is not None and source["config_revision"]!=expected_config_revision:
                raise repo.Conflict("CHART_SOURCE_CONFIG_MISMATCH")
            cutoff=int(source["close_ms"])
            revision=source["config_revision"]
        tape=repo.get(owner,"tape",f"{symbol}:{selected}:signal")
        context=repo.get(owner,"tape",f"{symbol}:{selected}:context")
        bars=[Bar.model_validate(b) for b in tape["bars"]]
        if cutoff is not None:
            bars=[b for b in bars if b.close_ms<=cutoff]
        cx=[Bar.model_validate(b) for b in context["bars"]]
        if selected=="swing":
            cx=aggregate_completed_bars(cx,10080,repo.now_ms(),exchange=symbol.split(":")[0])
        features=build_features(bars,cx)
        return {"symbol":symbol,"mode":selected,"features":[f.model_dump(mode="json") for f in features],
                "blockers":[] if features and features[-1].ready else ["WARMING_UP"],"read_only":True,
                "signal_id":signal_id,"config_revision":revision,"source_close_ms":cutoff}
    except repo.NotFound:
        return {"symbol":symbol,"mode":mode,"features":[],"blockers":["SCAN_TAPE_UNAVAILABLE"],"read_only":True}
    except Exception as exc:
        raise refusal(exc) from exc


@router.post("/replay")
async def post_replay(body:ReplayRequest,user:UserContext=Depends(get_current_user)):
    try:
        owner=uid(user)
        mode=canonical_mode(body.mode)
        bars=body.bars
        context=body.context_bars
        if bars is None:
            bars=[Bar.model_validate(b) for b in repo.get(owner,"tape",f"{body.symbol}:{mode}:signal")["bars"]]
        if context is None:
            context=[Bar.model_validate(b) for b in repo.get(owner,"tape",f"{body.symbol}:{mode}:context")["bars"]]
            if mode=="swing":
                from app.engines.ema_trend.features import aggregate_completed_bars
                context=aggregate_completed_bars(context,10080,repo.now_ms(),exchange=body.symbol.split(":")[0])
        return replay.start(owner,body.symbol,mode,bars,context,body.expected_config_revision)
    except Exception as exc:
        raise refusal(exc) from exc


@router.get("/runs")
async def runs(user:UserContext=Depends(get_current_user)):
    try:
        return {"items":[{k:v for k,v in r.items() if k not in {"bars","context_bars"}} for r in repo.list_documents(uid(user),"run",limit=100)]}
    except Exception as exc:
        raise refusal(exc) from exc


@router.get("/runs/{run_id}")
async def run(run_id:str,user:UserContext=Depends(get_current_user)):
    try:
        return {k:v for k,v in repo.get(uid(user),"run",run_id).items() if k not in {"bars","context_bars"}}
    except Exception as exc:
        raise refusal(exc) from exc


@router.get("/export")
async def export(user:UserContext=Depends(get_current_user)):
    try:
        return snapshot.export(uid(user))
    except Exception as exc:
        raise refusal(exc) from exc
