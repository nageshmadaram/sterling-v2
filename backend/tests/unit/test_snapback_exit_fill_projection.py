"""Transaction-safe Snapback terminal broker fills must never drift ownership."""
from copy import deepcopy
from types import SimpleNamespace as NS

import pytest

from app.services.snapback_exit_fill_projection import project_terminal_exit


def position():
    return NS(
        position_id="pos-1", uid="user-1", account_id="kite-1",
        option_exchange="NFO", option_symbol="NIFTY_PE",
        hedge_exchange="NFO", hedge_symbol="NIFTY_FUT", hedge_side="BUY",
        option_qty=75, option_exit_qty=0, option_exit_value=0.0,
        option_exit_order_id="BROKER-OPTION-EXIT", option_exit_requested=75,
        option_exit_booked_ids=[], option_exit_booked_quantities={},
        hedge_qty=25, hedge_exit_qty=0, hedge_exit_value=0.0,
        hedge_exit_order_id="BROKER-HEDGE-EXIT", hedge_exit_requested=25,
        hedge_exit_booked_ids=[], hedge_exit_booked_quantities={},
        status="EXITING", exit_attempts=0,
    )


def book(current, origin, *, leg="option", order_id="BROKER-OPTION-EXIT",
         requested=75, filled=75, average=90.0):
    return project_terminal_exit(
        current, origin, leg=leg, order_id=order_id,
        requested=requested, filled=filled, average=average,
    )


def test_terminal_option_fill_projects_once():
    p = position()
    baseline = deepcopy(p)
    book(p, baseline, filled=25)
    assert p.option_exit_qty == 25
    assert p.option_exit_value == 2250.0
    assert p.option_exit_booked_ids == ["BROKER-OPTION-EXIT"]
    assert p.option_exit_booked_quantities == {"BROKER-OPTION-EXIT": 25}
    book(p, baseline, filled=25)
    assert p.option_exit_qty == 25
    assert p.option_exit_value == 2250.0


def test_terminal_hedge_fill_uses_hedge_only():
    p = position()
    old = deepcopy(p)
    book(p, old, leg="hedge", order_id="BROKER-HEDGE-EXIT",
         requested=25, filled=25, average=20000)
    assert p.hedge_exit_qty == 25
    assert p.hedge_exit_value == 500000.0
    assert p.option_exit_qty == 0
    assert p.hedge_exit_booked_ids == ["BROKER-HEDGE-EXIT"]


def test_cancelled_no_fill_has_no_projection_or_attempt_side_effect():
    p = position()
    original = deepcopy(p)
    book(p, original, filled=0, average=0)
    book(p, original, filled=0, average=0)
    assert p == original
    # The enclosing transactional ACK retirement increments attempts once.
    # This pure fill projector is idempotent across restart/repeated callbacks.


@pytest.mark.parametrize("field,value", [
    ("uid", "other-user"),
    ("account_id", "other-account"),
    ("position_id", "other-position"),
    ("option_symbol", "OTHER_PE"),
    ("option_exchange", "BSE"),
    ("option_exit_order_id", "OTHER-EXIT"),
    ("option_exit_requested", 50),
    ("option_exit_qty", 50),
    ("option_qty", 25),
    ("status", "CLOSED"),
])
def test_transaction_rejects_option_drift(field, value):
    baseline = position()
    current = deepcopy(baseline)
    setattr(current, field, value)
    original = deepcopy(current)
    with pytest.raises(ValueError):
        book(current, baseline, filled=50, average=90)
    assert current == original


@pytest.mark.parametrize("field,value", [
    ("hedge_symbol", "OTHER_FUT"),
    ("hedge_exchange", "BSE"),
    ("hedge_side", "SELL"),
    ("hedge_exit_order_id", ""),
    ("hedge_exit_requested", 0),
    ("hedge_exit_qty", 10),
])
def test_transaction_rejects_hedge_drift(field, value):
    baseline = position()
    current = deepcopy(baseline)
    setattr(current, field, value)
    original = deepcopy(current)
    with pytest.raises(ValueError):
        book(current, baseline, leg="hedge", order_id="BROKER-HEDGE-EXIT",
             requested=25, filled=25, average=20000)
    assert current == original


@pytest.mark.parametrize("requested,filled,average", [
    (True, 1, 90), (75, True, 90), (75, -1, 90),
    (75, 76, 90), (75, 1, float("nan")),
    (75, 1, float("inf")), (75, 1, 0),
    (75, 1, -1), (75, 1, "90"),
])
def test_invalid_broker_receipt_cannot_mutate(requested, filled, average):
    p = position()
    old = deepcopy(p)
    with pytest.raises(ValueError):
        book(p, old, requested=requested, filled=filled, average=average)
    assert p == old


def test_terminal_receipt_cannot_overbook_remaining_units_after_other_exit():
    p = position()
    p.option_exit_qty = 50
    old = deepcopy(p)
    with pytest.raises(ValueError):
        book(p, old, filled=50)
    assert p == old


def test_already_booked_receipt_is_idempotent_without_second_value():
    p = position()
    p.option_exit_qty = 25
    p.option_exit_value = 2250.0
    p.option_exit_booked_ids = ["BROKER-OPTION-EXIT"]
    p.option_exit_booked_quantities = {"BROKER-OPTION-EXIT": 25}
    before = deepcopy(p)
    book(p, before, filled=25)
    assert p == before


@pytest.mark.parametrize("new_qty", [0, 1, 24, 26, 75])
def test_existing_order_changed_cumulative_fill_never_sends_false_residual(new_qty):
    # A newer broker snapshot can report a different cumulative fill.
    # The old ID-only dedupe silently ignored that discrepancy.
    p = position()
    original = deepcopy(p)
    book(p, original, filled=25)
    before = deepcopy(p)
    with pytest.raises(ValueError, match="cumulative fill unverified"):
        book(p, original, filled=new_qty)
    assert p == before


def test_old_row_with_booked_id_but_no_per_order_quantity_halts():
    p = position()
    p.option_exit_qty = 25
    p.option_exit_value = 2250.0
    p.option_exit_booked_ids = ["BROKER-OPTION-EXIT"]
    # The added map is empty on a row persisted by older app revisions.
    baseline = deepcopy(p)
    with pytest.raises(ValueError, match="cumulative fill unverified"):
        book(p, baseline, filled=25)
    assert p == baseline


def test_one_canonical_fill_records_exact_hedge_order_quantity():
    p = position()
    book(p, deepcopy(p), leg="hedge", order_id="BROKER-HEDGE-EXIT",
         requested=25, filled=10, average=20000)
    assert p.hedge_exit_booked_quantities == {"BROKER-HEDGE-EXIT": 10}
    before = deepcopy(p)
    with pytest.raises(ValueError, match="cumulative fill unverified"):
        book(p, deepcopy(position()), leg="hedge",
             order_id="BROKER-HEDGE-EXIT", requested=25,
             filled=25, average=20000)
    assert p == before
