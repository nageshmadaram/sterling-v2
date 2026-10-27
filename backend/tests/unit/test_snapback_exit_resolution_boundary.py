"""A broker exit remains owned until terminal, valid and durably projected."""
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock
import pytest

from app.services import snapback_live_exits as exits
from app.services import canonical_fill_reconciliation as fills
from app.services.snapback_live_positions import LivePosition


def pos():
    return LivePosition(
        position_id="position", uid="operator", account_id="account",
        signal_id="signal", lane="swing", underlying="NIFTY", side="fade_up",
        option_type="PE", option_exchange="NFO", option_symbol="NIFTY_PE",
        option_qty=75, option_entry_price=100.0,
        option_exit_order_id="EXIT-1", option_exit_requested=75,
        hedge_symbol="NIFTY_FUT", hedge_exchange="NFO", hedge_side="SELL",
        hedge_qty=25, hedge_entry_price=20000.0,
        hedge_exit_order_id="HEDGE-EXIT-1", hedge_exit_requested=25,
    )


def receipt(qty=75, price=90.0, **over):
    return NS(
        filled_quantity=qty, average_price=price,
        status="COMPLETE", terminal=True, settled=True,
        reconciliation_required=False, projection_version=3,
        intent_key="journal-1", exchange_ts_ms=1234,
        **over,
    )


def manager(response, current):
    def mutate(_id, callback):
        callback(current)
        return current
    service = exits.SnapbackExitManager(
        positions=NS(mutate=Mock(side_effect=mutate)),
        resolver=AsyncMock(return_value=response),
    )
    service._book_exit = Mock(return_value=True)
    return service


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_qty,price", [
    (-1, 90), (76, 90), (1.5, 90), (True, 90),
    (None, 90), (75, 0), (75, float("nan")), (75, float("inf")),
])
async def test_invalid_exit_fill_cannot_clear_own_order_id(monkeypatch, bad_qty, price):
    p = pos()
    manager_obj = manager(receipt(qty=bad_qty, price=price), p)
    halt = Mock()
    monkeypatch.setattr(exits, "engage_kill_switch", halt)
    response = await manager_obj._resolve_exit(NS(), p, leg="option", order_id="EXIT-1")
    assert response == "unresolved"
    assert p.option_exit_order_id == "EXIT-1"
    assert p.option_exit_qty == 0
    manager_obj._book_exit.assert_not_called()
    halt.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", [
    {"terminal": False, "status": "OPEN"},
    {"terminal": False, "status": "COMPLETE"},
    {"terminal": True, "status": "OPEN"},
    {"terminal": True, "status": "COMPLETE", "settled": False},
    {"terminal": True, "status": "COMPLETE", "reconciliation_required": True},
])
async def test_nonterminal_or_conflicted_exit_never_creates_replacement(monkeypatch, changed):
    p = pos()
    observed = receipt()
    observed.__dict__.update(changed)
    obj = manager(observed, p)
    ack = Mock()
    monkeypatch.setattr(fills, "mark_projected", ack)
    outcome = await obj._resolve_exit(NS(), p, leg="option", order_id="EXIT-1")
    assert outcome == "unresolved"
    assert p.option_exit_order_id == "EXIT-1"
    assert p.option_exit_qty == 0
    ack.assert_not_called()


@pytest.mark.asyncio
async def test_missing_projection_version_holds_exit_order_identity(monkeypatch):
    p = pos()
    observed = receipt()
    observed.projection_version = 0
    obj = manager(observed, p)
    ack = Mock()
    monkeypatch.setattr(fills, "mark_projected", ack)
    assert await obj._resolve_exit(NS(), p, leg="option", order_id="EXIT-1") == "projection_pending"
    assert p.option_exit_order_id == "EXIT-1"
    assert p.option_exit_qty == 0
    ack.assert_not_called()


@pytest.mark.asyncio
async def test_terminal_hedge_cancellation_with_no_fill_does_not_need_projection(monkeypatch):
    p = pos()
    observed = receipt(qty=0, price=0)
    observed.status = "CANCELLED"
    observed.projection_version = 0
    obj = manager(observed, p)
    ack = Mock()
    monkeypatch.setattr(fills, "mark_projected", ack)
    assert await obj._resolve_exit(NS(), p, leg="hedge", order_id="HEDGE-EXIT-1") == "unfilled"
    assert p.hedge_exit_order_id == ""
    assert p.hedge_exit_qty == 0
    ack.assert_not_called()


@pytest.mark.asyncio
async def test_terminal_complete_with_zero_fill_is_not_an_empty_safe_order(monkeypatch):
    p = pos()
    observed = receipt(qty=0, price=0)
    obj = manager(observed, p)
    assert await obj._resolve_exit(NS(), p, leg="hedge", order_id="HEDGE-EXIT-1") == "unresolved"
    assert p.hedge_exit_order_id == "HEDGE-EXIT-1"


