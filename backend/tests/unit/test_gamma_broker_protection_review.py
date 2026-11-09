"""Passive Gamma broker/GTT snapshot proof: no broker I/O or order authority."""
from __future__ import annotations

from copy import deepcopy

import pytest

from app.services.gamma_broker_protection_review import (
    assess_protected_remainder_snapshot,
)


BASE = dict(
    bound_account_id="acct-synthetic",
    expected_account_id="acct-synthetic",
    exchange="NFO",
    symbol="SYNTHETIC26NOV25000CE",
    product="NRML",
    remainder_quantity=30,
    recorded_trigger_id=131,
    projected_parent_order_id="PARENT-EXIT-1",
    projected_parent_filled_quantity=20,
    broker_positions={"net": [dict(
        tradingsymbol="SYNTHETIC26NOV25000CE", exchange="NFO",
        product="NRML", quantity=30,
    )]},
    broker_gtts=[dict(
        id=131, status="active", type="single",
        condition=dict(exchange="NFO", tradingsymbol="SYNTHETIC26NOV25000CE",
                       trigger_values=[80.0]),
        orders=[dict(
            exchange="NFO", tradingsymbol="SYNTHETIC26NOV25000CE",
            product="NRML", transaction_type="SELL", quantity=30,
            order_type="LIMIT", price=79.0,
        )],
    )],
    broker_orders=[dict(
        order_id="PARENT-EXIT-1", tradingsymbol="SYNTHETIC26NOV25000CE",
        exchange="NFO", product="NRML", transaction_type="SELL",
        status="CANCELLED", filled_quantity=20, quantity=50,
    )],
    snapshot_completed_ms=100_000,
    now_ms=100_250,
)


def assess(**updates):
    arguments = deepcopy(BASE)
    arguments.update(updates)
    return assess_protected_remainder_snapshot(**arguments)


def altered(source: str, action):
    updates = deepcopy(BASE)
    action(updates[source])
    return {source: updates[source]}


def test_passive_protection_is_consistent_but_never_authorized():
    result = assess()
    assert result.protected_for_review is True
    assert result.reason == "active_protection_observed_snapshot_only"
    assert result.quantity == 30 and result.trigger_id == 131
    assert result.execution_authorized is False
    assert assess() == result


def test_correct_parent_with_other_terminal_buy_is_still_passive():
    orders = deepcopy(BASE["broker_orders"])
    orders.append(dict(order_id="ENTRY-1", tradingsymbol=BASE["symbol"],
                       exchange="NFO", product="NRML", transaction_type="BUY",
                       status="COMPLETE", quantity=50, filled_quantity=50))
    result = assess(broker_orders=orders)
    assert result.protected_for_review and not result.execution_authorized


@pytest.mark.parametrize("mutation,expected", [
    ({"bound_account_id": "wrong-account"}, "broker_account_binding_mismatch"),
    ({"expected_account_id": ""}, "snapshot_identity_invalid"),
    ({"exchange": "nfo"}, "broker_bucket_invalid"),
    ({"product": "MIS"}, "broker_bucket_invalid"),
    ({"remainder_quantity": True}, "protection_identity_or_state_unverified"),
    ({"remainder_quantity": -30}, "protection_identity_or_state_unverified"),
    ({"recorded_trigger_id": 0}, "protection_identity_or_state_unverified"),
    ({"recorded_trigger_id": False}, "protection_identity_or_state_unverified"),
    ({"projected_parent_filled_quantity": 0}, "protection_identity_or_state_unverified"),
    ({"projected_parent_filled_quantity": True}, "protection_identity_or_state_unverified"),
    ({"projected_parent_order_id": " "}, "protection_identity_or_state_unverified"),
    ({"stop_placement_uncertain": True}, "protection_identity_or_state_unverified"),
    ({"stop_placement_uncertain": "false"}, "protection_identity_or_state_unverified"),
    ({"snapshot_completed_ms": 94_000}, "broker_snapshot_stale_or_unverifiable"),
    ({"snapshot_completed_ms": 100_251}, "broker_snapshot_stale_or_unverifiable"),
    ({"max_age_ms": 100_000}, "broker_snapshot_stale_or_unverifiable"),
    ({"now_ms": False}, "broker_snapshot_stale_or_unverifiable"),
    ({"broker_positions": {"day": []}}, "broker_snapshot_incomplete"),
    ({"broker_gtts": None}, "broker_snapshot_incomplete"),
    ({"broker_orders": None}, "broker_snapshot_incomplete"),
])
def test_invalid_or_stale_snapshots_fail_closed(mutation, expected):
    result = assess(**mutation)
    assert result.reason == expected
    assert not result.protected_for_review and not result.execution_authorized


