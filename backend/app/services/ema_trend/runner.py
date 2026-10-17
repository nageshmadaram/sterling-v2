"""Unattended virtual campaign management, independent of scanning/AUTO.

Two-second quote supervision is a PAPER monitor. It cannot guarantee a stop
through a missing feed/process. This limitation remains in every API snapshot.
"""
from __future__ import annotations
import asyncio
from datetime import datetime
from decimal import Decimal,ROUND_CEILING,ROUND_FLOOR
import logging

from app.engines.ema_trend.config import EmaTrendConfig,PROFILES,stable_id
from app.engines.ema_trend.contracts import Bar
from app.engines.ema_trend.features import build_features,aggregate_completed_bars
from app.engines.ema_trend.state_machine import decide,make_action,propose_stop
from app.services.execution_service import CanonicalExecutionService
from . import config_store,execution,repository as repo,scanner,health
from datetime import date

log=logging.getLogger(__name__)
_task=None
_locks:dict[tuple[int,str],asyncio.Lock]={}
_last_scan:dict[str,int]={}
_last_management_scan:dict[tuple[str,str,str],int]={}


def _features(uid,c):
    signal=repo.get(uid,"tape",f"{c.symbol}:{c.mode}:signal")
    context=repo.get(uid,"tape",f"{c.symbol}:{c.mode}:context")
    bars=[Bar.model_validate(b) for b in signal["bars"]]
    cx=[Bar.model_validate(b) for b in context["bars"]]
    if PROFILES[c.mode].context_minutes==10080:
        cx=aggregate_completed_bars(cx,10080,repo.now_ms(),exchange=c.symbol.split(":")[0])
    return build_features(bars,cx)


