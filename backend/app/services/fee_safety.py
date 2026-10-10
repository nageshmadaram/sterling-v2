"""Missing broker charges cannot certify permission for another live entry.

Books known calculated broker charges with provenance. An absent/malformed quote
is pending, never zero. Refresh is read-only at the broker; reductions do not wait
for it. Gamma fees use separate additive events to avoid shared cost-basis mixing.
"""
from __future__ import annotations

import json
import logging
from math import isfinite
from datetime import datetime
from app.services import db
from app.services.kite_engine import fill_ledger, state

log = logging.getLogger(__name__)


def _jobs(uid: str, account: str) -> list[dict]:
    day = fill_ledger.ist_today()
    start = int(datetime.fromisoformat(day + "T00:00:00+05:30").timestamp() * 1000)
    jobs = {}
    with db._conn() as conn:
        rows = conn.execute("SELECT * FROM kite_execution_increments WHERE uid=? AND account_id=? "
                            "AND day_iso=? ORDER BY id", (uid, account, day)).fetchall()
        for row in rows:
            if not str(row["fees_source"] or ""):
                jobs[row["order_id"]] = dict(order_id=row["order_id"], exchange=row["exchange"],
                    tradingsymbol=row["symbol"], transaction_type=row["side"], quantity=row["cumulative_quantity"],
                    average_price=float(row["cumulative_value"]) / row["cumulative_quantity"],
                    product="", day=day, ledger=True)
        # Include Gamma entry charges as well as exit charges. These executions
        # deliberately do not enter the shared cost-basis ledger.
        rows = conn.execute("SELECT * FROM kite_order_intents WHERE uid=? AND account_id=? "
                            "AND strategy_id='gamma_move' AND observed_quantity>0 AND created_ms>=?",
                            (uid, account, start)).fetchall()
        for row in rows:
            oid = str(row["order_id"] or "")
            if not oid or conn.execute("SELECT 1 FROM kite_realized_pnl_events WHERE uid=? AND event_key=?",
                                      (uid, f"broker-fees:{account}:{oid}:{row['observed_quantity']}")).fetchone():
                continue
            payload = json.loads(row["payload_json"] or "{}")
            jobs[oid] = dict(order_id=oid, exchange=row["exchange"], tradingsymbol=row["symbol"],
                transaction_type=row["side"], quantity=row["observed_quantity"],
                average_price=float(row["observed_value"]) / row["observed_quantity"],
                product=str(payload.get("product") or "NRML"), day=day, ledger=False)
        prefix = f"gamma-unwind:{account}:"
        rows = conn.execute("SELECT event_key,day_iso FROM kite_realized_pnl_events WHERE uid=? AND day_iso=?",
                            (uid, day)).fetchall()
        for row in rows:
            key = str(row["event_key"])
            if not key.startswith(prefix):
                continue
            oid = key[len(prefix):]
            fee_prefix = f"broker-fees:{account}:{oid}:"
            if not conn.execute("SELECT 1 FROM kite_realized_pnl_events WHERE uid=? AND substr(event_key,1,?)=?",
                                (uid, len(fee_prefix), fee_prefix)).fetchone():
                jobs.setdefault(oid, dict(order_id=oid, day=row["day_iso"], ledger=False))
    return list(jobs.values())


def admission_blocker(uid: str, account_id: str) -> tuple[str, str]:
    from app.services.simulation.identity import is_sim_uid
    if is_sim_uid(uid) or str(account_id).lower() == "paper":
        return "", ""
    try:
        if db.get_config_strict(f"fee_reconciliation_fault:{uid}:{account_id}"):
            return "fee_reconciliation_required", "Broker fee reconciliation failed"
        if _jobs(uid, account_id):
            return "broker_fees_pending", "Broker charges are incomplete; daily loss is still gross"
        return "", ""
    except Exception as exc:
        return "broker_fees_unreadable", f"Broker fee accounting cannot be established: {type(exc).__name__}"


