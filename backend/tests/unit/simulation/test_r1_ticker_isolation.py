"""The virtual tenant rides the owner's ticker without ever slowing it, and its claims are its own."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from tenancy_support import OWNER, SIM_UID

from app.services.exchanges.kite import accounts, ticker_manager as tm


# ── the tick feed ─────────────────────────────────────────────────────────────
@pytest.fixture
def feed(monkeypatch):
    """A broadcaster with a controllable simulation feed and a virtual tenant that exists."""
    monkeypatch.setattr(accounts, "sim_account_for", lambda owner: object())
    monkeypatch.setattr(accounts, "get_active", lambda uid: None)
    monkeypatch.setattr(tm, "_stream_matches_active_account", lambda *a: True)
    state = SimpleNamespace(batches=[], gate=asyncio.Event(), block=False, boom=False)

    async def fake_feed(user_id, ticks):
        state.batches.append(list(ticks))
        if state.boom:
            raise RuntimeError("sim blew up")
        if state.block:
            await state.gate.wait()

    monkeypatch.setattr(tm, "_feed_simulation", fake_feed)
    tm._sim_feed_tasks.clear()
    tm._sim_feed_pending.clear()
    yield state
    tm._sim_feed_tasks.clear()
    tm._sim_feed_pending.clear()


def _tick(n):
    return {"instrument_token": n, "last_price": float(n)}


async def test_a_slow_simulation_never_holds_the_live_tick_callback(feed):
    feed.block = True
    broadcast = tm._make_broadcaster(OWNER)
    await asyncio.wait_for(broadcast([_tick(1)]), timeout=1.0)        # returns although the sim is stuck
    await asyncio.sleep(0)
    assert feed.batches == [[_tick(1)]]
    feed.gate.set()
    await tm.drain_simulation_feed()


async def test_batches_arriving_while_the_sim_is_busy_are_coalesced_in_order(feed):
    feed.block = True
    broadcast = tm._make_broadcaster(OWNER)
    await broadcast([_tick(1)])
    await asyncio.sleep(0)
    await broadcast([_tick(2)])
    await broadcast([_tick(3), _tick(4)])
    assert len(feed.batches) == 1                                    # single flight per owner
    feed.block = False
    feed.gate.set()
    await tm.drain_simulation_feed()
    assert feed.batches == [[_tick(1)], [_tick(2), _tick(3), _tick(4)]]


async def test_the_pending_backlog_is_bounded(feed, monkeypatch):
    monkeypatch.setattr(tm, "_SIM_FEED_MAX_PENDING", 3)
    feed.block = True
    broadcast = tm._make_broadcaster(OWNER)
    await broadcast([_tick(1)])
    await asyncio.sleep(0)
    for n in range(2, 8):
        await broadcast([_tick(n)])
    feed.block = False
    feed.gate.set()
    await tm.drain_simulation_feed()
    assert feed.batches[1] == [_tick(5), _tick(6), _tick(7)]         # the newest survive


async def test_a_failing_simulation_feed_never_stops_later_batches(feed):
    feed.boom = True
    broadcast = tm._make_broadcaster(OWNER)
    await broadcast([_tick(1)])
    await tm.drain_simulation_feed()
    feed.boom = False
    await broadcast([_tick(2)])
    await tm.drain_simulation_feed()
    assert feed.batches == [[_tick(1)], [_tick(2)]]


async def test_a_stuck_simulation_feed_is_cut_off_and_the_next_batch_runs(feed, monkeypatch):
    monkeypatch.setattr(tm, "_SIM_FEED_TIMEOUT_S", 0.05)
    feed.block = True
    broadcast = tm._make_broadcaster(OWNER)
    await broadcast([_tick(1)])
    await asyncio.sleep(0)
    await broadcast([_tick(2)])
    await asyncio.wait_for(tm.drain_simulation_feed(), timeout=2.0)
    assert feed.batches == [[_tick(1)], [_tick(2)]]


async def test_without_a_virtual_tenant_no_feed_task_is_started(feed, monkeypatch):
    monkeypatch.setattr(accounts, "sim_account_for", lambda owner: None)
    await tm._make_broadcaster(OWNER)([_tick(1)])
    assert tm._sim_feed_tasks == {} and feed.batches == []


def test_an_idle_gamma_tenant_costs_no_config_read(monkeypatch):
    from app.services import gamma_move_runner as gm
    from app.services import gamma_move_scanner as scanner

    reads = []
    monkeypatch.setattr(gm.positions_store, "open_positions", lambda uid: [])
    monkeypatch.setattr(scanner, "has_watchers", lambda uid: False)
    monkeypatch.setattr(gm, "get_config", lambda uid: reads.append(uid) or SimpleNamespace(enabled=True))
    assert gm.has_tick_work(SIM_UID) is False and reads == []
    monkeypatch.setattr(scanner, "has_watchers", lambda uid: True)
    assert gm.has_tick_work(SIM_UID) is True and reads == [SIM_UID]


# ── the claims ────────────────────────────────────────────────────────────────
class _Ticker:
    def __init__(self):
        self.subscribed, self.unsubscribed = [], []

    async def subscribe(self, tokens, mode):
        self.subscribed.append(list(tokens))

    async def unsubscribe(self, tokens):
        self.unsubscribed.append(list(tokens))

    def status(self):
        return {"subscribed": []}


@pytest.fixture
def ticker(monkeypatch):
    t = _Ticker()
    monkeypatch.setattr(tm, "_owners", {})
    monkeypatch.setitem(tm._tickers, OWNER, t)
    monkeypatch.setattr(tm, "ensure", AsyncMock(return_value=t))
    yield t
    tm._tickers.pop(OWNER, None)


async def test_the_live_tenant_releasing_a_token_does_not_unsubscribe_what_the_sim_still_needs(ticker):
    await tm.subscribe(OWNER, [111], owner="gamma-unwind")
    await tm.subscribe(SIM_UID, [111], owner="gamma-unwind")
    assert tm.owners_of(OWNER, 111) == {"gamma-unwind", "sim:gamma-unwind"}

    await tm.release(OWNER, [111], owner="gamma-unwind")
    assert ticker.unsubscribed == [] and tm.owners_of(OWNER, 111) == {"sim:gamma-unwind"}

    await tm.release(SIM_UID, [111], owner="gamma-unwind")             # the last claimant leaves
    assert ticker.unsubscribed == [[111]] and tm.owners_of(OWNER, 111) == set()


async def test_the_sim_tenant_releasing_removes_only_its_own_claim(ticker):
    await tm.subscribe(OWNER, [111], owner="kite-protection:X")
    await tm.subscribe(SIM_UID, [111], owner="kite-protection:X")
    result = await tm.release(SIM_UID, [111], owner="kite-protection:X")
    assert result["unsubscribed"] == [] and ticker.unsubscribed == []
    assert tm.owners_of(OWNER, 111) == {"kite-protection:X"}           # the live claim is untouched


async def test_a_sim_only_claim_is_freed_by_the_sim_release(ticker):
    await tm.subscribe(SIM_UID, [222], owner="kite-engine:Y")
    await tm.release(SIM_UID, [222], owner="kite-engine:Y")
    assert ticker.unsubscribed == [[222]]


async def test_a_live_release_alone_still_unsubscribes(ticker):
    await tm.subscribe(OWNER, [333], owner="gamma-unwind")
    await tm.release(OWNER, [333], owner="gamma-unwind")
    assert ticker.unsubscribed == [[333]]


async def test_the_sims_untagged_subscription_stays_the_shared_untagged_claim(ticker):
    await tm.subscribe(SIM_UID, [444])
    assert tm.owners_of(OWNER, 444) == {tm._ANY}
