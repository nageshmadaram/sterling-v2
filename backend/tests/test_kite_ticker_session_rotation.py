"""A re-login must reach the running ticker, and the UI must hear about feed state.

Kite access tokens are re-issued on every login. The ticker used to keep the
token it was built with and ``ensure()`` kept returning it as healthy, so after
the morning login the process that survived the night reconnect-looped on a dead
token while every price in the app sat on the slow REST fallback.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.exchanges.kite import ticker_manager as tm
from app.services.exchanges.kite.ticker import KiteTicker


def _account(token: str, *, account_id: str = "acct-live"):
    return SimpleNamespace(
        id=account_id, connected=True, is_paper=False, api_key="key", access_token=token,
    )


class _Ticker:
    is_active = True
    died = False
    last_error = ""

    def __init__(self, token: str):
        self._token = token
        self.start = AsyncMock()
        self.stop = AsyncMock()
        self.update_credentials = AsyncMock()

    def credentials_match(self, api_key, access_token):
        return api_key == "key" and access_token == self._token

    def status(self):
        return {"subscribed": [1, 2, 3]}


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    monkeypatch.setattr(tm, "_tickers", {})
    monkeypatch.setattr(tm, "_ticker_accounts", {})
    monkeypatch.setattr(tm, "_owners", {})
    monkeypatch.setattr(tm, "_lifecycle_locks", {})


async def test_ensure_adopts_a_new_session_instead_of_returning_the_dead_ticker(monkeypatch):
    ticker = _Ticker("yesterday")
    tm._tickers["u"] = ticker
    tm._ticker_accounts["u"] = "acct-live"
    monkeypatch.setattr(tm._accounts, "get_active", lambda _uid: _account("today"))

    result = await tm.ensure("u")

    assert result is ticker, "the subscription set lives on the existing ticker"
    ticker.update_credentials.assert_awaited_once_with("key", "today")


async def test_ensure_leaves_a_ticker_with_current_credentials_alone(monkeypatch):
    ticker = _Ticker("today")
    tm._tickers["u"] = ticker
    tm._ticker_accounts["u"] = "acct-live"
    monkeypatch.setattr(tm._accounts, "get_active", lambda _uid: _account("today"))

    assert await tm.ensure("u") is ticker
    ticker.update_credentials.assert_not_awaited()


async def test_watchdog_rotates_a_live_looking_ticker_after_relogin(monkeypatch):
    ticker = _Ticker("yesterday")
    tm._tickers["u"] = ticker
    tm._ticker_accounts["u"] = "acct-live"
    monkeypatch.setattr(tm._accounts, "get_active", lambda _uid: _account("today"))
    rotated = asyncio.Event()

    async def mark(*_a):
        rotated.set()

    ticker.update_credentials.side_effect = mark
    task = asyncio.create_task(tm.supervise(interval=0.005))
    try:
        await asyncio.wait_for(rotated.wait(), timeout=0.5)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    ticker.update_credentials.assert_awaited_with("key", "today")
    ticker.stop.assert_not_awaited()


async def test_real_ticker_reports_credential_identity():
    t = KiteTicker("key", "tok")
    assert t.credentials_match("key", "tok") is True
    assert t.credentials_match("key", "other") is False
    assert t.credentials_match("other-key", "tok") is False


def test_status_event_for_a_user_without_a_ticker_is_not_an_alarm():
    event = tm.stream_status_event("nobody")
    assert event["type"] == "kite_stream_status"
    assert event["started"] is False and event["connected"] is False
    assert event["last_error"] is None


def test_status_event_never_leaks_credentials(monkeypatch):
    fake = SimpleNamespace(status=lambda: {
        "active": True, "started": True, "connected": False, "subscribed": [1],
        "last_tick_ms": 5,
        "last_error": "InvalidURI: wss://ws.kite.trade?api_key=KEY123&access_token=SECRET456 bad",
    })
    tm._tickers["u"] = fake
    event = tm.stream_status_event("u")
    assert "SECRET456" not in event["last_error"] and "KEY123" not in event["last_error"]
    assert event["started"] is True and event["connected"] is False
    assert event["subscribed_count"] == 1


async def test_status_is_published_to_the_users_tick_channel(monkeypatch):
    from app.api.v1.endpoints.stream import stream_manager

    publish = AsyncMock()
    monkeypatch.setattr(stream_manager, "broadcast_to_channel", publish)

    await tm.publish_stream_status("u")

    channel, message = publish.await_args.args
    assert channel == "kite_ticks:u"
    assert message["type"] == "kite_stream_status"


async def test_status_from_a_replaced_account_is_not_published(monkeypatch):
    from app.api.v1.endpoints.stream import stream_manager

    publish = AsyncMock()
    monkeypatch.setattr(stream_manager, "broadcast_to_channel", publish)
    monkeypatch.setattr(tm._accounts, "get_active", lambda _uid: _account("t", account_id="acct-new"))
    tm._ticker_accounts["u"] = "acct-old"

    await tm._make_status_broadcaster("u", account_id="acct-old")({"connected": True})

    publish.assert_not_awaited()


async def test_stopping_a_ticker_tells_the_browser(monkeypatch):
    from app.api.v1.endpoints.stream import stream_manager

    publish = AsyncMock()
    monkeypatch.setattr(stream_manager, "broadcast_to_channel", publish)
    ticker = _Ticker("t")
    tm._tickers["u"] = ticker
    tm._ticker_accounts["u"] = "acct-live"

    await tm.stop("u")

    ticker.stop.assert_awaited_once()
    message = publish.await_args.args[1]
    assert message["type"] == "kite_stream_status" and message["started"] is False
