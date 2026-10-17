"""Canonical virtual orders, fill-driven ownership and restart reconciliation.

Only the concrete SimulatedKiteClient is accepted. No setting, API field or
duck-typed `_is_paper` client can turn this adapter into a live broker writer.
Protection is explicitly a durable paper monitor, not broker-stop coverage.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR

from app.engines.ema_trend.config import EmaTrendConfig, PROFILES, stable_id, descriptor
from app.engines.ema_trend.contracts import Action, Campaign, Setup
from app.engines.ema_trend.instruments import contract,entry_expiry_block
from app.engines.ema_trend.sizing import size_plan, used_campaign_risk
from app.engines.ema_trend.state_machine import apply_fill, finalize_entry, make_action
from app.services.execution_service import CanonicalExecutionService, ExecutionRequest, RiskApproval, ExposureEffect
from app.services.kite_engine import fill_ledger, order_journal
from app.services.simulation.identity import sim_uid, sim_account_id
from app.services.simulation.sim_client import SimulatedKiteClient, get_sim_client
from app.services.simulation.quotes import QuoteSource
from . import config_store, repository as repo, scanner

D = lambda value: Decimal(str(value))
_locks: dict[tuple[int, str], asyncio.Lock] = {}


def lock(uid: str):
    return _locks.setdefault((id(asyncio.get_running_loop()), uid), asyncio.Lock())


def campaign_model(document: dict) -> Campaign:
    return Campaign.model_validate({k: v for k,v in document.items() if k in Campaign.model_fields})


def campaign_document(c: Campaign) -> dict:
    return c.model_dump(mode="json")


def public_campaign(c: Campaign | dict) -> dict:
    provenance=c.get("provenance") if isinstance(c,dict) else None
    c = campaign_model(c) if isinstance(c, dict) else c
    return {**campaign_document(c), "qty": c.qty, "vwap": str(c.vwap), "quarter": c.quarter,
            **({"provenance":provenance} if provenance else {})}


async def paper_client(uid: str) -> SimulatedKiteClient:
    from app.services.simulation.tenant import boot_tenant
    boot_tenant(repo.owner(uid))
    return get_sim_client(uid, lambda: scanner.market_client(uid), postbacks=False)


def require_virtual(client, uid: str):
    if not isinstance(client, SimulatedKiteClient) or client._account_id != sim_account_id(uid):
        raise PermissionError("EMA_TREND_ONLY_CONCRETE_VIRTUAL_TRANSPORT_ALLOWED")


async def quote(uid: str, symbol: str, mode: str, client=None):
    profile = PROFILES[mode]
    client = client or await scanner.market_client(uid)
    source = QuoteSource(lambda: client, owner=uid, max_quote_age_s=profile.max_quote_age_s)
    exchange, name = symbol.split(":", 1)
    book = await source.book(exchange, name, fresh=True)
    if book.bid <= 0 or book.ask <= 0 or book.ask < book.bid or not book.quote_ts:
        raise ValueError("EXECUTABLE_QUOTE_UNAVAILABLE")
    return book


def _risk_blockers(uid: str, cfg: EmaTrendConfig) -> list[str]:
    campaigns = [campaign_model(c) for c in repo.list_documents(uid, "campaign", limit=2000)]
    blockers = []
    net=sum((c.realized_net for c in campaigns),D(0))
    for c in campaigns:
        if not c.qty:
            continue
        try:
            mark=repo.get(uid,"mark",c.id)
        except repo.NotFound:
            blockers.append("OPEN_EXPOSURE_MARK_UNKNOWN")
            continue
        if repo.now_ms()-int(mark["at_ms"])>PROFILES[c.mode].max_quote_age_s*1000:
            blockers.append("OPEN_EXPOSURE_MARK_STALE")
            continue
        price=D(mark["price"])
        if price<=0 or not price.is_finite():
            blockers.append("OPEN_EXPOSURE_MARK_INVALID")
            continue
        net+=sum((lot.qty*(price-lot.price-lot.fee_per_share) for lot in c.lots),D(0))
        net-=c.qty*price*(cfg.fee_bps+cfg.slippage_bps)/D(10000)+cfg.brokerage_per_order
    # Missing marks are a refusal, never a zero unrealized loss or a reset peak.
    if blockers:
        return sorted(set(blockers))
    today = datetime.now(scanner.IST).date()
    with repo.transaction() as conn:
        risk=repo._load(conn,uid,"risk","equity")
        if risk is None:
            risk={"session_day":str(today),"session_start_net":"0","peak_net":"0",
                  "last_net":"0","daily_latched":False,"drawdown_latched":False,"revision":0}
        if risk["session_day"]!=str(today):
            risk.update(session_day=str(today),session_start_net=risk["last_net"],daily_latched=False)
        peak=max(D(risk["peak_net"]),net)
        session_loss=D(risk["session_start_net"])-net
        drawdown=peak-net
        if cfg.allocation_inr>0 and session_loss>=cfg.allocation_inr*cfg.daily_loss_fraction:
            risk["daily_latched"]=True
        if cfg.allocation_inr>0 and drawdown>=max(cfg.allocation_inr,cfg.allocation_inr+peak)*cfg.drawdown_fraction:
            risk["drawdown_latched"]=True
        risk.update(peak_net=str(peak),last_net=str(net),session_loss=str(session_loss),drawdown=str(drawdown),
                    allocation=str(cfg.allocation_inr),status="BLOCKED" if risk["daily_latched"] or risk["drawdown_latched"] else "CLEAR")
        repo._put(conn,uid,"risk","equity",risk,risk["revision"])
    if risk["daily_latched"]:
        blockers.append("DAILY_LOSS_BREAKER")
    if risk["drawdown_latched"]:
        blockers.append("ALLOCATION_DRAWDOWN_BREAKER")
    if cfg.allocation_inr <= 0:
        blockers.append("ZERO_ALLOCATION")
    return blockers


def record_mark(uid: str,campaign_id: str,price,*,at_ms:int|None=None,source="EXECUTABLE_BID"):
    value=D(price)
    if not value.is_finite() or value<=0:
        raise ValueError("INVALID_EXPOSURE_MARK")
    repo.put(uid,"mark",campaign_id,{"price":str(value),"at_ms":at_ms or repo.now_ms(),"source":source})


def _session_check(mode: str, now=None, exchange="NSE"):
    from app.core.session_calendar import is_trading_session
    now = now or datetime.now(scanner.IST)
    profile = PROFILES[mode]
    if not is_trading_session(now.date(), exchange) or not profile.entry_start <= now.strftime("%H:%M") <= profile.entry_end:
        raise ValueError("OUTSIDE_PROFILE_ENTRY_WINDOW")


async def build_plan(uid: str, signal_id: str, expected_revision: int) -> dict:
    settings = config_store.get(uid)
    if settings["revision"] != expected_revision:
        raise repo.Conflict("STALE_CONFIG_REVISION")
    cfg = EmaTrendConfig.model_validate(settings["config"])
    row = repo.get(uid, "signal", signal_id)
    # An index spot is a read-only underlying: refuse planning before any
    # broker quote, sizing, or virtual/real order path is entered.
    if row.get("watch_only") or row.get("signal_source")=="INDEX_SPOT_WATCH_ONLY":
        raise ValueError("INDEX_SPOT_WATCH_ONLY_NO_ORDERS")
    setup = Setup.model_validate({k:v for k,v in row.items() if k in Setup.model_fields})
    blockers = _risk_blockers(uid, cfg)
    origin=(row.get("provenance") or {}).get("origin")
    if not origin or origin.get("rule_hash")!=descriptor()["rule_hash"]:
        blockers.append("SIGNAL_RULE_IDENTITY_UNVERIFIED_OR_CHANGED")
    if settings.get("invalid") or not cfg.enabled:
        blockers.append("SCANNER_DISABLED_OR_CONFIG_INVALID")
    if settings.get("paused"):
        blockers.append("NEW_EXPOSURE_PAUSED")
    if setup.status != "armed" or setup.config_revision != expected_revision:
        blockers.append("SIGNAL_NOT_CURRENT_ARMED_SETUP")
    if setup.close_ms <= settings["activation_ms"]:
        blockers.append("PRE_ACTIVATION_SETUP")
    if setup.mode not in cfg.modes or setup.symbol not in cfg.symbols:
        blockers.append("CONFIG_SCOPE_CHANGED")
    now = repo.now_ms()
    p = PROFILES[setup.mode]
    # Intraday setup confirmation is good for one bar. Swing has only the
    # next verified session's explicit entry window.
    if p.mode == "swing":
        from app.core.session_calendar import next_trading_session
        signal_day = datetime.fromtimestamp(setup.close_ms/1000, scanner.IST).date()
        next_day = next_trading_session(signal_day, setup.symbol.split(":")[0])
        expires = int(datetime.combine(next_day, datetime.strptime(p.entry_end, "%H:%M").time(), scanner.IST).timestamp()*1000)
    else:
        expires = setup.close_ms+p.signal_minutes*60000
    if now > expires:
        blockers.append("ENTRY_EXPIRED")
    sizing = {"quantity": 0, "budget": D(0), "reason": "PLAN_BLOCKED"}
    entry, cash = D(str(setup.feature.bar.close)), D(0)
    info=None
    try:
        _session_check(setup.mode,exchange=setup.symbol.split(":")[0])
        meta = await scanner.equity_metadata(uid, setup.symbol)
        info=contract({**meta.as_meta(),"instrument_token":meta.token},setup.mode)
        if entry_expiry_block(info,datetime.now(scanner.IST).date()):
            raise ValueError("EXPIRY_DAY_OR_EXPIRED_CONTRACT")
        book = await quote(uid, setup.symbol, setup.mode)
        if int(book.quote_ts*1000) < setup.close_ms:
            raise ValueError("QUOTE_PRECEDES_SIGNAL")
        entry = (D(book.ask)/D(meta.tick_size)).to_integral_value(rounding=ROUND_CEILING)*D(meta.tick_size)
        if setup.ceiling is None or entry > setup.ceiling or setup.stop is None or entry <= setup.stop:
            raise ValueError("PRICE_CHASE_OR_STOP_INVALID")
        if D(book.ask-book.bid) > min(D(book.ask+book.bid)/2*D('.001'), (entry-setup.stop)*D('.1')):
            raise ValueError("SPREAD_COST_LIMIT")
        client = await paper_client(uid)
        positions=(await client.get_positions_raw()).get("net")
        if not isinstance(positions,list):
            raise ValueError("VIRTUAL_INVENTORY_UNREADABLE")
        ex,name=setup.symbol.split(":",1)
        if any(str(p.get("exchange"))==ex and str(p.get("tradingsymbol"))==name
               and int(p.get("quantity",0))!=0 for p in positions):
            raise ValueError("EXISTING_UNATTRIBUTED_VIRTUAL_INVENTORY")
        margins = await client.get_margins()
        cash = D(margins.get("equity", {}).get("available", {}).get("live_balance", 0))
        open_campaigns = [campaign_model(c) for c in repo.list_documents(uid,"campaign") if c["state"] != "CLOSED"]
        # Until a verified sector map exists, classify all as one unknown sector;
        # the conservative common bucket still enforces the configured cap.
        if len(open_campaigns) >= min(cfg.max_campaigns, cfg.max_sector_campaigns):
            raise ValueError("CAMPAIGN_OR_UNCLASSIFIED_SECTOR_CAP")
        if any(c.symbol == setup.symbol for c in open_campaigns):
            raise ValueError("INSTRUMENT_ALREADY_OWNED")
        exchange, symbol = setup.symbol.split(":",1)
        if any(h.symbol==symbol and not h.is_flat for h in fill_ledger.holdings(sim_uid(uid),sim_account_id(uid))):
            raise ValueError("EXISTING_UNATTRIBUTED_INVENTORY")
        used = sum((c.budget for c in open_campaigns), D(0))
        cash=min(cash,max(D(0),cfg.allocation_inr-sum((c.qty*c.vwap for c in open_campaigns),D(0))))
        sizing = size_plan(cfg, entry, setup.stop, setup.feature.atr10 or 0, cash, used,lot_size=info["lot_size"])
        if not sizing["quantity"]:
            blockers.append(sizing["reason"])
        if sizing["quantity"] > int(setup.feature.bar.volume*0.01) or sizing["quantity"] > book.ask_qty:
            blockers.append("PARTICIPATION_OR_DISPLAYED_DEPTH_LIMIT")
    except Exception as exc:
        blockers.append(str(exc))
    ident = stable_id(uid, signal_id, expected_revision, now)
    return repo.put(uid,"plan",ident,{"status": "BLOCKED" if blockers else "READY",
        "blockers": sorted(set(blockers)), "signal_id": signal_id, "symbol": setup.symbol,
        "contract":info,
        "dataset_id":row.get("dataset_id"),"dataset_sha256":row.get("dataset_sha256"),
        "mode": setup.mode, "quantity": sizing["quantity"], "entry": str(entry),
        "stop": str(setup.stop or 0), "budget": str(sizing["budget"]),
        "config_revision": expected_revision, "expires_ms": expires,
        "ceiling": str(setup.ceiling or 0), "estimated_cost": str(sizing.get("estimated_cost",0)),
        "fee_schedule":cfg.fee_schedule, "available_cash":str(cash),
        "feature":setup.feature.model_dump(mode="json")},0)


async def arm(uid: str, plan_id: str, expected_revision: int, idempotency_key: str,
              execution_mode: str = "PAPER") -> dict:
    if execution_mode != "PAPER":
        raise PermissionError("LIVE_EXECUTION_DISABLED_EQUITY_PROTECTION_UNVERIFIED")
    if not idempotency_key or len(idempotency_key) > 160:
        raise ValueError("idempotency_key is required (maximum 160 characters)")
    async with lock(uid):
        plan = repo.get(uid,"plan",plan_id)
        if plan.get("campaign_id"):
            return {"status":"ALREADY_ARMED", "campaign":public_campaign(repo.get(uid,"campaign",plan["campaign_id"]))}
        if plan["revision"] != expected_revision:
            raise repo.Conflict("STALE_PLAN_REVISION")
        if plan["status"] != "READY" or repo.now_ms() > plan["expires_ms"]:
            raise repo.Conflict("PLAN_BLOCKED_OR_EXPIRED")
        # Admission re-reads market, settings and cash rather than trusting the
        # browser's quantity or a quote from the plan preview.
        fresh = await build_plan(uid,plan["signal_id"],plan["config_revision"])
        if fresh["status"] != "READY":
            raise repo.Conflict("CURRENT_ADMISSION_BLOCKED: " + ", ".join(fresh["blockers"]))
        cfg = config_store.config(uid)
        if fresh.get("contract")!=plan.get("contract"):
            raise repo.Conflict("CONTRACT_METADATA_CHANGED")
        info=fresh["contract"]
        ident = stable_id(uid,plan_id,"campaign")
        c = Campaign(id=ident,uid=sim_uid(uid),account_id=sim_account_id(uid),
            symbol=plan["symbol"],mode=plan["mode"],product=info["product"],
            instrument_token=info["instrument_token"],lot_size=info["lot_size"],instrument_type=info["instrument_type"],expiry=info["expiry"],
            config_hash=cfg.config_hash,config_revision=plan["config_revision"],config_snapshot=cfg.model_dump(mode="json"),
            budget=D(fresh["budget"]),initial_stop=D(fresh["stop"]),stop=D(fresh["stop"]),
            tick_size=D((await scanner.equity_metadata(uid,plan["symbol"])).tick_size),
            gap_reserve_per_share=D(fresh["feature"].get("atr10") or 0)*cfg.gap_reserve_atr,
            entry_index=int(fresh["feature"]["index"]),last_close_ms=int(fresh["feature"]["bar"]["close_ms"]),
            last_index=int(fresh["feature"]["index"]),last_mark=D(fresh["entry"]))
        action = make_action(c,"ENTRY","BUY",min(int(plan["quantity"]),int(fresh["quantity"])),c.entry_index,
                             repo.now_ms(),D(fresh["entry"]))
        c.state = "ENTRY_PENDING"
        saved = repo.reserve_campaign(uid,campaign_document(c),action.model_dump(mode="json"),plan)
        client = await paper_client(uid)
        result = await submit_action(uid,saved["id"],action.id,client=client)
        await reconcile_action(uid,action.id,client=client)
        return {"status":result["status"],"execution":result,
                "campaign":public_campaign(repo.get(uid,"campaign",ident)),"action":repo.get(uid,"outbox",action.id)}


async def submit_action(uid: str, campaign_id: str, action_id: str, *, client=None):
    client = client or await paper_client(uid)
    require_virtual(client,uid)
    row = repo.get(uid,"outbox",action_id)
    campaign_record=repo.get(uid,"campaign",campaign_id)
    c = campaign_model(campaign_record)
    action = Action.model_validate(row["action"])
    ex,symbol = c.symbol.split(":",1)
    key = order_journal.make_key(uid=c.uid,account_id=c.account_id,strategy_id="ema_trend",
        generation_id=c.config_hash,signal_id=action.id,exchange=ex,symbol=symbol,side=action.side)
    existing = order_journal.get(key)
    if existing:
        repo.put(uid,"outbox",action_id,{**row,"intent_key":key,"order_id":existing.order_id,
            "status":existing.state},row["revision"])
        return {"status":existing.state,"intent_key":key,"order_id":existing.order_id}
    if row["status"] != "RESERVED":
        return {"status":row["status"],"error":"submission requires reconciliation; never retried blindly"}
    # An operator flatten is a persistent exposure veto, including for an
    # entry already reserved but not yet sent to the virtual exchange.
    if action.side == "BUY" and c.flatten_requested:
        raise repo.Conflict("FLATTEN_PENDING_NO_NEW_EXPOSURE")
    virtual_book=(await client.get_positions_raw()).get("net")
    if not isinstance(virtual_book,list):
        raise ValueError("VIRTUAL_INVENTORY_UNREADABLE")
    actual=sum(int(p.get("quantity",0)) for p in virtual_book if p.get("exchange")==ex
               and p.get("tradingsymbol")==symbol and p.get("product")==c.product)
    if action.side=="SELL" and (action.quantity>c.qty or actual<c.qty):
        raise repo.Conflict("OWNED_VIRTUAL_INVENTORY_RECONCILIATION_REQUIRED")
    if action.side == "BUY":
        origin=(campaign_record.get("provenance") or {}).get("origin")
        if not origin or origin.get("rule_hash")!=descriptor()["rule_hash"]:
            raise repo.Conflict("CAMPAIGN_RULE_IDENTITY_UNVERIFIED_OR_CHANGED")
        settings = config_store.get(uid)
        current_cfg=EmaTrendConfig.model_validate(settings["config"])
        if ((action.kind=="ENTRY" and settings["revision"] != c.config_revision)
                or settings.get("paused") or not current_cfg.enabled
                or c.mode not in current_cfg.modes or c.symbol not in current_cfg.symbols):
            raise repo.Conflict("NEW_EXPOSURE_CONFIG_CHANGED_OR_PAUSED")
        held_notional=sum((campaign_model(item).qty*campaign_model(item).vwap for item in repo.list_documents(uid,"campaign",limit=2000) if item["state"]!="CLOSED"),D(0))
        if held_notional+action.quantity*action.limit>current_cfg.allocation_inr:
            raise repo.Conflict("FULL_NOTIONAL_ALLOCATION_CAP")
        blockers = _risk_blockers(uid,current_cfg)
        if blockers:
            raise repo.Conflict(", ".join(blockers))
        _session_check(c.mode,exchange=ex)
        current_meta=await scanner.equity_metadata(uid,c.symbol)
        current_info=contract({**current_meta.as_meta(),"instrument_token":current_meta.token},c.mode)
        if current_info["instrument_token"]!=c.instrument_token or current_info["lot_size"]!=c.lot_size or current_info["instrument_type"]!=c.instrument_type or current_info["expiry"]!=c.expiry or D(current_info["tick_size"])!=c.tick_size:
            raise repo.Conflict("CONTRACT_METADATA_CHANGED")
        if entry_expiry_block(current_info,datetime.now(scanner.IST).date()):
            raise repo.Conflict("EXPIRY_DAY_OR_EXPIRED_CONTRACT")
        if action.kind!="ENTRY":
            if actual!=c.qty:
                raise repo.Conflict("ADD_REQUIRES_EXACT_OWNED_INVENTORY")
            book=await quote(uid,c.symbol,c.mode)
            if D(book.ask)<max(c.p0,c.vwap,c.last_add_price) or D(book.ask)>action.limit:
                raise repo.Conflict("NO_AVERAGING_DOWN_OR_ADD_CEILING")
            held_config=EmaTrendConfig.model_validate({k:v for k,v in c.config_snapshot.items() if k in EmaTrendConfig.model_fields})
            additional=action.quantity*(max(D(0),D(book.ask)-c.stop)+c.gap_reserve_per_share
                 +D(book.ask)*(current_cfg.fee_bps+current_cfg.slippage_bps)/D(10000)*2)+current_cfg.brokerage_per_order
            used=used_campaign_risk(c,held_config)
            current_campaign_cap=current_cfg.allocation_inr*current_cfg.risk_fraction
            parent_used=sum((used_campaign_risk(campaign_model(item),current_cfg) for item in repo.list_documents(uid,"campaign",limit=2000)
                if item["state"]!="CLOSED"),D(0))
            if used+additional>min(c.budget,current_campaign_cap) or parent_used+additional>current_cfg.allocation_inr*current_cfg.strategy_risk_fraction:
                raise repo.Conflict("CURRENT_TIGHTER_RISK_CAP_BLOCKS_ADD")
    # A committed SUBMITTING fence precedes send. A crash in the window with
    # no canonical intent is visible UNKNOWN, never a blind duplicate.
    row = repo.put(uid,"outbox",action_id,{**row,"status":"SUBMITTING","intent_key":key},row["revision"])
    margins = await client.get_margins()
    available = D(margins.get("equity",{}).get("available",{}).get("live_balance",0))
    frozen=EmaTrendConfig.model_validate({k:v for k,v in c.config_snapshot.items() if k in EmaTrendConfig.model_fields})
    needed = (action.limit*action.quantity*(1+frozen.fee_bps/D(10000))
              +frozen.brokerage_per_order) if action.side == "BUY" else D(0)
    request = ExecutionRequest(uid=c.uid,account_id=c.account_id,strategy_id="ema_trend",
        generation_id=c.config_hash,signal_id=action.id,exchange=ex,symbol=symbol,side=action.side,
        quantity=action.quantity,tag="EMA-TREND-PAPER",order_type="limit_order",price=float(action.limit),
        exposure_effect=ExposureEffect.INCREASE_EXPOSURE if action.side=="BUY" else ExposureEffect.REDUCE_EXPOSURE,
        capital_required=float(needed),available_capital=float(available),
        payload={"product":c.product,"campaign_id":c.id,"action_id":action.id,"lot_size":c.lot_size,
                 "tenant_kind":"SIMULATION","stop":str(c.stop)})
    approval = RiskApproval(approval_id=stable_id(action.id,"risk"),uid=c.uid,account_id=c.account_id,
        symbol=symbol,side=action.side,quantity=action.quantity,generation_id=c.config_hash,
        available_capital=float(available),capital_required=float(needed),strategy_id="ema_trend",signal_id=action.id)
    result = await CanonicalExecutionService().submit_order(request,broker_client=client,risk_approval=approval)
    value = {"status":result.status,"intent_key":result.intent_key,"order_id":result.order_id,"error":result.error}
    repo.put(uid,"outbox",action_id,{**row,**value},row["revision"])
    repo.event(uid,stable_id(action.id,"submitted"),c.id,"ORDER_SUBMITTED",value)
    return value


async def reconcile_action(uid: str, action_id: str, *, client=None):
    client = client or await paper_client(uid)
    require_virtual(client,uid)
    row = repo.get(uid,"outbox",action_id)
    document = repo.get(uid,"campaign",row["campaign_id"])
    c, action = campaign_model(document), Action.model_validate(row["action"])
    ex,symbol = c.symbol.split(":",1)
    if not row.get("order_id") and row.get("intent_key"):
        intent = order_journal.get(row["intent_key"])
        if intent and intent.order_id:
            row = repo.put(uid,"outbox",row["id"],{**row,"order_id":intent.order_id,"status":intent.state},row["revision"])
    if not row.get("order_id"):
        if row["status"] in {"REJECTED","HALTED","REJECTED_OUTSIDE_RELEASE_SCOPE"}:
            if c.pending and c.pending.id==action.id:
                c.pending=None
            if c.pending_close and c.pending_close.id==action.id:
                c.pending_close=None
            if action.kind=="ENTRY":
                finalize_entry(c)
            elif c.qty:
                if action.side=="SELL":
                    c.state,c.reason="CLOSING","RISK_EXIT"
                else:
                    c.state="OPEN"
            repo.save_campaign_action(uid,campaign_document(c),document["revision"])
        elif row["status"]=="SUBMITTING":
            c.state,c.reason="RECOVERY_REQUIRED","UNKNOWN_SUBMISSION_REQUIRES_CANONICAL_RECOVERY"
            repo.save_campaign_action(uid,campaign_document(c),document["revision"])
        return public_campaign(c)
    history = await client.get_order_history(row["order_id"])
    if not history:
        raise ValueError("VIRTUAL_ORDER_HISTORY_UNAVAILABLE")
    observation = history[-1]
    for field,expected in (("exchange",ex),("tradingsymbol",symbol),("product",c.product),("transaction_type",action.side)):
        if observation.get(field) != expected:
            raise ValueError("VIRTUAL_ORDER_IDENTITY_MISMATCH")
    qty,average = int(observation.get("filled_quantity",0)),D(observation.get("average_price",0))
    status = str(observation["status"]).upper()
    if qty%c.lot_size:
        raise ValueError("VIRTUAL_FILL_QUANTITY_NOT_WHOLE_LOTS")
    if qty>action.quantity or qty<int(row["projected_quantity"]):
        raise ValueError("VIRTUAL_FILL_QUANTITY_CONFLICT")
    journal_observation=order_journal.observe_order(row["intent_key"],status=status,order_id=row["order_id"],
        filled_quantity=qty,average_price=float(average),raw=observation)
    if journal_observation.reconciliation_required:
        raise ValueError("CANONICAL_FILL_RECONCILIATION_REQUIRED")
    # Public Kite-shaped trades strip _sim charges/timestamps. The concrete
    # virtual ledger is the authoritative source for these modelled facts.
    trades=client.ledger.trades(repo.owner(uid),order_id=row["order_id"])
    if sum(int(t["quantity"]) for t in trades)!=qty:
        raise ValueError("VIRTUAL_TRADE_FILL_RECONCILIATION_REQUIRED")
    fees=sum((D((t.get("_sim") or {}).get("charges",{}).get("total",0)) for t in trades),D(0))
    value=D(qty)*average
    delta=qty-int(row["projected_quantity"])
    old_net=c.realized_net
    if qty:
        fill_ledger.record(uid=c.uid,account_id=c.account_id,symbol=symbol,exchange=ex,side=action.side,
            order_id=row["order_id"],cumulative_quantity=qty,cumulative_value=float(value),source="entry" if action.side=="BUY" else "exit",
            lot_size=c.lot_size,raw=observation)
        fill_ledger.apply_fees(uid=c.uid,account_id=c.account_id,symbol=symbol,order_id=row["order_id"],
            fees=float(fees),source="VIRTUAL_EXCHANGE_MODEL_NOT_BROKER_FEES",expected_quantity=qty)
    if delta:
        incremental_price=(value-D(row["projected_value"]))/delta
        fee_delta=max(D(0),fees-D(row["projected_fees"]))
        if action.kind=="ENTRY" and c.entry_filled_qty==0:
            first_fill_ms=min((int((t.get("_sim") or {}).get("fill_ms",repo.now_ms())) for t in trades),default=repo.now_ms())
            c.first_fill_ms=first_fill_ms
            try:
                tape=repo.get(uid,"tape",f"{c.symbol}:{c.mode}:signal")
                indices=[i for i,b in enumerate(tape["bars"]) if int(b["close_ms"])<=first_fill_ms]
                if indices:
                    c.entry_index=indices[-1]+1
            except repo.NotFound:
                # A setup-derived fixture may have no full tape; real scans
                # always persist one. Keep the frozen action index visibly.
                pass
        apply_fill(c,action,stable_id(row["order_id"],qty),delta,incremental_price,fee_delta)
        if c.qty:
            record_mark(uid,c.id,incremental_price,source="VIRTUAL_FILL_PRICE")
    terminal=status in {"COMPLETE","CANCELLED","REJECTED","EXPIRED"}
    if terminal:
        if c.pending and c.pending.id==action.id:
            c.pending=None
        if c.pending_close and c.pending_close.id==action.id:
            c.pending_close=None
        if action.kind=="ENTRY":
            finalize_entry(c)
        elif c.qty and action.side=="SELL" and qty<action.quantity:
            # Known terminal remainder: do not lose a consumed weakness latch.
            # Conservatively close remaining exposure rather than repeat the
            # ordinary reduction; UNKNOWN is deliberately outside this branch.
            c.state,c.reason="CLOSING","RISK_EXIT"
        elif c.qty and c.state!="CLOSING":
            c.state="OPEN"
        action.status="FILLED" if qty==action.quantity else "CANCELLED" if status in {"CANCELLED","EXPIRED"} else "REJECTED"
    else:
        if c.pending_close and c.pending_close.id==action.id:
            c.pending_close=action
        elif c.pending and c.pending.id==action.id:
            c.pending=action
    if c.qty:
        held_rules=EmaTrendConfig.model_validate({k:v for k,v in c.config_snapshot.items() if k in EmaTrendConfig.model_fields})
        if used_campaign_risk(c,held_rules)>c.budget:
            c.state,c.reason="CLOSING","RISK_EXIT"
    c.protection="PAPER_MONITOR_ONLY" if c.qty else "NO_EXPOSURE"
    next_row={**row,"status":action.status if terminal else "PARTIAL" if qty else "ACKNOWLEDGED",
        "action":action.model_dump(mode="json"),"projected_quantity":qty,"projected_value":str(value),"projected_fees":str(fees)}
    saved=repo.save_projection(uid,campaign_document(c),document["revision"],next_row,row["revision"])
    if journal_observation.intent.projection_pending:
        order_journal.mark_projected(row["intent_key"],journal_observation.intent.projection_version)
    if delta:
        repo.event(uid,stable_id(row["order_id"],qty,"fill"),c.id,"FILL",{
            "action":action.kind,"side":action.side,"quantity":delta,"price":str(incremental_price),
            "fee":str(fee_delta),"realized_delta":str(c.realized_net-old_net),"order_id":row["order_id"],
            "canonical_intent":row["intent_key"],"evidence_class":"VIRTUAL_EXECUTION"})
        _journal(c,action,row,delta,incremental_price,fees)
    return public_campaign(saved)


def _journal(c: Campaign, action: Action, row: dict, qty: int, price: Decimal, fees: Decimal):
    from app.services import trade_journal as journal
    tid=journal.record_entry(uid=c.uid,strategy="ema_trend",symbol=c.symbol,key=c.id,status="OPEN" if c.qty else "PENDING",
        account_id=c.account_id,underlying=c.symbol,side="BUY",lot_size=c.lot_size,qty=c.q0 or c.qty,
        entry_price=float(c.p0 or c.vwap),initial_sl=float(c.initial_stop),
        signal_context={"mode":c.mode,"config_hash":c.config_hash,"campaign_id":c.id},
        extra={"execution_vehicle":"EQUITY" if c.instrument_type=="EQ" else "FUTURE" if c.instrument_type=="FUT" else "OPTION","product":c.product,"fee_source":"VIRTUAL_EXCHANGE_MODEL"},source="ema_trend_paper")
    if not tid:
        repo.event(c.uid.removeprefix("sim:"),stable_id(action.id,"journal-failed"),c.id,"JOURNAL_UNAVAILABLE",{})
        return
    journal.record_event(trade_id=tid,event_type="FILLED" if action.side=="BUY" else "PARTIAL_EXIT",
        event_key=stable_id(row["order_id"],action.filled_quantity),price=float(price),qty=qty,
        detail=action.kind,data={"campaign_id":c.id,"remaining":c.qty,"mode":c.mode})
    journal.record_charges(trade_id=tid,charges=float(fees),source="VIRTUAL_EXCHANGE_MODEL",order_key=row["order_id"])
    if action.side=="SELL":
        journal.record_exit(trade_id=tid,exit_price=float(price),qty=qty,reason=action.kind,
            partial=bool(c.qty),fill_key=stable_id(row["order_id"],action.filled_quantity,"exit"),
            gross_pnl=float(c.realized_net+c.total_fees) if not c.qty else None)


async def close_campaign(uid: str, campaign_id: str, expected_revision: int,
                         idempotency_key: str, percent: int=100, kind: str="FLATTEN"):
    if percent not in {25,50,100} or not idempotency_key:
        raise ValueError("valid reduction percent and idempotency key required")
    async with lock(uid):
        command_id=stable_id(uid,campaign_id,"manual_command",idempotency_key)
        try:
            previous=repo.get(uid,"command",command_id)
            if previous["percent"]!=percent or previous["kind"]!=kind:
                raise repo.Conflict("IDEMPOTENCY_PAYLOAD_CONFLICT")
            return {"status":"ALREADY_REQUESTED","campaign":public_campaign(repo.get(uid,"campaign",campaign_id)),
                    "action":repo.get(uid,"outbox",previous["action_id"])}
        except repo.NotFound:
            pass
        doc=repo.get(uid,"campaign",campaign_id)
        if doc["revision"]!=expected_revision:
            raise repo.Conflict("STALE_CAMPAIGN_REVISION")
        c=campaign_model(doc)
        # A full flatten is an operator safety instruction, not a quote-
        # contingent order request. Persist it BEFORE any broker/network I/O.
        # If the feed is down, the runner must still close on recovery.
        if kind=="FLATTEN" and percent==100 and (c.qty or c.pending or c.pending_close or c.state!="CLOSED"):
            if not c.flatten_requested:
                c.flatten_requested=True
                if not c.qty:
                    if c.state!="RECOVERY_REQUIRED":
                        c.state="CLOSING"
                    c.reason="FLATTEN_REQUESTED_PENDING_RECONCILIATION"
                doc=repo.save_campaign_action(uid,campaign_document(c),doc["revision"])
                c=campaign_model(doc)
        if not c.qty:
            # A pending BUY can fill *after* this command. Record the operator
            # intent durably, not just an unresolved API response.
            if c.pending or c.pending_close or c.state != "CLOSED":
                return {"status":"UNRESOLVED",
                        "reason":"PENDING_ORDER_OR_CAMPAIGN_RECONCILIATION",
                        "campaign":public_campaign(c)}
            return {"status":"CLOSED","campaign":public_campaign(c)}
        if c.pending_close or (c.pending and c.pending.side=="SELL"):
            if kind == "FLATTEN" and percent == 100:
                return {"status":"UNRESOLVED",
                        "reason":"PENDING_SELL_REQUIRES_RECONCILIATION",
                        "campaign":public_campaign(c)}
            raise repo.Conflict("PENDING_ACTION_REQUIRES_RECONCILIATION")
        try:
            client=await paper_client(uid)
            book=await quote(uid,c.symbol,c.mode)
            meta=await scanner.equity_metadata(uid,c.symbol)
        except Exception as exc:
            if kind=="FLATTEN" and percent==100:
                # A broker/feed outage cannot erase a recorded flatten intent.
                # No synthetic price or blind close order is permitted.
                return {"status":"UNRESOLVED",
                        "reason":"FLATTEN_RECORDED_MARKET_DATA_UNAVAILABLE",
                        "error_type":type(exc).__name__,"campaign":public_campaign(c)}
            raise
        quantity=c.qty if percent==100 else min(c.qty,max(0,(c.q0//c.lot_size*percent//100)*c.lot_size))
        if quantity<=0:
            raise ValueError("REDUCTION_TOO_SMALL")
        price=(D(book.bid)/D(meta.tick_size)).to_integral_value(rounding=ROUND_FLOOR)*D(meta.tick_size)
        action=make_action(c,kind if percent==100 else "MANUAL_REDUCE","SELL",quantity,
            c.last_index,repo.now_ms(),price)
        repo.save_campaign_action(uid,campaign_document(c),doc["revision"],action.model_dump(mode="json"),
            command={"id":command_id,"status":"RESERVED","campaign_id":c.id,"action_id":action.id,"percent":percent,"kind":kind})
        result=await submit_action(uid,c.id,action.id,client=client)
        await reconcile_action(uid,action.id,client=client)
        return {"status":result["status"],"campaign":public_campaign(repo.get(uid,"campaign",c.id)),"execution":result}