@pytest.mark.asyncio
async def test_known_unknown_exit_id_is_stored_for_reconciliation_without_second_sell(monkeypatch):
    p = pos()
    p.option_exit_order_id = ""
    p.option_exit_requested = 0
    store = NS(mutate=Mock(side_effect=lambda _, fn: (fn(p), p)[1]))
    canonical = NS(submit_order=AsyncMock(return_value=NS(
        success=False, status="UNKNOWN", order_id="BROKER-EXIT-123", error="timeout",
    )))
    obj = exits.SnapbackExitManager(positions=store, canonical=canonical)
    monkeypatch.setattr(obj, "_ensure_ledger_inventory", AsyncMock())
    order_id = await obj._submit_exit(NS(), p, leg="option", quantity=75)
    assert order_id == "BROKER-EXIT-123"
    assert p.option_exit_order_id == "BROKER-EXIT-123"
    assert p.option_exit_requested == 75
    canonical.submit_order.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("result", [
    NS(success=False, status="UNKNOWN", order_id="", error="timeout"),
    NS(success=False, status="SUBMITTING", order_id="", error="other worker"),
    NS(success=True, status="FILLED", order_id="", error="lost order id"),
    NS(success=False, status="COMPLETE", order_id="", error="contradictory"),
    NS(success=False, status="REJECTED", order_id="", error="unverified broker refusal"),
    NS(success=False, status="CANCELLED", order_id="", error="unknown exchange identity"),
])
async def test_unknown_exit_submission_without_id_is_not_treated_as_refusal(monkeypatch, result):
    p = pos()
    store = NS(mutate=Mock())
    canonical = NS(submit_order=AsyncMock(return_value=result))
    obj = exits.SnapbackExitManager(positions=store, canonical=canonical)
    monkeypatch.setattr(obj, "_ensure_ledger_inventory", AsyncMock())
    outcome = await obj._submit_exit(NS(), p, leg="option", quantity=75)
    assert outcome is None
    store.mutate.assert_not_called()
    canonical.submit_order.assert_awaited_once()


@pytest.mark.asyncio
async def test_exit_canonical_submission_exception_is_unknown_not_safe_no_send(monkeypatch):
    p = pos()
    canonical = NS(submit_order=AsyncMock(side_effect=TimeoutError("transport read timed out")))
    obj = exits.SnapbackExitManager(positions=NS(mutate=Mock()), canonical=canonical)
    monkeypatch.setattr(obj, "_ensure_ledger_inventory", AsyncMock())
    assert await obj._submit_exit(NS(), p, leg="option", quantity=75) is None


@pytest.mark.asyncio
async def test_verified_exit_refusal_without_broker_id_remains_no_send(monkeypatch):
    p = pos()
    result = NS(success=False, status="HALTED", order_id="", error="simulation safety")
    canonical = NS(submit_order=AsyncMock(return_value=result))
    obj = exits.SnapbackExitManager(positions=NS(mutate=Mock()), canonical=canonical)
    monkeypatch.setattr(obj, "_ensure_ledger_inventory", AsyncMock())
    monkeypatch.setattr(exits, "alert", Mock())
    assert await obj._submit_exit(NS(), p, leg="option", quantity=75) == ""


@pytest.mark.asyncio
async def test_success_true_without_order_id_requires_reconciliation(monkeypatch):
    p = pos()
    result = NS(success=True, status="FILLED", order_id="", filled_quantity=75, error="")
    broker = NS(submit_order=AsyncMock(return_value=result))
    manager_obj = exits.SnapbackExitManager(positions=NS(mutate=Mock()), canonical=broker)
    monkeypatch.setattr(manager_obj, "_ensure_ledger_inventory", AsyncMock())
    assert await manager_obj._submit_exit(NS(), p, leg="option", quantity=75) is None


@pytest.mark.parametrize("disposition", ["older_cumulative", "no_quantity", "", "unknown"])
def test_stale_exit_ledger_disposition_cannot_complete_position(monkeypatch, disposition):
    from app.services.kite_engine import fill_ledger
    p = pos()
    manager_obj = exits.SnapbackExitManager(positions=NS(mutate=Mock()))
    write = Mock(return_value=NS(
        reconciliation_required=False, reason=disposition,
    ))
    monkeypatch.setattr(fill_ledger, "record", write)
    halt = Mock()
    monkeypatch.setattr(exits, "engage_kill_switch", halt)
    assert not manager_obj._book_exit(
        p, leg="option", order_id="EXIT-1", filled=75,
        average=90.0, exchange_ts_ms=1234)
    write.assert_called_once()
    assert p.option_exit_qty == 0
    halt.assert_called_once()


@pytest.mark.parametrize("disposition", ["accepted", "duplicate"])
def test_only_trusted_exit_cumulative_disposition_can_be_booked(monkeypatch, disposition):
    from app.services.kite_engine import fill_ledger
    p = pos()
    manager_obj = exits.SnapbackExitManager(positions=NS(mutate=Mock()))
    monkeypatch.setattr(fill_ledger, "record", Mock(return_value=NS(
        reconciliation_required=False, reason=disposition,
    )))
    monkeypatch.setattr(exits, "engage_kill_switch", Mock())
    assert manager_obj._book_exit(
        p, leg="option", order_id="EXIT-1", filled=75,
        average=90.0, exchange_ts_ms=1234)


