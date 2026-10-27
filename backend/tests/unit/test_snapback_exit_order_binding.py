"""Broker exit ACK order identity must be durably bound without exposing extra units."""
from types import SimpleNamespace as NS
from copy import deepcopy
import pytest

from app.services.snapback_exit_order_binding import bind_acknowledged_exit as bind


def position():
    return NS(position_id="pos-1", uid="owner", account_id="acct-1",
              option_symbol="PE", hedge_symbol="FUT", option_qty=75,
              option_exit_qty=0, hedge_qty=25, hedge_exit_qty=0,
              option_exit_order_id="", hedge_exit_order_id="",
              option_exit_requested=0, hedge_exit_requested=0)


@pytest.mark.parametrize("leg,qty", [("option", 75), ("hedge", 25), ("option", 25)])
def test_valid_owned_leg(leg, qty):
    p = position()
    bind(p, deepcopy(p), leg=leg, broker_order_id="KITE-123", requested=qty)
    assert getattr(p, f"{leg}_exit_order_id") == "KITE-123"
    assert getattr(p, f"{leg}_exit_requested") == qty


def test_duplicate_same_id_is_idempotent():
    p = position()
    origin = deepcopy(p)
    bind(p, origin, leg="option", broker_order_id="KITE-123", requested=75)
    bind(p, origin, leg="option", broker_order_id="KITE-123", requested=75)
    assert p.option_exit_requested == 75


@pytest.mark.parametrize("field", [
    "uid", "account_id", "position_id", "option_symbol", "hedge_symbol",
])
def test_owner_or_contract_drift_rejected(field):
    p = position()
    old = deepcopy(p)
    setattr(p, field, "FOREIGN")
    with pytest.raises(ValueError, match="ownership"):
        bind(p, old, leg="hedge", broker_order_id="KITE-123", requested=25)
    assert p.hedge_exit_order_id == ""


@pytest.mark.parametrize("qty", [0, -1, 26, 75, True, 1.5])
def test_invalid_hedge_quantity_rejected(qty):
    p = position()
    with pytest.raises(ValueError):
        bind(p, deepcopy(p), leg="hedge", broker_order_id="KITE-123", requested=qty)
    assert p.hedge_exit_order_id == ""


def test_over_remaining_option_quantity_rejected():
    p = position()
    p.option_exit_qty = 50
    with pytest.raises(ValueError, match="remaining"):
        bind(p, deepcopy(p), leg="option", broker_order_id="KITE-123", requested=75)


@pytest.mark.parametrize("old_order,old_qty", [
    ("KITE-OLD", 75), ("KITE-123", 25),
])
def test_working_order_identity_or_amount_never_replaced(old_order, old_qty):
    p = position()
    p.option_exit_order_id = old_order
    p.option_exit_requested = old_qty
    with pytest.raises(ValueError, match="different broker"):
        bind(p, deepcopy(p), leg="option", broker_order_id="KITE-123", requested=75)
    assert p.option_exit_order_id == old_order
    assert p.option_exit_requested == old_qty


def test_reject_unknown_leg_and_blank_id():
    p = position()
    for leg, oid in [("strange", "KITE-123"), ("option", "")]:
        with pytest.raises(ValueError):
            bind(p, deepcopy(p), leg=leg, broker_order_id=oid, requested=75)


def test_partly_exited_but_exact_remaining_is_allowed():
    p = position()
    p.option_exit_qty = 50
    bind(p, deepcopy(p), leg="option", broker_order_id="KITE-123", requested=25)
    assert p.option_exit_requested == 25


@pytest.mark.parametrize("order_id", [" ", " KITE-123", "KITE-123 ", None, 123])
def test_malformed_ack_order_id_never_binds(order_id):
    p = position()
    with pytest.raises(ValueError, match="invalid broker"):
        bind(p, deepcopy(p), leg="option", broker_order_id=order_id, requested=75)
    assert p.option_exit_order_id == ""
    assert p.option_exit_requested == 0


@pytest.mark.parametrize("field,value", [
    ("option_qty", 75.5), ("option_qty", True),
    ("option_exit_qty", 0.5), ("option_exit_qty", True),
    ("option_exit_requested", 1.5), ("option_exit_requested", True),
    ("option_exit_order_id", None), ("option_exit_order_id", 17),
    ("option_exit_requested", -1),
])
def test_corrupt_stored_quantity_or_identity_cannot_be_coerced(field, value):
    p = position()
    setattr(p, field, value)
    original = deepcopy(p)
    with pytest.raises(ValueError):
        bind(p, original, leg="option", broker_order_id="KITE-123", requested=75)
    assert p.option_exit_order_id == original.option_exit_order_id
    assert p.option_exit_requested == original.option_exit_requested


def test_orphan_requested_exit_quantity_blocks_new_broker_ack():
    p = position()
    p.option_exit_requested = 75
    with pytest.raises(ValueError, match="different broker"):
        bind(p, deepcopy(p), leg="option", broker_order_id="SECOND-ORDER", requested=75)
    assert p.option_exit_order_id == ""
    assert p.option_exit_requested == 75


def test_orphan_exit_order_id_blocks_new_broker_ack():
    p = position()
    p.option_exit_order_id = "UNRESOLVED-ORDER"
    with pytest.raises(ValueError, match="different broker"):
        bind(p, deepcopy(p), leg="option", broker_order_id="SECOND-ORDER", requested=75)
    assert p.option_exit_order_id == "UNRESOLVED-ORDER"
    assert p.option_exit_requested == 0
