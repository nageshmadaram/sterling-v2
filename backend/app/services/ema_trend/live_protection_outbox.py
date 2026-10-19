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
_UNRESOLVED = ("RESERVED", "OUTCOME_UNKNOWN", "ACK_PENDING_VERIFICATION")


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
    broker_trigger_id: str = ""


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
    # Upgrade existing databases without discarding their pending commands.
    # A GTT ACK is still unverified and must hold the same durable fence.
    columns = {row["name"] for row in conn.execute(
        "PRAGMA table_info(ema_live_stop_commands)"
    ).fetchall()}
    if "broker_trigger_id" not in columns:
        conn.execute(
            "ALTER TABLE ema_live_stop_commands ADD COLUMN "
            "broker_trigger_id TEXT NOT NULL DEFAULT ''"
        )
    # One outstanding stop change per campaign *even across process restarts*.
    conn.execute("""
    CREATE UNIQUE INDEX IF NOT EXISTS ux_ema_live_stop_outstanding
    ON ema_live_stop_commands(campaign_id)
    WHERE status IN ('RESERVED','OUTCOME_UNKNOWN')
    """)
    conn.execute("""
    CREATE UNIQUE INDEX IF NOT EXISTS ux_ema_live_stop_outstanding_v2
    ON ema_live_stop_commands(campaign_id)
    WHERE status IN ('RESERVED','OUTCOME_UNKNOWN','ACK_PENDING_VERIFICATION')
    """)