@pytest.mark.asyncio
async def test_unknown_exit_send_after_stop_release_does_not_restore_competing_stop(monkeypatch):
    p = pos()
    p.option_exit_order_id = ""
    p.option_exit_requested = 0
    protection = NS(ensure=AsyncMock())
    positions = NS(
        get=Mock(return_value=p),
        mutate=Mock(),
    )
    obj = exits.SnapbackExitManager(positions=positions, protection=protection)
    monkeypatch.setattr(obj, "_broker_holds", AsyncMock(return_value=True))
    released = AsyncMock(return_value="released")
    monkeypatch.setattr(obj, "_release_stop", released)
    send = AsyncMock(return_value=None)
    monkeypatch.setattr(obj, "_submit_exit", send)
    hold = Mock()
    monkeypatch.setattr(obj, "_flag_reconciling", hold)

    result = await obj._close_leg(NS(), p, leg="option")

    assert result == "submit_unknown"
    released.assert_awaited_once()
    send.assert_awaited_once()
    hold.assert_called_once()
    protection.ensure.assert_not_awaited()
    positions.mutate.assert_not_called()


def test_reconciliation_halt_runs_even_if_position_store_is_out_of_space(monkeypatch):
    p = pos()
    store = NS(mutate=Mock(side_effect=OSError("disk full")))
    obj = exits.SnapbackExitManager(positions=store)
    kill = Mock()
    alert = Mock()
    monkeypatch.setattr(exits, "engage_kill_switch", kill)
    monkeypatch.setattr(exits, "alert", alert)
    obj._flag_reconciling(p, "exit_submission_identity_unknown")
    store.mutate.assert_called_once()
    kill.assert_called_once()
    alert.assert_called_once()


@pytest.mark.asyncio
async def test_unknown_exit_submission_stays_on_hold_when_position_read_fails(monkeypatch):
    p = pos()
    p.option_exit_order_id = ""
    p.option_exit_requested = 0
    store = NS(get=Mock(side_effect=OSError("disk full")), mutate=Mock())
    obj = exits.SnapbackExitManager(positions=store)
    monkeypatch.setattr(obj, "_broker_holds", AsyncMock(return_value=True))
    monkeypatch.setattr(obj, "_release_stop", AsyncMock(return_value="released"))
    monkeypatch.setattr(obj, "_submit_exit", AsyncMock(return_value=None))
    hold = Mock()
    monkeypatch.setattr(obj, "_flag_reconciling", hold)
    protection = NS(ensure=AsyncMock())
    obj._protection = protection
    assert await obj._close_leg(NS(), p, leg="option") == "submit_unknown"
    hold.assert_called_once_with(p, "option_exit_submission_identity_unknown")
    protection.ensure.assert_not_awaited()


@pytest.mark.asyncio
async def test_unverified_futures_hedge_side_never_creates_buy_or_sell_exit(monkeypatch):
    p = pos()
    p.hedge_side = "UNKNOWN"
    canonical = NS(submit_order=AsyncMock())
    obj = exits.SnapbackExitManager(positions=NS(mutate=Mock()), canonical=canonical)
    halt = Mock()
    monkeypatch.setattr(exits, "engage_kill_switch", halt)
    result = await obj._submit_exit(NS(), p, leg="hedge", quantity=25)
    assert result is None
    canonical.submit_order.assert_not_awaited()
    halt.assert_called_once()


@pytest.mark.asyncio
async def test_real_broker_account_switch_cannot_authorize_exit(monkeypatch):
    p = pos()
    canonical = NS(submit_order=AsyncMock())
    obj = exits.SnapbackExitManager(positions=NS(mutate=Mock()), canonical=canonical)
    halt = Mock()
    monkeypatch.setattr(exits, "engage_kill_switch", halt)
    client = NS(_is_paper=False, _account_id="OTHER_ACCOUNT")
    result = await obj._submit_exit(client, p, leg="option", quantity=75)
    assert result is None
    canonical.submit_order.assert_not_awaited()
    halt.assert_called_once()


@pytest.mark.asyncio
async def test_zero_holdings_shortfall_never_arms_naked_option_stop(monkeypatch):
    p = pos()
    ensure = AsyncMock()
    obj = exits.SnapbackExitManager(
        positions=NS(mutate=Mock()),
        protection=NS(ensure=ensure),
    )
    broker_net = AsyncMock(return_value={})
    monkeypatch.setattr(exits, "_broker_net", broker_net)
    monkeypatch.setattr(obj, "_held_beyond_siblings", Mock(return_value=0))
    monkeypatch.setattr(obj, "_note_shortfall", Mock(return_value=False))
    outcome = await obj._option_shortfall_step(NS(), p, remaining=75)
    assert outcome == ("option_missing_unconfirmed", 0)
    ensure.assert_not_awaited()


def test_invalid_hedge_direction_never_books_signed_ledger(monkeypatch):
    from app.services.kite_engine import fill_ledger
    p = pos()
    p.hedge_side = "INVALID"
    obj = exits.SnapbackExitManager(positions=NS(mutate=Mock()))
    ledger_record = Mock()
    halt = Mock()
    monkeypatch.setattr(fill_ledger, "record", ledger_record)
    monkeypatch.setattr(exits, "engage_kill_switch", halt)
    assert obj._book_exit(
        p, leg="hedge", order_id="HEDGE-ORDER-1", filled=25,
        average=20000.0) is False
    ledger_record.assert_not_called()
    halt.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["disk_full", "vanished", "owner_changed", "other_working_exit"])
