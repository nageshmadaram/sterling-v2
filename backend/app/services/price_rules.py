"""Live Price Rules: exact contracts, one-shot admission, owned broker OCO protection.

Independent implementation of the documented manual/price-rule behavior. No
binary execution, telemetry, registration, paper funds, or naked option shorts.
"""
from __future__ import annotations
import asyncio
import logging
import math
import operator
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from app.services import price_rules_store as store
from app.services.execution_service import ExecutionRequest, RiskApproval, ExposureEffect, execution_service
from app.services.exchanges.kite import accounts
from app.services.kite_engine import fill_ledger, order_journal
from app.services.simulation.quotes import QuoteSource
from app.services.simulation.virtual_exchange import snap
from app.services.snapback_family_mode import canonical_broker_capability, INTENT_PROTECT, INTENT_CANCEL

STRATEGY_ID = "price_rules"
OPS = {">=": operator.ge, "<=": operator.le, ">": operator.gt, "<": operator.lt}
TERMINAL = {"COMPLETE", "CANCELLED", "REJECTED"}
INDEX_NAMES = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX", "BANKEX", "NIFTYNXT50"}
log = logging.getLogger(__name__)
_locks = {}


def lock(uid):
    key = (id(asyncio.get_running_loop()), uid)
    return _locks.setdefault(key, asyncio.Lock())


def number(value):
    if isinstance(value, bool) or not math.isfinite(float(value)):
        raise ValueError("finite_number_required")
    return float(value)


def units(value):
    n = number(value)
    if n < 0 or n != int(n):
        raise ValueError("integer_quantity_required")
    return int(n)


def canonical_entry_refusal(request):
    """Re-read the exact owned rule at admission and after transport waits."""
    doc = store.load(request.uid)
    if doc.get("enabled") is not True:
        return "Price Rules new entries are disabled"
    rule = doc["rules"].get(request.generation_id)
    if not rule or rule.get("state") != "SUBMITTING":
        return "Price Rules entry has no owned submitting plan"
    if (request.signal_id != rule["id"] or request.account_id != rule["account_id"]
            or request.symbol != rule["symbol"] or request.exchange != rule["exchange"]
            or request.side != "BUY" or request.quantity != rule["quantity"]):
        return "Price Rules entry identity differs from its owned plan"
    revision = (request.payload or {}).get("rule_revision")
    if type(revision) is not int or rule["revision"] != revision + 1:
        return "Price Rules entry revision changed before send"
    return None


async def client_for(uid, account_id=""):
    if uid.startswith("sim:"):
        raise PermissionError("real_account_required")
    account = accounts.get_active(uid)
    if not account or account.is_paper or (account_id and account.id != account_id):
        raise PermissionError("bound_live_account_required")
    client = await accounts.acquire_client(account)
    if getattr(client, "_is_paper", None) is not False or str(getattr(client, "_account_id", "")) != account.id:
        raise PermissionError("live_client_identity_unknown")
    return account, client


async def create(uid, body):
    account, client = await client_for(uid)
    source = QuoteSource(lambda: client, owner=uid)
    ex, symbol = body["exchange"], body["symbol"].strip().upper()
    meta = await source.instrument(ex, symbol)
    if meta.instrument_type not in {"CE", "PE"} or meta.name.upper() not in INDEX_NAMES:
        raise ValueError("listed_index_option_required")
    today = datetime.now(timezone(timedelta(hours=5, minutes=30))).date()
    if not meta.expiry or date.fromisoformat(meta.expiry) < today:
        raise ValueError("contract_expired")
    lots = units(body["lots"])
    if not 1 <= lots <= 30 or meta.lot_size <= 1:
        raise ValueError("valid_lots_required")
    stop_points = number(body["stop_points"])
    target_points = number(body.get("target_points", 0))
    if stop_points <= 0 or target_points < 0:
        raise ValueError("positive_stop_required")
    watch = body.get("watch") or f"{ex}:{symbol}"
    if ":" not in watch or body.get("comparison", "MANUAL") not in {*OPS, "MANUAL"}:
        raise ValueError("invalid_price_condition")
    level = number(body.get("level", 0))
    if body.get("comparison", "MANUAL") != "MANUAL" and level <= 0:
        raise ValueError("positive_trigger_level_required")
    book = await source.book(ex, symbol, fresh=True)
    if book.ask <= stop_points:
        raise ValueError("stop_must_be_below_premium")
    rule = dict(id=str(uuid.uuid4()), revision=1, state="DRAFT", account_id=account.id,
                exchange=ex, symbol=symbol, option_type=meta.instrument_type, lot_size=meta.lot_size,
                tick=meta.tick_size, expiry=meta.expiry, quantity=lots * meta.lot_size,
                stop_points=stop_points, target_points=target_points, watch=watch,
                comparison=body.get("comparison", "MANUAL"), level=level,
                created_ms=int(time.time()*1000), expires_ms=int(time.time()*1000)+units(body.get("valid_minutes", 60))*60000,
                filled=0, exits={}, error="", protection_pending=False)
    def add(doc):
        if any(r["account_id"] == account.id and r["symbol"] == symbol and r["state"] not in {"CLOSED", "CANCELLED", "REFUSED", "EXPIRED"} for r in doc["rules"].values()):
            raise ValueError("existing_price_rule_for_contract")
        doc["rules"][rule["id"]] = rule
        return rule
    return store.mutate(uid, add)


