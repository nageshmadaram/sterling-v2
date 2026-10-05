"""Broker protocol fixtures test safety; these are not market-performance data."""
from unittest.mock import AsyncMock
import pytest

from app.services.kite_engine import execution_lifecycle as life
from app.services.kite_engine import order_journal as journal, positions, monitor, protective_stop


class Client:
    _is_paper = False
    _account_id = "account"


@pytest.fixture
def setup(monkeypatch):
    life._locks.clear()
    place = AsyncMock(return_value=42)
    async def submitted(_client, **kwargs):
        kwargs["before_submit"]()
        return place.return_value
    place.side_effect = submitted
    monkeypatch.setattr(protective_stop, "place_stop", place)
    monkeypatch.setattr(protective_stop, "move_stop", AsyncMock(return_value=True))
    intent = journal.reserve(uid="u", account_id="account", strategy_id="sterling-kite",
        generation_id="v1", signal_id="signal", exchange="NFO", symbol="OPT", side="BUY",
        quantity=100, capital_required=12000, available_capital=100000,
        payload=dict(lot_size=50, entry_premium=120, stop_premium=100, stop_mode="both"))
    assert journal.claim_submission(intent.intent_key)
    order = dict(order_id="O1", tag=intent.tag, tradingsymbol="OPT", exchange="NFO",
                 product="NRML", quantity=100, transaction_type="BUY", status="COMPLETE",
                 filled_quantity=100, average_price=121)
    return intent, order, Client(), place


@pytest.mark.asyncio
async def test_non_supertrend_intent_is_not_projected_into_kite_registry(setup):
    _intent, order, client, place = setup
    foreign = journal.reserve(
        uid="u",
        account_id="account",
        strategy_id="intraday",
        generation_id="v1",
        signal_id="foreign-signal",
        exchange="NFO",
        symbol="OTHEROPT",
        side="BUY",
        quantity=50,
        capital_required=5000,
        available_capital=100000,
        payload={"product": "MIS"},
    )
    assert journal.claim_submission(foreign.intent_key)
    journal.transition(foreign.intent_key, "SUBMITTED", order_id="OTHER-1")

    handled = await life.consume_order(
        client,
        "u",
        {
            **order,
            "order_id": "OTHER-1",
            "tag": foreign.tag,
            "tradingsymbol": "OTHEROPT",
            "product": "MIS",
            "quantity": 50,
            "filled_quantity": 50,
        },
    )

    assert handled is False
    assert positions.get("u", "OTHEROPT") is None
    assert journal.find(
        uid="u", account_id="account", order_id="OTHER-1"
    ).state == "SUBMITTED"
    place.assert_not_awaited()


@pytest.mark.asyncio
async def test_snapback_postback_updates_canonical_journal_without_kite_position(setup):
    _intent, order, client, place = setup
    snap = journal.reserve(
        uid="u",
        account_id="account",
        strategy_id="snapback",
        generation_id="pol-1",
        signal_id="OPP-SNAP",
        exchange="NFO",
        symbol="SNAPOPT",
        side="BUY",
        quantity=75,
        capital_required=5000,
        available_capital=100000,
        payload={"product": "NRML"},
    )
    assert journal.claim_submission(snap.intent_key)
    journal.transition(snap.intent_key, "SUBMITTED", order_id="SNAP-1")

    handled = await life.consume_order(
        client,
        "u",
        {
            **order,
            "order_id": "SNAP-1",
            "tag": snap.tag,
            "tradingsymbol": "SNAPOPT",
            "quantity": 75,
            "filled_quantity": 75,
            "average_price": 110.0,
        },
    )

    assert handled is True
    current = journal.get(snap.intent_key)
    assert current is not None
    assert current.state == "FILLED"
    assert current.filled_quantity == 75
    assert positions.get("u", "SNAPOPT") is None
    place.assert_not_awaited()


@pytest.mark.asyncio
async def test_ack_registers_zero_quantity_and_no_stop(setup):
    intent, _, client, place = setup
    journal.transition(intent.intent_key, "SUBMITTED", order_id="O1")
    p = life.register_pending(intent, "O1")
    assert p.qty == 0 and p.entry_pending and p.entry_requested_qty == 100
    place.assert_not_awaited()


@pytest.mark.asyncio
async def test_unknown_entry_recovers_without_registry_or_resubmission(setup):
    intent, order, client, place = setup
    journal.transition(intent.intent_key, "UNKNOWN")
    client.get_orders = AsyncMock(return_value=[order])
    await life.recover(client, "u")
    p = positions.get("u", "OPT")
    assert p.qty == 100 and p.fill_price == 121 and not p.entry_pending
    assert p.gtt_id == 42 and p.account_id == "account"
    assert place.await_args.kwargs["qty"] == 100
    assert journal.find(uid="u", account_id="account", order_id="O1").state == "FILLED"


@pytest.mark.asyncio
async def test_partial_open_and_cancel_protect_only_confirmed_quantity(setup):
    intent, order, client, place = setup
    await monitor.on_order_update("u", {**order, "status": "OPEN", "filled_quantity": 50}, client=client)
    p = positions.get("u", "OPT")
    assert p.qty == 50 and p.entry_pending
    assert place.await_args.kwargs["qty"] == 50
    await monitor.on_order_update("u", {**order, "status": "CANCELLED", "filled_quantity": 50}, client=client)
    assert p.qty == 50 and not p.entry_pending and p.status == positions.OPEN
    assert journal.find(uid="u", account_id="account", order_id="O1").state == "CANCELLED"


@pytest.mark.asyncio
async def test_duplicate_entry_event_does_not_restore_exited_quantity(setup):
    _, order, client, place = setup
    await life.consume_order(client, "u", order)
    p = positions.get("u", "OPT")
    p.qty = 50  # confirmed partial exit after the entry was already projected
    positions.persist_strict("u")
    await life.consume_order(client, "u", order)
    assert p.qty == 50 and place.await_count == 1