async def test_broker_ack_without_durable_owner_bound_order_id_halts_without_retry(
        monkeypatch, failure):
    p = pos()
    p.option_exit_order_id = ""
    p.option_exit_requested = 0
    ledger = NS(submit_order=AsyncMock(return_value=NS(
        status="UNKNOWN", success=False, order_id="BROKER-EXIT-987",
        error="ACK response lost",
    )))
    def mutate(_id, callback):
        if failure == "disk_full":
            raise OSError("disk full")
        if failure == "vanished":
            return None
        if failure == "other_working_exit":
            p.option_exit_order_id = "DIFFERENT-WORKING-BROKER-ORDER"
        if failure == "owner_changed":
            p.uid = "other-tenant"
        callback(p)
        return p
    positions = NS(mutate=Mock(side_effect=mutate))
    obj = exits.SnapbackExitManager(positions=positions, canonical=ledger)
    monkeypatch.setattr(obj, "_ensure_ledger_inventory", AsyncMock())
    halt = Mock()
    monkeypatch.setattr(exits, "engage_kill_switch", halt)
    outcome = await obj._submit_exit(NS(), p, leg="option", quantity=75)
    assert outcome is None
    ledger.submit_order.assert_awaited_once()
    positions.mutate.assert_called_once()
    halt.assert_called_once()
    if failure == "other_working_exit":
        assert p.option_exit_order_id == "DIFFERENT-WORKING-BROKER-ORDER"
    else:
        assert p.option_exit_order_id == ""


@pytest.mark.asyncio
async def test_idempotent_repeated_broker_ack_keeps_original_order_owner(monkeypatch):
    p = pos()
    p.option_exit_order_id = "BROKER-EXIT-987"
    p.option_exit_requested = 75
    ledger = NS(submit_order=AsyncMock(return_value=NS(
        status="ACKNOWLEDGED", success=True, order_id="BROKER-EXIT-987",
        error="",
    )))
    def mutate(_id, callback):
        callback(p)
        return p
    obj = exits.SnapbackExitManager(
        positions=NS(mutate=Mock(side_effect=mutate)), canonical=ledger)
    monkeypatch.setattr(obj, "_ensure_ledger_inventory", AsyncMock())
    halt = Mock()
    monkeypatch.setattr(exits, "engage_kill_switch", halt)
    assert await obj._submit_exit(NS(), p, leg="option", quantity=75) == "BROKER-EXIT-987"
    assert p.option_exit_order_id == "BROKER-EXIT-987"
    halt.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value,quantity", [
    ("option_qty", 75.5, 75),
    ("option_exit_qty", True, 75),
    ("option_exit_qty", 50, 75),
    ("option_exit_requested", 1.5, 75),
    ("option_exit_requested", 0, 75),
    ("option_exit_order_id", "", 75),
    ("option_exit_order_id", None, 75),
    ("option_exit_order_id", "EXIT-1", 1.5),
])
async def test_corrupt_exit_state_cannot_submit_another_broker_order(
        monkeypatch, field, value, quantity):
    p = pos()
    setattr(p, field, value)
    canonical = NS(submit_order=AsyncMock())
    obj = exits.SnapbackExitManager(
        positions=NS(mutate=Mock()), canonical=canonical)
    halt = Mock()
    monkeypatch.setattr(exits, "engage_kill_switch", halt)
    result = await obj._submit_exit(NS(), p, leg="option", quantity=quantity)
    assert result is None
    canonical.submit_order.assert_not_awaited()
    halt.assert_called_once()


@pytest.mark.asyncio
async def test_terminal_partial_exit_retires_both_ack_fields_for_safe_remainder(monkeypatch):
    p = pos()
    observed = receipt(qty=25, price=90.0)
    obj = manager(observed, p)
    protection = NS(ensure=AsyncMock())
    obj._protection = protection
    monkeypatch.setattr(fills, "mark_projected", Mock(return_value=True))
    assert await obj._resolve_exit(NS(), p, leg="option", order_id="EXIT-1") == "partial"
    assert p.option_exit_qty == 25
    assert p.option_exit_order_id == ""
    assert p.option_exit_requested == 0
    protection.ensure.assert_awaited_once()


@pytest.mark.asyncio
async def test_exit_ack_retirement_refuses_changed_order_and_halts(monkeypatch):
    p = pos()
    observed = receipt(qty=25, price=90.0)
    obj = manager(observed, p)
    monkeypatch.setattr(fills, "mark_projected", Mock(return_value=True))
    halt = Mock()
    monkeypatch.setattr(exits, "engage_kill_switch", halt)
    # Another worker changed this slot between the fill projection and
    # retirement. Never clear that worker's order or retry a replacement.
    original_mutate = obj.store.mutate.side_effect
    seen = {"n": 0}

    def competing_mutation(position_id, callback):
        seen["n"] += 1
        if seen["n"] == 2:
            p.option_exit_order_id = "OTHER-WORKING-ORDER"
        return original_mutate(position_id, callback)

    obj.store.mutate.side_effect = competing_mutation
    assert await obj._resolve_exit(NS(), p, leg="option", order_id="EXIT-1") == "unresolved"
    assert p.option_exit_order_id == "OTHER-WORKING-ORDER"
    assert p.option_exit_requested == 75
    halt.assert_called_once()