async def set_enabled(uid: str, enabled: bool, expected_enabled: bool):
    """Persist the per-user entry gate without stopping owned position management."""
    if uid.startswith("sim:"):
        raise PermissionError("real_account_required")
    if type(enabled) is not bool or type(expected_enabled) is not bool:
        raise ValueError("boolean_switch_required")
    # Serializes the switch with this process's entry cycle. The durable
    # submission claim also checks the flag atomically across processes.
    async with lock(uid):
        def change(doc):
            if (doc.get("enabled") is True) != expected_enabled:
                raise ValueError("price_rules_switch_stale")
            doc["enabled"] = enabled
            return {"enabled": enabled, "revision": doc["revision"] + 1}
        return store.mutate(uid, change)


def command(uid, rule_id, revision, action):
    def change(doc):
        r = doc["rules"][rule_id]
        if r["revision"] != revision:
            raise ValueError("stale_rule_revision")
        allowed = {"arm": {"DRAFT", "PAUSED"}, "pause": {"ARMED"}, "cancel": {"DRAFT", "PAUSED", "ARMED"}}
        if r["state"] not in allowed[action]:
            raise ValueError("rule_has_execution_or_invalid_state")
        r["state"] = {"arm": "ARMED", "pause": "PAUSED", "cancel": "CANCELLED"}[action]
        r["revision"] += 1
        return dict(r)
    return store.mutate(uid, change)


def update(uid, rid, **fields):
    def change(doc):
        r = doc["rules"][rid]
        r.update(fields)
        r["revision"] += 1
        return dict(r)
    return store.mutate(uid, change)


def remaining(r):
    qty = units(r["filled"]) - sum(units(o.get("filled", 0)) for o in r["exits"].values())
    if qty < 0:
        raise RuntimeError("owned_quantity_conflict")
    return qty


def project_journal(uid, r):
    """Replay-safe projection of confirmed durable facts, including partial fills."""
    from app.services import trade_journal as journal
    qty = int(r.get("filled", 0))
    if qty <= 0:
        return  # acknowledgement is not a fill
    tid = journal.record_entry(uid=uid, strategy=STRATEGY_ID, symbol=r["symbol"],
        key=f"{r['account_id']}:{r['id']}", status="OPEN", account_id=r["account_id"],
        side="BUY", qty=qty, lot_size=r["lot_size"], lots=qty/r["lot_size"],
        expiry=r.get("expiry"), option_type=r.get("option_type"),
        entry_order_id=r.get("order_id"), entry_price=r.get("entry_price"),
        initial_sl=r.get("stop"), target=r.get("target"),
        signal_ts_ms=r.get("created_ms"), entry_ts_ms=r.get("fill_ts_ms"),
        source="price_rules_confirmed", extra={"exchange":r["exchange"], "rule_id":r["id"]})
    if not tid:
        raise RuntimeError("price_rules_journal_projection_failed")
    journal.record_event(trade_id=tid, event_type=journal.FILLED,
        event_key=f"fill:{r.get('order_id')}", qty=qty, price=r.get("entry_price"), cumulative=True,
        ts_ms=r.get("fill_ts_ms"), detail="Confirmed cumulative entry fill")
    if r.get("gtt_id") and not r.get("protection_pending"):
        journal.record_event(trade_id=tid, event_type=journal.PROTECTION_PLACED,
            event_key=f"gtt:{r['gtt_id']}:{r.get('gtt_qty')}", price=r.get("stop"),
            qty=r.get("gtt_qty"), detail="Broker protection verified")
    sold = 0
    for oid, leg in r["exits"].items():
        filled = int(leg.get("filled", 0))
        if filled <= 0:
            continue
        sold += filled
        journal.record_exit(trade_id=tid, exit_price=leg.get("price"), qty=filled,
            ts_ms=leg.get("fill_ts_ms"), fill_key=f"exit:{oid}", cumulative=True,
            partial=sold < qty or r.get("entry_status") not in TERMINAL,
            reason=leg.get("reason", "broker_exit"), trailed=False)


