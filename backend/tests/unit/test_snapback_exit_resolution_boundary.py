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
