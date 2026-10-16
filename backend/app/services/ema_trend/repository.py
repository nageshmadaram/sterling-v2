"""Durable CAS documents, ownership and transactional action outbox.

The canonical order journal and fill ledger remain the order/accounting
authorities. These tables project strategy-owned campaigns from those facts.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import time
from typing import Any

from app.services import db


class Conflict(ValueError):
    pass


class NotFound(LookupError):
    pass


def now_ms() -> int:
    return int(time.time() * 1000)


def encode(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      allow_nan=False, default=str)


def owner(value: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError("authenticated owner is required")
    if value.startswith("sim:"):
        raise ValueError("owner routes cannot switch to a simulation uid")
    return value


def _schema(conn):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS ema_trend_documents(
          owner_uid TEXT NOT NULL, kind TEXT NOT NULL, id TEXT NOT NULL,
          revision INTEGER NOT NULL, status TEXT NOT NULL,
          created_ms INTEGER NOT NULL, updated_ms INTEGER NOT NULL,
          data_json TEXT NOT NULL, PRIMARY KEY(owner_uid,kind,id));
        CREATE INDEX IF NOT EXISTS ix_ema_trend_documents
          ON ema_trend_documents(owner_uid,kind,updated_ms);
        CREATE TABLE IF NOT EXISTS ema_trend_ownership(
          owner_uid TEXT NOT NULL, symbol TEXT NOT NULL, campaign_id TEXT NOT NULL,
          PRIMARY KEY(owner_uid,symbol));
        CREATE TABLE IF NOT EXISTS ema_trend_events(
          owner_uid TEXT NOT NULL, event_id TEXT NOT NULL, campaign_id TEXT NOT NULL,
          event_type TEXT NOT NULL, at_ms INTEGER NOT NULL, data_json TEXT NOT NULL,
          PRIMARY KEY(owner_uid,event_id));
        CREATE TABLE IF NOT EXISTS ema_trend_schema(version INTEGER PRIMARY KEY);
        INSERT OR IGNORE INTO ema_trend_schema VALUES(1);
    """)


@contextmanager
def transaction():
    if not db.is_available():
        raise RuntimeError("EMA_TREND_STORE_UNAVAILABLE")
    with db.connection() as conn:
        _schema(conn)
        conn.execute("PRAGMA synchronous=FULL")
        conn.execute("BEGIN IMMEDIATE")
        yield conn


def _load(conn, uid: str, kind: str, ident: str) -> dict | None:
    row = conn.execute("SELECT data_json FROM ema_trend_documents WHERE owner_uid=? AND kind=? AND id=?",
                       (uid, kind, ident)).fetchone()
    return json.loads(row[0]) if row else None


def _put(conn, uid: str, kind: str, ident: str, document: dict,
         expected_revision: int | None = None) -> dict:
    current = _load(conn, uid, kind, ident)
    revision = int(current.get("revision", 0)) if current else 0
    if expected_revision is not None and revision != expected_revision:
        raise Conflict("STALE_REVISION")
    value = dict(document)
    value["id"] = ident
    value["revision"] = revision + 1
    value.setdefault("created_ms", now_ms())
    value["updated_ms"] = now_ms()
    conn.execute("""INSERT INTO ema_trend_documents VALUES(?,?,?,?,?,?,?,?)
        ON CONFLICT(owner_uid,kind,id) DO UPDATE SET revision=excluded.revision,
        status=excluded.status,updated_ms=excluded.updated_ms,data_json=excluded.data_json""",
        (uid, kind, ident, value["revision"], str(value.get("status", value.get("state", ""))),
         value["created_ms"], value["updated_ms"], encode(value)))
    return value


def get(uid: str, kind: str, ident: str) -> dict:
    owner(uid)
    with transaction() as conn:
        result = _load(conn, uid, kind, ident)
    if result is None:
        raise NotFound(f"{kind} not found")
    return result


def put(uid: str, kind: str, ident: str, document: dict,
        expected_revision: int | None = None) -> dict:
    owner(uid)
    with transaction() as conn:
        return _put(conn, uid, kind, ident, document, expected_revision)


def list_documents(uid: str, kind: str, *, limit: int = 500, cursor: int = 0) -> list[dict]:
    owner(uid)
    with transaction() as conn:
        rows = conn.execute("""SELECT data_json FROM ema_trend_documents
          WHERE owner_uid=? AND kind=? ORDER BY updated_ms DESC,id LIMIT ? OFFSET ?""",
          (uid, kind, min(max(limit, 1), 2000), max(cursor, 0))).fetchall()
    return [json.loads(row[0]) for row in rows]


def owners() -> list[str]:
    with transaction() as conn:
        return [r[0] for r in conn.execute("SELECT DISTINCT owner_uid FROM ema_trend_documents")]


def event(uid: str, event_id: str, campaign_id: str, kind: str, data: dict):
    with transaction() as conn:
        conn.execute("INSERT OR IGNORE INTO ema_trend_events VALUES(?,?,?,?,?,?)",
                     (owner(uid), event_id, campaign_id, kind, now_ms(), encode(data)))


def events(uid: str, campaign_id: str = "", *, limit: int = 500, cursor: int = 0):
    with transaction() as conn:
        rows = conn.execute("""SELECT * FROM ema_trend_events WHERE owner_uid=?
          AND (?='' OR campaign_id=?) ORDER BY at_ms DESC,event_id LIMIT ? OFFSET ?""",
          (owner(uid), campaign_id, campaign_id, min(max(limit, 1), 2000), max(cursor, 0))).fetchall()
    return [{**dict(r), "data": json.loads(r["data_json"])} for r in rows]


