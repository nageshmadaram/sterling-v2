"""The owner's websocket ticks reach the owner's virtual tenant (it has no socket of its own), and only it."""
import pytest

from app.services.exchanges.kite import accounts, ticker_manager
from app.services.simulation.identity import sim_uid

OWNER = "default"
TICKS = [{"instrument_token": 1001, "last_price": 99.0}]


@pytest.fixture
def seen(monkeypatch):
    calls = {"monitor": [], "gamma": [], "runtime": []}

    async def monitor_on_ticks(uid, ticks, *, client):
        calls["monitor"].append((uid, client))

    async def gamma_on_ticks(uid, ticks):
        calls["gamma"].append(uid)

    from app.services import gamma_move_runner
    from app.services.kite_engine import monitor, positions
    from app.services.simulation import sim_runtime
    monkeypatch.setattr(monitor, "on_ticks", monitor_on_ticks)
    monkeypatch.setattr(positions, "open_positions", lambda uid: [object()] if uid == sim_uid(OWNER) else [])
    monkeypatch.setattr(gamma_move_runner, "has_tick_work", lambda uid: uid == sim_uid(OWNER))
    monkeypatch.setattr(gamma_move_runner, "on_ticks", gamma_on_ticks)
    monkeypatch.setattr(sim_runtime, "on_ticks", lambda owner, ticks: calls["runtime"].append(owner) or False)

    async def warm(uid):
        return f"client-of-{uid}"
    monkeypatch.setattr(ticker_manager, "_warm_client", warm)
    return calls


async def test_the_owners_ticks_drive_the_tenants_monitor_gamma_watchers_and_virtual_exchange(seen, monkeypatch):
    monkeypatch.setattr(accounts, "sim_account_for", lambda owner: object())
    await ticker_manager._feed_simulation(OWNER, TICKS)
    assert seen["monitor"] == [(sim_uid(OWNER), f"client-of-{sim_uid(OWNER)}")]
    assert seen["gamma"] == [sim_uid(OWNER)] and seen["runtime"] == [OWNER]


async def test_without_a_virtual_tenant_nothing_is_fed(seen, monkeypatch):
    monkeypatch.setattr(accounts, "sim_account_for", lambda owner: None)
    await ticker_manager._feed_simulation(OWNER, TICKS)
    assert seen["monitor"] == [] and seen["gamma"] == []


async def test_a_failure_in_the_tenants_feed_never_reaches_the_tick_loop(seen, monkeypatch):
    from app.services.kite_engine import monitor
    monkeypatch.setattr(accounts, "sim_account_for", lambda owner: object())

    async def boom(uid, ticks, *, client):
        raise RuntimeError("monitor blew up")
    monkeypatch.setattr(monitor, "on_ticks", boom)
    await ticker_manager._feed_simulation(OWNER, TICKS)               # does not raise
    assert seen["gamma"] == [sim_uid(OWNER)] and seen["runtime"] == [OWNER]    # and the rest of the feed still ran