@pytest.mark.parametrize("filled,average", [
    (None, 90), ("", 90), ("1.5", 90), (1.5, 90),
    (True, 90), (-1, 90), ("NaN", 90), ("Infinity", 90),
    (25, 0), (25, ""), (25, -1), (25, "NaN"),
])
def test_broker_stop_fill_never_rounds_or_invents_quantity(filled, average):
    with pytest.raises(ValueError):
        exits.SnapbackExitManager._filled({
            "filled_quantity": filled, "average_price": average})


def test_broker_stop_fill_accepts_exact_integral_quantities():
    assert exits.SnapbackExitManager._filled({
        "filled_quantity": "25", "average_price": "90.50"}) == (25, 90.5)
    assert exits.SnapbackExitManager._filled({
        "filled_quantity": 0, "average_price": None}) == (0, 0.0)


@pytest.mark.asyncio
@pytest.mark.parametrize("filled", [None, "", "1.5", 0])
async def test_complete_broker_stop_without_valid_fill_cannot_close_position(
        monkeypatch, filled):
    p = pos()
    obj = exits.SnapbackExitManager(positions=NS(mutate=Mock()))
    stop_order = {
        "status": "COMPLETE", "order_id": "STOP-CHILD-1",
        "quantity": 75, "filled_quantity": filled, "average_price": 90.0,
    }
    monkeypatch.setattr(obj, "_stop_child_orders",
                        AsyncMock(return_value=[stop_order]))
    monkeypatch.setattr(exits, "_broker_net", AsyncMock(return_value=None))
    booking = Mock()
    monkeypatch.setattr(obj, "_book_stop_fills", booking)
    halt = Mock()
    monkeypatch.setattr(exits, "engage_kill_switch", halt)
    client = NS(get_orders=AsyncMock(return_value=[stop_order]))
    assert await obj._settle_broker_stop(client, p) == "stop_fill_unverified"
    booking.assert_not_called()
    halt.assert_called_once()


@pytest.mark.parametrize("order_id,requested", [
    ("MARKET-EXIT-IN-FLIGHT", 75), ("", 75), ("ORPHAN-ID", 0),
])
def test_stop_fill_cannot_erase_unresolved_canonical_exit(
        monkeypatch, order_id, requested):
    p = pos()
    p.option_exit_order_id = order_id
    p.option_exit_requested = requested
    obj = exits.SnapbackExitManager(positions=NS(mutate=Mock()))
    hold = Mock()
    monkeypatch.setattr(obj, "_flag_reconciling", hold)
    booking = Mock()
    monkeypatch.setattr(obj, "_book_exit", booking)
    with pytest.raises(RuntimeError, match="unresolved canonical exit"):
        obj._book_stop_fills(p, [])
    booking.assert_not_called()
    obj.store.mutate.assert_not_called()
    hold.assert_called_once()
    assert p.option_exit_order_id == order_id
    assert p.option_exit_requested == requested


def test_stop_fill_rejects_concurrent_exit_inside_store_transaction(monkeypatch):
    p = pos()
    p.option_exit_order_id = ""
    p.option_exit_requested = 0
    def mutate(_position_id, callback):
        p.option_exit_order_id = "CONCURRENT-EXIT"
        p.option_exit_requested = 75
        callback(p)
        return p
    obj = exits.SnapbackExitManager(positions=NS(mutate=Mock(side_effect=mutate)))
    hold = Mock()
    monkeypatch.setattr(obj, "_flag_reconciling", hold)
    with pytest.raises(ValueError, match="canonical exit started"):
        obj._book_stop_fills(p, [])
    assert p.option_exit_order_id == "CONCURRENT-EXIT"
    assert p.option_exit_requested == 75
    hold.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [
    ("option_exit_requested", 0),
    ("option_exit_order_id", ""),
    ("option_exit_requested", -1),
    ("option_exit_requested", 1.5),
    ("option_exit_order_id", " "),
    ("option_exit_qty", 0.5),
    ("option_qty", 75.5),
])
async def test_exit_registry_anomaly_halts_before_releasing_broker_stop(
        monkeypatch, field, value):
    p = pos()
    setattr(p, field, value)
    manager_obj = exits.SnapbackExitManager(positions=NS(mutate=Mock()))
    hold = Mock()
    release = AsyncMock()
    check_held = AsyncMock()
    submit = AsyncMock()
    monkeypatch.setattr(manager_obj, "_flag_reconciling", hold)
    monkeypatch.setattr(manager_obj, "_release_stop", release)
    monkeypatch.setattr(manager_obj, "_broker_holds", check_held)
    monkeypatch.setattr(manager_obj, "_submit_exit", submit)

    result = await manager_obj._close_leg(NS(), p, leg="option")

    assert result == "exit_state_unverified"
    hold.assert_called_once_with(p, "exit_registry_state_unverified")
    release.assert_not_awaited()
    check_held.assert_not_awaited()
    submit.assert_not_awaited()


