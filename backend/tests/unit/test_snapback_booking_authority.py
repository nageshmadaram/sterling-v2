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


def _bound_leg_context():
    plan = {
        "owner_uid": "operator", "account_id": "account",
        "option_symbol": "NIFTY_TEST_PE", "option_exchange": "NFO",
        "futures_symbol": "NIFTY_TEST_FUT", "futures_exchange": "NFO",
        "hedge_side": "SELL",
    }
    existing = SimpleNamespace(
        uid="operator", account_id="account", status="ENTERING",
        option_symbol="NIFTY_TEST_PE", option_exchange="NFO",
        option_entry_order_id="BROKER-ORDER-1", option_qty=75,
        option_entry_price=100.0, stop_premium=60.0, desired_stop=60.0,
        hedge_entry_order_id="BROKER-HEDGE-1", hedge_qty=50,
        hedge_entry_price=200.0, hedge_side="SELL", hedge_exchange="NFO",
        hedge_symbol="NIFTY_TEST_FUT",
    )
    store = SimpleNamespace(get=Mock(return_value=existing), mutate=Mock())
    a = wiring.SnapbackExecutionAdapter(
        plan_store=SimpleNamespace(get=Mock(return_value=plan)), positions=store,
    )
    return a, plan, existing, store


@pytest.mark.parametrize("change", [
    {"uid": "other-user"},
    {"account_id": "other-account"},
    {"exchange": "BFO"},
    {"side": "SELL"},
    {"symbol": "OTHER_PE"},
])
def test_plan_owner_account_contract_and_option_buy_are_bound_before_registry_update(change):
    a, plan, existing, store = _bound_leg_context()
    req = request()
    req.__dict__.update(change)
    with pytest.raises(PermissionError):
        a._record_leg(req, "BROKER-ORDER-1", 75, 100.0)
    store.mutate.assert_not_called()


@pytest.mark.parametrize("changed", [
    {"owner_uid": ""},
    {"owner_uid": "foreign"},
    {"account_id": ""},
    {"account_id": "foreign"},
])
def test_incomplete_or_foreign_plan_ownership_is_not_booked(changed):
    a, plan, existing, store = _bound_leg_context()
    plan.update(changed)
    with pytest.raises(PermissionError, match="owner_mismatch|account_or_owner"):
        a._record_leg(request(), "BROKER-ORDER-1", 75, 100.0)
    store.mutate.assert_not_called()


def test_registry_owner_mismatch_is_not_overwritten():
    a, plan, existing, store = _bound_leg_context()
    existing.uid = "another-user"
    with pytest.raises(PermissionError, match="owned_position_mismatch"):
        a._record_leg(request(), "BROKER-ORDER-1", 75, 100.0)
    store.mutate.assert_not_called()


@pytest.mark.parametrize("new_qty,new_oid,reason", [
    (74, "BROKER-ORDER-1", "stale_cumulative"),
    (75, "BROKER-OTHER", "order_id_conflict"),
])
def test_stale_or_replaced_option_entry_evidence_cannot_modify_protected_risk(
        new_qty, new_oid, reason):
    a, plan, existing, store = _bound_leg_context()
    with pytest.raises(ValueError, match=reason):
        a._record_leg(request(), new_oid, new_qty, 100.0)
    assert existing.option_qty == 75
    assert existing.option_entry_order_id == "BROKER-ORDER-1"
    store.mutate.assert_not_called()


@pytest.mark.parametrize("new_qty,new_oid,reason", [
    (49, "BROKER-HEDGE-1", "stale_cumulative"),
    (50, "BROKER-HEDGE-OTHER", "order_id_conflict"),
])
def test_stale_or_replaced_futures_hedge_does_not_reduce_position_registry(
        new_qty, new_oid, reason):
    a, plan, existing, store = _bound_leg_context()
    req = request()
    req.symbol = "NIFTY_TEST_FUT"
    req.side = "SELL"
    with pytest.raises(ValueError, match=reason):
        a._record_leg(req, new_oid, new_qty, 200.0)
    assert existing.hedge_qty == 50
    store.mutate.assert_not_called()