async def submit(uid, r, client):
    source = QuoteSource(lambda: client, owner=uid)
    margins = await client.get_margins()
    capital = number(margins.get("equity", {}).get("available", {}).get("live_balance"))
    capital_ms = int(time.time()*1000)
    book = await source.book(r["exchange"], r["symbol"], fresh=True)
    from app.services.kite_engine.market_hours import is_market_open
    if not is_market_open(exchange=r["exchange"]):
        raise ValueError("market_session_closed")
    if r["comparison"] != "MANUAL":
        books = await source.books([r["watch"]], fresh=True)
        if not OPS[r["comparison"]](books[r["watch"]].ltp, r["level"]):
            return
    stop = snap(book.ask - r["stop_points"], r["tick"], "down")
    target = snap(book.ask + r["target_points"], r["tick"], "up") if r["target_points"] else 0
    if stop <= 0 or book.ask <= 0 or book.ask_qty < r["lot_size"]:
        raise ValueError("executable_quote_or_stop_unavailable")
    def claim(doc):
        current = doc["rules"][r["id"]]
        if doc.get("enabled") is not True or current["state"] != "ARMED" or current["revision"] != r["revision"]:
            return False
        current.update(state="SUBMITTING", stop=stop, target=target, protection_pending=False)
        current["revision"] += 1
        return True
    if not store.mutate(uid, claim):
        return
    request = ExecutionRequest(uid=uid, account_id=r["account_id"], strategy_id=STRATEGY_ID,
        generation_id=r["id"], signal_id=r["id"], exchange=r["exchange"], symbol=r["symbol"],
        side="BUY", quantity=r["quantity"], order_type="LIMIT", price=snap(book.ask,r["tick"],"up"),
        capital_required=book.ask*r["quantity"], available_capital=capital,
        payload={"product":"NRML", "rule_revision":r["revision"], "stop":stop, "target":target, "lot_size":r["lot_size"]})
    approval = RiskApproval(approval_id=r["id"], uid=uid, account_id=r["account_id"], symbol=r["symbol"],
        side="BUY", quantity=r["quantity"], generation_id=r["id"], strategy_id=STRATEGY_ID,
        signal_id=r["id"], available_capital=capital, capital_required=request.capital_required,timestamp_ms=capital_ms)
    result = await execution_service.submit_order(request, broker_client=client, risk_approval=approval)
    state = "SUBMITTED" if result.order_id else "UNKNOWN" if result.status in {"UNKNOWN", "SUBMITTING"} else "REFUSED"
    update(uid,r["id"], state=state, order_id=result.order_id, intent_key=result.intent_key,
           error=result.error, protection_pending=state != "REFUSED")


def verify_order(r, row, oid, side, max_qty):
    if not isinstance(row, dict) or str(row.get("order_id")) != str(oid):
        raise RuntimeError("order_identity_unknown")
    for field, expected in (("tradingsymbol",r["symbol"]),("exchange",r["exchange"]),("product","NRML"),("transaction_type",side)):
        if str(row.get(field, "")).upper() != expected:
            raise RuntimeError("order_identity_conflict")
    qty = units(row.get("filled_quantity",0));price=number(row.get("average_price",0))
    if qty > max_qty or (qty and price <= 0):
        raise RuntimeError("fill_evidence_invalid")
    return qty, price, str(row.get("status","")).upper()