@pytest.mark.asyncio
async def test_flat_position_with_still_working_exit_halts_before_more_broker_actions(
        monkeypatch):
    p = pos()
    p.option_exit_qty = p.option_qty
    obj = exits.SnapbackExitManager(positions=NS(mutate=Mock()))
    hold = Mock()
    release = AsyncMock()
    monkeypatch.setattr(obj, "_flag_reconciling", hold)
    monkeypatch.setattr(obj, "_release_stop", release)
    assert await obj._close_leg(NS(), p, leg="option") == "exit_state_unverified"
    hold.assert_called_once_with(p, "exit_order_still_working_after_flat")
    release.assert_not_awaited()


@pytest.mark.parametrize("filled_qty,already_exited", [
    (76, 0),
    (60, 25),
    (1, 75),
])
def test_stop_oversell_never_silently_clips_real_broker_fills(
        monkeypatch, filled_qty, already_exited):
    p = pos()
    p.option_exit_order_id = ""
    p.option_exit_requested = 0
    p.option_exit_qty = already_exited
    p.option_exit_booked_ids = []
    p.gtt_id = 99871
    store = NS(mutate=Mock())
    obj = exits.SnapbackExitManager(positions=store)
    hold = Mock()
    ledger = Mock(return_value=True)
    monkeypatch.setattr(obj, "_flag_reconciling", hold)
    monkeypatch.setattr(obj, "_book_exit", ledger)

    with pytest.raises(RuntimeError, match="short exposure reconciliation"):
        obj._book_stop_fills(p, [("STOP-SELL-1", filled_qty, 95.0)])

    hold.assert_called_once_with(p, "broker_stop_exceeds_owned_option_units")
    ledger.assert_not_called()
    store.mutate.assert_not_called()
    assert p.gtt_id == 99871
    assert p.option_exit_qty == already_exited


def test_two_stop_children_cannot_oversell_in_aggregate(monkeypatch):
    p = pos()
    p.option_exit_order_id = ""
    p.option_exit_requested = 0
    p.gtt_id = 99872
    obj = exits.SnapbackExitManager(positions=NS(mutate=Mock()))
    hold = Mock()
    book = Mock(return_value=True)
    monkeypatch.setattr(obj, "_flag_reconciling", hold)
    monkeypatch.setattr(obj, "_book_exit", book)
    with pytest.raises(RuntimeError, match="short exposure reconciliation"):
        obj._book_stop_fills(p, [("STOP-1", 40, 90.0),
                                 ("STOP-2", 40, 91.0)])
    hold.assert_called_once()
    book.assert_not_called()


def test_concurrent_stop_booking_cannot_clip_a_newly_reduced_position(monkeypatch):
    p = pos()
    p.option_exit_order_id = ""
    p.option_exit_requested = 0
    p.gtt_id = 99873

    def mutate(_id, callback):
        # A separate ledger observer has already booked part of the option.
        p.option_exit_qty = 25
        callback(p)
        return p

    obj = exits.SnapbackExitManager(
        positions=NS(mutate=Mock(side_effect=mutate)))
    hold = Mock()
    monkeypatch.setattr(obj, "_flag_reconciling", hold)
    monkeypatch.setattr(obj, "_book_exit", Mock(return_value=True))
    with pytest.raises(ValueError, match="concurrent broker stop overfill"):
        obj._book_stop_fills(p, [("STOP-SELL-2", 75, 90.0)])
    assert p.option_exit_qty == 25
    assert p.option_exit_booked_ids == []
    assert p.gtt_id == 99873
    hold.assert_called_once_with(p, "stop_fill_registry_conflict")


def test_unknown_stop_child_and_working_market_exit_never_book_unpriced(monkeypatch):
    p = pos()
    obj = exits.SnapbackExitManager(positions=NS(mutate=Mock()))
    hold = Mock()
    shortfall = Mock()
    booking = Mock()
    monkeypatch.setattr(obj, "_flag_reconciling", hold)
    monkeypatch.setattr(obj, "_note_shortfall", shortfall)
    monkeypatch.setattr(obj, "_book_unpriced_option_exit", booking)

    assert obj._book_if_proven_gone(p) == "reconciling"
    hold.assert_called_once_with(
        p, "broker_stop_and_canonical_exit_unresolved")
    shortfall.assert_not_called()
    booking.assert_not_called()


def test_unpriced_shortfall_rejects_exit_started_during_transaction(monkeypatch):
    p = pos()
    p.option_exit_order_id = ""
    p.option_exit_requested = 0
    p.gtt_id = 123456
    def mutate(_position_id, callback):
        p.option_exit_order_id = "NEW-BROKER-EXIT"
        p.option_exit_requested = 75
        callback(p)
        return p

    obj = exits.SnapbackExitManager(
        positions=NS(mutate=Mock(side_effect=mutate)))
    halt = Mock()
    monkeypatch.setattr(obj, "_flag_reconciling", halt)

    with pytest.raises(RuntimeError, match="must be reconciled"):
        obj._book_unpriced_option_exit(p, quantity=50, available=25)

    halt.assert_called_once_with(
        p, "unpriced_option_exit_conflicts_with_broker_state")
    assert p.option_exit_qty == 0
    assert p.option_exit_unknown_qty == 0
    assert p.option_exit_order_id == "NEW-BROKER-EXIT"
    assert p.gtt_id == 123456


