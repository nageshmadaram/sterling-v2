"""Durable, read-only EMA recovery when a CNC broker GTT stops being active.

A Kite GTT 'triggered' event is NOT a completed SELL. The associated LIMIT
child can rest, fill partly, be rejected, or belong to a different instrument.
Here we persist only an untrusted-but-attributed observation and quarantine
the previously PROTECTED campaign in one SQLite transaction. We never credit
sold quantity, release the orphan GTT, cancel a child or send another exit.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

from app.engines.ema_trend.live_contracts import LiveCampaign
from app.services.kite_engine import fill_ledger, order_journal
from . import live_campaign_store as store


class GTTChildRecoveryConflict(ValueError):
    """Unable to prove the exact live owner, stop and broker child identity."""


@dataclass(frozen=True)
class GTTChildObservation:
    campaign_id: str
    trigger_id: str
    verdict: str
    child_order_id: str
    child_status: str
    child_filled_observed: int
    held_qty_unmodified: int
    campaign_revision: int
    broker_orders_issued: bool = False
    signed_fill_projected: bool = False


def _schema(conn) -> None:
    conn.execute("""
    CREATE TABLE IF NOT EXISTS ema_live_gtt_child_recovery (
        campaign_id TEXT NOT NULL,
        trigger_id TEXT NOT NULL,
        owner_uid TEXT NOT NULL,
        account_id TEXT NOT NULL,
        exchange TEXT NOT NULL,
        symbol TEXT NOT NULL,
        product TEXT NOT NULL,
        verdict TEXT NOT NULL,
        child_order_id TEXT NOT NULL,
        child_status TEXT NOT NULL,
        child_filled_observed INTEGER NOT NULL,
        held_qty_unmodified INTEGER NOT NULL,
        campaign_revision INTEGER NOT NULL,
        observed_ms INTEGER NOT NULL,
        PRIMARY KEY(campaign_id,trigger_id)
    )""")


def _row(record) -> GTTChildObservation:
    return GTTChildObservation(
        campaign_id=record["campaign_id"],
        trigger_id=record["trigger_id"],
        verdict=record["verdict"],
        child_order_id=record["child_order_id"],
        child_status=record["child_status"],
        child_filled_observed=int(record["child_filled_observed"]),
        held_qty_unmodified=int(record["held_qty_unmodified"]),
        campaign_revision=int(record["campaign_revision"]),
    )


def get_observation(campaign_id: str, trigger_id: str) -> GTTChildObservation:
    from app.services import db
    order_journal._ready()
    with db._conn() as conn:
        _schema(conn)
        row=conn.execute("""
            SELECT * FROM ema_live_gtt_child_recovery
            WHERE campaign_id=? AND trigger_id=?
        """,(campaign_id,trigger_id)).fetchone()
        if row is None:
            raise GTTChildRecoveryConflict("EMA_GTT_CHILD_OBSERVATION_NOT_FOUND")
        return _row(row)


def _positive_count(value: Any, *, allow_zero: bool=False) -> int:
    # Decimal prevents truncating a broker quantity such as "3.5" to 3.
    from decimal import Decimal, InvalidOperation
    if isinstance(value,bool):
        raise GTTChildRecoveryConflict("EMA_GTT_CHILD_QUANTITY_INVALID")
    try:
        qty=Decimal(str(value))
        if (not qty.is_finite() or qty!=qty.to_integral_value()
                or qty<(0 if allow_zero else 1)):
            raise ValueError()
        return int(qty)
    except (ValueError,TypeError,InvalidOperation) as exc:
        raise GTTChildRecoveryConflict("EMA_GTT_CHILD_QUANTITY_INVALID") from exc


def _read_result(gtt: dict) -> str:
    orders=gtt.get("orders")
    if not isinstance(orders,list) or len(orders)!=1 or not isinstance(orders[0],dict):
        return ""
    result=orders[0].get("result")
    if isinstance(result,str):
        try:
            result=json.loads(result)
        except (ValueError,TypeError):
            return ""
    if not isinstance(result,dict):
        return ""
    child=result.get("order_result")
    if isinstance(child,str):
        try:
            child=json.loads(child)
        except (ValueError,TypeError):
            return ""
    if not isinstance(child,dict):
        return ""
    raw=child.get("order_id")
    return str(raw).strip() if raw is not None else ""


def _evaluate(campaign: LiveCampaign, gtt: Any, orders: Any) -> tuple[str,str,str,int]:
    if not isinstance(gtt,dict) or str(gtt.get("id"))!=campaign.protection.trigger_id:
        return ("GTT_DETAIL_UNVERIFIED","","",0)
    status=str(gtt.get("status") or "").strip().lower()
    if status=="active":
        # Caller independently verifies the active GTT contract before
        # deciding to leave the previously protected campaign unchanged.
        from .live_lifecycle import verify_active_cnc_gtt
        try:
            verify_active_cnc_gtt(campaign,gtt,
                                  trigger_id=campaign.protection.trigger_id)
        except ValueError:
            return ("ACTIVE_GTT_CONTRACT_CHANGED","","",0)
        return ("ACTIVE_VERIFIED","","",0)
    if status not in {"triggered","cancelled","canceled","expired",
                      "deleted","disabled","rejected"}:
        return ("GTT_STATUS_UNVERIFIED","","",0)
    if status!="triggered":
        return ("GTT_INERT_NO_CHILD_PROVEN","","",0)
    child_id=_read_result(gtt)
    if not child_id or not isinstance(orders,list):
        return ("TRIGGERED_CHILD_UNATTRIBUTED",child_id,"",0)
    matches=[row for row in orders if isinstance(row,dict)
             and str(row.get("order_id") or "").strip()==child_id]
    if len(matches)!=1:
        return ("TRIGGERED_CHILD_MISSING_OR_DUPLICATE",child_id,"",0)
    row=matches[0]
    if any(str(row.get(key) or "").upper()!=wanted for key,wanted in (
        ("exchange",campaign.exchange),
        ("tradingsymbol",campaign.symbol),
        ("product",campaign.product),
        ("transaction_type","SELL"),
    )):
        return ("TRIGGERED_CHILD_IDENTITY_CONFLICT",child_id,"",0)
    try:
        requested=_positive_count(row.get("quantity"))
        filled=_positive_count(row.get("filled_quantity"),allow_zero=True)
    except GTTChildRecoveryConflict:
        return ("TRIGGERED_CHILD_QUANTITY_UNVERIFIED",child_id,"",0)
    if requested!=campaign.protection.qty or filled>requested:
        return ("TRIGGERED_CHILD_QUANTITY_CONFLICT",child_id,"",0)
    child_status=str(row.get("status") or "").strip().upper()
    if child_status=="COMPLETE" and filled==requested:
        return ("TRIGGERED_CHILD_COMPLETE_UNPROJECTED",child_id,child_status,filled)
    if child_status in {"REJECTED","CANCELLED","CANCELED"}:
        return ("TRIGGERED_CHILD_TERMINAL_UNPROJECTED",child_id,child_status,filled)
    if child_status in {"OPEN","OPEN PENDING","TRIGGER PENDING",
                        "VALIDATION PENDING","PUT ORDER REQ RECEIVED"}:
        return ("TRIGGERED_CHILD_WORKING_UNPROJECTED",child_id,child_status,filled)
    return ("TRIGGERED_CHILD_STATUS_UNVERIFIED",child_id,child_status,filled)


async def inspect_and_quarantine(campaign_id: str, *, expected_revision: int,
                                 broker_client) -> GTTChildObservation:
    """Observe the broker GTT once; persist an unsafe-state recovery fence.

    Never claim GTT-child fills: even 'COMPLETE' leaves confirmed_sold and
    signed inventory untouched. The canonical execution journal must adopt
    and reconcile a separately proven child before the campaign can close.
    """
    from app.services.exchanges.kite.client import KiteClient
    from app.services.kite_engine import order_journal
    if (not isinstance(broker_client,KiteClient)
            or getattr(broker_client,"_is_paper",None) is not False):
        raise GTTChildRecoveryConflict("EMA_GTT_REAL_CLIENT_REQUIRED")
    campaign=store.get(campaign_id)
    if (campaign.account_id!=getattr(broker_client,"_account_id",None)
            or campaign.revision!=expected_revision
            or campaign.phase!="PROTECTED"
            or campaign.protection is None
            or not campaign.protection.verified_active
            or campaign.held_qty<=0):
        raise GTTChildRecoveryConflict("EMA_GTT_CAMPAIGN_NOT_ELIGIBLE")
    trigger_id=campaign.protection.trigger_id
    if not trigger_id.isascii() or not trigger_id.isdigit() or not int(trigger_id)>0:
        raise GTTChildRecoveryConflict("EMA_GTT_TRIGGER_ID_INVALID")
    try:
        gtt,orders=await asyncio.wait_for(
            asyncio.gather(broker_client.get_gtt(int(trigger_id)),
                           broker_client.get_orders()),timeout=10)
    except Exception:
        # A failed broker read proves neither protection nor a filled exit.
        gtt,orders=None,None
    verdict,child_id,child_status,observed_qty=_evaluate(campaign,gtt,orders)
    if verdict=="ACTIVE_VERIFIED":
        return GTTChildObservation(
            campaign_id,trigger_id,verdict,"","",0,campaign.held_qty,
            campaign.revision,
        )
    from time import time
    with order_journal._transaction() as conn:
        store._schema(conn)
        _schema(conn)
        current=store._get(conn,campaign_id)
        if current!=campaign or current.revision!=expected_revision:
            raise GTTChildRecoveryConflict("EMA_GTT_QUARANTINE_STALE_CAMPAIGN")
        signed=fill_ledger._read_inventory(
            conn,campaign.account_id,campaign.owner_uid,campaign.symbol)
        if (signed is None or signed.exchange!=campaign.exchange
                or signed.net_quantity!=campaign.held_qty
                or signed.reconciliation_required):
            raise GTTChildRecoveryConflict("EMA_GTT_QUARANTINE_LEDGER_DIVERGED")
        if conn.execute("""
            SELECT 1 FROM ema_live_gtt_child_recovery
            WHERE campaign_id=? AND trigger_id=?
        """,(campaign_id,trigger_id)).fetchone():
            raise GTTChildRecoveryConflict("EMA_GTT_PREVIOUS_RECOVERY_EXISTS")
        next_campaign=LiveCampaignQuarantine(campaign,trigger_id,verdict)
        store._save(conn,next_campaign)
        conn.execute("""
            INSERT INTO ema_live_gtt_child_recovery
            (campaign_id,trigger_id,owner_uid,account_id,exchange,symbol,
            product,verdict,child_order_id,child_status,child_filled_observed,
            held_qty_unmodified,campaign_revision,observed_ms)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,(
            campaign.id,trigger_id,campaign.owner_uid,campaign.account_id,
            campaign.exchange,campaign.symbol,campaign.product,verdict,
            child_id,child_status,observed_qty,campaign.held_qty,
            next_campaign.revision,int(time()*1000),
        ))
        return get_row_in_tx(conn,campaign.id,trigger_id)


def LiveCampaignQuarantine(campaign: LiveCampaign, trigger_id: str,
                           verdict: str) -> LiveCampaign:
    """Pure state transform. Preserve a potentially still active GTT identity."""
    return LiveCampaign.model_validate({
        **campaign.model_dump(mode="python"),
        "phase":"RECOVERY_REQUIRED",
        "protection":None,
        "orphan_stop_ids":list(dict.fromkeys([*campaign.orphan_stop_ids,trigger_id])),
        "protection_submission_unknown":True,
        "reason":"EMA_"+verdict,
        "revision":campaign.revision+1,
    })


def get_row_in_tx(conn, campaign_id: str, trigger_id: str) -> GTTChildObservation:
    return _row(conn.execute("""
        SELECT * FROM ema_live_gtt_child_recovery
        WHERE campaign_id=? AND trigger_id=?
    """,(campaign_id,trigger_id)).fetchone())