def test_valid_cumulative_option_book_updates_owner_bound_registry():
    a, plan, existing, store = _bound_leg_context()
    store.mutate.side_effect = lambda _id, callback: callback(existing)
    a._record_leg(request(), "BROKER-ORDER-1", 100, 101.0)
    assert existing.option_qty == 100
    assert existing.option_entry_order_id == "BROKER-ORDER-1"
    store.mutate.assert_called_once()


@pytest.mark.parametrize("leg", ["option", "futures"])
def test_concurrent_newer_position_state_cannot_be_overwritten_by_stale_callback(leg):
    a, plan, old, store = _bound_leg_context()
    from copy import copy
    newer = copy(old)
    req = request()
    if leg == "option":
        older_qty, order_id = 75, "BROKER-ORDER-1"
        newer.option_qty = 125
    else:
        req.symbol = "NIFTY_TEST_FUT"
        req.side = "SELL"
        older_qty, order_id = 50, "BROKER-HEDGE-1"
        newer.hedge_qty = 100
    # get() returned the older position. Between get() and SQLite
    # BEGIN IMMEDIATE/mutate(), another process recorded a larger fill.
    store.get.return_value = old
    store.mutate.side_effect = lambda _, callback: callback(newer)
    with pytest.raises(ValueError, match="stale_cumulative"):
        a._record_leg(req, order_id, older_qty, 100.0)
    assert newer.option_qty == 125 if leg == "option" else newer.hedge_qty == 100


def test_insert_new_first_fill_never_overwrites_a_different_existing_owner(tmp_path, monkeypatch):
    import sqlite3
    from dataclasses import replace
    from app.services import snapback_live_positions as live_positions
    monkeypatch.setattr(live_positions._journal, "sb_sync", lambda *_: None)
    store = live_positions.LivePositionStore(tmp_path / "snapback-test.db")
    original = live_positions.LivePosition(
        position_id="one-plan", uid="owner", account_id="account",
        signal_id="signal", lane="swing", underlying="NIFTY",
        side="fade_up", option_type="PE", option_exchange="NFO",
        option_symbol="NIFTY_TEST_PE", option_qty=75, option_entry_price=100.0,
        option_entry_order_id="BROKER-ORDER-1",
    )
    store.insert_new(original)
    with pytest.raises(sqlite3.IntegrityError):
        store.insert_new(replace(original, uid="intruder", account_id="foreign"))
    recovered = store.get("one-plan")
    assert recovered.uid == "owner"
    assert recovered.account_id == "account"
    assert recovered.option_entry_order_id == "BROKER-ORDER-1"


@pytest.mark.parametrize("change", [
    {"uid": "foreign-owner"},
    {"account_id": "other-account"},
    {"exchange": "BFO"},
    {"symbol": "OTHER-CONTRACT"},
    {"side": "SELL"},
    {"tag": "snapback:nonexistent"},
])
def test_foreign_broker_fill_does_not_touch_shared_ledger(monkeypatch, change):
    from app.services.kite_engine import fill_ledger
    a, plan, existing, store = _bound_leg_context()
    req = request()
    req.__dict__.update(change)
    if change.get("tag") == "snapback:nonexistent":
        a.plan_store.get = Mock(return_value=None)
    ledger_write = Mock()
    monkeypatch.setattr(fill_ledger, "record", ledger_write)
    killed = Mock()
    monkeypatch.setattr(wiring, "engage_kill_switch", killed)
    assert not a._book_fill(req, resolved(), "BROKER-ORDER-1", 75, 100.0)
    ledger_write.assert_not_called()
    store.mutate.assert_not_called()
    killed.assert_called()


def test_stale_broker_fill_does_not_touch_shared_ledger(monkeypatch):
    from app.services.kite_engine import fill_ledger
    a, plan, existing, store = _bound_leg_context()
    write = Mock()
    monkeypatch.setattr(fill_ledger, "record", write)
    monkeypatch.setattr(wiring, "engage_kill_switch", Mock())
    assert not a._book_fill(request(), resolved(quantity=74), "BROKER-ORDER-1", 74, 100.0)
    write.assert_not_called()