async def observe(uid,r,client,oid,side,max_qty,intent="",reason=""):
    history = await client.get_order_history(oid)
    if not isinstance(history,list) or not history:
        raise RuntimeError("order_history_unavailable")
    row=history[-1];qty,price,status=verify_order(r,row,oid,side,max_qty)
    observation = order_journal.observe_order(intent,status=status,order_id=oid,filled_quantity=qty,average_price=price,raw=row) if intent else None
    if observation and observation.reconciliation_required:
        raise RuntimeError("fill_observation_requires_reconciliation")
    stamp = row.get("exchange_timestamp") or row.get("order_timestamp")
    if qty:
        from app.services.simulation.quotes import _parse_ts
        ts=_parse_ts(stamp)
        if ts is None:
            raise RuntimeError("fill_exchange_time_unknown")
        fill_ledger.record(uid=uid,account_id=r["account_id"],symbol=r["symbol"],exchange=r["exchange"],side=side,
            order_id=oid,cumulative_quantity=qty,cumulative_value=qty*price,source="entry" if side=="BUY" else "exit",
            lot_size=r["lot_size"],exchange_ts_ms=int(ts*1000),raw=row)
    def project(doc):
        p=doc["rules"][r["id"]]
        if side=="BUY":
            if qty and not p["filled"]:
                p["stop"]=max(p.get("stop", 0), snap(price-p["stop_points"],p["tick"],"down"))
                p["target"]=snap(price+p["target_points"],p["tick"],"up") if p["target_points"] else 0
            p["filled"]=max(p["filled"],qty);p["entry_status"]=status;p["entry_price"]=price
            if qty: p.setdefault("fill_ts_ms", int(ts*1000))
        else:
            old=p["exits"].setdefault(str(oid),{"filled":0,"quantity":max_qty,"intent_key":intent})
            old["filled"]=max(old["filled"],qty);old["status"]=status;old["price"]=price
            if reason: old["reason"]=reason
            elif intent: old.setdefault("reason", "manual_exit")
            if qty: old.setdefault("fill_ts_ms", int(ts*1000))
        p["revision"]+=1
    store.mutate(uid,project)
    project_journal(uid, store.load(uid)["rules"][r["id"]])
    if observation and observation.intent.projection_pending and not order_journal.mark_projected(intent,observation.intent.projection_version):
        raise RuntimeError("projection_ack_conflict")
    return status


async def owned_units(uid,r,client):
    from app.services.snapback_reconciliation import attributable_long_quantity
    raw=await client.get_positions_raw()
    rows=raw.get("net") if isinstance(raw,dict) else None
    if not isinstance(rows,list):raise RuntimeError("broker_inventory_unreadable")
    matching=[p for p in rows if p.get("tradingsymbol")==r["symbol"] and p.get("exchange")==r["exchange"] and p.get("product")=="NRML"]
    if len(matching)!=1:raise RuntimeError("broker_inventory_unknown")
    signed=number(matching[0].get("quantity"))
    if signed!=int(signed):raise RuntimeError("fractional_broker_inventory")
    own=remaining(r)
    available=attributable_long_quantity(uid,r["account_id"],exchange=r["exchange"],product="NRML",symbol=r["symbol"],current_quantity=own,broker_quantity=int(signed))
    if available is None or available < own:raise RuntimeError("owned_quantity_unproved")
    return own


def protection_params(r,qty,last):
    triggers=[r["stop"]]+([r["target"]] if r.get("target") else [])
    return dict(trigger_type="two-leg" if len(triggers)==2 else "single",tradingsymbol=r["symbol"],exchange=r["exchange"],
        last_price=last,trigger_values=triggers,orders=[dict(exchange=r["exchange"],tradingsymbol=r["symbol"],transaction_type="SELL",quantity=qty,
        order_type="LIMIT",product="NRML",price=snap(p*.98 if i==0 else p,r["tick"],"down")) for i,p in enumerate(triggers)])


def verify_trigger(r,gtt,qty):
    if str(gtt.get("id"))!=str(r["gtt_id"]):raise RuntimeError("trigger_identity_conflict")
    cond=gtt.get("condition") or {}
    if cond.get("exchange")!=r["exchange"] or cond.get("tradingsymbol")!=r["symbol"]:raise RuntimeError("trigger_contract_conflict")
    params=protection_params(r,qty,1)
    if cond.get("trigger_values")!=params["trigger_values"]:raise RuntimeError("trigger_level_conflict")
    orders=gtt.get("orders")
    if not isinstance(orders,list) or len(orders)!=len(params["orders"]):raise RuntimeError("trigger_orders_unknown")
    for row,want in zip(orders,params["orders"]):
        if any(row.get(k)!=v for k,v in want.items()):raise RuntimeError("trigger_order_conflict")
    return gtt


