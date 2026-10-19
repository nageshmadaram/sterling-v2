"""Offline broker-fill projection drills: no Kite requests and no broker writes."""
from dataclasses import replace
from decimal import Decimal

import pytest

from app.engines.ema_trend.live_contracts import BrokerIntent, BrokerStop, LiveCampaign
from app.services.ema_trend.live_fill_projection import rehearse_verified_fill
from app.services.ema_trend.live_order_facts import (
    LiveOrderEvidenceError, LiveOrderIdentity, broker_order_fact,
)


def identity(side="BUY", quantity=4, **changes):
    fields = dict(uid="alice", account_id="KITE-REAL-1", order_id="ORDER-1",
                  exchange="NSE", symbol="INFY", product="CNC",
                  side=side, quantity=quantity)
    fields.update(changes)
    return LiveOrderIdentity(**fields)


def fact(key, status, filled, price="100"):
    event = dict(order_id=key.order_id, exchange=key.exchange,
                 tradingsymbol=key.symbol, product=key.product,
                 transaction_type=key.side, quantity=key.quantity,
                 status=status, filled_quantity=filled,
                 average_price=price if filled else "0")
    return broker_order_fact(key, [event], broker_account_id=key.account_id,
                             broker_is_paper=False)


def order(side="BUY", quantity=4, filled=0, *, order_id="ORDER-1",
          status="ACKNOWLEDGED", key="intent-1"):
    return BrokerIntent(intent_key=key, side=side, requested_qty=quantity,
                        order_id=order_id, confirmed_qty=filled,
                        cumulative_value=Decimal(filled) * 100, status=status)


def campaign(*, entries=None, exits=None, stop=None, **extra):
    entries = entries if entries is not None else [order()]
    exits = exits if exits is not None else []
    fields = dict(id="EMA-1", owner_uid="alice", account_id="KITE-REAL-1",
                  exchange="NSE", symbol="INFY", mode="swing", product="CNC",
                  strategy_version="1", config_hash="frozen",
                  signal_id="signal-1", phase="ENTRY_PENDING",
                  entries=entries, exits=exits,
                  confirmed_bought=sum(x.confirmed_qty for x in entries),
                  confirmed_sold=sum(x.confirmed_qty for x in exits),
                  stop=Decimal("95"), protection=stop)
    fields.update(extra)
    return LiveCampaign(**fields)


def protected_stop(qty=4):
    return BrokerStop(trigger_id="GTT-1", exchange="NSE", symbol="INFY",
                      product="CNC", qty=qty, trigger=Decimal("95"),
                      verified_active=True)


def test_broker_ack_does_not_invent_inventory_or_stop_coverage():
    original = campaign()
    result = rehearse_verified_fill(original, "intent-1", fact(identity(), "OPEN", 0))
    assert result.fill_delta.quantity == 0
    assert result.campaign.held_qty == 0
    assert result.campaign.phase == "RECOVERY_REQUIRED"
    assert result.next_proof == "BROKER_INVENTORY_RECONCILIATION"
    assert original.phase == "ENTRY_PENDING"  # never mutates caller


def test_partial_buy_requires_exact_broker_stop_coverage():
    result = rehearse_verified_fill(campaign(), "intent-1", fact(identity(), "OPEN", 2))
    assert result.fill_delta.quantity == 2
    assert result.fill_delta.value == Decimal("200")
    assert result.campaign.held_qty == 2
    assert result.campaign.entries[0].status == "PARTIAL"
    assert result.campaign.phase == "PROTECTION_REQUIRED"
    assert result.uncovered_qty == 2
    assert result.next_proof == "BROKER_STOP_VERIFICATION"


def test_complete_buy_is_idempotent_and_never_auto_verifies_a_stop():
    first = rehearse_verified_fill(campaign(), "intent-1", fact(identity(), "COMPLETE", 4))
    assert first.campaign.held_qty == 4
    assert first.campaign.phase == "PROTECTION_REQUIRED"
    assert first.fill_delta.quantity == 4
    again = rehearse_verified_fill(first.campaign, "intent-1", fact(identity(), "COMPLETE", 4))
    assert again.fill_delta.quantity == 0
    assert again.campaign.held_qty == 4
    assert again.campaign.protection is None


def test_cancelled_after_partial_fill_retains_real_held_quantity():
    old = campaign(entries=[order(filled=2, status="PARTIAL")],
                   confirmed_bought=2, phase="PROTECTION_REQUIRED")
    result = rehearse_verified_fill(old, "intent-1", fact(identity(), "CANCELLED", 2))
    assert result.campaign.held_qty == 2
    assert result.campaign.entries[0].status == "CANCELLED"
    assert result.fill_delta.quantity == 0
    assert result.uncovered_qty == 2


def test_existing_protected_shares_do_not_cover_unfilled_additional_entry():
    entries = [order(filled=4, status="COMPLETE"),
               order(quantity=2, filled=0, order_id="ORDER-2", key="add-2")]
    original = campaign(entries=entries, stop=protected_stop(4),
                        phase="PROTECTED")
    result = rehearse_verified_fill(
        original, "add-2", fact(identity(quantity=2, order_id="ORDER-2"), "COMPLETE", 2))
    assert result.campaign.held_qty == 6
    assert result.campaign.phase == "PROTECTION_REQUIRED"
    assert result.verified_stop_qty == 4
    assert result.uncovered_qty == 2
    assert result.next_proof == "BROKER_STOP_REPLACEMENT"


def test_rechecking_protected_position_never_creates_new_broker_stop():
    original = campaign(entries=[order(filled=4, status="COMPLETE")],
                        phase="PROTECTED", stop=protected_stop())
    result = rehearse_verified_fill(original, "intent-1", fact(identity(), "COMPLETE", 4))
    assert result.campaign.phase == "PROTECTED"
    assert result.fill_delta.quantity == 0
    assert result.campaign.protection.trigger_id == "GTT-1"
    assert result.uncovered_qty == 0


