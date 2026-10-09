"""The safety boundary of SimulatedKiteClient: it can never reach a real broker write.

Three layers of proof:
* the public surface of the real KiteClient is partitioned (virtual / allowlisted read / refused) and the
  partition is pinned, so a method added to KiteClient fails here until it is classified;
* every refused or unknown name raises SimulationBoundaryError and never touches the real client;
* a socket-level test drives a full trading session through a SimulatedKiteClient whose "real" client is
  the production KiteClient over a recording transport, and proves that only GETs of market data were issued.
"""
import inspect

import pytest

from app.services.exchanges.kite import constants as K
from app.services.exchanges.kite.client import KiteClient
from app.services.simulation import sim_client as sc
from app.services.simulation.identity import sim_account_id, sim_uid
from app.services.simulation.quotes import MarketDataUnavailable
from app.services.snapback_family_mode import (
    INTENT_CANCEL, INTENT_INCREASE, INTENT_PROTECT, INTENT_REDUCE, ReduceBound, canonical_broker_capability,
)
from tests.unit.simulation.sim_fakes import FakeReal

SYM = "NIFTY2611325000CE"


@pytest.fixture
def sim(ledger, real, clock, quotes):
    return sc.get_sim_client("default", lambda: real, ledger=ledger, clock=clock, quotes=quotes, postbacks=False)


async def buy(sim, qty=75):
    with canonical_broker_capability("entry", intent=INTENT_INCREASE):
        return await sim.place_order_option(SYM, "buy", qty, exchange="NFO", tag="t1")


# ── the partition ─────────────────────────────────────────────────────────────────────────
def test_every_public_name_of_the_real_client_is_classified_exactly_once():
    public = set(sc.public_surface(KiteClient))
    sets = [sc.VIRTUAL, sc.READ_ALLOWLIST, sc.REFUSED]
    assert set().union(*sets) == public, (
        "KiteClient's public surface changed: classify the new method as VIRTUAL, READ_ALLOWLIST or REFUSED "
        f"(unclassified: {sorted(public - set().union(*sets))}, gone: {sorted(set().union(*sets) - public)})")
    assert sum(len(s) for s in sets) == len(public)                     # disjoint


def test_the_read_allowlist_holds_reads_only():
    writes = ("place", "cancel", "modify", "delete", "create", "convert", "generate", "invalidate", "renew",
              "initiate", "reduce", "replace")
    assert not [n for n in sc.READ_ALLOWLIST if n.startswith(writes)]


def test_virtual_methods_are_implemented_here_not_inherited_from_the_real_client():
    for name in sorted(sc.VIRTUAL):
        mine = inspect.getattr_static(sc.SimulatedKiteClient, name)
        assert mine is not inspect.getattr_static(KiteClient, name), name
        assert name in vars(sc.SimulatedKiteClient), name


def test_the_simulated_client_is_not_a_kite_client_and_has_no_http_plumbing():
    assert not issubclass(sc.SimulatedKiteClient, KiteClient)
    for name in ("_auth_get", "_auth_post", "_auth_put", "_auth_delete", "_auth_request", "_auth_post_json",
                 "_get_client", "_handle", "_client", "_api_key", "_api_secret", "_base", "_fetch_instruments_csv"):
        assert name not in vars(sc.SimulatedKiteClient), name


def test_virtual_signatures_match_the_real_client_so_callers_cannot_tell_them_apart():
    def shape(fn):
        return [(p.name, p.kind, p.default) for p in inspect.signature(fn).parameters.values()]
    for name in sorted(sc.VIRTUAL - {"access_token"}):
        real_fn, mine = getattr(KiteClient, name), getattr(sc.SimulatedKiteClient, name)
        if name in ("get_mf_order", "get_mf_sip", "get_alert", "get_alert_history"):
            continue                                                    # parameter names differ only in the stub
        assert shape(mine) == shape(real_fn), name
    assert shape(sc.SimulatedKiteClient._place) == shape(KiteClient._place)


