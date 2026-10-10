"""The virtual tenant has no broker socket: its price subscriptions ride the owner's real feed."""
from __future__ import annotations

from tenancy_support import OWNER, SIM_UID

from app.services.exchanges.kite import ticker_manager as tm


class _Ticker:
    def __init__(self):
        self.subscribed, self.unsubscribed = [], []

    async def subscribe(self, tokens, mode):
        self.subscribed.append((list(tokens), mode))

    async def unsubscribe(self, tokens):
        self.unsubscribed.append(list(tokens))

    def status(self):
        return {"active": True, "connected": True, "subscribed": [1, 2], "tick_count": 5, "last_tick_ms": 9}


async def test_sim_subscribe_rides_the_owners_ticker(monkeypatch):
    ticker, asked = _Ticker(), []

    async def ensure(user_id):
        asked.append(user_id)
        return ticker

    monkeypatch.setattr(tm, "ensure", ensure)
    monkeypatch.setattr(tm, "_owners", {})
    result = await tm.subscribe(SIM_UID, [11, 12])
    assert result["ok"] and asked == [OWNER]                        # never an "ensure" for the sim uid
    assert ticker.subscribed == [([11, 12], tm.K.MODE_QUOTE)]
    assert tm.owners_of(OWNER, 11) and not tm.owners_of(SIM_UID, 11)


async def test_sim_unsubscribe_never_drops_the_live_views_tokens(monkeypatch):
    ticker = _Ticker()
    monkeypatch.setitem(tm._tickers, OWNER, ticker)
    monkeypatch.setattr(tm, "_owners", {(OWNER, 7): {tm._ANY}})
    result = await tm.unsubscribe(SIM_UID, [7])
    assert result["ok"] and ticker.unsubscribed == []
    assert tm.owners_of(OWNER, 7) == {tm._ANY}
    tm._tickers.pop(OWNER, None)


def test_sim_status_is_the_owners_feed_status(monkeypatch):
    ticker = _Ticker()
    monkeypatch.setitem(tm._tickers, OWNER, ticker)
    assert tm.status(SIM_UID)["connected"] is True
    assert tm.stream_status_event(SIM_UID)["connected"] is True
    tm._tickers.pop(OWNER, None)


async def test_a_sim_uid_never_gets_a_broker_ticker_of_its_own(monkeypatch):
    """ensure() for a sim uid would find no connected live account and return None."""
    from tenancy_support import live_account
    from app.services.exchanges.kite import accounts as kite_accounts

    live_account()
    kite_accounts.ensure_sim_account(OWNER)
    assert await tm.ensure(SIM_UID) is None
    assert SIM_UID not in tm._tickers
