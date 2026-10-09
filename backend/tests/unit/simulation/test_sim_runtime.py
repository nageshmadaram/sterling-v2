"""The Simulation loop and its public API: passes, snapshots, resilience, idempotent start/stop."""
import asyncio

import pytest

from app.services.simulation import sim_client as sc
from app.services.simulation import sim_runtime as rt
from app.services.simulation.quotes import QuoteSource
from app.services.simulation.virtual_exchange import VirtualExchange
from tests.unit.simulation.sim_fakes import OPT, FakeReal, quote_source
from tests.unit.simulation.test_sim_virtual_exchange import SYM, lim, mkt, oco

TODAY = "2026-10-12"


@pytest.fixture(autouse=True)
def _fresh_runtime():
    rt.forget_exchanges()
    rt._state.update(passes=0, last_pass_ms=0, last_error="", last_report={})
    yield
    rt.forget_exchanges()


def build_world(ledger, clock, owners=("default",)):
    """One FakeReal + VirtualExchange per owner, sharing a ledger."""
    reals = {o: FakeReal(owner=o, clock=clock) for o in owners}
    exchanges = {o: VirtualExchange(o, ledger, quote_source(reals[o], clock), clock=clock) for o in owners}
    return reals, exchanges


def in_session(_now):
    return True


def after_hours(_now):
    return False


# ── status and operator actions ───────────────────────────────────────────────────────────
async def test_status_reports_capital_cash_equity_and_todays_pnl(ledger, clock):
    reals, ex = build_world(ledger, clock)
    s = rt.status("default", ledger=ledger, day=TODAY)
    assert (s["capital"], s["cash"], s["equity"], s["realised_today"], s["unrealised"]) == (1e6, 1e6, 1e6, 0.0, 0.0)
    assert (s["mode"], s["uid"], s["running"]) == ("SIMULATION", "sim:default", False)
    assert s["account_id"].startswith("SIM-") and s["settings"]["slippage_ticks"] == 1
    await mkt(ex["default"], "BUY")
    reals["default"].set_quote(OPT, 110.0)
    await ex["default"].evaluate()
    await mkt(ex["default"], "SELL", qty=75)
    s = rt.status("sim:default", ledger=ledger, day=TODAY)
    assert s["realised_today"] == 735.0 and s["unrealised"] == 0.0 and s["trades_today"] == 2
    assert s["net_realised_today"] == round(735.0 - s["charges_today"], 2) and s["equity"] == s["cash"]


async def test_reset_and_set_capital_are_the_operator_actions(ledger, clock):
    reals, ex = build_world(ledger, clock)
    await mkt(ex["default"], "BUY")
    await oco(ex["default"])
    assert rt.set_capital("default", 2_500_000.0, ledger=ledger)["capital"] == 2_500_000.0
    account = rt.reset_owner("sim:default", ledger=ledger)
    assert account["cash"] == account["capital"] == 2_500_000.0
    s = rt.status("default", ledger=ledger, day=TODAY)
    assert (s["open_positions"], s["active_gtts"], s["trades_today"]) == (0, 0, 0)
    assert rt.reset_owner("default", capital=300_000.0, ledger=ledger)["capital"] == 300_000.0


def test_get_sim_client_is_re_exported():
    assert rt.get_sim_client is sc.get_sim_client and rt.SimulatedKiteClient is sc.SimulatedKiteClient


# ── one pass ──────────────────────────────────────────────────────────────────────────────
async def test_a_pass_fires_gtts_and_fills_resting_orders_for_every_owner_with_open_state(ledger, clock):
    reals, ex = build_world(ledger, clock, ("alice", "bob", "idle"))
    for o in ("alice", "bob"):
        await mkt(ex[o], "BUY")
    gid = (await oco(ex["alice"]))["trigger_id"]
    resting = (await lim(ex["bob"], "BUY", 90.0))["order_id"]
    ledger.ensure_account("idle")
    for o in ("alice", "bob"):
        reals[o].set_quote(OPT, 80.0, bid=79.95, ask=80.05)
    report = await rt.run_pass(ledger=ledger, now=clock(), session_open=in_session, exchange_for=lambda o: ex[o])
    assert report["session"] is True and report["errors"] == {}
    assert sorted(r["owner"] for r in report["owners"]) == ["alice", "bob"]          # 'idle' has no open state
    assert ex["alice"].gtt(gid)["status"] == "triggered"
    assert next(o for o in ex["bob"].orders() if o["order_id"] == resting)["status"] == "COMPLETE"