@pytest.mark.parametrize("quantity,already_exited", [
    (76, 0), (51, 25), (0, 0), (1.5, 0),
])
def test_unpriced_shortfall_does_not_book_invalid_excess_quantity(
        monkeypatch, quantity, already_exited):
    p = pos()
    p.option_exit_order_id = ""
    p.option_exit_requested = 0
    p.option_exit_qty = already_exited
    p.gtt_id = 987654
    def mutate(_id, callback):
        callback(p)
        return p

    obj = exits.SnapbackExitManager(
        positions=NS(mutate=Mock(side_effect=mutate)))
    halt = Mock()
    monkeypatch.setattr(obj, "_flag_reconciling", halt)
    with pytest.raises(RuntimeError, match="must be reconciled"):
        obj._book_unpriced_option_exit(p, quantity=quantity, available=0)
    halt.assert_called_once()
    assert p.option_exit_qty == already_exited
    assert p.option_exit_unknown_qty == 0
    assert p.gtt_id == 987654


def test_valid_unpriced_shortfall_preserves_remaining_known_option(monkeypatch):
    p = pos()
    p.option_exit_order_id = ""
    p.option_exit_requested = 0
    p.option_exit_qty = 25
    p.gtt_id = 987654
    def mutate(_id, callback):
        callback(p)
        return p

    obj = exits.SnapbackExitManager(
        positions=NS(mutate=Mock(side_effect=mutate)))
    monkeypatch.setattr(exits, "alert", Mock())
    halt = Mock()
    monkeypatch.setattr(exits, "engage_kill_switch", halt)
    obj._book_unpriced_option_exit(p, quantity=50, available=0)

    assert p.option_exit_qty == p.option_qty == 75
    assert p.option_exit_unknown_qty == 50
    assert p.gtt_id == 0
    assert "option_exit_price_unknown" in p.flags
    halt.assert_called_once()


@pytest.mark.asyncio
async def test_partially_missing_option_cannot_sell_after_shortfall_booking_race(
        monkeypatch):
    p = pos()
    p.option_exit_order_id = ""
    p.option_exit_requested = 0
    obj = exits.SnapbackExitManager(positions=NS(mutate=Mock()))
    monkeypatch.setattr(exits, "_broker_net", AsyncMock(return_value={}))
    monkeypatch.setattr(obj, "_held_beyond_siblings", Mock(return_value=25))
    monkeypatch.setattr(obj, "_note_shortfall", Mock(return_value=True))
    book = Mock(side_effect=RuntimeError("concurrent exit"))
    monkeypatch.setattr(obj, "_book_unpriced_option_exit", book)

    assert await obj._option_shortfall_step(NS(), p, 75) == ("reconciling", 0)
    book.assert_called_once_with(p, quantity=50, available=25)


@pytest.mark.parametrize("changed", [
    "uid", "account_id", "option_symbol", "option_exchange", "status",
])
def test_unpriced_exit_refuses_changed_persisted_owner_or_closed_state(
        monkeypatch, changed):
    from dataclasses import replace

    origin = pos()
    origin.option_exit_order_id = ""
    origin.option_exit_requested = 0
    current = replace(origin)
    if changed == "uid":
        current.uid = "another-tenant"
    elif changed == "account_id":
        current.account_id = "another-broker-account"
    elif changed == "option_symbol":
        current.option_symbol = "OTHER_OPTION"
    elif changed == "option_exchange":
        current.option_exchange = "BSE"
    elif changed == "status":
        current.status = "CLOSED"

    def mutate(_id, callback):
        callback(current)
        return current

    obj = exits.SnapbackExitManager(
        positions=NS(mutate=Mock(side_effect=mutate)))
    halt = Mock()
    monkeypatch.setattr(obj, "_flag_reconciling", halt)
    with pytest.raises(RuntimeError, match="must be reconciled"):
        obj._book_unpriced_option_exit(origin, quantity=75, available=0)

    halt.assert_called_once()
    assert current.option_exit_qty == 0
    assert current.option_exit_unknown_qty == 0
    assert current.option_symbol == ("OTHER_OPTION" if changed == "option_symbol"
                                     else origin.option_symbol)