# ── identity ──────────────────────────────────────────────────────────────────────────────
def test_identity_declares_no_real_money_and_carries_no_credentials(sim):
    assert sim._is_paper is True and sim._is_simulated is True and sc.is_simulated_client(sim)
    assert sim._account_id == sim.kite_user_id == sim_account_id("default")
    assert sim._owner_user_id == sim_uid("default")
    assert sim.access_token == "" and sim._access_token == ""
    assert sc.get_sim_client("sim:default", lambda: None, postbacks=False, ledger=sim.ledger)._account_id == sim._account_id


# ── refused and unknown names ─────────────────────────────────────────────────────────────
@pytest.mark.parametrize("name", sorted(sc.REFUSED))
async def test_refused_methods_raise_the_boundary_error_and_never_touch_the_real_client(sim, real, name):
    with pytest.raises(sc.SimulationBoundaryError, match=name):
        result = getattr(sim, name)("x", y=1)
        if inspect.isawaitable(result):
            await result
    assert real.calls == [] and real.writes == []


@pytest.mark.parametrize("name", ["_auth_post", "_auth_get", "_get_client", "_client", "_api_key", "_handle",
                                  "totally_new_method", "_base"])
def test_unknown_attributes_raise_the_boundary_error(sim, name):
    with pytest.raises(sc.SimulationBoundaryError):
        getattr(sim, name)
    assert not hasattr(sc.SimulatedKiteClient, name)


def test_probing_an_unknown_attribute_behaves_like_a_missing_attribute_on_a_real_client(sim):
    """execution_service does getattr(client, "_account_id", getattr(client, "account_id", None)) with the
    fallback evaluated eagerly, so an unknown probe must read as 'absent', not blow up."""
    assert getattr(sim, "account_id", None) == sim._account_id          # answers the canonical identity probe
    assert getattr(sim, "no_such_probe", "dflt") == "dflt" and not hasattr(sim, "no_such_probe")
    with pytest.raises(AttributeError):
        sim.no_such_probe


def test_dunder_lookups_stay_ordinary_attribute_errors(sim):
    with pytest.raises(AttributeError):
        sim.__wrapped__
    assert getattr(sim, "__nope__", "default") == "default"


def test_the_boundary_error_reads_as_not_sent_to_the_execution_path():
    from app.services.execution_service import _never_reached_the_broker
    from app.services.kite_engine.protective_stop import is_definitive_refusal
    exc = sc.SimulationRefused("x")
    assert isinstance(exc, sc.SimulationBoundaryError) and isinstance(exc, PermissionError)
    assert _never_reached_the_broker(exc) and is_definitive_refusal(exc)
    assert _never_reached_the_broker(MarketDataUnavailable("no price"))


# ── allowlisted reads are delegated to the owner's real client ─────────────────────────────
_READ_ARGS = {
    "get_quote": (["NFO:" + SYM],), "get_ltp": (["NFO:" + SYM],), "get_ohlc": (["NFO:" + SYM],),
    "get_historical": (1001, "minute", "2026-10-12 09:15:00", "2026-10-12 10:15:00"),
    "get_candles": (object(), "5m", 10), "get_option_chain": (object(),), "get_index_price": (object(),),
    "get_spot_price": (object(),), "search_instruments": ("NIFTY", "NFO", 5), "search_mf_instruments": ("x", 5),
    "instrument_lot_sizes": (["NFO:" + SYM],), "instrument_expiries": (["NFO:" + SYM],),
    "resolve_token": (SYM, "NFO"), "get_product_id": ("NFO:" + SYM,),
}


class _Recorder:
    _is_paper = False
    _is_simulated = False
    _owner_user_id = "default"

    def __init__(self):
        self.seen = []

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)

        async def call(*args, **kwargs):
            self.seen.append((name, args, kwargs))
            return {"via": name}
        return call


def test_every_allowlisted_read_has_a_test_argument_set():
    assert set(_READ_ARGS) == set(sc.READ_ALLOWLIST)


@pytest.mark.parametrize("name", sorted(sc.READ_ALLOWLIST))
async def test_allowlisted_reads_go_to_the_owners_real_client_unchanged(ledger, name):
    rec = _Recorder()
    sim = sc.get_sim_client("default", lambda: rec, ledger=ledger, postbacks=False)
    args = _READ_ARGS[name]
    assert await getattr(sim, name)(*args) == {"via": name}
    assert rec.seen == [(name, args, {})]


