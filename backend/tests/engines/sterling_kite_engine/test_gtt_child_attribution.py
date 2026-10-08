"""A GTT that fires at Zerodha reaches us as a fill for a symbol we hold and
nothing else — no exit order id of ours, no tag of ours, because Zerodha placed
the child order. Left unattributed the registry keeps the position OPEN and the
tick monitor later market-sells something we no longer own.

Attribution is only ever allowed to CLOSE a row, and only on the broker's own
state. Broker protocol fixtures test safety; these are not market-performance data.
"""
from unittest.mock import AsyncMock
import pytest

from app.services.kite_engine.canonical_entry import STRATEGY_ID as KITE_STRATEGY_ID

from app.services.kite_engine import (execution_lifecycle as life, fill_ledger as ledger,
                                      monitor, order_journal as journal, positions,
                                      protective_stop as pstop, state)


class Client:
    _is_paper = False
    _account_id = "account"


@pytest.fixture
def held(monkeypatch):
    """100 qty of OPT, live, guarded by GTT #42."""
    life._locks.clear()
    monkeypatch.setattr(pstop, "place_stop", AsyncMock(return_value=42))
    monkeypatch.setattr(pstop, "move_stop", AsyncMock(return_value=True))
    intent = journal.reserve(uid="u", account_id="account", strategy_id="sterling-kite",
        generation_id="v1", signal_id="s", exchange="NFO", symbol="OPT", side="BUY",
        quantity=100, payload=dict(lot_size=50, entry_premium=120, stop_premium=100,
                                   stop_mode="both"))
    assert journal.claim_submission(intent.intent_key)
    client = Client()
    return intent, client


async def enter(client, intent):
    await life.consume_order(client, "u", dict(
        order_id="O1", tag=intent.tag, tradingsymbol="OPT", exchange="NFO",
        product="NRML", quantity=100, transaction_type="BUY", status="COMPLETE",
        filled_quantity=100, average_price=121))
    assert positions.get("u", "OPT").gtt_id == 42


def net(quantity):
    return {"net": [{"tradingsymbol": "OPT", "exchange": "NFO", "product": "NRML",
                     "quantity": quantity}]}


@pytest.mark.asyncio
async def test_canonical_mis_entry_fill_reconciles_in_intent_product(monkeypatch):
    """Intraday uses canonical execution too, but its broker bucket is MIS."""
    life._locks.clear()
    positions.reset("mis-u")
    monkeypatch.setattr(pstop, "place_stop", AsyncMock(return_value=77))
    monkeypatch.setattr(pstop, "move_stop", AsyncMock(return_value=True))

    intent = journal.reserve(
        uid="mis-u",
        account_id="account",
        strategy_id=KITE_STRATEGY_ID,
        generation_id="v1",
        signal_id="mis-signal",
        exchange="NFO",
        symbol="MISOPT",
        side="BUY",
        quantity=75,
        payload={
            "product": "MIS",
            "lot_size": 25,
            "entry_premium": 120,
            "stop_premium": 100,
            "stop_mode": "both",
        },
    )
    assert journal.claim_submission(intent.intent_key)

    handled = await life.consume_order(
        Client(),
        "mis-u",
        {
            "order_id": "MIS-O1",
            "tag": intent.tag,
            "tradingsymbol": "MISOPT",
            "exchange": "NFO",
            "product": "MIS",
            "quantity": 75,
            "transaction_type": "BUY",
            "status": "COMPLETE",
            "filled_quantity": 75,
            "average_price": 121,
        },
    )

    held = positions.get("mis-u", "MISOPT")
    assert handled is True
    assert held is not None
    assert held.status == positions.OPEN
    assert held.product == "MIS"
    assert held.qty == 75
    assert held.gtt_id == 77


async def trigger_fill(client, *, filled=100, price=101.0, **overrides):
    """The child order Zerodha placed: our symbol, an order id we have never seen."""
    order = dict(
        tradingsymbol="OPT", exchange="NFO", product="NRML", quantity=100,
        order_id="GTTCHILD9", transaction_type="SELL", status="COMPLETE",
        filled_quantity=filled, average_price=price,
    )
    order.update(overrides)
    await monitor.on_order_update("u", order, client=client)