async def refresh(client, uid: str, account_id: str) -> None:
    if getattr(client, "_is_paper", None) is not False or str(getattr(client, "_account_id", "")) != account_id:
        return
    charges = getattr(client, "order_charges", None)
    if not callable(charges):
        return
    try:
        jobs = _jobs(uid, account_id)
        if not jobs:
            db.set_config_strict(f"fee_reconciliation_fault:{uid}:{account_id}", "")
            return
        orders = None
        for job in jobs:
            oid = job["order_id"]
            # Ledger raw evidence and the durable journal identify the order;
            # missing product/history is obtained by exact id, never field matches.
            if not job.get("product"):
                if orders is None:
                    orders = await client.get_orders()
                    if not isinstance(orders, list):
                        return
                order = next((o for o in orders if isinstance(o, dict) and str(o.get("order_id")) == oid), None)
                if order is None and callable(getattr(client, "get_order_history", None)):
                    history = await client.get_order_history(oid)
                    order = history[-1] if isinstance(history, list) and history else None
                if not isinstance(order, dict):
                    continue
                if str(order.get("order_id")) != oid:
                    raise ValueError("fee_order_identity_conflict")
                if "tradingsymbol" in job and (
                    str(order.get("tradingsymbol")) != job["tradingsymbol"]
                    or str(order.get("exchange")) != job["exchange"]
                    or str(order.get("transaction_type")).upper() != job["transaction_type"].upper()
                ):
                    raise ValueError("fee_order_identity_conflict")
                if "tradingsymbol" not in job:
                    for key in ("exchange", "tradingsymbol", "transaction_type", "average_price"):
                        job[key] = order.get(key)
                    job["quantity"] = order.get("filled_quantity")
                job["product"] = order.get("product")
            raw_quantity = float(job.get("quantity") or 0)
            if not isfinite(raw_quantity) or not raw_quantity.is_integer():
                raise ValueError("invalid_fee_quantity")
            quantity, price = int(raw_quantity), float(job.get("average_price") or 0)
            if quantity <= 0 or not isfinite(price) or price <= 0 or not job.get("product"):
                continue
            body = {key:job[key] for key in ("order_id", "exchange", "tradingsymbol", "transaction_type",
                                           "quantity", "average_price", "product")}
            body.update(variety="regular", order_type="MARKET")
            quote = await charges([body])
            if not isinstance(quote, list) or len(quote) != 1 or not isinstance(quote[0], dict):
                continue
            if quote[0].get("order_id") is not None and str(quote[0]["order_id"]) != oid:
                raise ValueError("fee_quote_identity_conflict")
            total = (quote[0].get("charges") or {}).get("total")
            if total is None:
                continue
            if isinstance(total, bool):
                raise ValueError("invalid_broker_fee_total")
            total = float(total)
            if not isfinite(total) or total < 0:
                raise ValueError("invalid_broker_fee_total")
            if job["ledger"]:
                fill_ledger.apply_fees(account_id=account_id, uid=uid, symbol=job["tradingsymbol"],
                    order_id=oid, fees=total, source="broker_calculated", expected_quantity=quantity)
            else:
                prefix = f"broker-fees:{account_id}:{oid}:"
                with db._conn() as conn:
                    previous = -float(conn.execute(
                        "SELECT COALESCE(SUM(pnl),0) FROM kite_realized_pnl_events "
                        "WHERE uid=? AND substr(event_key,1,?)=?",
                        (uid, len(prefix), prefix)).fetchone()[0])
                if total < previous:
                    raise ValueError("broker_fee_cumulative_regression")
                state.record_realized_pnl_once(uid, f"{prefix}{quantity}", -(total - previous),
                    day_iso=job["day"], source="broker_calculated_fees")
        db.set_config_strict(f"fee_reconciliation_fault:{uid}:{account_id}", "")
    except Exception as exc:
        log.error("Broker fee reconciliation remains pending for %s/%s: %s", uid, account_id, exc)
        try:
            db.set_config_strict(f"fee_reconciliation_fault:{uid}:{account_id}", type(exc).__name__)
        except Exception as persistence_exc:
            log.critical("Fee reconciliation fault persistence failed: %s", persistence_exc)