def test_verified_owner_can_book_to_shared_ledger_with_exact_exchange_time(monkeypatch):
    from app.services.kite_engine import fill_ledger
    a, plan, existing, store = _bound_leg_context()
    monkeypatch.setattr(a, "_record_leg", Mock())
    record = Mock(return_value=SimpleNamespace(reconciliation_required=False))
    monkeypatch.setattr(fill_ledger, "record", record)
    kill = Mock()
    monkeypatch.setattr(wiring, "engage_kill_switch", kill)
    assert a._book_fill(request(), SimpleNamespace(exchange_ts_ms=123456),
                        "BROKER-ORDER-1", 75, 100.0)
    assert record.call_args.kwargs["uid"] == "operator"
    assert record.call_args.kwargs["account_id"] == "account"
    assert record.call_args.kwargs["exchange_ts_ms"] == 123456
    kill.assert_not_called()


@pytest.mark.parametrize("status", ["CLOSED", "EXITING", "", "INVALID"])
def test_late_postback_cannot_reopen_closed_or_exiting_position(monkeypatch, status):
    from app.services.kite_engine import fill_ledger
    a, plan, existing, store = _bound_leg_context()
    existing.status = status
    ledger = Mock()
    monkeypatch.setattr(fill_ledger, "record", ledger)
    stop_new = Mock()
    monkeypatch.setattr(wiring, "engage_kill_switch", stop_new)
    assert not a._book_fill(request(), resolved(), "BROKER-ORDER-1", 75, 100.0)
    ledger.assert_not_called()
    store.mutate.assert_not_called()
    stop_new.assert_called_once()


def test_late_state_transition_inside_transaction_cannot_reopen_position():
    a, plan, existing, store = _bound_leg_context()
    from copy import copy
    fresh = copy(existing)
    fresh.status = "CLOSED"
    store.get.return_value = existing
    store.mutate.side_effect = lambda _, fn: fn(fresh)
    with pytest.raises(PermissionError, match="no_longer_accepts_entry"):
        a._record_leg(request(), "BROKER-ORDER-1", 75, 100.0)
    assert fresh.status == "CLOSED"


def test_existing_option_contract_drift_refuses_shared_ledger(monkeypatch):
    from app.services.kite_engine import fill_ledger
    a, plan, existing, store = _bound_leg_context()
    existing.option_exchange = "BFO"
    ledger = Mock()
    monkeypatch.setattr(fill_ledger, "record", ledger)
    monkeypatch.setattr(wiring, "engage_kill_switch", Mock())
    assert not a._book_fill(request(), resolved(), "BROKER-ORDER-1", 75, 100.0)
    ledger.assert_not_called()


def test_quarantined_broker_fill_never_rewrites_owned_position(monkeypatch):
    from app.services.kite_engine import fill_ledger
    a, plan, existing, store = _bound_leg_context()
    record = Mock(return_value=SimpleNamespace(reconciliation_required=True))
    monkeypatch.setattr(fill_ledger, "record", record)
    registry = Mock()
    monkeypatch.setattr(a, "_record_leg", registry)
    halt = Mock()
    monkeypatch.setattr(wiring, "engage_kill_switch", halt)

    assert a._book_fill(request(), resolved(), "BROKER-ORDER-1", 75, 110.0) is False
    record.assert_called_once()
    registry.assert_not_called()
    store.mutate.assert_not_called()
    assert existing.option_entry_price == 100.0
    halt.assert_called_once()


def test_ledger_write_failure_still_attempts_position_registration_for_protection(monkeypatch):
    from app.services.kite_engine import fill_ledger
    a, plan, existing, store = _bound_leg_context()
    monkeypatch.setattr(fill_ledger, "record", Mock(side_effect=OSError("disk full")))
    registry = Mock()
    monkeypatch.setattr(a, "_record_leg", registry)
    halt = Mock()
    monkeypatch.setattr(wiring, "engage_kill_switch", halt)
    assert a._book_fill(request(), resolved(), "BROKER-ORDER-1", 75, 100.0) is False
    registry.assert_called_once()
    halt.assert_called_once()
