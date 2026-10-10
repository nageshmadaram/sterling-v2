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


class _NewTicker:
    created: list["_NewTicker"] = []

    def __init__(self, **_kwargs):
        self.is_active = True
        self.died = False
        self.last_error = ""
        self.start = AsyncMock()
        self.stop = AsyncMock()
        self.created.append(self)

    def status(self):
        return {"subscribed": []}


@pytest.mark.asyncio
async def test_concurrent_ensure_creates_only_one_live_socket(monkeypatch):
    """Two overlapping subscribes cannot each create a ticker for one account."""
    _NewTicker.created = []
    account = SimpleNamespace(
        id="acct-live", connected=True, is_paper=False,
        api_key="key", access_token="token",
    )

    async def acquire(_account):
        await asyncio.sleep(0.01)  # force the original check/create race
        return object()

    monkeypatch.setattr(tm, "_tickers", {})
    monkeypatch.setattr(tm, "_ticker_accounts", {})
    monkeypatch.setattr(tm, "_owners", {})
    monkeypatch.setattr(tm, "_lifecycle_locks", {})
    monkeypatch.setattr(tm, "KiteTicker", _NewTicker)
    monkeypatch.setattr(tm._accounts, "get_active", lambda _uid: account)
    monkeypatch.setattr(tm._accounts, "acquire_client", acquire)

    first, second = await asyncio.gather(tm.ensure("u"), tm.ensure("u"))

    assert first is second
    assert len(_NewTicker.created) == 1
    assert tm._ticker_accounts == {"u": "acct-live"}
    first.start.assert_awaited_once()


@pytest.mark.asyncio
async def test_explicit_stop_serializes_with_an_inflight_ticker_creation(monkeypatch):
    """A logout racing a warm-client await must not leave a new socket behind."""
    _NewTicker.created = []
    account = SimpleNamespace(
        id="acct-live", connected=True, is_paper=False,
        api_key="key", access_token="token",
    )
    acquire_started = asyncio.Event()
    allow_acquire = asyncio.Event()

    async def acquire(_account):
        acquire_started.set()
        await allow_acquire.wait()
        return object()

    monkeypatch.setattr(tm, "_tickers", {})
    monkeypatch.setattr(tm, "_ticker_accounts", {})
    monkeypatch.setattr(tm, "_owners", {})
    monkeypatch.setattr(tm, "_lifecycle_locks", {})
    monkeypatch.setattr(tm, "KiteTicker", _NewTicker)
    monkeypatch.setattr(tm._accounts, "get_active", lambda _uid: account)
    monkeypatch.setattr(tm._accounts, "acquire_client", acquire)

    start_task = asyncio.create_task(tm.ensure("u"))
    await asyncio.wait_for(acquire_started.wait(), timeout=0.25)
    stop_task = asyncio.create_task(tm.stop("u"))
    await asyncio.sleep(0)  # let stop queue behind the in-flight ensure
    allow_acquire.set()
    created, stopped = await asyncio.gather(start_task, stop_task)

    assert stopped == {"ok": True}
    assert len(_NewTicker.created) == 1
    created.start.assert_awaited_once()
    created.stop.assert_awaited_once()
    assert tm._tickers == {}
    assert tm._ticker_accounts == {}
