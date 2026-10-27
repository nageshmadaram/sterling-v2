"""Snapback cannot treat broker-confirmed but unbooked exposure as success."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.services import snapback_live_wiring as wiring
from app.services import canonical_fill_reconciliation as fills
from app.services import fee_safety


def request():
    return SimpleNamespace(
        uid="operator", account_id="account", symbol="NIFTY_TEST_PE",
        exchange="NFO", side="BUY", quantity=75, price=100.0,
        payload={}, tag="snapback:plan",
    )


def resolved(*, quantity=75):
    return SimpleNamespace(
        filled_quantity=quantity, average_price=100.0,
        status="COMPLETE", terminal=True, settled=True, reconciliation_required=False,
        intent_key="intent", projection_version=3, reason="",
    )


def adapter(*, resolution=None):
    canonical = SimpleNamespace(submit_order=AsyncMock(return_value=SimpleNamespace(
        success=True, status="ACKNOWLEDGED",
        order_id="BROKER-ORDER-1", intent_key="intent",
    )))
    async def resolver(**kwargs):
        return resolution if resolution is not None else resolved()
    return wiring.SnapbackExecutionAdapter(
        plan_store=SimpleNamespace(), canonical=canonical, resolver=resolver,
    )


@pytest.mark.asyncio
async def test_real_fill_with_failed_ledger_or_registry_booking_is_never_success(monkeypatch):
    a = adapter()
    monkeypatch.setattr(a, "_book_fill", Mock(return_value=False))
    killed = []
    monkeypatch.setattr(wiring, "engage_kill_switch", lambda reason, uid: killed.append((reason, uid)))
    refresh = AsyncMock()
    monkeypatch.setattr(fee_safety, "refresh", refresh)
    ack = Mock()
    monkeypatch.setattr(fills, "mark_projected", ack)

    response = await a.submit_order(request(), broker_client=object())

    assert response.status == "UNKNOWN" and not response.success
    assert response.filled_quantity == 75
    assert response.order_id == "BROKER-ORDER-1"
    assert response.intent_key == "intent"
    assert "reconcile" in response.error
    assert len(killed) == 1 and killed[0][1] == "operator"
    refresh.assert_not_awaited()
    ack.assert_not_called()


@pytest.mark.asyncio
async def test_durably_booked_fill_retains_normal_success_and_projection_ack(monkeypatch):
    a = adapter()
    booked = Mock(return_value=True)
    monkeypatch.setattr(a, "_book_fill", booked)
    refresh = AsyncMock()
    monkeypatch.setattr(fee_safety, "refresh", refresh)
    ack = Mock(return_value=True)
    monkeypatch.setattr(fills, "mark_projected", ack)
    kill = Mock()
    monkeypatch.setattr(wiring, "engage_kill_switch", kill)

    response = await a.submit_order(request(), broker_client=object())

    assert response.status == "FILLED" and response.success
    assert response.filled_quantity == 75
    booked.assert_called_once()
    refresh.assert_awaited_once()
    ack.assert_called_once()
    kill.assert_not_called()


@pytest.mark.asyncio
async def test_fee_authority_failure_after_booked_fill_requires_reconciliation(monkeypatch):
    a = adapter()
    monkeypatch.setattr(a, "_book_fill", Mock(return_value=True))
    monkeypatch.setattr(fee_safety, "refresh", AsyncMock(side_effect=OSError("fee cache unavailable")))
    acknowledged = Mock()
    monkeypatch.setattr(fills, "mark_projected", acknowledged)
    killed = []
    monkeypatch.setattr(wiring, "engage_kill_switch", lambda reason, uid: killed.append((reason, uid)))

    response = await a.submit_order(request(), broker_client=object())

    assert not response.success and response.status == "UNKNOWN"
    assert response.filled_quantity == 75
    assert response.order_id == "BROKER-ORDER-1"
    assert "fee authority" in response.error
    assert killed and killed[0][1] == "operator"
    acknowledged.assert_not_called()


@pytest.mark.asyncio
async def test_broker_reconciliation_conflict_cannot_acknowledge_projected_fill(monkeypatch):
    a = adapter(resolution=resolved())
    conflicting = resolved()
    conflicting.reconciliation_required = True
    async def resolve_conflict(**kwargs):
        return conflicting
    a._resolver = resolve_conflict
    monkeypatch.setattr(a, "_book_fill", Mock(return_value=True))
    monkeypatch.setattr(fee_safety, "refresh", AsyncMock())
    ack = Mock()
    monkeypatch.setattr(fills, "mark_projected", ack)
    kill = Mock()
    monkeypatch.setattr(wiring, "engage_kill_switch", kill)

    response = await a.submit_order(request(), broker_client=object())

    assert response.status == "UNKNOWN" and not response.success
    assert response.filled_quantity == 75
    ack.assert_not_called()
    kill.assert_called_once()


@pytest.mark.asyncio
async def test_unsettled_partial_fill_cannot_acknowledge_projected_fill(monkeypatch):
    a = adapter()
    partial = resolved(quantity=25)
    partial.settled = False
    async def resolve_partial(**kwargs):
        return partial
    a._resolver = resolve_partial
    monkeypatch.setattr(a, "_book_fill", Mock(return_value=True))
    monkeypatch.setattr(fee_safety, "refresh", AsyncMock())
    ack = Mock()
    monkeypatch.setattr(fills, "mark_projected", ack)
    kill = Mock()
    monkeypatch.setattr(wiring, "engage_kill_switch", kill)

    response = await a.submit_order(request(), broker_client=object())

    assert response.status == "UNKNOWN" and not response.success
    assert response.filled_quantity == 25
    ack.assert_not_called()
    kill.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("quantity,price,preserved", [
    (-1, 100.0, 0),
    (None, 100.0, 0),
    (float("nan"), 100.0, 0),
    (True, 100.0, 0),
    (1.5, 100.0, 0),
    (76, 100.0, 76),  # overfill still needs emergency protection
    (75, 0.0, 75),
    (75, -1.0, 75),
    (75, float("nan"), 75),
    (75, float("inf"), 75),
])
async def test_invalid_broker_fill_evidence_halts_without_booking_or_ack(
        monkeypatch, quantity, price, preserved):
    candidate = resolved()
    candidate.filled_quantity = quantity
    candidate.average_price = price
    a = adapter(resolution=candidate)
    booking = Mock()
    monkeypatch.setattr(a, "_book_fill", booking)
    fee_refresh = AsyncMock()
    monkeypatch.setattr(fee_safety, "refresh", fee_refresh)
    ack = Mock()
    monkeypatch.setattr(fills, "mark_projected", ack)
    kills = []
    monkeypatch.setattr(wiring, "engage_kill_switch", lambda reason, uid: kills.append((reason, uid)))

    response = await a.submit_order(request(), broker_client=object())

    assert response.status == "UNKNOWN" and response.success is False
    assert response.order_id == "BROKER-ORDER-1"
    assert response.filled_quantity == preserved
    assert "invalid broker fill" in response.error
    assert kills and kills[0][1] == "operator"
    booking.assert_not_called()
    fee_refresh.assert_not_awaited()
    ack.assert_not_called()


@pytest.mark.asyncio
async def test_missing_resolution_object_stays_unknown_and_preserves_broker_identity(monkeypatch):
    a = adapter(resolution=resolved())
    async def missing(**kwargs):
        return None
    a._resolver = missing
    kill = Mock()
    monkeypatch.setattr(wiring, "engage_kill_switch", kill)
    response = await a.submit_order(request(), broker_client=object())
    assert response.status == "UNKNOWN" and not response.success
    assert response.order_id == "BROKER-ORDER-1"
    assert response.filled_quantity == 0
    kill.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("ack_outcome", ["missing", "stale", "exception"])
async def test_failed_journal_projection_acknowledgement_blocks_release(
        monkeypatch, ack_outcome):
    a = adapter()
    monkeypatch.setattr(a, "_book_fill", Mock(return_value=True))
    monkeypatch.setattr(fee_safety, "refresh", AsyncMock())
    ack = Mock(
        side_effect=OSError("disk fault") if ack_outcome == "exception" else None,
        return_value=False if ack_outcome in {"missing", "stale"} else True,
    )
    monkeypatch.setattr(fills, "mark_projected", ack)
    kills = []
    monkeypatch.setattr(wiring, "engage_kill_switch", lambda reason, uid: kills.append((reason, uid)))

    response = await a.submit_order(request(), broker_client=object())

    assert not response.success and response.status == "UNKNOWN"
    assert response.order_id == "BROKER-ORDER-1"
    assert response.filled_quantity == 75
    assert "projection" in response.error
    ack.assert_called_once()
    assert kills and kills[0][1] == "operator"


@pytest.mark.asyncio
@pytest.mark.parametrize("version", [0, -1, None, True, "1"])
async def test_settled_fill_without_valid_journal_projection_version_is_not_a_success(
        monkeypatch, version):
    candidate = resolved()
    candidate.projection_version = version
    a = adapter(resolution=candidate)
    monkeypatch.setattr(a, "_book_fill", Mock(return_value=True))
    monkeypatch.setattr(fee_safety, "refresh", AsyncMock())
    ack = Mock(return_value=True)
    monkeypatch.setattr(fills, "mark_projected", ack)
    killed = Mock()
    monkeypatch.setattr(wiring, "engage_kill_switch", killed)
    response = await a.submit_order(request(), broker_client=object())
    assert not response.success and response.status == "UNKNOWN"
    assert response.order_id == "BROKER-ORDER-1"
    assert response.filled_quantity == 75
    ack.assert_not_called()
    killed.assert_called_once()


@pytest.mark.asyncio
async def test_fully_filled_but_still_open_broker_order_remains_unresolved(monkeypatch):
    candidate = resolved(quantity=75)
    candidate.status = "OPEN"
    candidate.terminal = False
    candidate.settled = True  # the journal has all requested units, not final broker state
    a = adapter(resolution=candidate)
    monkeypatch.setattr(a, "_book_fill", Mock(return_value=True))
    monkeypatch.setattr(fee_safety, "refresh", AsyncMock())
    acknowledge = Mock(return_value=True)
    monkeypatch.setattr(fills, "mark_projected", acknowledge)
    killed = Mock()
    monkeypatch.setattr(wiring, "engage_kill_switch", killed)
    response = await a.submit_order(request(), broker_client=object())
    assert response.status == "UNKNOWN" and not response.success
    assert response.filled_quantity == 75
    assert response.order_id == "BROKER-ORDER-1"
    acknowledge.assert_not_called()
    killed.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["OPEN", "COMPLETE", "", "SUBMITTED"])
async def test_inconsistent_zero_fill_receipt_cannot_be_called_rejected(
        monkeypatch, status):
    candidate = resolved(quantity=0)
    candidate.status = status
    a = adapter(resolution=candidate)
    killed = Mock()
    monkeypatch.setattr(wiring, "engage_kill_switch", killed)
    response = await a.submit_order(request(), broker_client=object())
    assert response.status == "UNKNOWN" and not response.success
    assert response.order_id == "BROKER-ORDER-1"
    assert "reconcile" in response.error
    killed.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["CANCELLED", "REJECTED"])
async def test_verified_zero_fill_terminal_receipt_is_not_a_working_order(monkeypatch, status):
    candidate = resolved(quantity=0)
    candidate.status = status
    a = adapter(resolution=candidate)
    killed = Mock()
    monkeypatch.setattr(wiring, "engage_kill_switch", killed)
    response = await a.submit_order(request(), broker_client=object())
    assert response.status == status and not response.success
    assert response.filled_quantity == 0
    killed.assert_not_called()


@pytest.mark.asyncio
async def test_unexpected_ledger_booking_exception_after_real_fill_is_unresolved(monkeypatch):
    a = adapter()
    monkeypatch.setattr(a, "_book_fill", Mock(side_effect=OSError("db disk full")))
    refresh = AsyncMock()
    monkeypatch.setattr(fee_safety, "refresh", refresh)
    ack = Mock(return_value=True)
    monkeypatch.setattr(fills, "mark_projected", ack)
    killed = Mock()
    monkeypatch.setattr(wiring, "engage_kill_switch", killed)
    response = await a.submit_order(request(), broker_client=object())
    assert response.status == "UNKNOWN" and not response.success
    assert response.order_id == "BROKER-ORDER-1" and response.filled_quantity == 75
    assert "not fully booked" in response.error
    refresh.assert_not_awaited()
    ack.assert_not_called()
    killed.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_reference", ["not-a-price", float("nan"), float("inf"), -20, True, {}])
async def test_invalid_post_submit_reference_preserves_broker_resolution_without_authorizing(
        monkeypatch, bad_reference):
    a = adapter()
    booked = Mock(return_value=True)
    monkeypatch.setattr(a, "_book_fill", booked)
    monkeypatch.setattr(fee_safety, "refresh", AsyncMock())
    projected = Mock(return_value=True)
    monkeypatch.setattr(fills, "mark_projected", projected)
    killed = Mock()
    monkeypatch.setattr(wiring, "engage_kill_switch", killed)
    order = request()
    order.price = bad_reference

    response = await a.submit_order(order, broker_client=object())

    assert response.status == "UNKNOWN" and not response.success
    assert response.order_id == "BROKER-ORDER-1"
    assert response.filled_quantity == 75
    assert "reference price invalid" in response.error
    booked.assert_called_once()  # known broker exposure still registered for protection
    projected.assert_not_called()  # uncertainty must remain in the durable journal
    killed.assert_called_once()