async def protect(uid,r,client):
    if number(r["stop"]) <= 0:
        raise RuntimeError("required_stop_invalid")
    qty=await owned_units(uid,r,client)
    if not qty:return
    book=await QuoteSource(lambda:client,owner=uid).book(r["exchange"],r["symbol"],fresh=True)
    if book.ltp<=r["stop"] or (r.get("target") and book.ltp>=r["target"]):raise RuntimeError("protection_level_already_crossed")
    def claim_protection(doc):
        current = doc["rules"][r["id"]]
        if current["revision"] != r["revision"] or current.get("protection_claim") or current.get("exit_preparation") or current.get("exit_claim"):
            raise RuntimeError("protection_already_claimed_or_changed")
        current.update(protection_pending=True, protection_attempted=True, protection_claim=str(uuid.uuid4()))
        current["revision"] += 1
    store.mutate(uid, claim_protection)
    params=protection_params(r,qty,book.ltp)
    from app.services.execution_identity_lock import account_lock
    async with account_lock(uid):
        await client_for(uid,r["account_id"])
        if await owned_units(uid,r,client) != qty:
            raise RuntimeError("protection_quantity_changed")
        if r.get("gtt_id"):
            current_trigger = verify_trigger(r, await client.get_gtt(r["gtt_id"]), r["gtt_qty"])
            if current_trigger.get("status") != "active" or any(o.get("result") for o in current_trigger["orders"]):
                raise RuntimeError("stop_child_requires_reconciliation")
        with canonical_broker_capability(f"price-rule-protection:{r['id']}",intent=INTENT_PROTECT):
            response=await client.modify_gtt(r["gtt_id"],**params) if r.get("gtt_id") else await client.place_gtt(**params)
    gid=units(response.get("trigger_id"))
    if not gid:raise RuntimeError("protection_ack_missing")
    if not r.get("gtt_id"):
        from app.services.external_positions import owned_gtt_ids
        claimed = owned_gtt_ids(r["account_id"])
        if claimed is None or str(gid) in claimed:
            raise RuntimeError("protection_ack_already_owned_or_unreadable")
    if r.get("gtt_id") and gid != r["gtt_id"]:
        raise RuntimeError("protection_ack_identity_conflict")
    r=update(uid,r["id"],gtt_id=gid,gtt_qty=qty)
    trigger=verify_trigger(r,await client.get_gtt(gid),qty)
    if trigger.get("status")!="active":raise RuntimeError("protection_not_active")
    protected = update(uid,r["id"],state="OPEN",protection_pending=False,protection_claim="",error="")
    project_journal(uid, protected)