async def test_a_read_with_no_real_session_fails_closed(ledger):
    def nobody():
        raise RuntimeError("not logged in")
    sim = sc.get_sim_client("default", nobody, ledger=ledger, postbacks=False)
    with pytest.raises(MarketDataUnavailable):
        await sim.get_ltp(["NFO:" + SYM])
    with pytest.raises(MarketDataUnavailable):
        await sim.get_profile()
    assert await sim.test_connection() is False and await sim.ping() is True


async def test_a_simulated_provider_or_a_foreign_owners_client_is_refused_as_a_source(ledger, sim):
    other = sc.get_sim_client("default", lambda: None, ledger=ledger, postbacks=False)
    looped = sc.get_sim_client("default", lambda: other, ledger=ledger, postbacks=False)
    with pytest.raises(MarketDataUnavailable, match="simulated"):
        await looped.get_ltp(["NFO:" + SYM])
    foreign = sc.get_sim_client("default", lambda: FakeReal(owner="mallory"), ledger=ledger, postbacks=False)
    with pytest.raises(MarketDataUnavailable, match="different owner"):
        await foreign.get_quote(["NFO:" + SYM])


async def test_the_instrument_master_is_reachable_read_only_through_the_proxy(sim):
    rows = await sim._instruments.load("NFO")
    assert {r["tradingsymbol"] for r in rows} >= {SYM}
    assert not hasattr(sim._instruments, "place_order")                   # no write surface exists on the proxy


# ── virtual writes follow the live capability rules ───────────────────────────────────────
async def test_an_entry_without_the_canonical_capability_is_refused_exactly_as_live_refuses_it(sim, real):
    with pytest.raises(PermissionError, match="canonical execution"):
        await sim.place_order_option(SYM, "buy", 75, exchange="NFO")
    assert sim.exchange.orders() == []


async def test_the_capability_intent_bounds_what_a_virtual_order_may_do(sim):
    await buy(sim)
    bound = ReduceBound.closing(exchange="NFO", symbol=SYM, side="SELL", quantity=75)
    with canonical_broker_capability("exit", intent=INTENT_REDUCE, bound=bound):
        with pytest.raises(PermissionError, match="not what its reduce capability declared"):
            await sim.place_order_option(SYM, "sell", 150, exchange="NFO")         # more than the bound
        with pytest.raises(PermissionError, match="closing side"):
            await sim.place_order_option(SYM, "buy", 75, exchange="NFO")           # the wrong side
        res = await sim.place_order_option(SYM, "sell", 75, exchange="NFO", tag="exit1")
    assert res["order_id"] and (await sim.get_positions_raw())["net"][0]["quantity"] == 0


async def test_modify_always_needs_an_increase_capability_and_cancel_only_under_family_mode(sim, monkeypatch):
    oid = None
    with canonical_broker_capability("e", intent=INTENT_INCREASE):
        oid = (await sim.place_order_option(SYM, "buy", 75, exchange="NFO", order_type="limit", limit_price=90.0))["order_id"]
    with pytest.raises(PermissionError):
        await sim.modify_order(oid, price=91.0)                       # always guarded, like the live transport
    with canonical_broker_capability("m", intent=INTENT_INCREASE):
        await sim.modify_order(oid, price=91.0)
    monkeypatch.setenv("STERLING_FAMILY_MODE", "1")
    with pytest.raises(PermissionError):
        await sim.cancel_order(oid)
    with canonical_broker_capability("c", intent=INTENT_CANCEL):
        assert await sim.cancel_order(oid) == {"order_id": oid}
    monkeypatch.delenv("STERLING_FAMILY_MODE")
    assert (await sim.get_orders())[0]["status"] == "CANCELLED"


async def test_gtt_writes_work_without_a_capability_in_a_normal_deployment_and_need_one_in_family_mode(sim, monkeypatch):
    from tests.unit.simulation.test_sim_virtual_exchange import leg
    kw = dict(trigger_type="single", tradingsymbol=SYM, exchange="NFO", last_price=100.0, trigger_values=[90.0],
              orders=[leg(88.2)])
    tid = (await sim.place_gtt(**kw))["trigger_id"]                  # no PermissionError for a non-family deployment
    monkeypatch.setenv("STERLING_FAMILY_MODE", "1")
    with pytest.raises(PermissionError):
        await sim.modify_gtt(tid, **kw)
    with pytest.raises(PermissionError):
        await sim.delete_gtt(tid)
    with canonical_broker_capability("p", intent=INTENT_PROTECT):
        await sim.modify_gtt(tid, **kw)
        await sim.delete_gtt(tid)
    assert (await sim.get_gtt(tid))["status"] == "cancelled"


