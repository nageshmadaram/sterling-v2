"""A Kite websocket must never outlive the account identity that created it."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.exchanges.kite import ticker_manager as tm


class _ExistingTicker:
    died = False
    is_active = True

    def __init__(self):
        self.stop = AsyncMock()

    def status(self):
        return {"subscribed": [123]}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "active",
    [
        None,
        SimpleNamespace(id="acct-old", connected=False, is_paper=False),
        SimpleNamespace(id="acct-paper", connected=True, is_paper=True),
        SimpleNamespace(id="", connected=True, is_paper=False),
    ],
)
async def test_ensure_retires_live_ticker_when_active_account_is_not_live(monkeypatch, active):
    old = _ExistingTicker()
    monkeypatch.setattr(tm, "_tickers", {"trader": old})
    monkeypatch.setattr(tm, "_ticker_accounts", {"trader": "acct-old"})
    monkeypatch.setattr(tm, "_owners", {("trader", 123): {"protection"}})
    monkeypatch.setattr(tm._accounts, "get_active", lambda _uid: active)

    result = await tm.ensure("trader")

    assert result is None
    old.stop.assert_awaited_once()
    assert tm._tickers == {}
    assert tm._ticker_accounts == {}
    assert tm._owners == {}


@pytest.mark.asyncio
async def test_old_account_tick_and_order_callbacks_do_not_reach_new_account(monkeypatch):
    from app.api.v1.endpoints.stream import stream_manager
    from app.services.kite_engine import monitor

    monkeypatch.setattr(tm, "_ticker_accounts", {"trader": "acct-old"})
    monkeypatch.setattr(
        tm._accounts,
        "get_active",
        lambda _uid: SimpleNamespace(id="acct-new", connected=True, is_paper=False),
    )
    publish = AsyncMock()
    process_order = AsyncMock()
    monkeypatch.setattr(stream_manager, "broadcast_to_channel", publish)
    monkeypatch.setattr(monitor, "on_order_update", process_order)

    await tm._make_broadcaster("trader", account_id="acct-old")(
        [{"instrument_token": 123, "last_price": 100.0}]
    )
    await tm._make_order_broadcaster(
        "trader", client=object(), account_id="acct-old",
    )({"order_id": "OLD-ORDER", "status": "COMPLETE"})

    publish.assert_not_awaited()
    process_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_watchdog_retires_a_healthy_stream_after_account_switch(monkeypatch):
    """A healthy socket is still stale if the active Kite account changed."""
    old = _ExistingTicker()
    monkeypatch.setattr(tm, "_tickers", {"trader": old})
    monkeypatch.setattr(tm, "_ticker_accounts", {"trader": "acct-old"})
    monkeypatch.setattr(tm, "_owners", {("trader", 123): {"protection"}})
    monkeypatch.setattr(
        tm._accounts,
        "get_active",
        lambda _uid: SimpleNamespace(id="acct-new", connected=True, is_paper=False),
    )

    stopped = asyncio.Event()

    async def mark_stopped():
        stopped.set()

    old.stop.side_effect = mark_stopped
    task = asyncio.create_task(tm.supervise(interval=0.005))
    try:
        await asyncio.wait_for(stopped.wait(), timeout=0.25)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    old.stop.assert_awaited_once()
    assert tm._tickers == {}
    assert tm._ticker_accounts == {}
    assert tm._owners == {}


@pytest.mark.asyncio
async def test_ensure_returns_healthy_ticker_for_same_verified_account(monkeypatch):
    old = _ExistingTicker()
    monkeypatch.setattr(tm, "_tickers", {"trader": old})
    monkeypatch.setattr(tm, "_ticker_accounts", {"trader": "acct-old"})
    monkeypatch.setattr(
        tm._accounts,
        "get_active",
        lambda _uid: SimpleNamespace(id="acct-old", connected=True, is_paper=False),
    )

    assert await tm.ensure("trader") is old
    old.stop.assert_not_awaited()