async def manage(uid,r,client,*,repair_protection=True):
    if r.get("exit_claim"):
        key=order_journal.make_key(uid=uid,account_id=r["account_id"],strategy_id=STRATEGY_ID,generation_id=r["id"],
            signal_id=r["exit_signal"],exchange=r["exchange"],symbol=r["symbol"],side="SELL")
        intent=order_journal.get(key)
        if not intent or not intent.order_id:
            raise RuntimeError("exit_submission_requires_reconciliation")
        def recover(doc):
            current=doc["rules"][r["id"]]
            current["exits"][intent.order_id]={"filled":0,"quantity":intent.quantity,"status":"OPEN","intent_key":key}
            current["exit_claim"]=""
            current["exit_preparation"]=""
        store.mutate(uid,recover)
        r=store.load(uid)["rules"][r["id"]]
    if r.get("exit_preparation"):
        raise RuntimeError("exit_preparation_requires_reconciliation")
    if r.get("order_id"):
        await observe(uid,r,client,r["order_id"],"BUY",r["quantity"],r.get("intent_key",""))
    r=store.load(uid)["rules"][r["id"]]
    if r.get("gtt_id"):
        g=await client.get_gtt(r["gtt_id"])
        verify_trigger(r,g,r["gtt_qty"])
        if g.get("status")=="triggered":
            children=[]
            for leg_index, leg in enumerate(g["orders"]):
                result=leg.get("result")
                if result:
                    result=result.get("order_result",result)
                    oid=result.get("order_id")
                    if not oid:raise RuntimeError("trigger_child_unconfirmed")
                    children.append(str(oid))
                    await observe(uid,r,client,str(oid),"SELL",r["gtt_qty"],reason="SL" if leg_index == 0 else "TARGET")
            if not children:raise RuntimeError("trigger_child_missing")
            r=store.load(uid)["rules"][r["id"]]
            if any(r["exits"][oid]["status"] not in TERMINAL for oid in children):
                update(uid,r["id"],state="EXITING",protection_pending=True);return
            r=update(uid,r["id"],gtt_id=0,gtt_qty=0,protection_pending=True,protection_attempted=False,protection_claim="")
        elif g.get("status")!="active":raise RuntimeError("owned_protection_not_active")
    for oid,o in list(r["exits"].items()):
        if o.get("intent_key"):await observe(uid,r,client,oid,"SELL",o["quantity"],o["intent_key"])
    r=store.load(uid)["rules"][r["id"]]
    if any(o.get("status") not in TERMINAL for o in r["exits"].values()):return
    qty=remaining(r)
    if not qty:
        if r.get("entry_status") in TERMINAL and not r.get("gtt_id"):
            update(uid,r["id"],state="CLOSED",protection_pending=False)
        return
    if not r.get("gtt_id") or r.get("gtt_qty")!=qty:
        if r.get("protection_pending") and r.get("protection_attempted") and not r.get("gtt_id"):
            raise RuntimeError("uncertain_protection_requires_reconciliation")
        if repair_protection:
            await protect(uid,r,client)