async def test_the_canonical_services_call_shape_works_unchanged(sim):
    """execution_service forwards a fat kwargs dict (symbol, tradingsymbol, side, size, quantity, ...)."""
    kwargs = dict(symbol=SYM, tradingsymbol=SYM, side="BUY", transaction_type="BUY", size=75, quantity=75,
                  exchange="NFO", product="NRML", allow_amo=False, tag="intent-abc", order_type="market_order",
                  signal_id="sig-1", stop_loss=90.0, strategy="sterling-kite", generation_id="g1")
    with canonical_broker_capability("sig-1", intent=INTENT_INCREASE):
        res = await sim.place_order(**kwargs)
    [order] = await sim.get_orders()
    assert order["order_id"] == res["order_id"] and order["tag"] == "intent-abc" and order["product"] == "NRML"
    assert (order["status"], order["order_type"], order["transaction_type"]) == ("COMPLETE", "MARKET", "BUY")


async def test_market_orders_need_valid_price_protection_like_the_real_client(sim):
    with canonical_broker_capability("x", intent=INTENT_INCREASE):
        with pytest.raises(Exception, match="price protection"):
            await sim.place_order_option(SYM, "buy", 75, exchange="NFO", market_protection=250)
        with pytest.raises(Exception, match="post_only"):
            await sim.place_order_option(SYM, "buy", 75, exchange="NFO", post_only=True)


# ── Kite-shaped reads ─────────────────────────────────────────────────────────────────────
async def test_reads_come_back_in_the_shapes_the_engines_parse(sim, real):
    await buy(sim)
    [order] = await sim.get_orders()
    assert {"order_id", "status", "tradingsymbol", "exchange", "transaction_type", "product", "quantity",
            "filled_quantity", "average_price", "tag", "order_timestamp", "order_type"} <= set(order)
    [hist] = [h for h in await sim.get_order_history(order["order_id"]) if h["status"] == "COMPLETE"]
    assert hist["filled_quantity"] == 75
    [trade] = await sim.get_trades()
    assert await sim.get_order_trades(order["order_id"]) == [trade]
    raw = await sim.get_positions_raw()
    assert raw["net"][0]["tradingsymbol"] == SYM and raw["net"][0]["quantity"] == 75
    [pos] = await sim.get_positions()
    assert (pos.symbol, pos.size, pos.side, pos.position_type) == (SYM, 75.0, "long", "NRML")
    assert await sim.get_holdings() == [] and await sim.get_auctions() == []
    margins = await sim.get_margins("equity")
    assert margins["available"]["live_balance"] > 0
    assert set(await sim.get_margins()) == {"equity", "commodity"}
    [fill] = await sim.get_fills()
    assert fill.symbol == SYM and fill.side == "buy" and fill.fee > 0
    snap = await sim.get_portfolio_snapshot()
    assert snap.exchange == "simulation" and snap.positions_count == 1 and len(snap.balances) == 2
    assert (await sim.get_profile())["user_id"] == sim._account_id
    assert await sim.get_alerts() == [] and await sim.get_mf_holdings() == [] and await sim.get_mf_sips() == []


async def test_positions_are_marked_to_the_real_price_when_read(sim, real):
    await buy(sim)
    real.set_quote("NFO:" + SYM, 120.0)
    [row] = (await sim.get_positions_raw())["net"]
    assert row["last_price"] == 120.0 and row["unrealised"] == round((120.0 - 100.1) * 75, 2)


async def test_positions_fall_back_to_the_last_known_real_price_when_quotes_are_down(sim, real):
    from app.services.exchanges.kite.errors import KiteTokenError
    await buy(sim)
    real.fail = KiteTokenError("expired", error_type="TokenException")
    [row] = (await sim.get_positions_raw())["net"]
    assert row["last_price"] == 100.0 and row["quantity"] == 75        # never an invented price, never an error


