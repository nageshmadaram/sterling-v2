"""Durable EMA CNC stop-command fences; strictly no broker writes.

A RESERVED command is *not* a broker acknowledgement, and UNKNOWN means a
request might already have reached Kite. No function here sends, modifies, or
deletes a broker GTT. A following production adapter must use the canonical
execution/protection capability and reconcile a timed-out request BEFORE it
can release this fence. This module intentionally has no release operation.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Literal

from app.services.kite_engine import fill_ledger, order_journal
from . import live_campaign_store as store

ChangeKind = Literal["PLACE", "TIGHTEN"]
_UNRESOLVED = ("RESERVED", "OUTCOME_UNKNOWN")


class StopCommandConflict(ValueError):
    """Missing authority, changing inventory, or an unresolved stop command."""


@dataclass(frozen=True)
class StopCommand:
    command_id: str
    campaign_id: str
    owner_uid: str
    account_id: str
    kind: ChangeKind
    prior_gtt_id: str
    desired_trigger: Decimal
    held_qty: int
    revision: int
    status: str


def _parse_trigger(value) -> Decimal:
    if isinstance(value, bool):
        raise StopCommandConflict("EMA_STOP_TRIGGER_INVALID")
    try:
        number=Decimal(str(value))
        if not number.is_finite() or number<=0:
            raise ValueError()
        return number
    except (TypeError,ValueError,InvalidOperation) as exc:
        raise StopCommandConflict("EMA_STOP_TRIGGER_INVALID") from exc


def _schema(conn) -> None:
    conn.execute("""
    CREATE TABLE IF NOT EXISTS ema_live_stop_commands(
        command_id TEXT PRIMARY KEY,
        campaign_id TEXT NOT NULL,
        owner_uid TEXT NOT NULL,
        account_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        prior_gtt_id TEXT NOT NULL,
        desired_trigger TEXT NOT NULL,
        held_qty INTEGER NOT NULL,
        campaign_revision INTEGER NOT NULL,
        status TEXT NOT NULL,
        created_ms INTEGER NOT NULL
    )""")
    # One outstanding stop change per campaign *even across process restarts*.
    conn.execute("""
    CREATE UNIQUE INDEX IF NOT EXISTS ux_ema_live_stop_outstanding
    ON ema_live_stop_commands(campaign_id)
    WHERE status IN ('RESERVED','OUTCOME_UNKNOWN')
    """)


def _row(row) -> StopCommand:
    return StopCommand(
        command_id=row["command_id"],campaign_id=row["campaign_id"],
        owner_uid=row["owner_uid"],account_id=row["account_id"],
        kind=row["kind"],prior_gtt_id=row["prior_gtt_id"],
        desired_trigger=Decimal(row["desired_trigger"]),
        held_qty=int(row["held_qty"]),
        revision=int(row["campaign_revision"]),status=row["status"],
    )


def reserve_stop_command(campaign_id: str, command_id: str, *,
                         expected_revision: int, kind: ChangeKind,
                         desired_trigger) -> StopCommand:
    """Reserve one durable stop mutation intent, but never send to the broker.

    A RESERVED or UNKNOWN command fences every additional attempt. Passing
    the same idempotency key returns the same record, never a second intent.
    """
    if (not isinstance(command_id,str) or not command_id.strip()
            or command_id!=command_id.strip() or len(command_id)>160):
        raise StopCommandConflict("EMA_STOP_IDEMPOTENCY_KEY_INVALID")
    if kind not in ("PLACE","TIGHTEN"):
        raise StopCommandConflict("EMA_STOP_CHANGE_KIND_UNSUPPORTED")
    trigger=_parse_trigger(desired_trigger)
    with order_journal._transaction() as conn:
        store._schema(conn)
        _schema(conn)
        campaign=store._get(conn,campaign_id)
        old=conn.execute(
            "SELECT * FROM ema_live_stop_commands WHERE command_id=?",
            (command_id,),
        ).fetchone()
        if old is not None:
            prior=_row(old)
            if (prior.campaign_id!=campaign_id or prior.kind!=kind
                    or prior.desired_trigger!=trigger
                    or prior.revision!=expected_revision):
                raise StopCommandConflict("EMA_STOP_IDEMPOTENCY_CONFLICT")
            return prior
        if campaign.revision!=expected_revision:
            raise StopCommandConflict("EMA_STOP_CAMPAIGN_STALE_REVISION")
        if (campaign.product!="CNC" or campaign.held_qty<=0
                or campaign.orphan_stop_ids
                or campaign.protection_submission_unknown
                or campaign.flatten_requested):
            raise StopCommandConflict("EMA_STOP_CAMPAIGN_UNSAFE")
        if kind=="PLACE":
            if campaign.phase!="PROTECTION_REQUIRED" or campaign.protection is not None:
                raise StopCommandConflict("EMA_STOP_PLACE_REQUIRES_UNPROTECTED_HOLDING")
            if trigger<campaign.stop:
                raise StopCommandConflict("EMA_STOP_CANNOT_WEAKEN")
            prior_gtt_id=""
        else:
            prior=campaign.protection
            if (campaign.phase!="PROTECTED" or prior is None
                    or not prior.verified_active or prior.child_order_unresolved
                    or prior.qty!=campaign.held_qty):
                raise StopCommandConflict("EMA_STOP_TRAIL_REQUIRES_VERIFIED_COVERAGE")
            if trigger<=prior.trigger:
                raise StopCommandConflict("EMA_STOP_TRAIL_MUST_STRICTLY_TIGHTEN")
            prior_gtt_id=prior.trigger_id
        inv=fill_ledger._read_inventory(
            conn,campaign.account_id,campaign.owner_uid,campaign.symbol)
        if (inv is None or inv.net_quantity!=campaign.held_qty
                or inv.exchange!=campaign.exchange or inv.reconciliation_required):
            raise StopCommandConflict("EMA_STOP_SIGNED_INVENTORY_UNVERIFIED")
        if conn.execute("""
            SELECT 1 FROM kite_order_intents
            WHERE uid=? AND account_id=?
            AND (projection_pending=1 OR reconciliation_required=1
                 OR state IN ('RESERVED','SUBMITTING','UNKNOWN'))
            LIMIT 1
        """,(campaign.owner_uid,campaign.account_id)).fetchone():
            raise StopCommandConflict("EMA_STOP_CANONICAL_ORDER_RECOVERY_PENDING")
        # Refuse a second key for any existing pending/unknown command. Never
        # let the UNIQUE constraint itself be the only explanation to users.
        if conn.execute("""
            SELECT 1 FROM ema_live_stop_commands
            WHERE campaign_id=? AND status IN ('RESERVED','OUTCOME_UNKNOWN')
        """,(campaign_id,)).fetchone():
            raise StopCommandConflict("EMA_STOP_PREVIOUS_CHANGE_UNRESOLVED")
        from time import time
        try:
            conn.execute("""
                INSERT INTO ema_live_stop_commands
                (command_id,campaign_id,owner_uid,account_id,kind,prior_gtt_id,
                desired_trigger,held_qty,campaign_revision,status,created_ms)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """,(command_id,campaign_id,campaign.owner_uid,campaign.account_id,
                 kind,prior_gtt_id,str(trigger),campaign.held_qty,
                 campaign.revision,"RESERVED",int(time()*1000)))
        except Exception as exc:
            import sqlite3
            if isinstance(exc,sqlite3.IntegrityError):
                raise StopCommandConflict("EMA_STOP_DUPLICATE_COMMAND") from exc
            raise
        return _row(conn.execute(
            "SELECT * FROM ema_live_stop_commands WHERE command_id=?",
            (command_id,)).fetchone())


def mark_outcome_unknown(command_id: str) -> StopCommand:
    """Crash/timeout fence: UNKNOWN must never be retried blindly.

    A call here cannot send a GTT. A safe future adapter calls this before
    any external write, so process termination retains the uncertainty.
    """
    with order_journal._transaction() as conn:
        _schema(conn)
        record=conn.execute(
            "SELECT * FROM ema_live_stop_commands WHERE command_id=?",
            (command_id,),
        ).fetchone()
        if record is None:
            raise StopCommandConflict("EMA_STOP_COMMAND_NOT_FOUND")
        command=_row(record)
        if command.status=="OUTCOME_UNKNOWN":
            return command
        if command.status!="RESERVED":
            raise StopCommandConflict("EMA_STOP_COMMAND_TERMINAL")
        conn.execute("""
            UPDATE ema_live_stop_commands
            SET status='OUTCOME_UNKNOWN'
            WHERE command_id=? AND status='RESERVED'
        """,(command_id,))
        return _row(conn.execute(
            "SELECT * FROM ema_live_stop_commands WHERE command_id=?",
            (command_id,)).fetchone())


def get(command_id: str) -> StopCommand:
    """Read the durable record after a restart; no broker side effects."""
    from app.services import db
    order_journal._ready()
    with db._conn() as conn:
        _schema(conn)
        row=conn.execute(
            "SELECT * FROM ema_live_stop_commands WHERE command_id=?",
            (command_id,),
        ).fetchone()
        if row is None:
            raise StopCommandConflict("EMA_STOP_COMMAND_NOT_FOUND")
        return _row(row)
