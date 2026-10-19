"""Pure EMA CNC protective GTT request preview, never a broker write.

A StopDraft is NOT an execution capability. Its inputs are offline observations
and are not permission to call Kite: a production adapter must separately prove
fresh broker quotes, order/GTT books, ownership, account binding and acquire a
cross-process canonical broker-write lease immediately before sending.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
from typing import Any

from app.services.kite_engine import protective_stop
from . import live_campaign_store as store, live_protection_outbox as outbox


class StopDraftBlocked(ValueError):
    pass


@dataclass(frozen=True)
class StopDraft:
    command_id: str
    campaign_id: str
    change_kind: str
    prior_trigger_id: str
    fingerprint: str
    request: dict[str, Any]
    broker_write_authorized: bool = False


def _number(value: Any, name: str) -> Decimal:
    if isinstance(value, bool):
        raise StopDraftBlocked(name)
    try:
        number = Decimal(str(value))
        if not number.is_finite() or number <= 0:
            raise ValueError()
        return number
    except (ValueError, TypeError, InvalidOperation) as exc:
        raise StopDraftBlocked(name) from exc


def draft_stop_command(command_id: str, *, reference_bid,
                       tick_size) -> StopDraft:
    """Build one CNC SELL GTT from a RESERVED durable stop command.

    No trusted quote freshness is inferred from a supplied number. A separate
    broker-side admission gate is mandatory for real submission; this module
    contains no Kite client, no broker capability and no write methods.
    """
    command = outbox.get(command_id)
    if command.status != "RESERVED":
        raise StopDraftBlocked("EMA_STOP_DRAFT_UNKNOWN_OUTCOME_CANNOT_RETRY")
    campaign = store.get(command.campaign_id)
    if (campaign.revision != command.revision
            or campaign.owner_uid != command.owner_uid
            or campaign.account_id != command.account_id
            or campaign.product != "CNC"
            or campaign.held_qty != command.held_qty
            or campaign.held_qty <= 0
            or campaign.orphan_stop_ids
            or campaign.protection_submission_unknown):
        raise StopDraftBlocked("EMA_STOP_DRAFT_CAMPAIGN_IDENTITY_OR_INVENTORY_CHANGED")
    if command.kind == "PLACE":
        if campaign.protection is not None or campaign.phase != "PROTECTION_REQUIRED":
            raise StopDraftBlocked("EMA_STOP_DRAFT_ALREADY_PROTECTED")
        if command.desired_trigger < campaign.stop:
            raise StopDraftBlocked("EMA_STOP_DRAFT_WEAKER_THAN_CAMPAIGN")
    elif command.kind == "TIGHTEN":
        stop = campaign.protection
        if (campaign.phase != "PROTECTED" or stop is None
                or not stop.verified_active or stop.child_order_unresolved
                or stop.qty != campaign.held_qty
                or stop.trigger_id != command.prior_gtt_id
                or command.desired_trigger <= stop.trigger):
            raise StopDraftBlocked("EMA_STOP_DRAFT_TRAIL_NOT_STRICTLY_TIGHTER")
    else:
        raise StopDraftBlocked("EMA_STOP_DRAFT_KIND_UNSUPPORTED")
    bid = _number(reference_bid, "EMA_STOP_DRAFT_REFERENCE_BID_INVALID")
    tick = _number(tick_size, "EMA_STOP_DRAFT_TICK_INVALID")
    if command.desired_trigger >= bid:
        raise StopDraftBlocked("EMA_STOP_DRAFT_BREACHED_OR_CROSSED_BID")
    if command.desired_trigger % tick or bid % tick:
        raise StopDraftBlocked("EMA_STOP_DRAFT_PRICE_OFF_TICK_GRID")
    request = protective_stop.build_gtt_request(
        tradingsymbol=campaign.symbol,
        exchange=campaign.exchange,
        qty=command.held_qty,
        trigger_premium=float(command.desired_trigger),
        last_price=float(bid),
        direction="long",
        target_premium=0.0,
        product="CNC",
        tick_size=float(tick),
    )
    # Defensive assertions validate the final output, not just its inputs.
    legs = request.get("orders")
    triggers = request.get("trigger_values")
    if (request.get("exchange") != campaign.exchange
            or request.get("tradingsymbol") != campaign.symbol
            or len(legs or []) != 1
            or len(triggers or []) != 1
            or str(legs[0].get("transaction_type") or "").upper() != "SELL"
            or str(legs[0].get("product") or "").upper() != "CNC"
            or str(legs[0].get("order_type") or "").upper() != "LIMIT"
            or legs[0].get("quantity") != command.held_qty
            or Decimal(str(triggers[0])) != command.desired_trigger
            or _number(legs[0].get("price"), "EMA_STOP_DRAFT_LIMIT_INVALID") >
                command.desired_trigger):
        raise StopDraftBlocked("EMA_STOP_DRAFT_BROKER_REQUEST_UNSAFE")
    normalized = json.dumps(
        [command.command_id,command.kind,command.prior_gtt_id,request],
        sort_keys=True,separators=(",",":"),allow_nan=False,
    )
    return StopDraft(
        command_id=command.command_id,
        campaign_id=campaign.id,
        change_kind=command.kind,
        prior_trigger_id=command.prior_gtt_id,
        request=request,
        fingerprint=sha256(normalized.encode()).hexdigest(),
    )
