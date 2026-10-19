"""Broker-read-only EMA stop verification and fail-closed exit proposals.

No order, GTT or cancellation is sent by this module. Intent creation remains
disabled for real EMA accounts; these observations prepare the deterministic
stop/TSL/exit state machine for fake-Kite failure drills.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Literal, Mapping, Sequence

from app.engines.ema_trend.live_contracts import BrokerStop, LiveCampaign

Action = Literal[
    "WAIT", "VERIFY_STOP", "REPLACE_STOP", "EXIT_REQUIRES_ORDER",
    "RECONCILE_BROKER", "BROKER_STOP_UNSUPPORTED",
]


class LiveLifecycleEvidenceError(ValueError):
    pass


@dataclass(frozen=True)
class ExitDecision:
    action: Action
    reason: str
    held_qty: int
    desired_stop: Decimal | None = None


def _money(value, label: str) -> Decimal:
    if isinstance(value, bool):
        raise LiveLifecycleEvidenceError(label)
    try:
        number = Decimal(str(value))
        if not number.is_finite() or number <= 0:
            raise ValueError()
        return number
    except (ValueError, InvalidOperation, TypeError) as exc:
        raise LiveLifecycleEvidenceError(label) from exc


def verify_active_cnc_gtt(campaign: LiveCampaign, snapshot: Mapping,
                          *, trigger_id: str) -> BrokerStop:
    """Parse a fresh Kite GTT snapshot; a trigger is NOT proof of an exit fill."""
    if campaign.product != "CNC" or campaign.held_qty <= 0:
        raise LiveLifecycleEvidenceError("EMA_MIS_GTT_NOT_A_PROTECTIVE_STOP")
    if not isinstance(snapshot, Mapping) or str(snapshot.get("id")) != str(trigger_id):
        raise LiveLifecycleEvidenceError("EMA_GTT_ID_NOT_VERIFIED")
    if str(snapshot.get("status","")).lower() != "active":
        raise LiveLifecycleEvidenceError("EMA_GTT_NOT_ACTIVE")
    condition = snapshot.get("condition")
    if not isinstance(condition, Mapping):
        raise LiveLifecycleEvidenceError("EMA_GTT_CONDITION_UNREADABLE")
    if (str(condition.get("exchange")) != campaign.exchange
            or str(condition.get("tradingsymbol")) != campaign.symbol):
        raise LiveLifecycleEvidenceError("EMA_GTT_INSTRUMENT_MISMATCH")
    triggers = condition.get("trigger_values")
    if not isinstance(triggers, (list,tuple)) or len(triggers) != 1:
        raise LiveLifecycleEvidenceError("EMA_GTT_STOP_TRIGGER_UNVERIFIED")
    trigger = _money(triggers[0],"EMA_GTT_INVALID_STOP_TRIGGER")
    if trigger < campaign.stop:
        raise LiveLifecycleEvidenceError("EMA_GTT_STOP_WEAKER_THAN_CAMPAIGN")
    orders = snapshot.get("orders")
    if not isinstance(orders, (list,tuple)) or len(orders)!=1 or not isinstance(orders[0], Mapping):
        raise LiveLifecycleEvidenceError("EMA_GTT_SELL_CHILD_CONTRACT_UNVERIFIED")
    sell = orders[0]
    if (str(sell.get("transaction_type")).upper() != "SELL"
            or str(sell.get("product")).upper() != "CNC"
            or str(sell.get("order_type")).upper() != "LIMIT"):
        raise LiveLifecycleEvidenceError("EMA_GTT_SELL_CHILD_NOT_PROTECTIVE")
    try:
        qty = int(sell["quantity"])
        if isinstance(sell["quantity"], bool) or str(qty) != str(sell["quantity"]):
            raise ValueError()
    except (KeyError,TypeError,ValueError) as exc:
        raise LiveLifecycleEvidenceError("EMA_GTT_SELL_QUANTITY_UNVERIFIED") from exc
    if qty != campaign.held_qty:
        raise LiveLifecycleEvidenceError("EMA_GTT_SELL_QUANTITY_MISMATCH")
    limit_price = _money(sell.get("price"),"EMA_GTT_SELL_LIMIT_UNVERIFIED")
    if limit_price > trigger:
        raise LiveLifecycleEvidenceError("EMA_GTT_LIMIT_CANNOT_EXCEED_TRIGGER")
    if campaign.protection_submission_unknown:
        raise LiveLifecycleEvidenceError("EMA_GTT_SUBMISSION_UNRESOLVED")
    return BrokerStop(
        trigger_id=str(trigger_id),exchange=campaign.exchange,
        symbol=campaign.symbol,product="CNC",qty=qty,trigger=trigger,
        verified_active=True,child_order_unresolved=False,
    )


def decide_exit(campaign: LiveCampaign, *, bid, stop_candidate=None,
                target=None, deadline_reached=False,
                quote_age_s=0, max_quote_age_s=10) -> ExitDecision:
    """Conservative proposal only; an exit still needs a canonical SELL intent.

    Do not use a stale price, missing stop, or ambiguous order to generate
    a blind reduction. Quotes do NOT prove held inventory is unchanged.
    """
    if campaign.phase == "CLOSED":
        return ExitDecision("WAIT","CAMPAIGN_CLOSED",0)
    held = campaign.held_qty
    if (campaign.phase == "RECOVERY_REQUIRED"
            or campaign.protection_submission_unknown
            or campaign.orphan_stop_ids
            or any(o.status in {"UNKNOWN","SUBMITTING"} for o in campaign.entries+campaign.exits)):
        return ExitDecision("RECONCILE_BROKER","UNRESOLVED_BROKER_STATE",held)
    if any(o.status not in {"COMPLETE","CANCELLED","REJECTED"} for o in campaign.exits):
        return ExitDecision("RECONCILE_BROKER","EXIT_ALREADY_PENDING_OR_UNKNOWN",held)
    if any(o.status in {"RESERVED","ACKNOWLEDGED","PARTIAL","SUBMITTED"} for o in campaign.entries):
        return ExitDecision("RECONCILE_BROKER","PENDING_ENTRY_CAN_INCREASE_LIVE_EXPOSURE",held)
    if held == 0:
        return ExitDecision("RECONCILE_BROKER","ZERO_PROJECTED_QUANTITY_NEEDS_BROKER_PROOF",0)
    if (isinstance(quote_age_s,bool) or isinstance(max_quote_age_s,bool)
            or not isinstance(quote_age_s,(int,float))
            or not isinstance(max_quote_age_s,(int,float))
            or quote_age_s < 0 or max_quote_age_s<=0
            or quote_age_s>max_quote_age_s):
        return ExitDecision("RECONCILE_BROKER","STALE_OR_UNVERIFIED_EXECUTABLE_QUOTE",held)
    try:
        current = _money(bid,"EMA_BID_UNKNOWN")
    except LiveLifecycleEvidenceError:
        return ExitDecision("RECONCILE_BROKER","EXECUTABLE_BID_UNAVAILABLE",held)
    if campaign.flatten_requested or deadline_reached or current <= campaign.stop:
        reason=("OPERATOR_FLATTEN" if campaign.flatten_requested else
                "SESSION_DEADLINE" if deadline_reached else "STOP_BREACH")
        return ExitDecision("EXIT_REQUIRES_ORDER",reason,held)
    if target is not None and current >= _money(target,"EMA_TARGET_INVALID"):
        return ExitDecision("EXIT_REQUIRES_ORDER","TARGET_REACHED",held)
    if campaign.product != "CNC":
        return ExitDecision("BROKER_STOP_UNSUPPORTED","MIS_STOP_ORDER_NOT_WIRED",held)
    stop = campaign.protection
    if stop is None or campaign.phase != "PROTECTED" or not stop.verified_active:
        return ExitDecision("VERIFY_STOP","BROKER_CNC_STOP_NOT_VERIFIED",held)
    if (stop.qty != held or stop.child_order_unresolved or stop.trigger<campaign.stop):
        return ExitDecision("RECONCILE_BROKER","BROKER_STOP_COVERAGE_MISMATCH",held)
    if stop_candidate is not None:
        candidate = _money(stop_candidate,"EMA_TRAIL_INVALID")
        # Never weaken a broker stop, even if a new model/replay says to.
        if candidate > stop.trigger:
            if candidate >= current:
                return ExitDecision("EXIT_REQUIRES_ORDER","TRAIL_ALREADY_BREACHED",held)
            return ExitDecision("REPLACE_STOP","TIGHTEN_VERIFIED_STOP",held,candidate)
    return ExitDecision("WAIT","BROKER_STOP_COVERS_POSITION",held)
