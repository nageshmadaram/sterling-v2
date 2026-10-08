"""A headless boot must still open the broker stream for a live account.

``ticker_manager.ensure`` was only ever called by a subscribe (the browser, or a strategy
that already holds a position or a watchlist). With nobody on the Kite tab and nothing
held yet, no websocket existed, so the order postback that confirms a SuperTrend entry
had nowhere to land and the filled position stayed PENDING with no broker stop until the
next five-minute scan. The watchdog now opens the stream for the account itself.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.exchanges.kite import ticker_manager as tm


def _account(user_id="u1", **over):
    base = dict(id=f"KITE-{user_id}", user_id=user_id, is_paper=False, is_active=True,
                connected=True, token_is_live=True, api_key="key", access_token="tok")
    base.update(over)
    return SimpleNamespace(**base)


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    monkeypatch.setattr(tm, "_tickers", {})
    monkeypatch.setattr(tm, "_ticker_accounts", {})
    monkeypatch.setattr(tm, "_owners", {})
    monkeypatch.setattr(tm, "_lifecycle_locks", {})
    monkeypatch.setattr(tm._accounts, "bootstrap", lambda: None)


def _stored(monkeypatch, *accounts):
    monkeypatch.setattr(tm._accounts, "all_accounts", lambda: list(accounts))
    monkeypatch.setattr(tm._accounts, "get_active",
                        lambda uid: next((a for a in accounts if a.user_id == uid), None))


async def test_a_live_account_with_no_stream_gets_one(monkeypatch):
    _stored(monkeypatch, _account("u1"))
    ensure = AsyncMock()
    monkeypatch.setattr(tm, "ensure", ensure)

    await tm.supervise_once()

    ensure.assert_awaited_once_with("u1")


@pytest.mark.parametrize("over", [
    {"is_paper": True},            # no non-live mode exists, but a stored simulated row opens nothing
    {"is_active": False},          # a stored-but-inactive login cannot receive an order
    {"connected": False},          # no session yet
    {"token_is_live": False},      # an expired token only reconnect-loops against HTTP 403
])
async def test_an_account_without_a_usable_live_session_gets_no_stream(monkeypatch, over):
    _stored(monkeypatch, _account("u1", **over))
    ensure = AsyncMock()
    monkeypatch.setattr(tm, "ensure", ensure)

    await tm.supervise_once()

    ensure.assert_not_awaited()


async def test_an_account_that_already_has_a_stream_is_left_alone(monkeypatch):
    _stored(monkeypatch, _account("u1"))
    ticker = SimpleNamespace(died=False, credentials_match=lambda *_a: True)
    tm._tickers["u1"] = ticker
    tm._ticker_accounts["u1"] = "KITE-u1"
    ensure = AsyncMock()
    monkeypatch.setattr(tm, "ensure", ensure)

    await tm.supervise_once()

    ensure.assert_not_awaited()


async def test_failing_to_open_a_stream_does_not_stop_the_repair_of_a_dead_one(monkeypatch):
    live = _account("u1")
    dead_owner = _account("u2")
    _stored(monkeypatch, live, dead_owner)
    tm._tickers["u2"] = SimpleNamespace(died=True, last_error="boom",
                                        credentials_match=lambda *_a: True)
    tm._ticker_accounts["u2"] = "KITE-u2"
    calls = []

    async def ensure(uid):
        calls.append(uid)
        if uid == "u1":
            raise RuntimeError("websocket refused")

    monkeypatch.setattr(tm, "ensure", ensure)

    await tm.supervise_once()                      # must not raise

    assert calls == ["u1", "u2"], "the dead stream was still restarted after the open failed"
