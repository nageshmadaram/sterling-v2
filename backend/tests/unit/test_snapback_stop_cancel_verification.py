"""Stop-child cancellation must be verified by broker identity, not row counts."""
from __future__ import annotations

from contextlib import nullcontext
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

import pytest

from app.services.snapback_stop_cancel_verification import (
    verified_terminal_stop_children,
)
from app.services import snapback_live_exits as exits
from app.services import snapback_family_mode as family
from app.services.snapback_live_positions import LivePosition


def _order(oid: str, status: str = "CANCELLED") -> dict:
    return {"order_id": oid, "status": status, "filled_quantity": 0,
            "average_price": 0.0}


@pytest.mark.parametrize("book", [
    [_order("A"), _order("A")],  # duplicate A never proves B is gone
    [_order("A")],  # absent B
    [_order("A"), _order("B", "OPEN")],  # B can still execute
    [_order("A"), _order("B", "TRIGGER PENDING")],
    [_order("A"), _order("B", "UNKNOWN")],
    [_order("A"), _order("B"), _order("B")],
    [_order("A"), _order("B", "")],
    None,
    {},
])
def test_missing_duplicate_active_or_unreadable_order_book_refused(book):
    assert verified_terminal_stop_children({"A", "B"}, book) is None


@pytest.mark.parametrize("ids", [
    set(), {""}, {" "}, {"A "},
])
def test_unidentified_expected_stop_order_never_authorizes_market_exit(ids):
    assert verified_terminal_stop_children(ids, [_order("A")]) is None


def test_exact_terminal_child_ids_and_irrelevant_other_orders():
    original = [_order("FOREIGN", "OPEN"), _order("A", "COMPLETE"),
                _order("B", "CANCELLED"), _order("OTHER", "OPEN")]
    verified = verified_terminal_stop_children({"A", "B"}, original)
    assert verified is not None
    assert [item["order_id"] for item in verified] == ["A", "B"]


@pytest.mark.asyncio
async def test_cancel_confirmation_duplicate_cannot_start_a_rival_market_exit(
        monkeypatch):
    p = LivePosition(
        position_id="pos", uid="user", account_id="account",
        signal_id="sig", lane="swing", underlying="NIFTY", side="fade_up",
        option_type="PE", option_exchange="NFO", option_symbol="NIFTY_PE",
        option_qty=75, option_entry_price=100.0,
    )
    p.stop_fill_pending_ms = 1  # Grace elapsed; force cancel verification path.
    owned = [_order("STOP-A", "OPEN"), _order("STOP-B", "CANCELLED")]
    book = [_order("STOP-A"), _order("STOP-A")]
    client = NS(
        cancel_order=AsyncMock(),
        get_orders=AsyncMock(return_value=book),
    )
    obj = exits.SnapbackExitManager(positions=NS(mutate=Mock()))
    book_fills = Mock()
    monkeypatch.setattr(obj, "_book_stop_fills", book_fills)
    monkeypatch.setattr(family, "canonical_broker_capability",
                        lambda *a, **kw: nullcontext())

    assert await obj._stop_fill_pending(client, p, owned) == "stop_cancel_unconfirmed"
    client.cancel_order.assert_awaited_once_with("STOP-A")
    client.get_orders.assert_awaited_once()
    book_fills.assert_not_called()


@pytest.mark.asyncio
async def test_stop_child_missing_identity_cannot_attempt_cancellation(monkeypatch):
    p = LivePosition(
        position_id="pos", uid="user", account_id="account",
        signal_id="sig", lane="scalp", underlying="NIFTY", side="fade_up",
        option_type="PE", option_exchange="NFO", option_symbol="NIFTY_PE",
        option_qty=75, option_entry_price=100.0,
    )
    p.stop_fill_pending_ms = 1
    own = [_order("", "OPEN")]
    client = NS(cancel_order=AsyncMock(), get_orders=AsyncMock())
    obj = exits.SnapbackExitManager(positions=NS(mutate=Mock()))
    monkeypatch.setattr(obj, "_book_stop_fills", Mock())
    assert await obj._stop_fill_pending(client, p, own) == "stop_cancel_unconfirmed"
    client.cancel_order.assert_not_awaited()
    client.get_orders.assert_not_awaited()
