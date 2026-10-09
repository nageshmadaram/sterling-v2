"""Simulation end to end, offline: the three strategies on the VIRTUAL tenant reach virtual fills, virtual
broker-style stops, a virtual stop-out and a complete journal row, and no real order ever leaves.

The fake Kite exchange plays the OWNER's live session: it supplies prices and the instrument master and
nothing else. The Simulation tenant (``sim:default``) is booted the way ``tenant.boot_tenant`` does it and driven
through the same production entry paths ``test_admission_dry_run`` drives for live: the Supertrend place
callback, Gamma's ``_register_signals`` and Snapback's dispatcher cycle. Every order, GTT and fill below is a row
in the SQLite virtual ledger; every test ends by reading the fake broker's audit log and asserting that no order,
GTT or modification was ever sent to it.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from tests.e2e_live import scenarios as S
from tests.e2e_live.machine import UID
from tests.e2e_live.test_admission_dry_run import _fresh_live_machine

SIM = "sim:default"


# ───────────────────────────────────────────────────────────────────────────── harness
async def _sim_world(machine) -> SimpleNamespace:
    """A live owner session (the price source), the booted virtual tenant and its client."""
    await _fresh_live_machine(machine)
    S.operator_sets_daily_loss(machine)
    machine.m("app.services.simulation.tenant").boot_tenant(UID)
    accounts = machine.m("app.services.exchanges.kite.accounts")
    client = await accounts.acquire_client(accounts.sim_account_for(UID))
    assert client._is_paper is True and client._is_simulated is True and client._account_id.startswith("SIM-")
    return SimpleNamespace(machine=machine, client=client, ex=machine.exchange, ledger=client.ledger)


def sim_http(machine, method, path, **kw):
    headers = {**kw.pop("headers", {}), "X-Sterling-View": "simulation"}
    return S.http(machine, method, path, headers=headers, **kw)


def _broker_writes(machine):
    """Everything the application POSTed / PUT / DELETEd to the (fake) broker."""
    return [r for r in machine.exchange.requests if r[0] in ("POST", "PUT", "DELETE")]


def _assert_nothing_real_was_sent(machine):
    """The audit log of the broker: no order, GTT, modification or cancellation was ever requested."""
    writes = _broker_writes(machine)
    assert [w for w in writes if w[1].startswith(("/orders", "/gtt"))] == [], writes
    ex = machine.exchange
    assert ex.order_posts == [] and ex.orders == {} and ex.gtts == {} and ex.trades == [] and ex.postbacks == []


async def settle(world):
    """Deliver the order postbacks the virtual exchange queued (what the websocket does for a live account)."""
    rt = world.machine.m("app.services.simulation.sim_runtime")
    for exch in (world.client.exchange, rt.runtime_exchange(UID)):
        await exch.deliver_postbacks()


async def sim_pass(world):
    """One runtime pass: resting orders, GTTs and marks, evaluated regardless of the wall-clock session."""
    rt = world.machine.m("app.services.simulation.sim_runtime")
    report = await rt.run_pass(owners=[UID], force=True)
    assert report["errors"] == {}, report
    await settle(world)
    return report


def _journal(machine):
    return machine.m("app.services.trade_journal")


def _sim_trades(machine, **filters):
    return _journal(machine).list_trades([SIM], **filters)["items"]


def _live_rows(machine):
    return _journal(machine).list_trades([UID])["items"]


def _day(world) -> str:
    return world.client.exchange.today()


def _orders(world, **match):
    return [o for o in world.client.exchange.orders() if all(o[k] == v for k, v in match.items())]


def _assert_books_balance(world, trade):
    """The journal row, the fills and the cash agree to the paisa."""
    view = world.ledger.account_view(UID, _day(world))
    assert view["open_positions"] == 0 and view["active_gtts"] == 0 and view["open_orders"] == 0
    assert trade["gross_pnl"] == pytest.approx(view["realised_today"], abs=0.01)
    assert trade["charges"] == pytest.approx(view["charges_today"], abs=0.01)
    assert trade["net_pnl"] == pytest.approx(view["net_realised_today"], abs=0.01)
    assert view["equity"] == pytest.approx(view["capital"] + view["net_realised_today"], abs=0.01)
    assert view["cash"] == view["equity"]


# ───────────────────────────────────────────────────────────────────────────── Supertrend
@pytest.mark.asyncio
async def test_a_supertrend_entry_on_the_sim_tenant_fills_virtually_is_protected_and_stopped_out(machine):
    world = await _sim_world(machine)
    assert sim_http(machine, "POST", "/kite/engine/config/production").status_code == 200
    resp = sim_http(machine, "PATCH", "/kite/engine/config", json={
        "auto_execute": True, "supertrend_auto_execute": True, "stop_mode": "both", "engine_enabled": True})
    assert resp.status_code == 200, resp.text

    service = machine.m("app.services.kite_engine.service")
    state = machine.m("app.services.kite_engine.state")
    positions = machine.m("app.services.kite_engine.positions")
    ex = machine.exchange
    ex.set_quote(f"NFO:{S.SUPERTREND_SYMBOL}", 180.0)                       # bid 179.75 / ask 180.25
    ex.spot["NIFTY 50"] = 25000.0
    row = S.supertrend_row(token=int(ex.instrument(S.SUPERTREND_SYMBOL, "NFO")["instrument_token"]))
    await service._make_place_cb(world.client, SIM)(row, None)
    activity = [f"{e.kind}: {e.message}" for e in state.activity(SIM)]

    (entry,) = _orders(world, transaction_type="BUY")                       # a virtual order, Kite-shaped
    assert entry["status"] == "COMPLETE" and entry["filled_quantity"] == 75 and entry["placed_by"] == world.client._account_id
    assert entry["order_id"].startswith("261012") and entry["average_price"] == 180.30      # best ask + one tick of slippage
    await settle(world)                                                     # the fill postback, a moment later
    pos = positions.get(SIM, S.SUPERTREND_SYMBOL)
    assert pos is not None and pos.status == positions.OPEN, activity
    assert pos.fill_price == 180.30 and pos.qty == 75 and pos.account_id == world.client._account_id

    (gtt,) = world.client.exchange.gtts()                                   # the broker-style stop, in the virtual book
    assert gtt["status"] == "active" and gtt["id"] == pos.gtt_id
    assert gtt["condition"]["trigger_values"] == [pos.stop_premium] and gtt["orders"][0]["transaction_type"] == "SELL"
    (open_trade,) = _sim_trades(machine)                                    # a complete OPEN journal row, tagged SIMULATION
    assert open_trade["mode"] == "SIMULATION" and open_trade["status"] == "OPEN" and open_trade["strategy"] == "supertrend"
    assert open_trade["entry_price"] == 180.30 and open_trade["initial_sl"] == pos.stop_premium and open_trade["qty"] == 75
    held = world.ledger.account_view(UID, _day(world))
    assert held["open_positions"] == 1 and held["active_gtts"] == 1 and held["cash"] < held["capital"]
    assert _live_rows(machine) == [] and positions.get(UID, S.SUPERTREND_SYMBOL) is None      # nothing leaked to the live tenant

    # the market falls through the stop: the virtual GTT fires, its LIMIT child fills at the bid
    ex.set_quote(f"NFO:{S.SUPERTREND_SYMBOL}", 93.2, bid=93.0, ask=93.4)
    await sim_pass(world)
    (child,) = _orders(world, transaction_type="SELL")
    assert child["status"] == "COMPLETE" and child["average_price"] == 92.95 and child["price"] == 91.7
    assert world.client.exchange.gtt(gtt["id"])["status"] == "triggered"
    closed = positions.get(SIM, S.SUPERTREND_SYMBOL)
    assert closed.status == positions.CLOSED, [f"{e.kind}: {e.message}" for e in state.activity(SIM)][-4:]
    assert closed.exit_reason.startswith("broker GTT triggered")

    (trade,) = _sim_trades(machine)
    assert trade["trade_id"] == open_trade["trade_id"] and trade["status"] == "CLOSED" and trade["mode"] == "SIMULATION"
    assert trade["exit_reason"] == "SL" and trade["exit_price"] == 92.95 and trade["exit_qty"] == 75
    assert trade["gross_pnl"] == pytest.approx((92.95 - 180.30) * 75, abs=0.01) and trade["net_pnl"] < trade["gross_pnl"] < 0
    assert trade["r_multiple"] < 0 and trade["charges"] > 0 and trade["capital_used"] == pytest.approx(180.30 * 75, abs=0.01)
    assert [e["type"] for e in _journal(machine).get_trade(trade["trade_id"], [SIM])["timeline"]] == [
        "SIGNAL", "ORDER_SUBMITTED", "FILLED", "PROTECTION_PLACED", "EXIT_FILLED"]
    _assert_books_balance(world, trade)
    assert _live_rows(machine) == []
    _assert_nothing_real_was_sent(machine)


# ───────────────────────────────────────────────────────────────────────────── Gamma
@pytest.mark.asyncio
async def test_a_gamma_entry_on_the_sim_tenant_fills_virtually_is_protected_and_stopped_out(machine):
    world = await _sim_world(machine)
    assert sim_http(machine, "PATCH", "/kite/engine/config", json={"auto_execute": True}).status_code == 200
    resp = sim_http(machine, "PUT", "/config/gamma-move", json={
        "enabled": True, "auto_execute": True, "execution_lots": 1, "stop_mode": "both"})
    assert resp.status_code == 200, resp.text

    runner = machine.m("app.services.gamma_move_runner")
    store = machine.m("app.services.gamma_move_positions")
    ex = machine.exchange
    ex.set_quote(f"NFO:{S.GAMMA_SYMBOL}", 120.0, bid=119.5, ask=120.0)
    await runner._register_signals(SIM, [S.gamma_signal(machine, entry=120.0)])
    session = runner.session_for(SIM)
    notes = [f"{n['kind']}: {n['message']}" for n in session.notes]

    (entry,) = _orders(world, transaction_type="BUY")
    assert entry["order_type"] == "LIMIT" and entry["price"] == 120.0 and entry["status"] == "COMPLETE"
    assert entry["average_price"] == 120.0                                   # never worse than its limit
    await settle(world)
    pos = store.get(SIM, S.GAMMA_SYMBOL)
    assert pos is not None and pos.is_open and pos.quantity == 75 and pos.hold == "intraday", notes
    assert pos.account_id == world.client._account_id and pos.gtt_id and not pos.gtt_pending
    (gtt,) = world.client.exchange.gtts()
    assert gtt["status"] == "active" and gtt["condition"]["trigger_values"] == [98.0] and gtt["orders"][0]["price"] == 96.0
    (open_trade,) = _sim_trades(machine)
    assert open_trade["strategy"] == "gamma" and open_trade["mode"] == "SIMULATION" and open_trade["status"] == "OPEN"
    assert open_trade["entry_price"] == 120.0 and open_trade["initial_sl"] == 98.0
    assert _live_rows(machine) == [] and store.get(UID, S.GAMMA_SYMBOL) is None

    ex.set_quote(f"NFO:{S.GAMMA_SYMBOL}", 98.0, bid=97.5, ask=98.5)          # through the stop: the child fills at 97.45
    await sim_pass(world)
    await runner.reconcile(SIM)
    (child,) = _orders(world, transaction_type="SELL")
    assert child["status"] == "COMPLETE" and child["average_price"] == 97.45 and child["price"] == 96.0
    assert world.client.exchange.gtt(gtt["id"])["status"] == "triggered"

    (trade,) = _sim_trades(machine)
    assert (trade["strategy"], trade["status"], trade["mode"]) == ("gamma", "CLOSED", "SIMULATION")
    assert trade["exit_reason"] == "SL" and trade["exit_price"] == 97.45
    assert trade["gross_pnl"] == pytest.approx((97.45 - 120.0) * 75, abs=0.01) and trade["net_pnl"] < trade["gross_pnl"]
    assert trade["signal_context"]["tier"] == "ENTRY" and trade["signal_context"]["gates_passed"]
    _assert_books_balance(world, trade)
    assert _live_rows(machine) == []
    _assert_nothing_real_was_sent(machine)


# ───────────────────────────────────────────────────────────────────────────── Snapback
@pytest.mark.asyncio
async def test_a_snapback_entry_on_the_sim_tenant_fills_virtually_with_its_hedge_and_is_stopped_out(machine):
    world = await _sim_world(machine)
    assert sim_http(machine, "PATCH", "/kite/engine/config", json={"auto_execute": True}).status_code == 200
    resp = sim_http(machine, "PUT", "/config/snapback", json={"enabled": True, "auto_execute": True, "trading_mode": "swing"})
    assert resp.status_code == 200, resp.text

    snapback = machine.m("app.services.snapback")
    live_positions = machine.m("app.services.snapback_live_positions")
    dispatcher = machine.m("app.services.snapback_dispatcher").get_dispatcher()
    ex = machine.exchange
    ex.set_quote(f"NFO:{S.SNAPBACK_OPTION}", 299.5, bid=299.0, ask=300.0)
    ex.set_quote(f"NFO:{S.SNAPBACK_FUTURE}", 25020.0, bid=25019.5, ask=25020.5, oi=9_000_000)
    ex.set_quote("NSE:NIFTY 50", 25000.0)
    st = snapback.status(SIM)
    st.rows = [S.snapback_row(machine, ask=300.0)]
    st.last_scan_ms = snapback.ist_now_ms()

    cycle = await dispatcher.cycle(SIM)
    assert cycle["state"] == "ready" and cycle["entries"] == 1, cycle
    await settle(world)
    store = live_positions.default_store()
    (pos,) = store.open_positions(SIM)
    assert pos.option_symbol == S.SNAPBACK_OPTION and pos.status == live_positions.OPEN
    assert pos.protection_state == live_positions.PROTECTION_ACTIVE and pos.gtt_id and pos.account_id == world.client._account_id
    assert pos.hedge_symbol == S.SNAPBACK_FUTURE and pos.hedge_qty == 75
    option_buy = _orders(world, tradingsymbol=S.SNAPBACK_OPTION, transaction_type="BUY")[0]
    hedge_buy = _orders(world, tradingsymbol=S.SNAPBACK_FUTURE, transaction_type="BUY")[0]
    assert option_buy["average_price"] == 300.05 and hedge_buy["average_price"] == 25020.55      # the touch + one tick
    held = world.ledger.account_view(UID, _day(world))
    assert held["open_positions"] == 2 and held["active_gtts"] == 1 and held["blocked_margin"] > 0
    (gtt,) = world.client.exchange.gtts()
    assert gtt["status"] == "active" and gtt["condition"]["trigger_values"] == [195.0] and gtt["orders"][0]["price"] == 191.1
    (open_trade,) = _sim_trades(machine)
    assert (open_trade["strategy"], open_trade["mode"], open_trade["status"]) == ("snapback", "SIMULATION", "OPEN")
    assert open_trade["entry_price"] == 300.05 and open_trade["initial_sl"] == 195.0
    assert _live_rows(machine) == [] and store.open_positions(UID) == []

    ex.set_quote(f"NFO:{S.SNAPBACK_OPTION}", 194.0, bid=193.5, ask=194.5)    # through the stop; the child (limit 191.1) fills
    await sim_pass(world)
    for _ in range(3):
        await dispatcher.cycle(SIM)
        await settle(world)
    assert store.open_positions(SIM) == []
    stop_child = _orders(world, tradingsymbol=S.SNAPBACK_OPTION, transaction_type="SELL")[0]
    hedge_exit = _orders(world, tradingsymbol=S.SNAPBACK_FUTURE, transaction_type="SELL")[0]
    assert stop_child["status"] == "COMPLETE" and stop_child["average_price"] == 193.45
    assert hedge_exit["order_type"] == "MARKET" and hedge_exit["status"] == "COMPLETE" and hedge_exit["average_price"] == 25019.45

    (trade,) = _sim_trades(machine)
    assert (trade["strategy"], trade["status"], trade["mode"]) == ("snapback", "CLOSED", "SIMULATION")
    assert trade["exit_reason"] == "SL" and trade["exit_price"] == 193.45
    option_pnl, hedge_pnl = (193.45 - 300.05) * 75, (25019.45 - 25020.55) * 75
    assert trade["gross_pnl"] == pytest.approx(option_pnl + hedge_pnl, abs=0.01)
    assert trade["extra"]["hedge"]["symbol"] == S.SNAPBACK_FUTURE and trade["net_pnl"] < trade["gross_pnl"]
    _assert_books_balance(world, trade)
    assert _live_rows(machine) == []
    _assert_nothing_real_was_sent(machine)


# ───────────────────────────────────────────────────────────────────────────── trailing and tick-driven exits
@pytest.mark.asyncio
async def test_a_trailed_virtual_stop_moves_and_the_owners_ticks_exit_the_sim_position_at_the_virtual_bid(machine):
    world = await _sim_world(machine)
    assert sim_http(machine, "POST", "/kite/engine/config/production").status_code == 200
    assert sim_http(machine, "PATCH", "/kite/engine/config", json={
        "auto_execute": True, "supertrend_auto_execute": True, "stop_mode": "both", "engine_enabled": True}).status_code == 200
    service = machine.m("app.services.kite_engine.service")
    positions = machine.m("app.services.kite_engine.positions")
    ticker_manager = machine.m("app.services.exchanges.kite.ticker_manager")
    ex = machine.exchange
    ex.set_quote(f"NFO:{S.SUPERTREND_SYMBOL}", 180.0)
    ex.spot["NIFTY 50"] = 25000.0
    token = int(ex.instrument(S.SUPERTREND_SYMBOL, "NFO")["instrument_token"])
    await service._make_place_cb(world.client, SIM)(S.supertrend_row(token=token), None)
    await settle(world)
    pos = positions.get(SIM, S.SUPERTREND_SYMBOL)
    assert pos.status == positions.OPEN and pos.gtt_id and pos.stop_premium == 93.6

    # the SuperTrend ratchets up: the registry stop and the virtual broker stop follow
    machine.monkeypatch.setattr(service, "_new_trail_for_open", lambda p, rows: 120.0)
    await service._update_open_position_trails(world.client, SIM)
    trailed = positions.get(SIM, S.SUPERTREND_SYMBOL)
    assert trailed.stop_premium == 120.0
    (gtt,) = world.client.exchange.gtts()
    assert gtt["status"] == "active" and gtt["condition"]["trigger_values"] == [120.0] and gtt["id"] == pos.gtt_id

    # the owner's websocket delivers a tick through the trail: the tenant's own monitor sells it, virtually
    ex.set_quote(f"NFO:{S.SUPERTREND_SYMBOL}", 119.0, bid=118.9, ask=119.1)
    await ticker_manager._make_broadcaster(UID)([{"instrument_token": token, "last_price": 119.0}])
    await settle(world)
    (exit_order,) = _orders(world, transaction_type="SELL")
    assert exit_order["order_type"] == "MARKET" and exit_order["status"] == "COMPLETE" and exit_order["average_price"] == 118.85
    assert world.client.exchange.gtt(gtt["id"])["status"] == "cancelled"            # the broker stop stepped aside first
    assert positions.get(SIM, S.SUPERTREND_SYMBOL).status == positions.CLOSED
    (trade,) = _sim_trades(machine)
    assert trade["status"] == "CLOSED" and trade["exit_reason"] == "TSL" and trade["exit_price"] == 118.85
    assert trade["gross_pnl"] == pytest.approx((118.85 - 180.30) * 75, abs=0.01) and trade["mode"] == "SIMULATION"
    _assert_books_balance(world, trade)
    assert _live_rows(machine) == []
    _assert_nothing_real_was_sent(machine)


# ───────────────────────────────────────────────────────────────────────────── a gap through the stop
@pytest.mark.asyncio
async def test_a_gap_through_the_virtual_stop_leaves_its_child_resting_and_the_engine_clears_it_and_sells(machine):
    world = await _sim_world(machine)
    assert sim_http(machine, "POST", "/kite/engine/config/production").status_code == 200
    assert sim_http(machine, "PATCH", "/kite/engine/config", json={
        "auto_execute": True, "supertrend_auto_execute": True, "stop_mode": "both", "engine_enabled": True}).status_code == 200
    service = machine.m("app.services.kite_engine.service")
    positions = machine.m("app.services.kite_engine.positions")
    ticker_manager = machine.m("app.services.exchanges.kite.ticker_manager")
    ex = machine.exchange
    ex.set_quote(f"NFO:{S.SUPERTREND_SYMBOL}", 180.0)
    ex.spot["NIFTY 50"] = 25000.0
    token = int(ex.instrument(S.SUPERTREND_SYMBOL, "NFO")["instrument_token"])
    await service._make_place_cb(world.client, SIM)(S.supertrend_row(token=token), None)
    await settle(world)
    pos = positions.get(SIM, S.SUPERTREND_SYMBOL)
    assert pos.status == positions.OPEN and pos.stop_premium == 93.6 and pos.gtt_id

    ex.set_quote(f"NFO:{S.SUPERTREND_SYMBOL}", 80.0, bid=79.9, ask=80.1)       # gaps far below the stop and its 91.7 limit
    await sim_pass(world)
    (gtt,) = world.client.exchange.gtts()
    assert gtt["status"] == "triggered"
    (child,) = _orders(world, transaction_type="SELL")
    assert child["status"] == "OPEN" and child["price"] == 91.7 and child["filled_quantity"] == 0      # resting: unfillable
    assert positions.get(SIM, S.SUPERTREND_SYMBOL).status == positions.OPEN                           # nothing has sold yet
    assert world.ledger.account_view(UID, _day(world))["open_positions"] == 1

    # inside the grace window the engine waits (the price may come back to the limit) and sells nothing twice...
    await ticker_manager._make_broadcaster(UID)([{"instrument_token": token, "last_price": 80.0}])
    await settle(world)
    assert [o["status"] for o in _orders(world, transaction_type="SELL")] == ["OPEN"]
    assert positions.get(SIM, S.SUPERTREND_SYMBOL).status == positions.OPEN
    # ...once it has passed, the next tick makes the tenant's monitor read the fired trigger, cancel its dead child
    # and sell at the bid
    machine.monkeypatch.setattr(machine.m("app.services.kite_engine.protective_stop"), "TRIGGER_CHILD_GRACE_S", 0.0)
    machine.monkeypatch.setattr(machine.m("app.services.kite_engine.monitor"), "_PROBE_TTL_S", 0.0)     # and its verdict cache
    await ticker_manager._make_broadcaster(UID)([{"instrument_token": token, "last_price": 80.0}])
    await settle(world)
    sells = _orders(world, transaction_type="SELL")
    assert [o["status"] for o in sells] == ["CANCELLED", "COMPLETE"], [(o["order_type"], o["status"]) for o in sells]
    assert sells[1]["order_type"] == "MARKET" and sells[1]["average_price"] == 79.85          # bid 79.9 less one tick
    assert positions.get(SIM, S.SUPERTREND_SYMBOL).status == positions.CLOSED
    (trade,) = _sim_trades(machine)
    assert trade["status"] == "CLOSED" and trade["exit_price"] == 79.85 and trade["mode"] == "SIMULATION"
    assert trade["gross_pnl"] == pytest.approx((79.85 - 180.30) * 75, abs=0.01)
    _assert_books_balance(world, trade)
    _assert_nothing_real_was_sent(machine)


# ───────────────────────────────────────────────────────────────────────────── fail closed
async def _owner_logs_out(world):
    accounts = world.machine.m("app.services.exchanges.kite.accounts")
    assert accounts.clear_session(UID, world.machine.account_id) is not None


async def _enter_supertrend(world, *, ltp: float = 180.0):
    machine = world.machine
    assert sim_http(machine, "POST", "/kite/engine/config/production").status_code == 200
    assert sim_http(machine, "PATCH", "/kite/engine/config", json={
        "auto_execute": True, "supertrend_auto_execute": True, "stop_mode": "both", "engine_enabled": True}).status_code == 200
    service = machine.m("app.services.kite_engine.service")
    machine.exchange.set_quote(f"NFO:{S.SUPERTREND_SYMBOL}", ltp)
    machine.exchange.spot["NIFTY 50"] = 25000.0
    token = int(machine.exchange.instrument(S.SUPERTREND_SYMBOL, "NFO")["instrument_token"])
    await service._make_place_cb(world.client, SIM)(S.supertrend_row(token=token), None)
    await settle(world)
    return token


@pytest.mark.asyncio
async def test_with_the_owners_session_gone_the_sim_tenant_places_nothing_and_invents_no_price(machine):
    world = await _sim_world(machine)
    await _owner_logs_out(world)
    await _enter_supertrend(world)
    assert world.client.exchange.orders() == [] and world.client.exchange.gtts() == []
    assert world.ledger.account_view(UID, _day(world))["trades_today"] == 0 and _sim_trades(machine) == []
    assert machine.m("app.services.kite_engine.positions").get(SIM, S.SUPERTREND_SYMBOL) is None
    from app.services.simulation.quotes import MarketDataUnavailable
    with pytest.raises(MarketDataUnavailable):
        await world.client.get_profile()                                  # the liveness probe pauses the tenant
    _assert_nothing_real_was_sent(machine)


@pytest.mark.asyncio
async def test_with_the_owners_session_gone_a_resting_virtual_stop_waits_instead_of_firing_on_an_invented_price(machine):
    world = await _sim_world(machine)
    token = await _enter_supertrend(world)
    (gtt,) = world.client.exchange.gtts()
    await _owner_logs_out(world)
    machine.exchange.set_quote(f"NFO:{S.SUPERTREND_SYMBOL}", 50.0, bid=49.9, ask=50.1)   # the market crashes; nobody can see it
    report = await sim_pass(world)
    assert report["owners"][0]["unpriced"] >= 1 and report["owners"][0]["fired"] == 0
    assert world.client.exchange.gtt(gtt["id"])["status"] == "active"
    assert [o["status"] for o in _orders(world, transaction_type="SELL")] == []
    assert machine.m("app.services.kite_engine.positions").get(SIM, S.SUPERTREND_SYMBOL).status == "open"
    del token
    _assert_nothing_real_was_sent(machine)


# ───────────────────────────────────────────────────────────────────────────── live and simulation never mix
@pytest.mark.asyncio
async def test_a_live_entry_and_a_sim_entry_on_the_same_contract_stay_in_their_own_books(machine):
    world = await _sim_world(machine)
    S.configure_supertrend(machine)                                           # the LIVE tenant's AUTO
    live = await S.drive_supertrend(machine)
    assert live.ordered and live.protected, live
    positions = machine.m("app.services.kite_engine.positions")
    live_pos = positions.get(UID, S.SUPERTREND_SYMBOL)
    live_orders_before = list(machine.exchange.order_posts)
    live_gtts_before = [g["id"] for g in machine.exchange.active_gtts()]
    assert len(live_orders_before) == 1 and len(live_gtts_before) == 1 and live_pos.account_id == machine.account_id

    token = await _enter_supertrend(world, ltp=180.0)
    sim_pos = positions.get(SIM, S.SUPERTREND_SYMBOL)
    assert sim_pos.status == positions.OPEN and sim_pos.account_id == world.client._account_id != live_pos.account_id
    assert machine.exchange.order_posts == live_orders_before                  # the sim entry sent the broker nothing
    assert [g["id"] for g in machine.exchange.active_gtts()] == live_gtts_before
    assert machine.exchange.net_quantity(S.SUPERTREND_SYMBOL) == 75           # the broker holds only the LIVE 75
    assert world.ledger.account_view(UID, _day(world))["open_positions"] == 1

    # the sim stop fires; the live position, its order and its stop are untouched
    machine.exchange.set_quote(f"NFO:{S.SUPERTREND_SYMBOL}", 93.2, bid=93.0, ask=93.4)
    await sim_pass(world)
    assert positions.get(SIM, S.SUPERTREND_SYMBOL).status == positions.CLOSED
    live_pos = positions.get(UID, S.SUPERTREND_SYMBOL)
    assert live_pos.status == positions.OPEN and live_pos.gtt_id == live_gtts_before[0]
    assert [g["id"] for g in machine.exchange.active_gtts()] == live_gtts_before
    assert machine.exchange.net_quantity(S.SUPERTREND_SYMBOL) == 75 and len(machine.exchange.order_posts) == 1

    rows = {t["mode"]: t for t in _journal(machine).list_trades([UID, SIM])["items"]}
    assert set(rows) == {"LIVE", "SIMULATION"} and rows["LIVE"]["status"] == "OPEN" and rows["SIMULATION"]["status"] == "CLOSED"
    assert rows["LIVE"]["uid"] == UID and rows["SIMULATION"]["uid"] == SIM
    assert rows["LIVE"]["account_id"] == machine.account_id and rows["SIMULATION"]["account_id"] == world.client._account_id
    del token


# ───────────────────────────────────────────────────────────────────────────── restart
@pytest.mark.asyncio
async def test_the_virtual_book_survives_a_restart_and_the_restarted_process_still_stops_the_position_out(machine):
    world = await _sim_world(machine)
    await _enter_supertrend(world)
    positions = machine.m("app.services.kite_engine.positions")
    before = positions.get(SIM, S.SUPERTREND_SYMBOL)
    assert before.status == positions.OPEN
    held = world.ledger.account_view(UID, _day(world))

    await machine.restart()                                                    # a new process on the same files
    await machine.run_live_runtime_once()
    machine.m("app.services.simulation.tenant").boot_tenant(UID)
    accounts = machine.m("app.services.exchanges.kite.accounts")
    client = await accounts.acquire_client(accounts.sim_account_for(UID))
    world = SimpleNamespace(machine=machine, client=client, ex=machine.exchange, ledger=client.ledger)
    positions = machine.m("app.services.kite_engine.positions")
    after = positions.get(SIM, S.SUPERTREND_SYMBOL)
    assert after.status == positions.OPEN and after.gtt_id == before.gtt_id and after.fill_price == before.fill_price
    assert world.ledger.account_view(UID, _day(world)) == held                 # cash, margin, positions and stops: the same rows
    (gtt,) = client.exchange.gtts()
    assert gtt["status"] == "active" and gtt["id"] == before.gtt_id
    recovered = await machine.m("app.services.simulation.tenant").recover_tenant(UID)
    assert recovered.get("status") != "no_account"

    machine.exchange.set_quote(f"NFO:{S.SUPERTREND_SYMBOL}", 93.2, bid=93.0, ask=93.4)
    await sim_pass(world)
    assert positions.get(SIM, S.SUPERTREND_SYMBOL).status == positions.CLOSED
    (trade,) = _sim_trades(machine)
    assert trade["status"] == "CLOSED" and trade["exit_reason"] == "SL" and trade["exit_price"] == 92.95
    _assert_books_balance(world, trade)
    _assert_nothing_real_was_sent(machine)


# ───────────────────────────────────────────────────────────────────────────── the real scan, on delegated market data
@pytest.mark.asyncio
async def test_the_sim_tenants_own_scan_reads_real_candles_and_chains_and_enters_virtually(machine):
    """Candles in, virtual order out: the production scan (instruments, universe, 1H candles, regime, option chain,
    place callback) runs for the virtual tenant on market data delegated to the owner's real client."""
    world = await _sim_world(machine)
    assert sim_http(machine, "POST", "/kite/engine/config/production").status_code == 200
    assert sim_http(machine, "PATCH", "/kite/engine/config", json={
        "auto_execute": True, "supertrend_auto_execute": True, "stop_mode": "both", "engine_enabled": True}).status_code == 200
    service = machine.m("app.services.kite_engine.service")
    positions = machine.m("app.services.kite_engine.positions")
    state = machine.m("app.services.kite_engine.state")
    S.serve_fresh_long_index_signal(machine)
    await service.scan_user(world.client, SIM)
    await settle(world)
    entries = _orders(world, transaction_type="BUY")
    activity = [f"{e.kind}: {e.message}" for e in state.activity(SIM)][-6:]
    assert len(entries) == 1, activity
    symbol = entries[0]["tradingsymbol"]
    assert symbol.startswith("NIFTY26OCT") and entries[0]["status"] == "COMPLETE"
    pos = positions.get(SIM, symbol)
    assert pos.status == positions.OPEN and pos.gtt_id and pos.account_id == world.client._account_id, activity
    assert pos.expiry == "2026-10-27"                                      # the monthly, not the weekly the square-off would sell
    (trade,) = _sim_trades(machine)
    assert trade["mode"] == "SIMULATION" and trade["strategy"] == "supertrend" and trade["status"] == "OPEN"
    assert _live_rows(machine) == []
    # every market-data read went to the OWNER's real client (the fake broker saw only GETs)
    assert {r[0] for r in machine.exchange.requests if not r[1].startswith("/session")} <= {"GET"}
    _assert_nothing_real_was_sent(machine)