async def manage_campaign(uid,document,client):
    c=execution.campaign_model(document)
    if c.state=="CLOSED" and not (c.pending or c.pending_close):
        return
    profile=PROFILES[c.mode]
    config=EmaTrendConfig.model_validate({k:v for k,v in c.config_snapshot.items() if k in EmaTrendConfig.model_fields})
    now=datetime.now(scanner.IST)
    row=None
    # Emergency reductions have a separate durable slot. They cannot erase or
    # wait forever on an uncertain outstanding buy.
    pending_ids=[a.id for a in (c.pending_close,c.pending) if a]
    for pending_id in pending_ids:
        row=repo.get(uid,"outbox",pending_id)
        if row["status"]=="RESERVED":
            pending_action=next((a for a in (c.pending,c.pending_close) if a and a.id==pending_id),None)
            settings=config_store.get(uid)
            expired=repo.now_ms()>int(row.get("expires_ms",repo.now_ms()+1))
            if pending_action and pending_action.side=="BUY" and (c.flatten_requested or expired or settings.get("paused") or
                    (pending_action.kind=="ENTRY" and settings["revision"]!=c.config_revision)):
                pending_action.status="CANCELLED"
                if c.pending and c.pending.id==pending_id:
                    c.pending=None
                if pending_action.kind=="ENTRY":
                    from app.engines.ema_trend.state_machine import finalize_entry
                    finalize_entry(c)
                elif c.state!="CLOSING":
                    c.state="OPEN"
                repo.save_projection(uid,execution.campaign_document(c),document["revision"],
                    {**row,"status":"CANCELLED","action":pending_action.model_dump(mode="json")},row["revision"])
                document=repo.get(uid,"campaign",c.id)
                c=execution.campaign_model(document)
                continue
            await execution.submit_action(uid,c.id,pending_id,client=client)
        await execution.reconcile_action(uid,pending_id,client=client)
        document=repo.get(uid,"campaign",c.id)
        c=execution.campaign_model(document)
        if not c.qty and c.state=="CLOSED" and not (c.pending or c.pending_close):
            return
    # A RESERVED buy cancelled above can close the campaign without reaching
    # the reconciliation branch. Do not request market data after that closure.
    if not c.qty and c.state=="CLOSED" and not (c.pending or c.pending_close):
        return
    # An independently reserved emergency SELL may survive after its sibling
    # BUY is resolved. Reconcile that SELL before considering any new exit.
    # Otherwise another close could oversell the same confirmed inventory.
    if c.pending_close:
        return
    # Query current touch even if signals/scans/AUTO have been disabled.
    book=await execution.quote(uid,c.symbol,c.mode)
    meta=await scanner.equity_metadata(uid,c.symbol)
    c.last_mark=Decimal(str(book.ltp))
    execution.record_mark(uid,c.id,book.bid,at_ms=int(book.quote_ts*1000))
    entry_actions=[r for r in repo.list_documents(uid,"outbox",limit=2000)
                   if r["campaign_id"]==c.id and r["action"]["kind"]=="ENTRY"]
    entry_day=datetime.fromtimestamp(entry_actions[0]["action"]["at_ms"]/1000,scanner.IST).date() if entry_actions else now.date()
    force_close=bool(profile.force_close and (now.strftime("%H:%M")>=profile.force_close or now.date()>entry_day))
    stop_due=c.qty>0 and Decimal(str(book.bid))<=c.stop
    current_config=config_store.config(uid)
    risk_codes=execution._risk_blockers(uid,current_config)
    risk_due=any(code in {"DAILY_LOSS_BREAKER","ALLOCATION_DRAWDOWN_BREAKER"} for code in risk_codes)
    if c.expiry and now.date()>=date.fromisoformat(c.expiry) and c.qty:
        c.state,c.reason="CLOSING","RISK_EXIT"
    closing_due=(c.state=="CLOSING" or c.flatten_requested) and c.qty>0
    if c.pending:
        row=repo.get(uid,"outbox",c.pending.id)
        expired=repo.now_ms()>int(row.get("expires_ms",c.pending.at_ms+profile.signal_minutes*60000))
        reprice_close=(c.pending.side=="SELL" and c.state=="CLOSING" and Decimal(str(book.bid))<c.pending.limit)
        if row.get("order_id") and ((c.pending.side=="BUY" and (expired or c.flatten_requested))
                or stop_due or force_close or risk_due or closing_due or reprice_close):
            result=await CanonicalExecutionService().cancel_order(uid=c.uid,account_id=c.account_id,
                order_id=row["order_id"],broker_client=client)
            if result.status=="UNKNOWN":
                c.state,c.reason="RECOVERY_REQUIRED","UNKNOWN_CANCELLATION"
                saved=repo.save_campaign_action(uid,execution.campaign_document(c),document["revision"])
                document=saved
                # Continue to close positively confirmed owned shares. Any
                # subsequent buy fill remains owned and will be closed too.
            else:
                await execution.reconcile_action(uid,c.pending.id,client=client)
                document=repo.get(uid,"campaign",c.id)
                c=execution.campaign_model(document)
        if (c.pending_close or (c.pending and c.pending.side=="SELL")
                or (c.pending and not(stop_due or force_close or risk_due or closing_due))):
            return
    action=None
    closing_due=(c.state=="CLOSING" or c.flatten_requested) and c.qty>0
    if c.qty and (stop_due or force_close or risk_due or closing_due):
        action=make_action(c,"STOP" if stop_due else "SESSION_EXIT" if force_close else "FLATTEN" if c.flatten_requested else c.reason if closing_due and c.reason in {"ENTRY_TOO_SMALL","RISK_EXIT","FLATTEN","STRUCTURAL_EXIT"} else "RISK_EXIT",
            "SELL",c.qty,c.last_index,repo.now_ms(),Decimal(str(book.bid)))
    else:
        settings=config_store.get(uid)
        from app.services.kite_engine import state as shared_state
        allowed_add=(settings["config"]["enabled"] and settings["config"]["auto_execute"]
                     and bool(shared_state.get_config(c.uid).auto_execute)
                     and not settings.get("paused") and c.mode in settings["config"]["modes"]
                     and c.symbol in settings["config"]["symbols"] and not risk_codes)
        try:
            features=_features(uid,c)
        except repo.NotFound:
            features=[]
        for i,f in enumerate(features):
            if f.bar.close_ms<=c.last_close_ms:
                continue
            action=decide(c,f,features[i-1] if i else None,profile,config,allow_add=allowed_add)
            if action:
                # Signal limits are not fills. Market execution uses subsequent
                # current touch and respects BUY ceiling; sells do not wait at
                # a stale historical close during a deteriorating market.
                if action.side=="BUY" and Decimal(str(book.ask))>action.limit:
                    c.pending=None
                    c.state="OPEN"
                    c.reason="CURRENT_PRICE_EXCEEDS_ADD_CEILING"
                    action=None
                    continue
                break
            proposed=propose_stop(c,f,Decimal(str(meta.tick_size)))
            if proposed is not None:
                prior=c.stop
                c.stop=proposed
                repo.event(uid,stable_id(c.id,f.bar.close_ms,"paper-stop"),c.id,"PAPER_STOP_ACKNOWLEDGED",
                    {"previous_stop":str(prior),"stop":str(proposed),"feature_close_ms":f.bar.close_ms,
                     "coverage":"PAPER_MONITOR_ONLY","broker_protection":False})
    if action:
        tick=Decimal(str(meta.tick_size))
        price=Decimal(str(book.ask if action.side=="BUY" else book.bid))
        action.limit=(price/tick).to_integral_value(rounding=ROUND_CEILING if action.side=="BUY" else ROUND_FLOOR)*tick
        repo.save_campaign_action(uid,execution.campaign_document(c),document["revision"],action.model_dump(mode="json"))
        await execution.submit_action(uid,c.id,action.id,client=client)
        await execution.reconcile_action(uid,action.id,client=client)
    else:
        repo.save_campaign_action(uid,execution.campaign_document(c),document["revision"])