async def exit_position(uid,rid,percent):
    async with lock(uid):
        r=store.load(uid)["rules"][rid];_,client=await client_for(uid,r["account_id"])
        await manage(uid,r,client,repair_protection=False);r=store.load(uid)["rules"][rid]
        if r.get("entry_status") not in TERMINAL:raise RuntimeError("entry_remainder_requires_reconciliation")
        if any(o.get("status") not in TERMINAL for o in r["exits"].values()):raise RuntimeError("exit_already_working")
        qty=await owned_units(uid,r,client)
        take=min(qty,max(r["lot_size"],qty*percent//100//r["lot_size"]*r["lot_size"]))
        if not take:raise ValueError("no_owned_position")
        preparation = str(uuid.uuid4())
        def claim_exit(doc):
            current = doc["rules"][rid]
            if current["revision"] != r["revision"] or current.get("exit_preparation") or current.get("exit_claim") or current.get("protection_claim"):
                raise RuntimeError("exit_already_claimed")
            current.update(exit_preparation=preparation, state="EXITING", protection_pending=True)
            current["revision"] += 1
        store.mutate(uid, claim_exit)
        if r.get("gtt_id"):
            g=verify_trigger(r,await client.get_gtt(r["gtt_id"]),r["gtt_qty"])
            if g.get("status")!="active" or any(o.get("result") for o in g["orders"]):raise RuntimeError("stop_child_requires_reconciliation")
            update(uid,rid,state="EXITING",protection_pending=True)
            from app.services.execution_identity_lock import account_lock
            async with account_lock(uid):
                await client_for(uid,r["account_id"])
                await owned_units(uid,r,client)
                with canonical_broker_capability(f"price-rule-cancel:{rid}",intent=INTENT_CANCEL):await client.delete_gtt(r["gtt_id"])
            after=await client.get_gtt(r["gtt_id"])
            if after.get("status") not in {"deleted","cancelled","disabled"} or any(o.get("result") for o in after.get("orders",[])):
                raise RuntimeError("stop_release_unconfirmed")
            r=update(uid,rid,gtt_id=0,gtt_qty=0,protection_attempted=False)
        if remaining(r)!=await owned_units(uid,r,client):raise RuntimeError("exit_quantity_changed")
        nonce=str(uuid.uuid4())
        request=ExecutionRequest(uid=uid,account_id=r["account_id"],strategy_id=STRATEGY_ID,generation_id=rid,
            signal_id=f"{rid}:exit:{nonce}",exchange=r["exchange"],symbol=r["symbol"],side="SELL",quantity=take,
            exposure_effect=ExposureEffect.REDUCE_EXPOSURE,payload={"product":"NRML"})
        update(uid,rid,state="EXITING",protection_pending=True,exit_claim=nonce,exit_signal=request.signal_id)
        result=await execution_service.submit_order(request,broker_client=client)
        if result.order_id:
            def save(doc):
                doc["rules"][rid]["exits"][result.order_id]={"filled":0,"quantity":take,"status":"OPEN","intent_key":result.intent_key}
                doc["rules"][rid]["exit_claim"]=""
                doc["rules"][rid]["exit_preparation"]=""
            store.mutate(uid,save)
        else:
            # Only explicit pre-send refusals or durable rejection may release the
            # sell claim. A timeout/unknown outcome never permits another SELL.
            intent = order_journal.get(result.intent_key) if result.intent_key else None
            definite = (result.status == "HALTED" or str(result.status).startswith("REJECTED")) and (intent is None or intent.state == "REJECTED")
            fields = dict(exit_claim="", exit_preparation="") if definite else {}
            update(uid,rid,state="RECOVERY_REQUIRED",error=result.error or "exit_outcome_unknown",**fields)
        return result.__dict__


async def cycle(uid):
    async with lock(uid):
        snapshot = store.load(uid)
        for r in list(snapshot["rules"].values()):
            if r["state"] in {"DRAFT","PAUSED","CANCELLED","CLOSED","REFUSED","EXPIRED"}:continue
            if r["state"] == "ARMED" and snapshot.get("enabled") is not True:
                if int(time.time()*1000) > r["expires_ms"]:
                    update(uid,r["id"],state="EXPIRED")
                continue
            try:
                _,client=await client_for(uid,r["account_id"])
                if r["state"]=="ARMED":
                    if int(time.time()*1000)>r["expires_ms"]:update(uid,r["id"],state="EXPIRED")
                    else:await submit(uid,r,client)
                elif r["state"] in {"SUBMITTING","UNKNOWN"} and not r.get("order_id"):
                    key=order_journal.make_key(uid=uid,account_id=r["account_id"],strategy_id=STRATEGY_ID,generation_id=r["id"],
                        signal_id=r["id"],exchange=r["exchange"],symbol=r["symbol"],side="BUY")
                    intent=order_journal.get(key)
                    if intent and intent.order_id:update(uid,r["id"],order_id=intent.order_id,intent_key=key,state="SUBMITTED")
                    else:update(uid,r["id"],state="UNKNOWN",error="submission_requires_read_only_recovery")
                else:await manage(uid,r,client)
            except Exception as exc:
                log.exception("Price Rules management failed for %s/%s",uid,r["id"])
                current=store.load(uid)["rules"][r["id"]]
                state=current["state"] if current["state"]=="ARMED" else "UNKNOWN" if current["state"]=="SUBMITTING" else "RECOVERY_REQUIRED"
                update(uid,r["id"],error=str(exc),state=state,protection_pending=state!="ARMED")


async def run_loop():
    while True:
        try:
            owners = store.known_uids()
        except Exception:
            log.exception("Price Rules owner registry unavailable")
            owners = []
        for uid in owners:
            try:
                await cycle(uid)
            except Exception:
                log.exception("Price Rules store/runtime unavailable for %s", uid)
        await asyncio.sleep(3)


async def flatten_all(uid, client=None, *, reason="emergency_square_off"):
    """Cancel unsent plans and request owned exits; ACKs never count as flat."""
    def disarm(doc):
        for rule in doc["rules"].values():
            if rule["state"] in {"DRAFT", "PAUSED", "ARMED"}:
                rule.update(state="CANCELLED", error=reason)
                rule["revision"] += 1
    store.mutate(uid, disarm)
    out = {"closed": 0, "failed": [], "skipped": []}
    for rule in list(store.load(uid)["rules"].values()):
        if rule["state"] in {"CANCELLED", "CLOSED", "REFUSED", "EXPIRED"}:
            continue
        try:
            result = await exit_position(uid, rule["id"], 100)
            if result.get("order_id"):
                out["skipped"].append(f"{rule['symbol']}: exit is already in progress; awaiting fill")
            else:
                out["failed"].append(f"{rule['symbol']}: {result.get('error') or 'unknown exit'}")
        except Exception as exc:
            out["failed"].append(f"{rule['symbol']}: {exc}")
    return out