async def test_marks_move_with_the_real_price_so_equity_follows_the_market(ledger, clock):
    reals, ex = build_world(ledger, clock)
    await mkt(ex["default"], "BUY")
    reals["default"].set_quote(OPT, 130.0)
    await rt.run_pass(ledger=ledger, now=clock(), session_open=in_session, exchange_for=lambda o: ex[o])
    s = rt.status("default", ledger=ledger, day=TODAY)
    assert s["unrealised"] == round((130.0 - 100.1) * 75, 2) and s["equity"] == round(s["cash"] + s["unrealised"], 2)


async def test_outside_the_session_nothing_is_evaluated_but_day_orders_still_lapse(ledger, clock):
    reals, ex = build_world(ledger, clock)
    await mkt(ex["default"], "BUY")
    gid = (await oco(ex["default"]))["trigger_id"]
    resting = (await lim(ex["default"], "BUY", 90.0))["order_id"]
    reals["default"].set_quote(OPT, 50.0, bid=49.95, ask=50.05)
    report = await rt.run_pass(ledger=ledger, now=clock(), session_open=after_hours, exchange_for=lambda o: ex[o])
    assert report["session"] is False and report["owners"][0]["expired"] == 0
    assert ex["default"].gtt(gid)["status"] == "active"                  # the market is shut: nothing fires
    clock.advance(hours=5, minutes=30)
    report = await rt.run_pass(ledger=ledger, now=clock(), session_open=after_hours, exchange_for=lambda o: ex[o])
    assert report["owners"][0]["expired"] == 1
    assert next(o for o in ex["default"].orders() if o["order_id"] == resting)["status"] == "CANCELLED"
    assert ex["default"].gtt(gid)["status"] == "active"                  # a GTT outlives the session


async def test_force_evaluates_outside_the_session(ledger, clock):
    reals, ex = build_world(ledger, clock)
    await mkt(ex["default"], "BUY")
    gid = (await oco(ex["default"]))["trigger_id"]
    reals["default"].set_quote(OPT, 80.0, bid=79.95, ask=80.05)
    await rt.run_pass(ledger=ledger, now=clock(), session_open=after_hours, force=True, exchange_for=lambda o: ex[o])
    assert ex["default"].gtt(gid)["status"] == "triggered"


async def test_an_owner_with_no_live_session_is_simply_not_priced(ledger, clock):
    def no_session():
        raise RuntimeError("the owner's broker session is not logged in")
    ex = VirtualExchange("default", ledger, QuoteSource(no_session, owner="default", use_websocket=False), clock=clock)
    real = FakeReal(owner="default", clock=clock)
    seed = VirtualExchange("default", ledger, quote_source(real, clock), clock=clock)
    await mkt(seed, "BUY")
    gid = (await oco(seed))["trigger_id"]
    real.set_quote(OPT, 50.0, bid=49.95, ask=50.05)                        # the market has crashed, but we cannot see it
    report = await rt.run_pass(ledger=ledger, now=clock(), session_open=in_session, exchange_for=lambda o: ex)
    assert report["errors"] == {} and report["owners"][0]["unpriced"] >= 1
    assert seed.gtt(gid)["status"] == "active" and len(seed.trades()) == 1   # no price, no fill, no invention


async def test_one_owners_failure_never_stops_the_others_and_the_pass_never_raises(ledger, clock):
    reals, ex = build_world(ledger, clock, ("good", "bad"))
    for o in ("good", "bad"):
        await mkt(ex[o], "BUY")
    gid = (await oco(ex["good"]))["trigger_id"]
    reals["good"].set_quote(OPT, 80.0, bid=79.95, ask=80.05)

    def build(owner):
        if owner == "bad":
            raise RuntimeError("cannot build this owner's exchange")
        return ex[owner]
    report = await rt.run_pass(ledger=ledger, now=clock(), session_open=in_session, exchange_for=build)
    assert "bad" in report["errors"] and "cannot build" in report["errors"]["bad"]
    assert ex["good"].gtt(gid)["status"] == "triggered"
    assert "cannot build" in rt.status("good", ledger=ledger)["last_error"]
    assert (await rt.run_pass(ledger=ledger, owners=["bad"], now=clock(), session_open=lambda n: 1 / 0,
                              exchange_for=build))["errors"]                  # a broken session check is contained too