async def run_user(uid:str):
    guard=_locks.setdefault((id(asyncio.get_running_loop()),uid),asyncio.Lock())
    if guard.locked():
        return {"status":"OVERLAP_SUPPRESSED"}
    async with guard:
        settings=config_store.get(uid)
        cfg=EmaTrendConfig.model_validate(settings["config"])
        docs=[c for c in repo.list_documents(uid,"campaign",limit=2000)
              if c["state"]!="CLOSED" or c.get("pending") or c.get("pending_close")]
        errors=[]
        if docs:
            client=await execution.paper_client(uid)
            # Recovery asks the canonical journal to observe its own virtual
            # book; it never retries an uncertain order.
            from app.services.simulation.tenant import recover_tenant
            await recover_tenant(uid,client=client)
            for doc in docs:
                try:
                    key=(uid,doc["symbol"],doc["mode"])
                    if repo.now_ms()-_last_management_scan.get(key,0)>=60000:
                        _last_management_scan[key]=repo.now_ms()
                        try:
                            await scanner.scan_symbol(uid,doc["symbol"],doc["mode"])
                        except Exception as exc:
                            errors.append({"campaign_id":doc["id"],"reason":"MANAGEMENT_FEATURE_REFRESH: "+str(exc)})
                    await manage_campaign(uid,doc,client)
                except Exception as exc:
                    errors.append({"campaign_id":doc["id"],"reason":str(exc)})
                    repo.event(uid,stable_id(doc["id"],"management-error",repo.now_ms()//60000),doc["id"],"MANAGEMENT_BLOCKED",{"reason":str(exc)})
        now=repo.now_ms()
        interval=60000 if any(m!="swing" for m in cfg.modes) else 300000
        scans=repo.list_documents(uid,"scan",limit=1)
        scanning=bool(scans and scans[0]["status"] in {"QUEUED","RUNNING"})
        if cfg.enabled and cfg.symbols and not scanning and now-_last_scan.get(uid,0)>=interval:
            _last_scan[uid]=now
            scanner.start_scan(uid,cfg.modes,settings["revision"])
        from app.services.kite_engine import state as shared_state
        master=bool(shared_state.get_config(execution.sim_uid(uid)).auto_execute)
        if cfg.enabled and cfg.auto_execute and master and not settings.get("paused"):
            for row in repo.list_documents(uid,"signal",limit=100):
                if row["status"]!="armed" or row["config_revision"]!=settings["revision"]:
                    continue
                try:
                    prior=repo.get(uid,"decision",row["id"])
                    if prior:
                        continue
                except repo.NotFound:
                    pass
                try:
                    plan=await execution.build_plan(uid,row["id"],settings["revision"])
                    repo.put(uid,"decision",row["id"],{"status":plan["status"],"plan_id":plan["id"],"blockers":plan["blockers"]},0)
                    if plan["status"]=="READY":
                        await execution.arm(uid,plan["id"],plan["revision"],stable_id(row["id"],"auto"))
                except Exception as exc:
                    errors.append({"signal_id":row["id"],"reason":str(exc)})
        return {"status":"PAPER_ONLY","open_campaigns":len(docs),"errors":errors}


async def run_loop():
    # A process lost during a scan/replay cannot leave a permanently running UI.
    for uid in repo.owners():
        for kind in ("scan","run"):
            for row in repo.list_documents(uid,kind,limit=2000):
                if row["status"] in {"QUEUED","RUNNING"}:
                    repo.put(uid,kind,row["id"],{**row,"status":"FAILED","error":"PROCESS_RESTARTED","finished_ms":repo.now_ms()},row["revision"])
    while True:
        try:
            for uid in repo.owners():
                try:
                    result=await run_user(uid)
                    if result["status"]!="OVERLAP_SUPPRESSED":
                        health.record(uid,result)
                except Exception as exc:
                    try:
                        health.record(uid,{"errors":[{"reason":str(exc)}]})
                    except Exception:
                        log.exception("EMA supervision health could not be persisted")
                    log.warning("EMA trend tenant %s management unavailable: %s",uid,exc)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("EMA trend loop failed")
        await asyncio.sleep(2)


def start():
    global _task
    if _task is None or _task.done():
        _task=asyncio.create_task(run_loop(),name="ema-trend-paper-runner")
    return _task


async def stop():
    global _task
    tasks=[t for t in [_task,*scanner._tasks.values()] if t is not None]
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks,return_exceptions=True)
    _task=None