@pytest.mark.parametrize("mutation,expected", [
    (lambda x: x["net"].clear(), "broker_remainder_not_exact"),
    (lambda x: x["net"][0].update(quantity=50), "broker_remainder_not_exact"),
    (lambda x: x["net"][0].update(quantity=-30), "broker_remainder_not_exact"),
    (lambda x: x["net"][0].update(quantity=True), "broker_position_quantity_invalid"),
    (lambda x: x["net"][0].update(quantity=30.0), "broker_position_quantity_invalid"),
    (lambda x: x["net"][0].pop("exchange"), "broker_position_identity_incomplete"),
    (lambda x: x["net"].append(dict(tradingsymbol=BASE["symbol"], exchange="NFO",
                                    product="MIS", quantity=4)), "broker_other_product_exposure"),
    (lambda x: x["net"].append({"bad": "row"}), "broker_position_identity_incomplete"),
    (lambda x: x["net"].append(None), "broker_position_row_invalid"),
])
def test_invalid_broker_position_book_fails_closed(mutation, expected):
    changes = altered("broker_positions", mutation)
    result = assess(**changes)
    assert result.reason == expected
    assert not result.protected_for_review and not result.execution_authorized


@pytest.mark.parametrize("mutation,expected", [
    (lambda x: x.clear(), "recorded_protective_stop_missing"),
    (lambda x: x[0].update(status="triggered"), "recorded_stop_not_active"),
    (lambda x: x[0].update(status="deleted"), "recorded_stop_not_active"),
    (lambda x: x[0].update(type="two-leg"), "recorded_stop_shape_unverified"),
    (lambda x: x[0].update(id=132), "other_contract_trigger_unverified"),
    (lambda x: x[0]["condition"].update(exchange="BFO"), "recorded_trigger_contract_mismatch"),
    (lambda x: x[0]["orders"][0].update(quantity=50), "recorded_stop_does_not_protect_remainder"),
    (lambda x: x[0]["orders"][0].update(quantity=True), "recorded_stop_does_not_protect_remainder"),
    (lambda x: x[0]["orders"][0].update(transaction_type="BUY"), "recorded_stop_does_not_protect_remainder"),
    (lambda x: x[0]["orders"][0].update(product="MIS"), "recorded_stop_does_not_protect_remainder"),
    (lambda x: x[0].update(orders=[]), "recorded_stop_legs_unverified"),
    (lambda x: x.append(deepcopy(x[0])), "broker_gtt_identity_invalid"),
    (lambda x: x.append(dict(id=132, status="active", type="single", condition=dict(
        exchange="NFO", tradingsymbol=BASE["symbol"]), orders=[])), "other_contract_trigger_unverified"),
    (lambda x: x.append(None), "broker_gtt_row_invalid"),
])
def test_incorrect_or_other_broker_stops_fail_closed(mutation, expected):
    changes = altered("broker_gtts", mutation)
    result = assess(**changes)
    assert result.reason == expected
    assert not result.protected_for_review and not result.execution_authorized