def test_partial_sell_flags_orphan_stop_instead_of_claiming_coverage():
    entries = [order(filled=4, status="COMPLETE")]
    exits = [order(side="SELL", quantity=4, order_id="ORDER-X",
                   key="exit-1")]
    original = campaign(entries=entries, exits=exits, phase="EXIT_PENDING",
                        stop=protected_stop())
    result = rehearse_verified_fill(original, "exit-1", fact(
        identity(side="SELL", order_id="ORDER-X"), "OPEN", 2))
    assert result.campaign.held_qty == 2
    assert result.campaign.confirmed_sold == 2
    assert result.fill_delta.quantity == 2
    assert result.campaign.phase == "RECOVERY_REQUIRED"
    assert result.campaign.protection is None
    assert result.campaign.protection_submission_unknown
    assert result.next_proof == "BROKER_STOP_CLEANUP"
    assert result.prior_stop_id == "GTT-1"
    assert original.protection is not None  # previous broker fact preserved


def test_complete_sell_never_claims_flat_without_broker_inventory_reconciliation():
    original = campaign(
        entries=[order(filled=4, status="COMPLETE")],
        exits=[order(side="SELL", quantity=4, order_id="ORDER-X", key="exit-1")],
        phase="EXIT_PENDING",
    )
    result = rehearse_verified_fill(original, "exit-1", fact(
        identity(side="SELL", order_id="ORDER-X"), "COMPLETE", 4))
    assert result.campaign.held_qty == 0
    assert result.campaign.phase == "RECOVERY_REQUIRED"
    assert result.next_proof == "BROKER_INVENTORY_RECONCILIATION"


@pytest.mark.parametrize("changed", [
    {"uid": "other"}, {"account_id": "OTHER"}, {"symbol": "TCS"},
    {"product": "MIS"}, {"exchange": "BSE"},
])
def test_cross_account_or_contract_broker_facts_are_refused(changed):
    key = identity(**changed)
    with pytest.raises(LiveOrderEvidenceError, match="broker_identity_mismatch"):
        rehearse_verified_fill(campaign(), "intent-1", fact(key, "OPEN", 0))


def test_side_and_quantity_changes_are_refused():
    with pytest.raises(LiveOrderEvidenceError, match="side_conflict"):
        rehearse_verified_fill(campaign(), "intent-1", fact(identity(side="SELL"), "OPEN", 0))
    with pytest.raises(LiveOrderEvidenceError, match="quantity_conflict"):
        rehearse_verified_fill(campaign(), "intent-1", fact(identity(quantity=5), "OPEN", 0))


def test_order_id_and_unknown_intent_cannot_be_rebound():
    with pytest.raises(LiveOrderEvidenceError, match="order_id_conflict"):
        rehearse_verified_fill(campaign(), "intent-1", fact(identity(order_id="OTHER"), "OPEN", 0))
    with pytest.raises(LiveOrderEvidenceError, match="canonical_intent_missing"):
        rehearse_verified_fill(campaign(), "nonexistent", fact(identity(), "OPEN", 0))


@pytest.mark.parametrize("status", ["RESERVED", "SUBMITTING", "UNKNOWN"])
def test_crash_window_order_needs_canonical_journal_binding_first(status):
    o = order(order_id="", status=status)
    with pytest.raises(LiveOrderEvidenceError, match="binding_must_be_reconciled"):
        rehearse_verified_fill(campaign(entries=[o]), "intent-1", fact(identity(), "OPEN", 0))


def test_duplicate_or_stale_fill_cannot_reverse_confirmed_quantity():
    original = campaign(entries=[order(filled=2, status="PARTIAL")],
                        phase="PROTECTION_REQUIRED")
    with pytest.raises(LiveOrderEvidenceError, match="not_monotone"):
        rehearse_verified_fill(original, "intent-1", fact(identity(), "OPEN", 1))


def test_unattributed_sell_more_than_owned_is_refused():
    original = campaign(entries=[order(quantity=2, filled=2, status="COMPLETE")],
                        exits=[order(side="SELL", quantity=4, order_id="ORDER-X", key="exit-1")],
                        phase="EXIT_PENDING")
    with pytest.raises(LiveOrderEvidenceError, match="exceed_owned_inventory"):
        rehearse_verified_fill(original, "exit-1", fact(
            identity(side="SELL", order_id="ORDER-X"), "OPEN", 3))


def test_unknown_broker_order_status_keeps_recovery_required():
    result = rehearse_verified_fill(campaign(), "intent-1", fact(identity(), "UNKNOWN", 1))
    assert result.campaign.phase == "RECOVERY_REQUIRED"
    assert result.campaign.protection_submission_unknown
    assert result.next_proof == "ORDER_RECONCILIATION"

def test_noncanonical_expired_broker_status_stays_unresolved():
    result = rehearse_verified_fill(campaign(), "intent-1", fact(identity(), "EXPIRED", 0))
    assert result.campaign.entries[0].status == "UNKNOWN"
    assert result.campaign.protection_submission_unknown
    assert result.campaign.phase == "RECOVERY_REQUIRED"
    assert result.next_proof == "ORDER_RECONCILIATION"


def test_replay_same_partial_fill_remains_idempotent():
    first = rehearse_verified_fill(campaign(), "intent-1", fact(identity(), "OPEN", 2))
    again = rehearse_verified_fill(first.campaign, "intent-1", fact(identity(), "OPEN", 2))
    assert again.fill_delta.quantity == 0
    assert again.fill_delta.value == Decimal("0")
    assert again.campaign.confirmed_bought == 2