def _row(row) -> StopCommand:
    return StopCommand(
        command_id=row["command_id"],campaign_id=row["campaign_id"],
        owner_uid=row["owner_uid"],account_id=row["account_id"],
        kind=row["kind"],prior_gtt_id=row["prior_gtt_id"],
        desired_trigger=Decimal(row["desired_trigger"]),
        held_qty=int(row["held_qty"]),
        revision=int(row["campaign_revision"]),status=row["status"],
        broker_trigger_id=row["broker_trigger_id"],
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
        # Even an ACKed order may fill later and change the broker stop's
        # required size. A PARTIAL buy/exit is NOT a stable inventory snapshot.
        if any(item.status not in {"COMPLETE","CANCELLED","REJECTED"}
               for item in campaign.entries+campaign.exits):
            raise StopCommandConflict("EMA_STOP_CAMPAIGN_HAS_WORKING_ORDER")
        if conn.execute("""
            SELECT 1 FROM kite_order_intents
            WHERE uid=? AND account_id=?
            AND (projection_pending=1 OR reconciliation_required=1
                 OR state IN ('RESERVED','SUBMITTING','UNKNOWN','SUBMITTED','PARTIAL'))
            LIMIT 1
        """,(campaign.owner_uid,campaign.account_id)).fetchone():
            raise StopCommandConflict("EMA_STOP_CANONICAL_ORDER_RECOVERY_PENDING")
        # Refuse a second key for any existing pending/unknown command. Never
        # let the UNIQUE constraint itself be the only explanation to users.
        if conn.execute("""
            SELECT 1 FROM ema_live_stop_commands
            WHERE campaign_id=? AND status IN ('RESERVED','OUTCOME_UNKNOWN','ACK_PENDING_VERIFICATION')
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
        if command.status in {"OUTCOME_UNKNOWN", "ACK_PENDING_VERIFICATION"}:
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



def claim_stop_transmission(command_id: str) -> StopCommand:
    """Single-use durable pre-send claim, suitable for fake-Kite failure drills.

    This operation itself sends NOTHING to Kite. It atomically transitions
    RESERVED to OUTCOME_UNKNOWN before a broker adapter could attempt a write.
    A second concurrent/restarted worker cannot claim the same command.
    A crash after claim always requires reconciliation; never blind retry.
    The production EMA broker-write adapter remains unconnected.
    """
    with order_journal._transaction() as conn:
        store._schema(conn)
        _schema(conn)
        row=conn.execute(
            "SELECT * FROM ema_live_stop_commands WHERE command_id=?",
            (command_id,),
        ).fetchone()
        if row is None:
            raise StopCommandConflict("EMA_STOP_COMMAND_NOT_FOUND")
        cmd=_row(row)
        if cmd.status != "RESERVED":
            raise StopCommandConflict("EMA_STOP_ALREADY_CLAIMED_OR_UNRESOLVED")
        campaign=store._get(conn,cmd.campaign_id)
        if (campaign.revision != cmd.revision
                or campaign.owner_uid != cmd.owner_uid
                or campaign.account_id != cmd.account_id
                or campaign.held_qty != cmd.held_qty
                or campaign.product != "CNC"
                or campaign.orphan_stop_ids
                or campaign.protection_submission_unknown
                or campaign.flatten_requested):
            raise StopCommandConflict("EMA_STOP_CLAIM_CAMPAIGN_CHANGED")
        if any(o.status not in {"COMPLETE","CANCELLED","REJECTED"}
               for o in campaign.entries+campaign.exits):
            raise StopCommandConflict("EMA_STOP_CLAIM_PENDING_CANONICAL_ORDER")
        if cmd.kind=="PLACE":
            if campaign.phase!="PROTECTION_REQUIRED" or campaign.protection is not None:
                raise StopCommandConflict("EMA_STOP_CLAIM_PLACE_NO_LONGER_ELIGIBLE")
        elif cmd.kind=="TIGHTEN":
            existing=campaign.protection
            if (campaign.phase!="PROTECTED" or existing is None
                    or not existing.verified_active or existing.child_order_unresolved
                    or existing.trigger_id!=cmd.prior_gtt_id
                    or existing.qty!=cmd.held_qty
                    or cmd.desired_trigger<=existing.trigger):
                raise StopCommandConflict("EMA_STOP_CLAIM_TRAIL_NO_LONGER_ELIGIBLE")
        else:
            raise StopCommandConflict("EMA_STOP_CLAIM_KIND_UNSUPPORTED")
        inv=fill_ledger._read_inventory(
            conn,cmd.account_id,cmd.owner_uid,campaign.symbol)
        if (inv is None or inv.net_quantity != cmd.held_qty
                or inv.exchange != campaign.exchange or inv.reconciliation_required):
            raise StopCommandConflict("EMA_STOP_CLAIM_INVENTORY_CHANGED")
        if conn.execute("""
            SELECT 1 FROM kite_order_intents
            WHERE uid=? AND account_id=?
              AND (projection_pending=1 OR reconciliation_required=1
                   OR state IN ('RESERVED','SUBMITTING','UNKNOWN','SUBMITTED','PARTIAL'))
            LIMIT 1
        """,(cmd.owner_uid,cmd.account_id)).fetchone():
            raise StopCommandConflict("EMA_STOP_CLAIM_CANONICAL_ORDER_PENDING")
        updated=conn.execute("""
            UPDATE ema_live_stop_commands SET status='OUTCOME_UNKNOWN'
            WHERE command_id=? AND status='RESERVED'
        """,(command_id,)).rowcount
        if updated != 1:
            raise StopCommandConflict("EMA_STOP_CLAIM_CONCURRENT_WORKER")
        return _row(conn.execute(
            "SELECT * FROM ema_live_stop_commands WHERE command_id=?",
            (command_id,)).fetchone())


def record_unverified_broker_ack(command_id: str, trigger_id: str) -> StopCommand:
    """Store an HTTP GTT ACK without treating it as active stop coverage.

    This must be called only after a durable OUTCOME_UNKNOWN pre-send fence.
    An ACK never authorizes retry, modification or exit. Any conflicting
    trigger id fails closed and leaves the first acknowledgement intact.
    """
    if (not isinstance(trigger_id,str) or not trigger_id.isascii()
            or not trigger_id.isdigit() or not 1<=len(trigger_id)<=24
            or trigger_id.startswith("0")):
        raise StopCommandConflict("EMA_STOP_BROKER_ACK_ID_INVALID")
    with order_journal._transaction() as conn:
        _schema(conn)
        row=conn.execute(
            "SELECT * FROM ema_live_stop_commands WHERE command_id=?",
            (command_id,),
        ).fetchone()
        if row is None:
            raise StopCommandConflict("EMA_STOP_COMMAND_NOT_FOUND")
        cmd=_row(row)
        if cmd.status=="ACK_PENDING_VERIFICATION":
            if cmd.broker_trigger_id!=trigger_id:
                raise StopCommandConflict("EMA_STOP_CONFLICTING_BROKER_ACK")
            return cmd
        if cmd.status!="OUTCOME_UNKNOWN":
            raise StopCommandConflict("EMA_STOP_ACK_REQUIRES_PRE_SEND_FENCE")
        if cmd.kind=="TIGHTEN" and cmd.prior_gtt_id!=trigger_id:
            raise StopCommandConflict("EMA_STOP_TRAIL_ACK_GTT_ID_MISMATCH")
        if conn.execute("""
            SELECT 1 FROM ema_live_stop_commands
            WHERE account_id=? AND broker_trigger_id=? AND command_id<>?
            LIMIT 1
        """,(cmd.account_id,trigger_id,command_id)).fetchone():
            raise StopCommandConflict("EMA_STOP_BROKER_TRIGGER_ALREADY_BOUND")
        updated=conn.execute("""
            UPDATE ema_live_stop_commands
            SET broker_trigger_id=?,status='ACK_PENDING_VERIFICATION'
            WHERE command_id=? AND status='OUTCOME_UNKNOWN'
        """,(trigger_id,command_id)).rowcount
        if updated!=1:
            raise StopCommandConflict("EMA_STOP_ACK_CONCURRENT_CHANGE")
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


@dataclass(frozen=True)
class StopReadback:
    """Advisory recovery evidence. Never unblocks a GTT retry or live entry."""
    command_id: str
    status: str
    blocker: str
    candidate_gtt_ids: tuple[str, ...] = ()
    broker_writes: bool = False
    retry_authorized: bool = False


async def readback_stop_command(command_id: str, *, broker_client) -> StopReadback:
    """Read Kite's books after restart without attributing an unknown request.

    A matching active GTT is only a candidate: Kite GTTs lack this application's
    immutable idempotency key, and a triggered LIMIT child may still be live.
    The fence remains UNKNOWN until independently certified reconciliation.
    """
    import asyncio
    import json
    from app.services.exchanges.kite.client import KiteClient
    cmd=get(command_id)
    if (not isinstance(broker_client,KiteClient)
            or getattr(broker_client,"_is_paper",None) is not False
            or getattr(broker_client,"_account_id",None)!=cmd.account_id):
        return StopReadback(command_id,cmd.status,"BROKER_CLIENT_IDENTITY_UNVERIFIED")
    campaign=store.get(cmd.campaign_id)
    if campaign.owner_uid!=cmd.owner_uid or campaign.account_id!=cmd.account_id:
        return StopReadback(command_id,cmd.status,"CAMPAIGN_ACCOUNT_IDENTITY_CHANGED")
    try:
        gtts,orders=await asyncio.wait_for(
            asyncio.gather(broker_client.get_gtts(),broker_client.get_orders()),10)
    except Exception:
        return StopReadback(command_id,cmd.status,"BROKER_BOOK_READ_UNAVAILABLE")
    if (not isinstance(gtts,list) or not all(isinstance(item,dict) for item in gtts)
            or not isinstance(orders,list) or not all(isinstance(item,dict) for item in orders)):
        return StopReadback(command_id,cmd.status,"BROKER_BOOK_SCHEMA_UNVERIFIED")
    ids=[]
    for item in gtts:
        cond=item.get("condition")
        if isinstance(cond,str):
            try:
                cond=json.loads(cond)
            except (TypeError,ValueError):
                return StopReadback(command_id,cmd.status,"GTT_CONDITION_UNREADABLE")
        if not isinstance(cond,dict):
            return StopReadback(command_id,cmd.status,"GTT_CONDITION_UNREADABLE")
        if (str(cond.get("exchange") or "").upper()==campaign.exchange
                and str(cond.get("tradingsymbol") or "").upper()==campaign.symbol):
            ident=item.get("id")
            if ident is None or not str(ident).isdigit():
                return StopReadback(command_id,cmd.status,"GTT_ID_UNVERIFIED")
            ids.append(str(ident))
    for item in orders:
        if (str(item.get("exchange") or "").upper()==campaign.exchange
                and str(item.get("tradingsymbol") or "").upper()==campaign.symbol
                and str(item.get("status") or "").upper() not in {
                    "COMPLETE","CANCELLED","CANCELED","REJECTED","EXPIRED"
                }):
            return StopReadback(command_id,cmd.status,
                                "BROKER_CHILD_OR_MANUAL_ORDER_UNRESOLVED",
                                tuple(sorted(set(ids))))
    candidates=tuple(sorted(set(ids)))
    if cmd.status=="ACK_PENDING_VERIFICATION":
        if cmd.broker_trigger_id not in candidates:
            return StopReadback(command_id,cmd.status,
                                "ACKED_GTT_NOT_PRESENT_IN_BROKER_BOOK",candidates)
        if len(candidates)!=1:
            return StopReadback(command_id,cmd.status,
                                "MULTIPLE_GTT_IDENTITIES_REQUIRE_RECONCILIATION",candidates)
        return StopReadback(command_id,cmd.status,
                            "ACKED_GTT_REQUIRES_EXACT_STOP_COVERAGE_VERIFICATION",
                            candidates)
    if candidates:
        return StopReadback(command_id,cmd.status,
                            "POSSIBLE_EXISTING_GTT_REQUIRES_OPERATOR_RECONCILIATION",
                            candidates)
    return StopReadback(command_id,cmd.status,
                        "ABSENCE_OF_GTT_DOES_NOT_PROVE_NO_PRIOR_SUBMISSION")


async def verify_acknowledged_stop(command_id: str, *, broker_client) -> StopCommand:
    """Atomically settle an ACK only after independent live broker read-back.

    NO broker mutation occurs. GTT placement/replacement must already have
    happened through a future, separately certified canonical write adapter.
    The existence of one active GTT only establishes declared broker coverage,
    not that its LIMIT child will fill in a price gap.
    """
    import asyncio
    import json
    from app.engines.ema_trend.live_contracts import LiveCampaign
    from app.services.exchanges.kite.client import KiteClient
    from .live_lifecycle import verify_active_cnc_gtt
    from .live_exit_preflight import _inventory

    cmd=get(command_id)
    if cmd.status!="ACK_PENDING_VERIFICATION" or not cmd.broker_trigger_id:
        raise StopCommandConflict("EMA_STOP_BROKER_ACK_UNVERIFIED")
    if (not isinstance(broker_client,KiteClient)
            or getattr(broker_client,"_is_paper",None) is not False
            or getattr(broker_client,"_account_id",None)!=cmd.account_id):
        raise StopCommandConflict("EMA_STOP_READBACK_ACCOUNT_IDENTITY_UNPROVEN")
    campaign=store.get(cmd.campaign_id)
    if (campaign.revision!=cmd.revision
            or campaign.owner_uid!=cmd.owner_uid
            or campaign.account_id!=cmd.account_id
            or campaign.held_qty!=cmd.held_qty
            or campaign.product!="CNC" or campaign.orphan_stop_ids
            or campaign.protection_submission_unknown):
        raise StopCommandConflict("EMA_STOP_READBACK_CAMPAIGN_CHANGED")
    if cmd.kind=="PLACE":
        if campaign.phase!="PROTECTION_REQUIRED" or campaign.protection is not None:
            raise StopCommandConflict("EMA_STOP_READBACK_PLACE_NOT_ELIGIBLE")
    elif cmd.kind=="TIGHTEN":
        prior=campaign.protection
        if (campaign.phase!="PROTECTED" or prior is None
                or not prior.verified_active or prior.child_order_unresolved
                or prior.trigger_id!=cmd.prior_gtt_id
                or prior.qty!=campaign.held_qty
                or cmd.broker_trigger_id!=prior.trigger_id
                or cmd.desired_trigger<=prior.trigger):
            raise StopCommandConflict("EMA_STOP_READBACK_TRAIL_NOT_ELIGIBLE")
    else:
        raise StopCommandConflict("EMA_STOP_READBACK_KIND_INVALID")
    if any(item.status not in {"COMPLETE","CANCELLED","REJECTED"}
           for item in campaign.entries+campaign.exits):
        raise StopCommandConflict("EMA_STOP_READBACK_WORKING_CANONICAL_ORDER")
    try:
        trigger_id=int(cmd.broker_trigger_id)
        details,gtts,orders,positions,holdings=await asyncio.wait_for(
            asyncio.gather(
                broker_client.get_gtt(trigger_id),
                broker_client.get_gtts(),
                broker_client.get_orders(),
                broker_client.get_positions_raw(),
                broker_client.get_holdings(),
            ),timeout=10)
    except Exception as exc:
        raise StopCommandConflict("EMA_STOP_READBACK_BROKER_FACTS_UNAVAILABLE") from exc
    if (not isinstance(gtts,list) or not all(isinstance(r,dict) for r in gtts)
            or not isinstance(orders,list) or not all(isinstance(r,dict) for r in orders)):
        raise StopCommandConflict("EMA_STOP_READBACK_BROKER_BOOK_INVALID")
    active=[]
    found=False
    for row in gtts:
        condition=row.get("condition")
        if isinstance(condition,str):
            try:
                condition=json.loads(condition)
            except (ValueError,TypeError) as exc:
                raise StopCommandConflict("EMA_STOP_READBACK_GTT_CONDITION_INVALID") from exc
        if not isinstance(condition,dict):
            raise StopCommandConflict("EMA_STOP_READBACK_GTT_CONDITION_INVALID")
        if (str(condition.get("exchange") or "").upper()==campaign.exchange
                and str(condition.get("tradingsymbol") or "").upper()==campaign.symbol):
            status=str(row.get("status") or "").lower()
            if status not in {"cancelled","canceled","disabled","deleted","expired","rejected"}:
                active.append(str(row.get("id") or ""))
            if str(row.get("id") or "")==cmd.broker_trigger_id and status=="active":
                found=True
    if not found or active!=[cmd.broker_trigger_id]:
        raise StopCommandConflict("EMA_STOP_READBACK_MISSING_OR_COMPETING_GTT")
    if any(
        str(row.get("exchange") or "").upper()==campaign.exchange
        and str(row.get("tradingsymbol") or "").upper()==campaign.symbol
        and str(row.get("status") or "").upper()
        not in {"COMPLETE","CANCELLED","CANCELED","REJECTED","EXPIRED"}
        for row in orders
    ):
        raise StopCommandConflict("EMA_STOP_READBACK_WORKING_BROKER_ORDER")
    try:
        broker_qty=_inventory(campaign,positions,holdings)
    except ValueError as exc:
        raise StopCommandConflict("EMA_STOP_READBACK_INVENTORY_UNVERIFIABLE") from exc
    if broker_qty!=campaign.held_qty:
        raise StopCommandConflict("EMA_STOP_READBACK_BROKER_HOLDING_MISMATCH")
    try:
        confirmed=verify_active_cnc_gtt(campaign,details,
                                         trigger_id=cmd.broker_trigger_id)
    except ValueError as exc:
        # The strict GTT parser rejects a stop below the campaign floor
        # before the request-specific tighter-stop comparison is reached.
        # Preserve this more precise fail-closed reason; other invalid broker
        # contracts remain classified as unverifiable.
        if str(exc) == "EMA_GTT_STOP_WEAKER_THAN_CAMPAIGN":
            raise StopCommandConflict("EMA_STOP_READBACK_GTT_WEAKER_THAN_REQUEST") from exc
        raise StopCommandConflict("EMA_STOP_READBACK_GTT_CONTRACT_UNVERIFIED") from exc
    if confirmed.trigger<cmd.desired_trigger:
        raise StopCommandConflict("EMA_STOP_READBACK_GTT_WEAKER_THAN_REQUEST")

    # All state writes, including releasing the outstanding fence, must commit
    # with FULL synchronous durability in ONE transaction. A crash rolls back
    # the stop state, journal check and command together.
    with order_journal._transaction() as conn:
        store._schema(conn)
        _schema(conn)
        fresh=store._get(conn,cmd.campaign_id)
        row=conn.execute(
            "SELECT * FROM ema_live_stop_commands WHERE command_id=?",
            (command_id,),
        ).fetchone()
        if row is None or _row(row)!=cmd or fresh!=campaign:
            raise StopCommandConflict("EMA_STOP_READBACK_CHANGED_DURING_VERIFY")
        inv=fill_ledger._read_inventory(
            conn,campaign.account_id,campaign.owner_uid,campaign.symbol)
        if (inv is None or inv.net_quantity!=campaign.held_qty
                or inv.exchange!=campaign.exchange or inv.reconciliation_required):
            raise StopCommandConflict("EMA_STOP_READBACK_SIGNED_LEDGER_DIVERGED")
        if conn.execute("""
            SELECT 1 FROM kite_order_intents
            WHERE uid=? AND account_id=?
              AND (projection_pending=1 OR reconciliation_required=1
                   OR state IN ('RESERVED','SUBMITTING','UNKNOWN','SUBMITTED','PARTIAL'))
            LIMIT 1
        """,(campaign.owner_uid,campaign.account_id)).fetchone():
            raise StopCommandConflict("EMA_STOP_READBACK_CANONICAL_ORDER_PENDING")
        updated=LiveCampaign.model_validate({
            **campaign.model_dump(mode="python"),
            "protection":confirmed.model_dump(mode="python"),
            "stop":max(campaign.stop,confirmed.trigger),
            "phase":"PROTECTED",
            "revision":campaign.revision+1,
        })
        store._save(conn,updated)
        changed=conn.execute("""
            UPDATE ema_live_stop_commands SET status='VERIFIED'
            WHERE command_id=? AND status='ACK_PENDING_VERIFICATION'
              AND broker_trigger_id=?
        """,(command_id,cmd.broker_trigger_id)).rowcount
        if changed!=1:
            raise StopCommandConflict("EMA_STOP_READBACK_ACK_CONCURRENT_CHANGE")
        return _row(conn.execute(
            "SELECT * FROM ema_live_stop_commands WHERE command_id=?",
            (command_id,)).fetchone())