async def test_an_equity_snapshot_is_written_once_a_minute_for_every_account(ledger, clock):
    reals, ex = build_world(ledger, clock, ("alice", "flat"))
    await mkt(ex["alice"], "BUY")
    ledger.ensure_account("flat")
    kwargs = dict(ledger=ledger, session_open=in_session, exchange_for=lambda o: ex[o])
    first = await rt.run_pass(now=clock(), **kwargs)
    again = await rt.run_pass(now=clock(), **kwargs)                       # same minute: no second point
    assert (first["snapshots"], again["snapshots"]) == (2, 0)
    clock.advance(seconds=61)
    reals["alice"].set_quote(OPT, 110.0)
    third = await rt.run_pass(now=clock(), **kwargs)
    assert third["snapshots"] == 2
    rows = ledger.snapshots("alice")
    assert len(rows) == 2 and rows[1]["ts_ms"] - rows[0]["ts_ms"] == 60_000
    assert rows[1]["unrealised"] == round((110.0 - 100.1) * 75, 2) and rows[1]["equity"] == round(rows[1]["cash"] + rows[1]["unrealised"], 2)
    assert [r["unrealised"] for r in ledger.snapshots("flat")] == [0.0, 0.0] and rows[0]["positions"] == 1


async def test_no_snapshots_are_written_outside_the_session(ledger, clock):
    reals, ex = build_world(ledger, clock)
    await mkt(ex["default"], "BUY")
    report = await rt.run_pass(ledger=ledger, now=clock(), session_open=after_hours, exchange_for=lambda o: ex[o])
    assert report["snapshots"] == 0 and ledger.snapshots("default") == []


async def test_fills_made_by_the_loop_reach_the_tenants_order_handlers(ledger, clock, monkeypatch):
    from app.services.exchanges.kite import ticker_manager
    seen = []

    async def recorder(user_id, order, **kw):
        seen.append((user_id, order["status"], order["tag"]))
    monkeypatch.setattr(ticker_manager, "broadcast_order_update", recorder)
    real = FakeReal(owner="default", clock=clock)
    ex = rt.runtime_exchange("default", ledger=ledger, provider=lambda: real, clock=clock)
    ex._postback_delay_s = 0
    ex.quotes.ttl_s = 0
    ex.quotes._tick_reader = None
    assert rt.runtime_exchange("sim:default") is ex                      # built once per owner
    await mkt(ex, "BUY")
    await oco(ex)
    seen.clear()
    real.set_quote(OPT, 89.0, bid=88.95, ask=89.0)
    await rt.run_pass(ledger=ledger, now=clock(), session_open=in_session, exchange_for=lambda o: ex)
    await asyncio.sleep(0.05)
    assert [s for s in seen if s[2] == "gtt"] == [("sim:default", "OPEN", "gtt"), ("sim:default", "COMPLETE", "gtt")]


# ── the loop ──────────────────────────────────────────────────────────────────────────────
async def test_start_is_idempotent_runs_a_pass_at_once_and_stop_is_clean(monkeypatch):
    calls = []

    async def fake_pass(**kw):
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("a pass blew up")                          # the loop must survive it
        return {}

    async def quick_sleep(seconds):
        await asyncio.sleep(0.001)

    monkeypatch.setattr(rt, "run_pass", fake_pass)
    monkeypatch.setattr(rt, "_sleep", quick_sleep)
    h1 = rt.start_sim_runtime(interval_s=0.001)                           # sync style, like the other loops in main.py
    h2 = rt.start_sim_runtime(interval_s=0.001)
    t1 = await h1                                                         # awaiting the handle never blocks
    assert h1 is h2 and rt.is_running() and not t1.done() and await h2 is t1
    await asyncio.sleep(0.05)
    assert len(calls) >= 3                                                # past the failing pass
    await rt.stop_sim_runtime()
    assert not rt.is_running() and t1.done()
    n = len(calls)
    await asyncio.sleep(0.02)
    assert len(calls) == n
    await rt.stop_sim_runtime()                                           # stopping twice is fine
    t3 = await rt.start_sim_runtime(interval_s=0.001)                     # `await start()` style: a fresh running task
    assert t3 is not t1 and rt.is_running()
    await rt.stop_sim_runtime()


async def test_the_first_pass_happens_before_any_sleep(monkeypatch):
    order = []

    async def fake_pass(**kw):
        order.append("pass")
        return {}

    async def fake_sleep(seconds):
        order.append("sleep")
        await asyncio.sleep(0.001)
    monkeypatch.setattr(rt, "run_pass", fake_pass)
    monkeypatch.setattr(rt, "_sleep", fake_sleep)
    rt.start_sim_runtime(interval_s=0.001)
    await asyncio.sleep(0.02)
    await rt.stop_sim_runtime()
    assert order[:2] == ["pass", "sleep"]


def test_the_default_provider_is_the_owners_live_client_provider(monkeypatch):
    from app.services.exchanges.kite import accounts
    monkeypatch.setattr(accounts, "owner_live_client_provider", lambda owner: ("provider-for", owner), raising=False)
    assert rt.owner_provider("sim:default") == ("provider-for", "default")


