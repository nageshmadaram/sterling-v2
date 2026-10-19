"""Durable, offline-safe EMA live campaign projection from canonical Kite facts.

A production entry adapter is NOT connected here. The only accepted input is a
pre-existing canonical journal intent, an independently parsed broker order
history fact, and a signed fill-ledger projection. It is impossible to reach a
Kite order-write method from this module.

The EMA campaign and the canonical journal projection acknowledgement are
committed in the SAME SQLite FULL-sync transaction. A crash before commit
leaves projection_pending=1; retry sees the same cumulative evidence and is
idempotent. A crash after commit cannot replay any quantity twice.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from time import time

from app.engines.ema_trend.live_contracts import LiveCampaign
from app.services.kite_engine import fill_ledger, order_journal
from .live_fill_projection import ProjectionRehearsal, rehearse_verified_fill
from .live_order_facts import LiveOrderEvidenceError, LiveOrderFact


class LiveStoreConflict(ValueError):
    """An unsafe campaign/journal/ledger disagreement blocks projection."""


@dataclass(frozen=True)
class CommittedProjection:
    campaign: LiveCampaign
    observation_version: int
    next_proof: str
    uncovered_qty: int


def _schema(conn) -> None:
    conn.execute("""
    CREATE TABLE IF NOT EXISTS ema_trend_live_campaigns (
        id TEXT PRIMARY KEY,
        owner_uid TEXT NOT NULL,
        account_id TEXT NOT NULL,
        exchange TEXT NOT NULL,
        symbol TEXT NOT NULL,
        product TEXT NOT NULL,
        phase TEXT NOT NULL,
        revision INTEGER NOT NULL,
        document_json TEXT NOT NULL,
        updated_ms INTEGER NOT NULL
    )""")
    conn.execute("""
    CREATE UNIQUE INDEX IF NOT EXISTS ux_ema_live_active_instrument
    ON ema_trend_live_campaigns(owner_uid,account_id,exchange,symbol,product)
    WHERE phase!='CLOSED'""")


def _decode(row) -> LiveCampaign:
    if row is None:
        raise LiveStoreConflict("EMA_LIVE_CAMPAIGN_NOT_FOUND")
    return LiveCampaign.model_validate(json.loads(row["document_json"]))


def _get(conn, ident: str) -> LiveCampaign:
    return _decode(conn.execute(
        "SELECT * FROM ema_trend_live_campaigns WHERE id=?", (ident,)
    ).fetchone())


def _save(conn, model: LiveCampaign) -> None:
    conn.execute("""
        UPDATE ema_trend_live_campaigns
        SET phase=?,revision=?,document_json=?,updated_ms=?
        WHERE id=? AND revision=?
    """, (
        model.phase, model.revision,
        model.model_dump_json(), int(time()*1000),
        model.id, model.revision-1,
    ))
    if conn.execute("SELECT changes()").fetchone()[0] != 1:
        raise LiveStoreConflict("EMA_LIVE_CAMPAIGN_CAS_CONFLICT")


def create_prepared(campaign: LiveCampaign) -> LiveCampaign:
    """Persist an externally approved PREPARED campaign, never send an order."""
    if campaign.phase != "PREPARED" or campaign.held_qty:
        raise LiveStoreConflict("EMA_LIVE_PREPARED_EMPTY_ONLY")
    if campaign.revision:
        raise LiveStoreConflict("EMA_LIVE_INITIAL_REVISION_MUST_BE_ZERO")
    with order_journal._transaction() as conn:
        _schema(conn)
        try:
            conn.execute("""
                INSERT INTO ema_trend_live_campaigns
                (id,owner_uid,account_id,exchange,symbol,product,phase,revision,document_json,updated_ms)
                VALUES(?,?,?,?,?,?,?,?,?,?)
            """, (campaign.id,campaign.owner_uid,campaign.account_id,campaign.exchange,
                  campaign.symbol,campaign.product,campaign.phase,campaign.revision,
                  campaign.model_dump_json(),int(time()*1000)))
        except Exception as exc:
            import sqlite3
            if isinstance(exc, sqlite3.IntegrityError):
                raise LiveStoreConflict("EMA_LIVE_CAMPAIGN_ID_OR_OWNERSHIP_DUPLICATED") from exc
            raise
    return campaign



def bind_acknowledged_order(campaign_id: str, intent_key: str, *,
                            expected_revision: int) -> LiveCampaign:
    """Attach only a journal-confirmed HTTP ACK; never place an order."""
    from app.engines.ema_trend.live_contracts import BrokerIntent
    with order_journal._transaction() as conn:
        _schema(conn)
        campaign = _get(conn,campaign_id)
        if campaign.revision != expected_revision or campaign.phase == "CLOSED":
            raise LiveStoreConflict("EMA_LIVE_CAMPAIGN_STALE_OR_CLOSED")
        try:
            intent = order_journal._get(conn,intent_key)
        except KeyError as exc:
            raise LiveStoreConflict("EMA_LIVE_CANONICAL_INTENT_NOT_FOUND") from exc
        if (intent.strategy_id != "ema_trend"
            or intent.uid != campaign.owner_uid
            or intent.account_id != campaign.account_id
            or intent.generation_id != campaign.config_hash
            or intent.exchange != campaign.exchange or intent.symbol != campaign.symbol
            or (intent.payload or {}).get("product") != campaign.product
            or not intent.order_id or intent.state != "SUBMITTED"
            or intent.reconciliation_required or intent.filled_quantity):
            raise LiveStoreConflict("EMA_LIVE_UNVERIFIED_CANONICAL_ORDER_ACK")
        entries, exits = list(campaign.entries), list(campaign.exits)
        matches = [(side,i,v) for side,orders in (("BUY",entries),("SELL",exits))
                   for i,v in enumerate(orders) if v.intent_key == intent_key]
        if len(matches) != 1:
            raise LiveStoreConflict("EMA_LIVE_MISSING_RESERVED_INTENT")
        side,index,previous = matches[0]
        if (side != intent.side or previous.requested_qty != intent.quantity
            or previous.order_id or previous.status != "RESERVED"
            or previous.confirmed_qty):
            raise LiveStoreConflict("EMA_LIVE_ORDER_ID_ALREADY_BOUND_OR_CONFLICTED")
        updated = BrokerIntent.model_validate({
            **previous.model_dump(mode="python"),
            "order_id":intent.order_id,"status":"ACKNOWLEDGED",
        })
        if side == "BUY":
            entries[index] = updated
        else:
            exits[index] = updated
        model = LiveCampaign.model_validate({
            **campaign.model_dump(mode="python"),
            "entries":[x.model_dump(mode="python") for x in entries],
            "exits":[x.model_dump(mode="python") for x in exits],
            "phase":"ENTRY_PENDING" if side=="BUY" else "EXIT_PENDING",
            "revision":campaign.revision+1,
        })
        _save(conn,model)
        return model


def get(ident: str) -> LiveCampaign:
    order_journal._ready()
    from app.services import db
    with db._conn() as conn:
        _schema(conn)
        return _get(conn, ident)


def _broker_status_agrees(intent: order_journal.Intent, fact: LiveOrderFact) -> bool:
    status = fact.status.upper()
    if status == "COMPLETE":
        return intent.state == "FILLED"
    if status in {"CANCELLED", "CANCELED"}:
        return intent.state == "CANCELLED"
    if status == "REJECTED":
        return intent.state == "REJECTED"
    if status in {"OPEN", "OPEN PENDING", "TRIGGER PENDING"}:
        return intent.state == ("PARTIAL" if fact.filled_quantity else "SUBMITTED")
    return False


def _verified_ledger(conn, fact: LiveOrderFact, campaign: LiveCampaign) -> None:
    key = fact.identity
    if fact.filled_quantity:
        # Not even a canonical journal observation is sufficient to book
        # holdings. Require an independently persisted signed ledger increment.
        row = conn.execute("""
            SELECT uid,symbol,exchange,side,cumulative_quantity,cumulative_value
            FROM kite_execution_increments
            WHERE account_id=? AND order_id=?
            ORDER BY cumulative_quantity DESC LIMIT 1
        """, (key.account_id,key.order_id)).fetchone()
        if (row is None or row["uid"] != key.uid or row["symbol"] != key.symbol
                or row["exchange"] != key.exchange or row["side"] != key.side
                or int(row["cumulative_quantity"]) != fact.filled_quantity
                or abs(Decimal(str(row["cumulative_value"])) - fact.cumulative_value) > Decimal("0.0001")):
            raise LiveStoreConflict("EMA_LIVE_SIGNED_ORDER_FILL_LEDGER_NOT_VERIFIED")
    inv = fill_ledger._read_inventory(conn,key.account_id,key.uid,key.symbol)
    if campaign.held_qty:
        if (inv is None or inv.exchange != key.exchange or inv.reconciliation_required
                or inv.net_quantity != campaign.held_qty):
            raise LiveStoreConflict("EMA_LIVE_BROKER_ACCOUNT_INVENTORY_RECONCILIATION_REQUIRED")
    elif inv is not None and (inv.net_quantity != 0 or inv.reconciliation_required):
        raise LiveStoreConflict("EMA_LIVE_NONZERO_OR_AMBIGUOUS_INVENTORY")


def project_verified_order(
    campaign_id: str, intent_key: str, broker_fact: LiveOrderFact,
    *, expected_revision: int,
) -> CommittedProjection:
    """Atomically CAS-update campaign and acknowledge existing journal projection.

    Call order_journal.observe_order and fill_ledger.record FIRST, using genuine
    broker facts. If either fails, this commit refuses to run; no fake broker
    acknowledgement can trigger a live order send or protective write.
    """
    with order_journal._transaction() as conn:
        _schema(conn)
        campaign = _get(conn,campaign_id)
        if campaign.revision != expected_revision:
            raise LiveStoreConflict("EMA_LIVE_CAMPAIGN_STALE_REVISION")
        if campaign.phase == "CLOSED":
            raise LiveStoreConflict("EMA_LIVE_CLOSED_RECONCILIATION_REQUIRES_OPERATOR")
        try:
            intent = order_journal._get(conn,intent_key)
        except KeyError as exc:
            raise LiveStoreConflict("EMA_LIVE_CANONICAL_INTENT_NOT_FOUND") from exc
        key = broker_fact.identity
        # Check both the canonical intent AND the independently supplied broker
        # fact against the bound campaign before inspecting projection status.
        # An unrelated account's order must not be classified as merely
        # "not yet projectable" (or accepted after a journal state transition).
        if (key.uid != campaign.owner_uid
                or key.account_id != campaign.account_id
                or key.exchange != campaign.exchange
                or key.symbol != campaign.symbol
                or key.product != campaign.product
                or intent.strategy_id != "ema_trend"
                or intent.intent_key != intent_key
                or intent.uid != campaign.owner_uid
                or intent.account_id != campaign.account_id
                or intent.generation_id != campaign.config_hash
                or intent.exchange != key.exchange or intent.symbol != key.symbol
                or intent.side != key.side or intent.quantity != key.quantity
                or intent.order_id != key.order_id
                or (intent.payload or {}).get("product") != key.product):
            raise LiveStoreConflict("EMA_LIVE_CANONICAL_INTENT_IDENTITY_CONFLICT")
        if intent.reconciliation_required:
            raise LiveStoreConflict("EMA_LIVE_CANONICAL_JOURNAL_RECONCILIATION_PENDING")
        if (not intent.projection_pending
                or intent.filled_quantity != broker_fact.filled_quantity
                or abs(Decimal(str(intent.filled_value))-broker_fact.cumulative_value)>Decimal("0.0001")
                or not _broker_status_agrees(intent,broker_fact)):
            raise LiveStoreConflict("EMA_LIVE_CANONICAL_ORDER_EVIDENCE_NOT_PROJECTABLE")

        projected: ProjectionRehearsal = rehearse_verified_fill(
            campaign,intent_key,broker_fact
        )
        _verified_ledger(conn,broker_fact,projected.campaign)
        next_campaign = projected.campaign.model_copy(update={"revision":campaign.revision+1})
        # Pydantic model_copy does not validate updates; validate the strict
        # contract before any SQLite mutation.
        next_campaign = LiveCampaign.model_validate(next_campaign.model_dump(mode="python"))
        _save(conn,next_campaign)
        updated = conn.execute("""
            UPDATE kite_order_intents SET projection_pending=0
            WHERE intent_key=? AND projection_version=?
              AND projection_pending=1
        """,(intent_key,intent.projection_version)).rowcount
        if updated != 1:
            raise LiveStoreConflict("EMA_LIVE_CANONICAL_PROJECTION_VERSION_CHANGED")
        return CommittedProjection(next_campaign,intent.projection_version,
                                   projected.next_proof,projected.uncovered_qty)


async def confirm_existing_cnc_stop(
    campaign_id: str, trigger_id: str, *, expected_revision: int,
    broker_client,
) -> LiveCampaign:
    """Read a fresh live Kite GTT and prove existing coverage; place nothing.

    A caller must never fabricate a stop from a returned place_gtt ACK. This
    records only a separately fetched active trigger with exact SELL quantity.
    The triggered LIMIT child's fillability remains uncertified.
    """
    import asyncio
    from app.services.exchanges.kite.client import KiteClient
    from .live_lifecycle import (
        LiveLifecycleEvidenceError, verify_active_cnc_gtt,
    )
    if (not isinstance(broker_client,KiteClient)
        or getattr(broker_client,"_is_paper",None) is not False):
        raise LiveStoreConflict("EMA_LIVE_REAL_KITE_READ_CLIENT_REQUIRED")
    campaign = get(campaign_id)
    if (campaign.revision != expected_revision
        or campaign.account_id != getattr(broker_client,"_account_id",None)
        or campaign.phase != "PROTECTION_REQUIRED"
        or campaign.protection_submission_unknown
        or campaign.held_qty <= 0 or campaign.protection is not None):
        raise LiveStoreConflict("EMA_LIVE_STOP_CONFIRMATION_GATE_BLOCKED")
    if not trigger_id.isdigit():
        raise LiveStoreConflict("EMA_LIVE_GTT_ID_INVALID")
    snapshot, orderbook = await asyncio.gather(
        asyncio.wait_for(broker_client.get_gtt(int(trigger_id)),10),
        asyncio.wait_for(broker_client.get_orders(),10),
    )
    if not isinstance(orderbook,list) or not all(isinstance(row,dict) for row in orderbook):
        raise LiveStoreConflict("EMA_LIVE_BROKER_ORDER_BOOK_UNREADABLE")
    # An outstanding SELL child or manual exit might fill after the read and
    # make the proposed protection oversized. Refuse until it is reconciled.
    if any(
        row.get("exchange")==campaign.exchange
        and row.get("tradingsymbol")==campaign.symbol
        and str(row.get("transaction_type","")).upper()=="SELL"
        and str(row.get("status","")).upper()
        not in {"COMPLETE","CANCELLED","REJECTED"}
        for row in orderbook
    ):
        raise LiveStoreConflict("EMA_LIVE_POTENTIAL_DUPLICATE_EXIT_ORDER")
    stop = verify_active_cnc_gtt(campaign,snapshot,trigger_id=trigger_id)
    with order_journal._transaction() as conn:
        _schema(conn)
        fresh = _get(conn,campaign_id)
        if (fresh.revision != expected_revision
                or fresh.model_dump(mode="json") != campaign.model_dump(mode="json")):
            raise LiveStoreConflict("EMA_LIVE_STOP_CONFIRMATION_CAS_CONFLICT")
        inv = fill_ledger._read_inventory(conn,campaign.account_id,
                                          campaign.owner_uid,campaign.symbol)
        if (inv is None or inv.net_quantity != campaign.held_qty
                or inv.exchange != campaign.exchange
                or inv.reconciliation_required):
            raise LiveStoreConflict("EMA_LIVE_STOP_INVENTORY_UNVERIFIED")
        if conn.execute("""
            SELECT 1 FROM kite_order_intents
            WHERE uid=? AND account_id=?
              AND (projection_pending=1 OR reconciliation_required=1)
            LIMIT 1
        """,(campaign.owner_uid,campaign.account_id)).fetchone():
            raise LiveStoreConflict("EMA_LIVE_STOP_CANONICAL_PROJECTION_PENDING")
        # A timed-out PLACE or TIGHTEN may already have created another GTT.
        # Checking a single active trigger is NOT sufficient to claim unique
        # protective coverage while that prior mutation remains unresolved.
        from . import live_protection_outbox
        live_protection_outbox._schema(conn)
        if conn.execute("""
            SELECT 1 FROM ema_live_stop_commands
            WHERE campaign_id=? AND status IN ('RESERVED','OUTCOME_UNKNOWN','ACK_PENDING_VERIFICATION')
            LIMIT 1
        """,(campaign.id,)).fetchone():
            raise LiveStoreConflict("EMA_LIVE_STOP_CHANGE_OUTCOME_UNRESOLVED")
        model = LiveCampaign.model_validate({
            **campaign.model_dump(mode="python"),
            "protection":stop.model_dump(mode="python"),
            "phase":"PROTECTED","revision":campaign.revision+1,
        })
        _save(conn,model)
        return model