def reserve_campaign(uid: str, campaign: dict, action: dict, plan: dict) -> dict:
    """Ownership, campaign, consumed plan and outbox are one durable commit."""
    with transaction() as conn:
        settings = _load(conn, owner(uid), "settings", "config")
        if settings is None or settings["revision"] != plan["config_revision"]:
            raise Conflict("CONFIG_CHANGED")
        if settings.get("paused"):
            raise Conflict("NEW_EXPOSURE_PAUSED")
        current_plan = _load(conn, uid, "plan", plan["id"])
        if current_plan is None or current_plan.get("status") != "READY":
            raise Conflict("PLAN_ALREADY_CONSUMED_OR_BLOCKED")
        active = [json.loads(r[0]) for r in conn.execute(
            "SELECT data_json FROM ema_trend_documents WHERE owner_uid=? AND kind='campaign' AND status!='CLOSED'", (uid,))]
        cfg = settings["config"]
        if len(active) >= int(cfg["max_campaigns"]):
            raise Conflict("MAX_CAMPAIGNS")
        from decimal import Decimal
        budget = sum((Decimal(str(c["budget"])) for c in active), Decimal(0)) + Decimal(str(campaign["budget"]))
        if budget > Decimal(str(cfg["allocation_inr"])) * Decimal(str(cfg["strategy_risk_fraction"])):
            raise Conflict("STRATEGY_RISK_CAP")
        try:
            conn.execute("INSERT INTO ema_trend_ownership VALUES(?,?,?)", (uid, campaign["symbol"], campaign["id"]))
        except Exception as exc:
            import sqlite3
            if isinstance(exc, sqlite3.IntegrityError):
                raise Conflict("INSTRUMENT_ALREADY_OWNED") from exc
            raise
        saved = _put(conn, uid, "campaign", campaign["id"], campaign, 0)
        _put(conn, uid, "outbox", action["id"], {"status": "RESERVED", "action": action,
             "campaign_id": campaign["id"], "order_id": "", "intent_key": "", "projected_quantity": 0,
             "projected_value": "0", "projected_fees": "0", "expires_ms":plan["expires_ms"]}, 0)
        _put(conn, uid, "plan", plan["id"], {**current_plan, "status": "CONSUMED", "campaign_id": campaign["id"]}, current_plan["revision"])
        return saved


def save_campaign_action(uid: str, campaign: dict, expected_revision: int, action: dict | None = None,
                         command: dict | None = None):
    with transaction() as conn:
        if command is not None and _load(conn,uid,"command",command["id"]) is not None:
            raise Conflict("COMMAND_ALREADY_RESERVED")
        saved = _put(conn, uid, "campaign", campaign["id"], campaign, expected_revision)
        if saved["state"] == "CLOSED":
            conn.execute("DELETE FROM ema_trend_ownership WHERE owner_uid=? AND campaign_id=?", (uid, campaign["id"]))
        if action is not None and _load(conn, uid, "outbox", action["id"]) is None:
            _put(conn, uid, "outbox", action["id"], {"status": "RESERVED", "action": action,
                "campaign_id": campaign["id"], "order_id": "", "intent_key": "",
                "projected_quantity": 0, "projected_value": "0", "projected_fees": "0"}, 0)
        if command is not None:
            _put(conn,uid,"command",command["id"],command,0)
        return saved


def save_projection(uid: str, campaign: dict, campaign_revision: int, outbox: dict, outbox_revision: int):
    with transaction() as conn:
        result = _put(conn, uid, "campaign", campaign["id"], campaign, campaign_revision)
        _put(conn, uid, "outbox", outbox["id"], outbox, outbox_revision)
        if result["state"] == "CLOSED":
            conn.execute("DELETE FROM ema_trend_ownership WHERE owner_uid=? AND campaign_id=?", (uid, campaign["id"]))
        return result


def reset_owner(uid: str) -> dict:
    """Purge this virtual strategy after the explicit global Simulation reset.

    This API is intentionally not mounted as a strategy operation: deleting a
    campaign while its virtual order still exists would lose attribution. The
    existing owner Simulation reset must empty its virtual broker books first.
    """
    from app.services.simulation.ledger import get_ledger
    owner(uid)
    ledger=get_ledger()
    if ledger.open_orders(uid) or ledger.positions(uid,open_only=True):
        raise Conflict("SIMULATION_BOOK_MUST_BE_RESET_BEFORE_STRATEGY_ARTIFACTS")
    from . import scanner,replay
    for kind,tasks in (("scan",scanner._tasks),("run",replay._tasks)):
        for row in list_documents(uid,kind,limit=2000):
            if row["id"] in tasks:
                tasks[row["id"]].cancel()
    with transaction() as conn:
        current=_load(conn,uid,"settings","config")
        if current:
            current["config"]["auto_execute"]=False
            current["config"]["enabled"]=False
            current["paused"]=True
            _put(conn,uid,"settings","config",current,current["revision"])
        count=conn.execute("SELECT COUNT(*) FROM ema_trend_documents WHERE owner_uid=? AND kind!='settings'",(uid,)).fetchone()[0]
        conn.execute("DELETE FROM ema_trend_documents WHERE owner_uid=? AND kind!='settings'",(uid,))
        conn.execute("DELETE FROM ema_trend_ownership WHERE owner_uid=?",(uid,))
        conn.execute("DELETE FROM ema_trend_events WHERE owner_uid=?",(uid,))
    return {"deleted_documents":count,"paused":True,"live_rows_touched":0}
