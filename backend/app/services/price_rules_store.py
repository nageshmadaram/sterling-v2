"""Durable fourth-strategy ownership. JSON commits use the existing FULL-sync DB."""
from __future__ import annotations
import json
import math
from types import SimpleNamespace
from app.services import db
PREFIX = "price_rules:"


def validate(doc):
    if not isinstance(doc, dict) or not isinstance(doc.get("rules"), dict):
        raise RuntimeError("price_rules_store_unreadable")
    if "enabled" in doc and type(doc["enabled"]) is not bool:
        raise RuntimeError("price_rules_enabled_unreadable")
    states = {"DRAFT", "PAUSED", "ARMED", "CANCELLED", "CLOSED", "REFUSED", "EXPIRED", "SUBMITTING", "UNKNOWN", "SUBMITTED", "OPEN", "EXITING", "RECOVERY_REQUIRED"}
    for key, rule in doc["rules"].items():
        if not isinstance(rule, dict) or rule.get("id") != key or rule.get("state") not in states:
            raise RuntimeError("price_rules_record_unreadable")
        if not rule.get("account_id") or not rule.get("symbol") or rule.get("exchange") not in {"NFO", "BFO"}:
            raise RuntimeError("price_rules_identity_unreadable")
        for field in ("quantity", "filled", "lot_size", "revision"):
            value = rule.get(field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise RuntimeError("price_rules_quantity_unreadable")
        if not rule["quantity"] or not rule["lot_size"] or rule["filled"] > rule["quantity"]:
            raise RuntimeError("price_rules_quantity_conflict")
        if not isinstance(rule.get("exits"), dict):
            raise RuntimeError("price_rules_exits_unreadable")
        exited = 0
        for order in rule["exits"].values():
            value = order.get("filled") if isinstance(order, dict) else None
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise RuntimeError("price_rules_exit_quantity_unreadable")
            exited += value
        if exited > rule["filled"]:
            raise RuntimeError("price_rules_owned_quantity_negative")
        if rule["state"] in {"CLOSED", "CANCELLED", "REFUSED", "EXPIRED"} and exited != rule["filled"]:
            raise RuntimeError("price_rules_terminal_exposure_conflict")
        tick = rule.get("tick")
        if isinstance(tick, bool) or not isinstance(tick, (int, float)) or not math.isfinite(tick) or tick <= 0:
            raise RuntimeError("price_rules_tick_unreadable")
    return doc


def load(uid):
    raw = db.get_config_strict(PREFIX + uid)
    if not raw:
        return {"revision": 0, "enabled": False, "rules": {}}
    doc = json.loads(raw)
    return validate(doc)


def mutate(uid, change):
    if not db.is_available():
        raise RuntimeError("price_rules_store_unavailable")
    with db._conn() as conn:
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT value FROM system_config WHERE key=?", (PREFIX + uid,)).fetchone()
        doc = json.loads(row[0]) if row and row[0] else {"revision": 0, "enabled": False, "rules": {}}
        validate(doc)
        result = change(doc)
        validate(doc)
        doc["revision"] += 1
        conn.execute("INSERT OR REPLACE INTO system_config(key,value) VALUES (?,?)",
                     (PREFIX + uid, json.dumps(doc, allow_nan=False, sort_keys=True)))
    return result


def known_uids():
    return [key[len(PREFIX):] for key in db.get_all_configs_strict() if key.startswith(PREFIX)]


def open_positions(uid):
    positions = []
    for r in load(uid)["rules"].values():
        if r.get("state") in {"SUBMITTING", "UNKNOWN", "SUBMITTED", "OPEN", "EXITING", "RECOVERY_REQUIRED"}:
            q = int(r.get("filled", 0)) - sum(int(o.get("filled", 0)) for o in r.get("exits", {}).values())
            if q < 0:
                raise RuntimeError("price_rules_owned_quantity_negative")
            positions.append(SimpleNamespace(
                symbol=r["symbol"], exchange=r["exchange"], product="NRML", vehicle="option",
                account_id=r["account_id"], qty=q, quantity=q, status="OPEN" if q else r["state"], stop_mode="broker",
                stop=r.get("stop", 0), gtt_id=r.get("gtt_id", 0), gtt_qty=r.get("gtt_qty", 0),
                protection_pending=r.get("protection_pending", True) or r["state"] in {"UNKNOWN", "RECOVERY_REQUIRED"},
                order_id=r.get("order_id", ""), exit_order_id=r.get("exit_order_id", ""),
                qty_by_order={oid: o.get("filled", 0) for oid, o in r.get("exits", {}).items()},
            ))
    return positions
