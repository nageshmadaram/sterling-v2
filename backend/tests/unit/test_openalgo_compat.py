"""OpenAlgo read-model compatibility is descriptive, not broker-write authority."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services.openalgo_compat import canonical_order_status


def intent(**updates):
    payload = {
        "state": "SUBMITTED", "order_id": "SYNTHETIC-1", "side": "BUY",
        "exchange": "NFO", "symbol": "SYNTHETIC26NOV25000CE",
        "quantity": 25, "filled_quantity": 0, "filled_value": 0.0,
        "payload": {"product": "MIS"},
    }
    payload.update(updates)
    return SimpleNamespace(**payload)


@pytest.mark.parametrize("state", ["UNKNOWN", "SUBMITTING", "RESERVED"])
def test_uncertain_orders_never_masquerade_as_success(state):
    result = canonical_order_status(intent(state=state, order_id="PENDING"))
    assert result["status"] == "error"
    assert result["code"] == "ORDER_OUTCOME_UNRESOLVED"
    assert "data" not in result and "orderid" not in result


def test_acknowledgement_is_only_pending_confirmation():
    result = canonical_order_status(intent())
    assert result["status"] == "success"
    assert result["data"]["order_status"] == "pending_confirmation"
    assert result["data"]["average_price"] == 0
    assert result["data"]["orderid"] == "SYNTHETIC-1"
    assert result["data"]["quantity"] == "25"


def test_real_fill_is_complete_only_on_consistent_broker_evidence():
    result = canonical_order_status(intent(
        state="FILLED", filled_quantity=25, filled_value=2500,
    ))
    assert result["status"] == "success"
    assert result["data"]["order_status"] == "complete"
    assert result["data"]["average_price"] == 100
    assert result["data"]["filled_quantity"] == "25"


def test_partial_fill_preserves_partial_status():
    result = canonical_order_status(intent(
        state="PARTIAL", filled_quantity=10, filled_value=1000,
    ))
    assert result["data"]["order_status"] == "partially_filled"
    assert result["data"]["filled_quantity"] == "10"


@pytest.mark.parametrize("changes", [
    {"state": "FILLED", "filled_quantity": 0},
    {"state": "FILLED", "filled_quantity": 25, "filled_value": 0},
    {"state": "PARTIAL", "filled_quantity": 25, "filled_value": 2500},
    {"state": "SUBMITTED", "filled_quantity": 1, "filled_value": 99},
    {"state": "REJECTED", "filled_quantity": 1, "filled_value": 0},
])
def test_conflicting_fill_evidence_cannot_be_presented_as_order_success(changes):
    result = canonical_order_status(intent(**changes))
    assert result["status"] == "error" and result["code"] == "CONFLICTING_BROKER_FILL"


@pytest.mark.parametrize("changes", [
    {"order_id": "UNKNOWN"},
    {"order_id": "PENDING"},
    {"order_id": ""},
    {"quantity": -1},
    {"quantity": True},
    {"side": "INVALID"},
    {"payload": {"product": "BOGUS"}},
    {"filled_value": float("nan")},
])
def test_malformed_or_synthetic_order_identity_is_rejected(changes):
    result = canonical_order_status(intent(**changes))
    assert result["status"] == "error"


def test_no_intent_is_a_not_found_error():
    assert canonical_order_status(None)["code"] == "ORDER_NOT_FOUND"


def test_quarantined_terminal_fill_is_not_exposed_as_success():
    response = canonical_order_status(intent(
        state="FILLED", filled_quantity=25, filled_value=2500,
        reconciliation_required=True,
    ))
    assert response == {
        "status": "error",
        "code": "BROKER_EVIDENCE_CONFLICT",
        "message": "Order evidence is quarantined and requires operator reconciliation",
    }


def test_pending_position_projection_is_disclosed_on_confirmed_broker_fill():
    pending = canonical_order_status(intent(
        state="FILLED", filled_quantity=25, filled_value=2500,
        projection_pending=True,
    ))
    assert pending["status"] == "success"
    assert pending["data"]["order_status"] == "complete"
    assert pending["data"]["position_projection_pending"] is True
    saved = canonical_order_status(intent(
        state="FILLED", filled_quantity=25, filled_value=2500,
        projection_pending=False,
    ))
    assert saved["data"]["position_projection_pending"] is False


@pytest.mark.parametrize("state", ["SUBMITTED", "PARTIAL", "CANCELLED", "REJECTED"])
def test_any_quarantined_state_refuses_success(state):
    response = canonical_order_status(intent(
        state=state, reconciliation_required=True,
    ))
    assert response["code"] == "BROKER_EVIDENCE_CONFLICT"