@pytest.mark.asyncio
async def test_a_fired_trigger_that_left_us_flat_is_attributed(held, monkeypatch):
    intent, client = held
    await enter(client, intent)
    monkeypatch.setattr(pstop, "stop_status", AsyncMock(return_value=pstop.STOP_TRIGGERED))
    monkeypatch.setattr(pstop, "cancel_stop_result", AsyncMock(return_value=pstop.GONE))
    client.get_positions_raw = AsyncMock(return_value=net(0))
    await trigger_fill(client)
    p = positions.get("u", "OPT")
    assert p.status == positions.CLOSED and not p.pnl_reconciliation_required
    holding = ledger.inventory("account", "u", "OPT")
    assert holding.net_quantity == 0 and holding.realized_pnl == pytest.approx(-2000.0)
    assert [r["source"] for r in ledger.increments("u")] == ["entry", "gtt"]
    assert state.daily_realized_pnl("u") == pytest.approx(-2000.0)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "override",
    [
        {"exchange": "BFO"},
        {"exchange": None},
        {"product": "MIS"},
        {"product": None},
        {"quantity": 50},
    ],
)
async def test_trigger_child_requires_exact_exchange_product_and_order_quantity(
    held, monkeypatch, override
):
    intent, client = held
    await enter(client, intent)
    probe = AsyncMock(return_value=pstop.STOP_TRIGGERED)
    monkeypatch.setattr(pstop, "stop_status", probe)
    client.get_positions_raw = AsyncMock(return_value=net(0))

    await trigger_fill(client, **override)

    p = positions.get("u", "OPT")
    assert p.status != positions.CLOSED
    assert p.qty == 100
    assert p.pnl_reconciliation_required
    probe.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_still_resting_trigger_does_not_own_the_fill(held, monkeypatch):
    """Our stop is still on the book, so whatever sold, it was not our trigger."""
    intent, client = held
    await enter(client, intent)
    monkeypatch.setattr(pstop, "stop_status", AsyncMock(return_value=pstop.STOP_ACTIVE))
    client.get_positions_raw = AsyncMock(return_value=net(0))
    await trigger_fill(client)
    p = positions.get("u", "OPT")
    assert p.status != positions.CLOSED and p.qty == 100
    assert p.pnl_reconciliation_required
    assert ledger.inventory("account", "u", "OPT").net_quantity == 100


@pytest.mark.asyncio
async def test_an_unanswered_holdings_read_is_not_read_as_flat(held, monkeypatch):
    """None and 0 are different claims. A failed read must not close a position."""
    intent, client = held
    await enter(client, intent)
    monkeypatch.setattr(pstop, "stop_status", AsyncMock(return_value=pstop.STOP_TRIGGERED))
    client.get_positions_raw = AsyncMock(side_effect=RuntimeError("read failed"))
    await trigger_fill(client)
    assert positions.get("u", "OPT").status != positions.CLOSED
    assert positions.get("u", "OPT").pnl_reconciliation_required


@pytest.mark.asyncio
async def test_a_holding_that_did_not_shrink_by_this_fill_is_not_attributed(held, monkeypatch):
    """The trigger fired and something sold, but not this quantity — two events are
    in play and consuming 100 against one of them would be a guess."""
    intent, client = held
    await enter(client, intent)
    monkeypatch.setattr(pstop, "stop_status", AsyncMock(return_value=pstop.STOP_TRIGGERED))
    client.get_positions_raw = AsyncMock(return_value=net(40))
    await trigger_fill(client)
    assert positions.get("u", "OPT").qty == 100
    assert positions.get("u", "OPT").pnl_reconciliation_required


@pytest.mark.asyncio
async def test_a_partial_trigger_fill_leaves_the_rest_held(held, monkeypatch):
    intent, client = held
    await enter(client, intent)
    monkeypatch.setattr(pstop, "stop_status", AsyncMock(return_value=pstop.STOP_TRIGGERED))
    client.get_positions_raw = AsyncMock(return_value=net(60))
    await trigger_fill(client, filled=40, price=131.0)
    p = positions.get("u", "OPT")
    assert p.qty == 60 and p.status in (positions.OPEN, positions.PENDING)
    holding = ledger.inventory("account", "u", "OPT")
    assert holding.net_quantity == 60 and holding.realized_pnl == pytest.approx(400.0)


@pytest.mark.asyncio
async def test_an_unattributable_fill_blocks_automatic_entries(held, monkeypatch):
    from app.services.kite_engine import service
    intent, client = held
    await enter(client, intent)
    monkeypatch.setattr(pstop, "stop_status", AsyncMock(return_value=pstop.STOP_ACTIVE))
    client.get_positions_raw = AsyncMock(return_value=net(0))
    await trigger_fill(client)
    assert [r for r in service.autoexec_preflight("u")
            if r.startswith("Fill-level PnL reconciliation required")]


@pytest.mark.asyncio
async def test_a_paper_position_is_never_reconciled_against_a_broker(held, monkeypatch):
    """Simulation has no trigger at Zerodha to ask about."""
    intent, client = held
    await enter(client, intent)
    paper = Client()
    paper._is_paper = True
    probe = AsyncMock(return_value=pstop.STOP_TRIGGERED)
    monkeypatch.setattr(pstop, "stop_status", probe)
    await trigger_fill(paper)
    probe.assert_not_awaited()
    assert positions.get("u", "OPT").qty == 100
