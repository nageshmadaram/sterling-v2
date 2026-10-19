"""Read-only, fail-closed rehearsal of EMA live broker fill projection.

This is a building block, NOT live execution or a database writer. It never
submits orders, cancels a GTT, marks broker coverage as verified, or changes
canonical journals. Production integration must persist the signed canonical
fill delta and prove broker inventory/stop state before committing a campaign.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.engines.ema_trend.live_contracts import BrokerIntent, LiveCampaign
from .live_order_facts import (
    LiveFillDelta, LiveOrderEvidenceError, LiveOrderFact,
    incremental_confirmed_fill,
)

NextProof = Literal[
    "ORDER_RECONCILIATION", "BROKER_STOP_VERIFICATION",
    "BROKER_STOP_REPLACEMENT", "BROKER_STOP_CLEANUP",
    "BROKER_INVENTORY_RECONCILIATION",
]


@dataclass(frozen=True)
class ProjectionRehearsal:
    campaign: LiveCampaign
    fill_delta: LiveFillDelta
    next_proof: NextProof
    # Coverage counts are only broker-observed declarations. They are not
    # permission to consider the live position protected.
    verified_stop_qty: int
    uncovered_qty: int
    prior_stop_id: str | None = None


def _intent_status(fact: LiveOrderFact) -> str:
    status = fact.status
    if status == "COMPLETE":
        return "COMPLETE"
    if status in {"CANCELLED", "CANCELED"}:
        return "CANCELLED"
    # "EXPIRED" is not a canonical terminal broker fact in the evidence
    # parser. Treat it as UNKNOWN until order history is reconciled.
    if status == "REJECTED":
        return "REJECTED"
    if status in {"UNKNOWN", "SUBMITTING"}:
        return "UNKNOWN"
    return "PARTIAL" if fact.filled_quantity else "ACKNOWLEDGED"


def rehearse_verified_fill(campaign: LiveCampaign, intent_key: str,
                          fact: LiveOrderFact) -> ProjectionRehearsal:
    """Compute next campaign evidence from one exact canonical order intent.

    All output is advisory. Never apply it before the canonical signed fill
    ledger and the actual broker inventory have been independently reconciled.
    """
    identity = fact.identity
    if campaign.phase == "CLOSED":
        raise LiveOrderEvidenceError("ema_closed_campaign_must_not_reopen")
    if (identity.uid != campaign.owner_uid
            or identity.account_id != campaign.account_id
            or identity.exchange != campaign.exchange
            or identity.symbol != campaign.symbol
            or identity.product != campaign.product):
        raise LiveOrderEvidenceError("ema_campaign_broker_identity_mismatch")

    entries = list(campaign.entries)
    exits = list(campaign.exits)
    matches = [(side, index, order) for side, orders in
               (("BUY", entries), ("SELL", exits))
               for index, order in enumerate(orders) if order.intent_key == intent_key]
    if len(matches) != 1:
        raise LiveOrderEvidenceError("ema_canonical_intent_missing_or_ambiguous")
    side, index, previous = matches[0]
    if side != identity.side or previous.side != side:
        raise LiveOrderEvidenceError("ema_order_side_conflict")
    if previous.requested_qty != identity.quantity:
        raise LiveOrderEvidenceError("ema_order_quantity_conflict")
    if previous.order_id and previous.order_id != identity.order_id:
        raise LiveOrderEvidenceError("ema_order_id_conflict")
    if previous.status in {"RESERVED", "SUBMITTING", "UNKNOWN"} and previous.order_id == "":
        # Broker history alone is not enough to bind a crash-window submission
        # to one canonical order. That association requires journal recovery.
        raise LiveOrderEvidenceError("ema_order_binding_must_be_reconciled_first")

    prior = LiveOrderFact(identity, previous.status, previous.confirmed_qty,
                          previous.cumulative_value)
    next_status = _intent_status(fact)
    if previous.status in {"COMPLETE", "CANCELLED", "REJECTED"} and next_status != previous.status:
        raise LiveOrderEvidenceError("ema_terminal_order_must_not_reopen")
    delta = incremental_confirmed_fill(prior, fact)
    next_order = BrokerIntent.model_validate({
        **previous.model_dump(mode="python"),
        "order_id": identity.order_id,
        "confirmed_qty": fact.filled_quantity,
        "cumulative_value": fact.cumulative_value,
        "status": next_status,
    })
    if side == "BUY":
        entries[index] = next_order
    else:
        exits[index] = next_order
    bought = sum(order.confirmed_qty for order in entries)
    sold = sum(order.confirmed_qty for order in exits)
    if sold > bought:
        raise LiveOrderEvidenceError("ema_exits_exceed_owned_inventory")
    held = bought - sold
    stop = campaign.protection
    stop_id = stop.trigger_id if stop is not None else None
    # Broker stop quantities must never exceed the *new* confirmed holding.
    # The rehearsal cannot cancel a GTT, so preserve an explicit UNKNOWN
    # cleanup state instead of quietly pretending that the stop disappeared.
    cleanup = bool(stop and stop.qty > held)
    if cleanup:
        stop = None
    covered = (stop.qty if stop and stop.verified_active
               and not stop.child_order_unresolved
               and not campaign.protection_submission_unknown else 0)
    gap = max(0, held - covered)
    ambiguous = (campaign.protection_submission_unknown or cleanup
                 or campaign.phase == "RECOVERY_REQUIRED"
                 or next_order.status == "UNKNOWN")
    if ambiguous:
        phase = "RECOVERY_REQUIRED"
        proof: NextProof = "BROKER_STOP_CLEANUP" if cleanup else "ORDER_RECONCILIATION"
    elif not held:
        # A zero arithmetic balance is NOT broker-verified flat: the broker
        # inventory, orders, stop children and holdings still need inspection.
        phase = "RECOVERY_REQUIRED"
        proof = "BROKER_INVENTORY_RECONCILIATION"
    elif stop is None or not stop.verified_active or stop.child_order_unresolved:
        phase = "PROTECTION_REQUIRED"
        proof = "BROKER_STOP_VERIFICATION"
    elif gap:
        phase = "PROTECTION_REQUIRED"
        proof = "BROKER_STOP_REPLACEMENT"
    elif campaign.phase != "PROTECTED":
        # A fill observation cannot promote a previously unprotected campaign
        # to PROTECTED, even if an old cached stop record says ACTIVE.
        phase = "PROTECTION_REQUIRED"
        proof = "BROKER_STOP_VERIFICATION"
    else:
        # Preserve PROTECTED only if an earlier, independently verified stop
        # still exactly covers the current holding. No GTT is fabricated here.
        phase = "PROTECTED"
        proof = "BROKER_STOP_VERIFICATION"

    snapshot = LiveCampaign.model_validate({
        **campaign.model_dump(mode="python"),
        "entries": [order.model_dump(mode="python") for order in entries],
        "exits": [order.model_dump(mode="python") for order in exits],
        "confirmed_bought": bought,
        "confirmed_sold": sold,
        "protection": stop.model_dump(mode="python") if stop is not None else None,
        "protection_submission_unknown": ambiguous,
        "phase": phase,
        "reason": (("UNVERIFIED_ORPHAN_BROKER_STOP" if cleanup
                    else "BROKER_RECONCILIATION_REQUIRED") if ambiguous
                   else campaign.reason),
    })
    return ProjectionRehearsal(snapshot, delta, proof, covered, gap, stop_id)
