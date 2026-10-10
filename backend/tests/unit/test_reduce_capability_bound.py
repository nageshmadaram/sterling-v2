"""A REDUCE capability that declares what it closes is enforced at the transport.

Without a bound a REDUCE capability only said "this is an exit", so any exit-path bug
(wrong side, stale quantity) could open or flip exposure past the kill switch and the
live-minimum envelope. A bounded capability names the contract, the closing side and
the most it may send; the broker transport refuses anything else, with or without the
retired STERLING_FAMILY_MODE switch.
"""
import pytest

from app.services.snapback_family_mode import (
    INTENT_INCREASE,
    INTENT_REDUCE,
    ReduceBound,
    canonical_broker_capability,
    guard_broker_reduction,
)

SYMBOL = "NIFTY26OCT25000CE"


@pytest.fixture(autouse=True)
def _no_family_mode(monkeypatch):
    monkeypatch.delenv("STERLING_FAMILY_MODE", raising=False)


def _bound(side="SELL", quantity=75):
    return ReduceBound.closing(exchange="NFO", symbol=SYMBOL, side=side, quantity=quantity)


def _send(**overrides):
    order = dict(exchange="NFO", tradingsymbol=SYMBOL, transaction_type="SELL", quantity=75)
    order.update(overrides)
    guard_broker_reduction(**order)


def test_a_matching_exit_is_allowed_and_a_smaller_one_too():
    with canonical_broker_capability("x", intent=INTENT_REDUCE, bound=_bound()):
        _send()
        _send(quantity=25)


@pytest.mark.parametrize("override", [
    {"transaction_type": "BUY"},          # opens or flips instead of closing
    {"quantity": 76},                      # more than the stale remainder
    {"quantity": 750},
    {"tradingsymbol": "NIFTY26OCT25100CE"},
    {"exchange": "BFO"},
])
def test_anything_beyond_the_declared_bound_is_refused_without_family_mode(override):
    with canonical_broker_capability("x", intent=INTENT_REDUCE, bound=_bound()):
        with pytest.raises(PermissionError):
            _send(**override)


def test_a_short_close_buys_back_and_cannot_sell_more():
    with canonical_broker_capability("x", intent=INTENT_REDUCE, bound=_bound("BUY", 50)):
        _send(transaction_type="BUY", quantity=50)
        with pytest.raises(PermissionError):
            _send(transaction_type="SELL", quantity=50)


def test_the_symbol_is_compared_without_an_exchange_prefix_or_case():
    bound = ReduceBound.closing(exchange="", symbol=f"nfo:{SYMBOL.lower()}", side="sell", quantity=75)
    with canonical_broker_capability("x", intent=INTENT_REDUCE, bound=bound):
        _send()


def test_unbounded_reduce_capability_is_refused_in_legacy_exits():
    # Exits must never be refused because a bound was forgotten; those sites are
    # named in the log until they declare one.
    with canonical_broker_capability("legacy", intent=INTENT_REDUCE):
        with pytest.raises(PermissionError, match="bound"):
            _send(transaction_type="BUY", quantity=10_000)


def test_the_bound_does_not_apply_to_other_intents_or_outside_a_capability():
    _send(transaction_type="BUY")  # no capability at all: the entry guard owns that case
    with canonical_broker_capability("entry", intent=INTENT_INCREASE):
        _send(transaction_type="BUY")


def test_an_inner_bound_applies_only_inside_its_own_block():
    with canonical_broker_capability("outer", intent=INTENT_REDUCE, bound=_bound(quantity=25)):
        with canonical_broker_capability("inner", intent=INTENT_REDUCE, bound=_bound(quantity=75)):
            _send(quantity=75)
        with pytest.raises(PermissionError):
            _send(quantity=75)


@pytest.mark.asyncio
async def test_the_live_transport_refuses_a_buy_under_a_sell_reduce_bound(monkeypatch):
    from app.services.exchanges.kite.client import KiteClient

    client = KiteClient(is_paper=False)
    posted = []

    async def _no_network(path, body):
        posted.append((path, body))
        return {"order_id": "SHOULD-NOT-SEND"}

    monkeypatch.setattr(client, "_auth_post", _no_network)
    with canonical_broker_capability("exit", intent=INTENT_REDUCE, bound=_bound()):
        with pytest.raises(PermissionError):
            await client.place_order(f"NFO:{SYMBOL}", "buy", 750, exchange="NFO", product="NRML")
    assert posted == []


@pytest.mark.asyncio
async def test_the_live_transport_sends_the_declared_exit(monkeypatch):
    from app.services.exchanges.kite.client import KiteClient

    client = KiteClient(is_paper=False)
    posted = []

    async def _fake_post(path, body):
        posted.append((path, body))
        return {"order_id": "OK-1"}

    monkeypatch.setattr(client, "_auth_post", _fake_post)
    with canonical_broker_capability("exit", intent=INTENT_REDUCE, bound=_bound()):
        out = await client.place_order(f"NFO:{SYMBOL}", "sell", 75, exchange="NFO", product="NRML")
    assert out["order_id"] == "OK-1"
    assert posted[0][1]["transaction_type"] == "SELL" and posted[0][1]["quantity"] == 75


@pytest.mark.asyncio
async def test_canonical_execution_declares_the_bound_for_a_close(tmp_path, monkeypatch):
    """The canonical path hands the transport the contract, closing side and quantity."""
    from types import SimpleNamespace

    from app.services import db, live_safety
    from app.services.execution_service import (
        CanonicalExecutionService, ExecutionRequest, ExposureEffect)
    from app.services.snapback_family_mode import _capability

    monkeypatch.setattr(db, "_DB_PATH", str(tmp_path / "bound.db"))
    monkeypatch.setattr(db, "_available", True)
    live_safety.reset_all_for_tests()
    db.init()
    monkeypatch.setattr(
        "app.services.execution_service.snapback_live_gate_decision",
        lambda **kw: SimpleNamespace(allowed=True, protective=False, reasons=[]), raising=False)
    monkeypatch.setattr("app.core.lane_registry.entry_transport_allowed", lambda _c: True)
    db.set_inventory(account_id="acc1", uid="u1", symbol=SYMBOL, net_quantity=75, exchange="NFO")
    seen = {}

    class Broker:
        _account_id = "acc1"
        _is_paper = False

        async def get_positions_raw(self):
            return {"net": [{"tradingsymbol": SYMBOL, "exchange": "NFO",
                             "product": "NRML", "quantity": 75}]}

        async def place_order(self, **kwargs):
            seen["capability"] = _capability.get()
            return {"order_id": "X1"}

    req = ExecutionRequest(
        uid="u1", account_id="acc1", strategy_id="sterling-kite", generation_id="g1",
        signal_id="close-1", exchange="NFO", symbol=SYMBOL, side="SELL", quantity=75,
        exposure_effect=ExposureEffect.CLOSE_POSITION, payload={"product": "NRML"})
    res = await CanonicalExecutionService().submit_order(req, broker_client=Broker())

    assert res.success is True
    _, intent, bound = seen["capability"]
    assert intent == INTENT_REDUCE
    assert (bound.exchange, bound.symbol, bound.side, bound.max_quantity) == (
        "NFO", SYMBOL, "SELL", 75)
    live_safety.reset_all_for_tests()