async def test_open_orders_margins_and_charges_are_computed_locally(sim, real):
    with canonical_broker_capability("e", intent=INTENT_INCREASE):
        await sim.place_order_option(SYM, "buy", 75, exchange="NFO", order_type="limit", limit_price=90.0)
    [open_order] = await sim.get_open_orders()
    assert (open_order.symbol, open_order.side, open_order.status, open_order.order_type) == (SYM, "buy", "open", "limit")
    leg = {"exchange": "NFO", "tradingsymbol": SYM, "transaction_type": "BUY", "variety": "regular",
           "product": "NRML", "order_type": "MARKET", "quantity": 75}
    [m] = await sim.order_margins([leg])
    assert m["total"] == m["option_premium"] == round(100.05 * 75, 2)
    assert (await sim.basket_margins([leg, leg]))["final"]["total"] == round(2 * 100.05 * 75, 2)
    [c] = await sim.order_charges([{**leg, "average_price": 100.0}])
    assert c["charges"]["brokerage"] == 20.0 and c["charges"]["total"] == 26.94
    assert real.calls and all(call[0] == "get_quote" for call in real.calls)      # only market data was read


# ── protective-stop code runs unchanged against the virtual book ──────────────────────────
async def test_the_protective_stop_helpers_work_on_a_gap_through_a_virtual_gtt(sim, real):
    from app.services.kite_engine import protective_stop as pstop
    await buy(sim)
    with canonical_broker_capability("protect", intent=INTENT_PROTECT):
        tid = await pstop.place_stop(sim, tradingsymbol=SYM, exchange="NFO", qty=75, trigger_premium=90.0,
                                     last_price=100.0, direction="long", target_premium=120.0, product="NRML")
    assert tid and tid > 9_000_000_000
    ident = dict(tradingsymbol=SYM, direction="long", exchange="NFO", product="NRML", qty=75)
    assert await pstop.stop_status(sim, tid, **ident) == pstop.STOP_ACTIVE
    assert [g["id"] for g in await sim.get_gtts()] == [tid]
    real.set_quote("NFO:" + SYM, 80.0, bid=79.95, ask=80.05)                       # gap far through the stop
    await sim.exchange.evaluate()
    state = await pstop.stop_state(sim, tid, reference_price=80.0, **ident)
    assert (state.verdict, state.child_state) == (pstop.STOP_TRIGGERED_STALE, pstop.CHILD_RESTING)
    assert state.child_order_id and state.child_quantity == 75 and state.child_filled_quantity == 0
    cleared = await pstop.cancel_fired_child(sim, state, tradingsymbol=SYM)
    assert (cleared.outcome, cleared.status, cleared.filled_quantity) == (pstop.CANCEL_CLEARED, "CANCELLED", 0)
    assert (await sim.get_order_history(state.child_order_id))[-1]["status"] == "CANCELLED"
    assert (await sim.get_positions_raw())["net"][0]["quantity"] == 75             # the caller now sells it itself


async def test_a_stop_that_fills_reads_as_done_and_cancel_and_trail_work(sim, real):
    from app.services.kite_engine import protective_stop as pstop
    await buy(sim)
    ident = dict(tradingsymbol=SYM, direction="long", exchange="NFO", product="NRML", qty=75)
    tid = await pstop.place_stop(sim, tradingsymbol=SYM, exchange="NFO", qty=75, trigger_premium=90.0, last_price=100.0,
                                 direction="long", product="NRML")
    assert await pstop.move_stop(sim, trigger_id=tid, tradingsymbol=SYM, exchange="NFO", qty=75, trigger_premium=95.0,
                                 last_price=100.0, direction="long", product="NRML")
    assert (await sim.get_gtt(tid))["condition"]["trigger_values"] == [95.0]
    real.set_quote("NFO:" + SYM, 94.0, bid=93.95, ask=94.05)
    await sim.exchange.evaluate()
    state = await pstop.stop_state(sim, tid, reference_price=94.0, **ident)
    assert state.verdict == pstop.STOP_TRIGGERED and state.child_state == pstop.CHILD_FILLED
    assert await pstop.cancel_stop_result(sim, tid) == pstop.GONE                  # a spent trigger cannot be cancelled
    tid2 = await pstop.place_stop(sim, tradingsymbol=SYM, exchange="NFO", qty=75, trigger_premium=80.0,
                                  last_price=94.0, direction="long", product="NRML")
    assert await pstop.cancel_stop(sim, tid2) is True
    assert await pstop.stop_status(sim, tid2, **ident) == pstop.STOP_ABSENT