async def test_the_loop_can_also_be_run_as_a_plain_coroutine(monkeypatch):
    calls = []

    async def fake_pass(**kw):
        calls.append(1)
        return {}

    async def quick_sleep(seconds):
        await asyncio.sleep(0.001)
    monkeypatch.setattr(rt, "run_pass", fake_pass)
    monkeypatch.setattr(rt, "_sleep", quick_sleep)
    task = asyncio.create_task(rt.run_sim_runtime(0.001))                 # how a lifespan may start it
    await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(calls) >= 2


# ── tick-driven evaluation ────────────────────────────────────────────────────────────────
async def test_a_tick_that_crosses_a_trigger_runs_a_pass_at_once_and_only_one(ledger, clock, monkeypatch):
    reals, ex = build_world(ledger, clock)
    await mkt(ex["default"], "BUY")
    await oco(ex["default"])                                              # stop 90 / target 120 on token 1001
    rt.refresh_watch("default", ledger=ledger)
    passes = []
    gate = asyncio.Event()

    async def fake_pass(**kw):
        passes.append(kw)
        await gate.wait()
        return {}
    monkeypatch.setattr(rt, "run_pass", fake_pass)
    tick = lambda ltp, token=1001: {"instrument_token": token, "last_price": ltp}          # noqa: E731
    assert rt.on_ticks("default", [tick(100.0), tick(50.0, token=999)]) is False          # inside the band / unwatched
    assert rt.on_ticks("nobody", [tick(1.0)]) is False
    assert rt.on_ticks("sim:default", [tick(89.9)]) is True                               # crosses the 90 stop
    assert rt.on_ticks("default", [tick(80.0)]) is False                                  # one pass in flight: no storm
    await asyncio.sleep(0)
    assert passes == [{"owners": ["default"]}]
    gate.set()
    await asyncio.sleep(0.01)
    assert rt.on_ticks("default", [tick(121.0)]) is True                                  # free again; the target leg too
    gate.set()
    await asyncio.sleep(0.01)


async def test_a_tick_pass_drops_the_cached_book_so_the_new_price_is_seen(ledger, clock, monkeypatch):
    monkeypatch.setattr(rt, "in_session", in_session)                     # ticks only flow in session; do not read the wall clock
    real = FakeReal(owner="default", clock=clock)
    ex = rt.runtime_exchange("default", ledger=ledger, provider=lambda: real, clock=clock)
    ex.quotes.ttl_s = 60.0                                                # a long cache that would hide the move
    ex.quotes._tick_reader = None
    await mkt(ex, "BUY")
    gid = (await oco(ex))["trigger_id"]
    real.set_quote(OPT, 85.0, bid=84.95, ask=85.05)
    rt.refresh_watch("default", ledger=ledger)
    assert rt.on_ticks("default", [{"instrument_token": 1001, "last_price": 85.0}]) is True
    await asyncio.sleep(0.05)
    assert ex.gtt(gid)["status"] == "triggered"


async def test_the_watch_list_follows_the_books(ledger, clock):
    reals, ex = build_world(ledger, clock)
    assert rt.refresh_watch("default", ledger=ledger) is None and "default" not in rt._watch
    gid = (await oco(ex["default"]))["trigger_id"]
    rt.refresh_watch("default", ledger=ledger)
    assert sorted(rt._watch["default"][1001]) == [("down", 90.0), ("up", 120.0)]
    await ex["default"].delete_gtt(gid)
    rt.refresh_watch("default", ledger=ledger)
    assert "default" not in rt._watch
    await ex["default"].place_order(exchange="NFO", tradingsymbol=SYM, transaction_type="BUY", quantity=75,
                                    product="NRML", order_type="SL-M", trigger_price=105.0)
    rt.refresh_watch("default", ledger=ledger)
    assert rt._watch["default"][1001] == [("up", 105.0)]


def test_the_websocket_reader_serves_only_a_live_fresh_stream(monkeypatch):
    import time
    from app.services.exchanges.kite import ticker_manager
    from app.services.simulation.quotes import default_tick_reader

    class Ticker:
        is_active, connected = True, True
        last = int(time.time() * 1000)

        def status(self):
            return {"last_tick_ms": self.last}

        def snapshot(self):
            return [{"instrument_token": 1001, "last_price": 1.0}]
    ticker = Ticker()
    monkeypatch.setattr(ticker_manager, "_tickers", {"default": ticker})
    read = default_tick_reader(max_age_s=3.0)
    assert read("default") == {1001: {"instrument_token": 1001, "last_price": 1.0}}
    assert read("sim:default") is not None and read("someone-else") is None            # keyed by the OWNER's own stream
    ticker.last = int(time.time() * 1000) - 10_000
    assert read("default") is None                                                       # stale stream: REST instead
    ticker.last, ticker.connected = int(time.time() * 1000), False
    assert read("default") is None