@pytest.mark.asyncio
async def test_broker_zero_cannot_clear_triggered_stop_with_unknown_child(monkeypatch):
    p = pos()
    p.gtt_id = 778899
    p.option_exit_order_id = ""
    p.option_exit_requested = 0
    obj = exits.SnapbackExitManager(positions=NS(mutate=Mock()))
    monkeypatch.setattr(obj, "_stop_child_orders",
                        AsyncMock(return_value=None))
    monkeypatch.setattr(exits, "_broker_net", AsyncMock(return_value={}))
    monkeypatch.setattr(obj, "_held_beyond_siblings",
                        Mock(return_value=0))
    book = Mock()
    hold = Mock()
    monkeypatch.setattr(obj, "_book_if_proven_gone", book)
    monkeypatch.setattr(obj, "_flag_reconciling", hold)

    result = await obj._settle_broker_stop(NS(
        get_orders=AsyncMock(return_value=[])), p)

    assert result == "reconciling"
    hold.assert_called_once_with(p, "broker_stop_child_identity_unknown")
    book.assert_not_called()
    assert p.gtt_id == 778899
    assert p.option_exit_unknown_qty == 0


@pytest.mark.asyncio
async def test_duplicate_triggered_child_order_id_is_unverified(monkeypatch):
    from app.services.kite_engine import protective_stop as pstop

    p = pos()
    p.gtt_id = 999888
    obj = exits.SnapbackExitManager(positions=NS(mutate=Mock()))
    monkeypatch.setattr(pstop, "stop_state", AsyncMock(return_value=NS(
        child_order_id="STOP-CHILD-1", verdict="TRIGGERED")))
    monkeypatch.setattr(obj, "_booked_exit_ids", Mock(return_value=set()))
    match = {"order_id": "STOP-CHILD-1", "status": "COMPLETE",
             "filled_quantity": 75, "average_price": 100}
    own = await obj._stop_child_orders(NS(), p, [match, dict(match)])
    assert own is None, "one child ID in two rows is not unique ownership evidence"


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [
    ("uid", "another-user"),
    ("account_id", "another-broker"),
    ("option_symbol", "NIFTY_OTHER_PE"),
    ("option_exit_order_id", "CHANGED-ORDER-ID"),
    ("option_exit_requested", 50),
    ("option_exit_qty", 25),
    ("option_qty", 25),
    ("status", "CLOSED"),
])
async def test_terminal_exit_resolution_halts_if_registry_changed_during_broker_query(
        monkeypatch, field, value):
    from copy import deepcopy

    original = pos()
    current = deepcopy(original)
    setattr(current, field, value)
    snapshot = deepcopy(current)
    obj = manager(receipt(qty=75, price=90), current)
    hold = Mock()
    projected = Mock()
    monkeypatch.setattr(obj, "_flag_reconciling", hold)
    monkeypatch.setattr(fills, "mark_projected", projected)

    result = await obj._resolve_exit(
        NS(), original, leg="option", order_id="EXIT-1")

    assert result == "unresolved"
    hold.assert_called_once_with(original, "terminal_exit_projection_conflict")
    projected.assert_not_called()
    assert current == snapshot, "broker receipt must not mutate a changed row"


@pytest.mark.asyncio
async def test_terminal_exit_vanished_registry_row_requires_reconciliation(monkeypatch):
    original = pos()
    obj = exits.SnapbackExitManager(
        positions=NS(mutate=Mock(return_value=None)),
        resolver=AsyncMock(return_value=receipt(qty=75, price=90)),
    )
    obj._book_exit = Mock(return_value=True)
    hold = Mock()
    projected = Mock()
    monkeypatch.setattr(obj, "_flag_reconciling", hold)
    monkeypatch.setattr(fills, "mark_projected", projected)

    assert await obj._resolve_exit(
        NS(), original, leg="option", order_id="EXIT-1") == "unresolved"
    hold.assert_called_once_with(original, "terminal_exit_projection_conflict")
    projected.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [
    ("option_exit_order_id", "OTHER-EXIT"),
    ("option_exit_order_id", ""),
    ("option_exit_requested", True),
    ("option_exit_requested", 75.5),
    ("option_exit_requested", 0),
    ("option_exit_requested", 76),
    ("option_exit_qty", 25),
    ("option_qty", 75.5),
])
async def test_corrupt_exit_request_cannot_even_query_broker_resolver(
        monkeypatch, field, value):
    p = pos()
    setattr(p, field, value)
    resolver = AsyncMock()
    obj = exits.SnapbackExitManager(
        positions=NS(mutate=Mock()), resolver=resolver)
    hold = Mock()
    ledger = Mock()
    monkeypatch.setattr(obj, "_flag_reconciling", hold)
    monkeypatch.setattr(obj, "_book_exit", ledger)
    assert await obj._resolve_exit(
        NS(), p, leg="option", order_id="EXIT-1") == "unresolved"
    resolver.assert_not_awaited()
    ledger.assert_not_called()
    hold.assert_called_once_with(p, "exit_receipt_order_state_unverified")


@pytest.mark.asyncio
async def test_unknown_exit_leg_does_not_query_broker(monkeypatch):
    p = pos()
    resolver = AsyncMock()
    obj = exits.SnapbackExitManager(positions=NS(mutate=Mock()), resolver=resolver)
    hold = Mock()
    monkeypatch.setattr(obj, "_flag_reconciling", hold)
    assert await obj._resolve_exit(
        NS(), p, leg="other", order_id="EXIT-1") == "unresolved"
    resolver.assert_not_awaited()
    hold.assert_called_once_with(p, "exit_receipt_leg_unknown")