# ── socket level: a whole session, only market-data GETs ever leave ───────────────────────
async def test_no_http_write_is_ever_issued_through_a_simulated_client(ledger, clock, _no_sockets):
    """The 'real' client is the production KiteClient over a recording transport (the e2e harness
    style). Run a complete trading session through the simulated client, then read the wire log."""
    from tests.e2e_live.fake_kite import WEEKLY_EXPIRY, FakeKiteExchange, weekly_symbol
    fake = FakeKiteExchange()
    symbol = weekly_symbol("NIFTY", WEEKLY_EXPIRY, 25000, "CE")
    fake.set_quote(f"NFO:{symbol}", 100.0, bid=99.95, ask=100.05)
    real_client = KiteClient(api_key="k", api_secret="s", access_token="t", is_paper=False)
    real_client._client = fake.async_client(K.BASE_URL)
    real_client._owner_user_id = "default"
    from app.services.simulation.quotes import QuoteSource
    quotes = QuoteSource(lambda: real_client, owner="default", ttl_s=0, use_websocket=False)
    sim = sc.get_sim_client("default", lambda: real_client, ledger=ledger, clock=clock, quotes=quotes, postbacks=False)

    def leg(price):
        return {"exchange": "NFO", "tradingsymbol": symbol, "transaction_type": "SELL", "quantity": 75,
                "order_type": "LIMIT", "product": "NRML", "price": price}

    with canonical_broker_capability("entry", intent=INTENT_INCREASE):
        entry = await sim.place_order_option(symbol, "buy", 75, exchange="NFO", tag="e1")
        resting = await sim.place_order_option(symbol, "buy", 75, exchange="NFO", order_type="limit", limit_price=90.0)
        await sim.modify_order(resting["order_id"], price=91.0)
    with canonical_broker_capability("cancel", intent=INTENT_CANCEL):
        await sim.cancel_order(resting["order_id"])
    with canonical_broker_capability("protect", intent=INTENT_PROTECT):
        tid = (await sim.place_gtt(trigger_type="two-leg", tradingsymbol=symbol, exchange="NFO", last_price=100.0,
                                   trigger_values=[90.0, 120.0], orders=[leg(88.2), leg(117.6)]))["trigger_id"]
        await sim.modify_gtt(tid, trigger_type="two-leg", tradingsymbol=symbol, exchange="NFO", last_price=100.0,
                             trigger_values=[92.0, 120.0], orders=[leg(90.2), leg(117.6)])
    fake.set_quote(f"NFO:{symbol}", 91.0, bid=90.95, ask=91.05)      # through the 92 stop, above its 90.20 limit
    await sim.exchange.evaluate()                                    # the stop fires and fills virtually
    await sim.get_orders(), await sim.get_positions_raw(), await sim.get_margins(), await sim.get_gtts()
    await sim.order_margins([{**leg(0), "transaction_type": "BUY"}]), await sim.get_ltp([f"NFO:{symbol}"])
    for refused in sorted(sc.REFUSED):
        with pytest.raises(sc.SimulationBoundaryError):
            r = getattr(sim, refused)()
            if inspect.isawaitable(r):
                await r
    assert entry["order_id"]
    assert (await sim.get_positions_raw())["net"][0]["quantity"] == 0      # the position really traded, virtually

    methods = {method for method, *_ in fake.requests}
    assert methods == {"GET"}, f"a non-GET request left the simulation: {[r for r in fake.requests if r[0] != 'GET']}"
    paths = {path.split("/")[1] for _, path, *_ in fake.requests}
    assert paths <= {"quote", "instruments"}, paths                       # market data only: no /orders, /gtt, /portfolio
    assert fake.order_posts == [] and fake.gtt_posts == [] and fake.orders == {} and fake.gtts == {}
    assert _no_sockets == []