@pytest.mark.asyncio
async def test_committed_fill_recovers_after_projection_crash(setup, monkeypatch):
    intent, order, client, place = setup
    original = life.register_pending
    def crash(*args):
        raise RuntimeError("simulated process failure before registry projection")
    monkeypatch.setattr(life, "register_pending", crash)
    with pytest.raises(RuntimeError):
        await life.consume_order(client, "u", order)
    assert journal.pending_projection("u", "account")
    monkeypatch.setattr(life, "register_pending", original)
    client.get_orders = AsyncMock(return_value=[order])
    await life.recover(client, "u")
    assert positions.get("u", "OPT").qty == 100
    assert not journal.pending_projection("u", "account")


@pytest.mark.asyncio
async def test_unknown_gtt_outcome_is_not_retried(setup):
    _, order, client, place = setup
    place.return_value = None
    await life.consume_order(client, "u", order)
    await life.consume_order(client, "u", order)
    assert positions.get("u", "OPT").protection_pending
    assert place.await_count == 1
    p = positions.get("u", "OPT")
    client.cancel_gtt = AsyncMock()
    client.place_order_option = AsyncMock()
    assert not await monitor._exit_position(client, "u", p, 99, reason="manual")
    client.place_order_option.assert_not_awaited()


@pytest.mark.asyncio
async def test_recover_restores_tick_subscription_for_existing_protected_position(
    setup, monkeypatch
):
    _, order, client, place = setup
    await life.consume_order(client, "u", order)
    p = positions.get("u", "OPT")
    assert p is not None and p.gtt_id == 42 and p.token == 0

    p.token = 987
    positions.persist_strict("u")

    from app.services.exchanges.kite import ticker_manager
    subscribe = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(ticker_manager, "subscribe", subscribe)

    client.get_orders = AsyncMock(return_value=[])
    await life.recover(client, "u")

    subscribe.assert_awaited_once()
    assert subscribe.await_args.kwargs["owner"] == ticker_manager.protection_owner("OPT")
    assert subscribe.await_args.args[1] == [987]
    assert place.await_count == 1


@pytest.mark.asyncio
async def test_active_gtt_resync_keeps_pending_fence_until_modify_confirms(setup, monkeypatch):
    _, order, client, _place = setup
    await life.consume_order(client, "u", order)
    p = positions.get("u", "OPT")
    assert p is not None
    p.gtt_id = 42
    p.protection_pending = True
    positions.persist_strict("u")

    monkeypatch.setattr(
        protective_stop,
        "stop_status",
        AsyncMock(return_value=protective_stop.STOP_ACTIVE),
    )

    async def crash_before_confirm(_client, held):
        assert held.protection_pending is True
        raise RuntimeError("simulated crash before GTT modify confirmation")

    monkeypatch.setattr(life, "_place_protection", crash_before_confirm)

    with pytest.raises(RuntimeError, match="before GTT modify confirmation"):
        await life._reconcile_pending_protection(client, p)

    current = positions.get("u", "OPT")
    assert current is not None
    assert current.gtt_id == 42
    assert current.protection_pending is True


@pytest.mark.asyncio
async def test_unknown_gtt_ack_never_adopts_field_identical_unowned_trigger(setup, monkeypatch):
    _, order, client, place = setup
    place.return_value = None

    await life.consume_order(client, "u", order)
    p = positions.get("u", "OPT")
    assert p is not None and p.protection_pending and p.gtt_id == 0

    client.get_positions_raw = AsyncMock(return_value={
        "net": [{
            "tradingsymbol": "OPT",
            "exchange": "NFO",
            "product": "NRML",
            "quantity": 100,
        }],
    })
    client.get_gtts = AsyncMock(return_value=[{
        "id": 77,
        "status": "active",
        "condition": {
            "tradingsymbol": "OPT",
            "exchange": "NFO",
            "trigger_values": [100.0],
        },
        "orders": [{
            "tradingsymbol": "OPT",
            "exchange": "NFO",
            "transaction_type": "SELL",
            "quantity": 100,
            "order_type": "MARKET",
            "product": "NRML",
        }],
    }])
    halts = []
    from app.services import live_safety
    monkeypatch.setattr(
        live_safety,
        "set_kill_switch",
        lambda enabled, reason="": halts.append((enabled, reason)),
    )

    await life.recover(client, "u")

    current = positions.get("u", "OPT")
    assert current is not None
    assert current.gtt_id == 0
    assert current.protection_pending is True
    assert place.await_count == 1
    assert halts and halts[-1][0] is True
    assert "ownership is unknown" in halts[-1][1]


@pytest.mark.asyncio
async def test_unknown_gtt_ack_does_not_retry_when_broker_has_no_exact_match(setup):
    _, order, client, place = setup
    place.return_value = None

    await life.consume_order(client, "u", order)
    client.get_positions_raw = AsyncMock(return_value={
        "net": [{
            "tradingsymbol": "OPT",
            "exchange": "NFO",
            "product": "NRML",
            "quantity": 100,
        }],
    })
    client.get_gtts = AsyncMock(return_value=[])

    await life.recover(client, "u")

    current = positions.get("u", "OPT")
    assert current is not None
    assert current.gtt_id == 0
    assert current.protection_pending is True
    assert place.await_count == 1


@pytest.mark.asyncio
async def test_wrong_venue_never_projects_or_arms(setup):
    intent, order, client, place = setup
    await life.consume_order(client, "u", {**order, "exchange": "BFO"})
    assert positions.get("u", "OPT") is None
    assert journal.unresolved("u")
    place.assert_not_awaited()