@pytest.mark.parametrize("mutation,expected", [
    (lambda x: x.clear(), "projected_parent_order_not_confirmed_in_broker_book"),
    (lambda x: x[0].update(status="OPEN"), "contract_sell_order_history_requires_reconciliation"),
    (lambda x: x[0].update(status="COMPLETE"), "contract_sell_order_history_requires_reconciliation"),
    (lambda x: x[0].update(filled_quantity=50), "contract_sell_order_history_requires_reconciliation"),
    (lambda x: x[0].update(filled_quantity=20.0), "contract_sell_order_history_requires_reconciliation"),
    (lambda x: x[0].update(order_id="OTHER"), "contract_sell_order_history_requires_reconciliation"),
    (lambda x: x.append(deepcopy(x[0])), "contract_sell_order_history_requires_reconciliation"),
    (lambda x: x.append(dict(order_id="SECOND-SELL", exchange="NFO", tradingsymbol=BASE["symbol"],
                                    product="NRML", transaction_type="SELL", status="CANCELLED",
                                    filled_quantity=0)), "contract_sell_order_history_requires_reconciliation"),
    (lambda x: x.append(dict(order_id="PENDING-BUY", exchange="NFO", tradingsymbol=BASE["symbol"],
                                    product="NRML", transaction_type="BUY", status="OPEN")), "contract_nonterminal_order_present"),
    (lambda x: x.append(dict(order_id="PENDING", exchange="NFO", tradingsymbol=BASE["symbol"],
                                    transaction_type="UNKNOWN", status="UNKNOWN")), "contract_order_side_unverified"),
    (lambda x: x.append(None), "broker_order_row_invalid"),
])
def test_conflicting_broker_order_books_fail_closed(mutation, expected):
    changes = altered("broker_orders", mutation)
    result = assess(**changes)
    assert result.reason == expected
    assert not result.protected_for_review and not result.execution_authorized


def test_all_boundaries_are_pure_and_never_mutate_supplied_snapshots():
    snapshots = deepcopy(BASE)
    original = deepcopy(snapshots)
    result = assess_protected_remainder_snapshot(**snapshots)
    assert snapshots == original
    assert result.protected_for_review
    assert result.execution_authorized is False


@pytest.mark.parametrize("mutation,expected", [
    (lambda x: x[0]["condition"].update(trigger_values=[]), "recorded_stop_trigger_price_unverified"),
    (lambda x: x[0]["condition"].update(trigger_values=[False]), "recorded_stop_trigger_price_unverified"),
    (lambda x: x[0]["condition"].update(trigger_values=[-1]), "recorded_stop_trigger_price_unverified"),
    (lambda x: x[0]["condition"].update(trigger_values=[float('nan')]), "recorded_stop_trigger_price_unverified"),
    (lambda x: x[0]["orders"][0].update(price=0), "recorded_stop_does_not_protect_remainder"),
    (lambda x: x[0]["orders"][0].update(price=float('inf')), "recorded_stop_does_not_protect_remainder"),
    (lambda x: x[0]["orders"][0].update(price=85), "recorded_stop_does_not_protect_remainder"),
    (lambda x: x[0]["orders"][0].update(order_type='MARKET'), "recorded_stop_does_not_protect_remainder"),
])
def test_gtt_protective_price_and_leg_shape_are_strict(mutation, expected):
    result = assess(**altered("broker_gtts", mutation))
    assert result.reason == expected
    assert not result.protected_for_review and not result.execution_authorized


def test_disguised_other_gtt_condition_cannot_hide_selling_order_leg():
    gtts = deepcopy(BASE["broker_gtts"])
    disguised = deepcopy(gtts[0])
    disguised["id"] = 132
    disguised["condition"]["tradingsymbol"] = "ANOTHERCONTRACT"
    gtts.append(disguised)
    result = assess(broker_gtts=gtts)
    assert result.reason == "other_trigger_could_sell_target_contract"
    assert not result.protected_for_review and not result.execution_authorized


def test_terminal_parent_order_request_quantity_must_equal_filled_plus_remainder():
    orders = deepcopy(BASE["broker_orders"])
    orders[0]["quantity"] = 90
    result = assess(broker_orders=orders)
    assert result.reason == "contract_sell_order_history_requires_reconciliation"
    assert not result.protected_for_review and not result.execution_authorized


@pytest.mark.parametrize("position_rows", [
    [15, 15],  # a split net can look equal to the expected holding
    [30, 0],   # a duplicate zero row still makes ownership ambiguous
    [40, -10], # offsetting long/short records do not prove one held bucket
])
def test_multiple_same_bucket_positions_never_prove_a_unique_remainder(position_rows):
    positions = deepcopy(BASE["broker_positions"])
    positions["net"] = [
        dict(tradingsymbol=BASE["symbol"], exchange="NFO", product="NRML", quantity=qty)
        for qty in position_rows
    ]
    result = assess(broker_positions=positions)
    assert result.reason == "broker_position_bucket_ambiguous"
    assert result.protected_for_review is False
    assert result.execution_authorized is False
