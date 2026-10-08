"""Gamma-Unwind recovery regressions against a stateful live-mode broker double.

Each test drives the real runner (real position registry on the test database)
against a double that behaves like Kite: a timed-out GTT POST still creates the
trigger, a market SELL fills against the position, and a restart reloads the
registry from its durable row.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest

from app.engines.gamma_move import (
    GammaMoveConfig,
    GammaSignal,
    GateContext,
    InstrumentRef,
    PositionState,
    TIER_ENTRY,
    TradeRecord,
    WatchContract,
)
from app.services import gamma_move_positions as store
from app.services import gamma_move_runner as runner
from app.services.exchanges.kite.errors import KiteInputError

IST = timezone(timedelta(hours=5, minutes=30))
NOW = datetime(2026, 10, 5, 11, 0, tzinfo=IST)
NOW_MS = int(NOW.timestamp() * 1000)
UID = "u-gamma-recovery"
SYMBOL = "RELIANCE26OCT3000CE"


def _signal() -> GammaSignal:
    inst = InstrumentRef("222", SYMBOL, "CE", 3000, "2026-10-29", 10, 0.05, "NFO")
    watch = WatchContract(
        "RELIANCE", 111, "BREAKOUT-CE", inst, "HOI-1",
        10_000, 1, 2990.0, 3000.0, 2.0,
        build_oi=10_000, chain_ltp=100.0, dte=1,
    )
    gates = GateContext(
        0.2, 1.0, 8.0, 0, True, True, True, (), (),
        ("zone", "liquidity", "direction", "unwind"), "SC",
    )
    return GammaSignal(
        id="gamma:recovery", at_ms=NOW_MS, contract=watch, ltp=100.0,
        oi_drop_pct=10.0, vol_ratio=2.0, strong=False,
        base_low=90.0, base_high=99.0, reason="E1", snapshot={},
        gates=gates, tier=TIER_ENTRY,
    )


def _position(*, qty: int = 50) -> PositionState:
    pos = PositionState.from_signal(
        _signal(), fill=100.0, quantity=qty, lots=qty // 10,
        at_ms=NOW_MS - 600_000, day="2026-10-05",
    )
    pos.status = "open"
    pos.account_id = "acct-live"
    pos.order_id = "ENTRY-1"
    pos.stop_mode = "both"
    pos.stop = 90.0
    return pos


class LiveBroker:
    """Kite-shaped live double. Everything it was asked to do is recorded."""

    _is_paper = False
    _account_id = "acct-live"

    def __init__(self, *, qty: int = 50, ltp: float = 120.0) -> None:
        self.net_qty = qty
        self.ltp = ltp
        self.gtts: list[dict] = []
        self.gtt_seq = 100
        self.place_gtt_calls: list[dict] = []
        self.gtt_error_after_create: BaseException | None = None
        self.gtt_error_before_create: BaseException | None = None
        self.deleted: list[int] = []
        self.modified: list[tuple[int, dict]] = []
        self.orders: list[dict] = []
        self.order_seq = 0
        self.place_order_calls: list[dict] = []
        self.place_order_error_after_create: BaseException | None = None
        self.place_order_error_before_create: BaseException | None = None
        self.order_fill: int | None = None   # None = fill everything
        self.orders_unreadable = False
        self.gtts_unreadable = False

    # ----- reads
    async def get_positions(self):
        return {"net": [{
            "tradingsymbol": SYMBOL, "exchange": "NFO", "product": "NRML",
            "quantity": self.net_qty,
        }]}

    async def get_ltp(self, keys):
        return {key: {"last_price": self.ltp} for key in keys}

    async def get_gtts(self):
        if self.gtts_unreadable:
            raise httpx.ReadTimeout("gtt book")
        return [dict(row) for row in self.gtts]

    async def get_gtt(self, trigger_id):
        for row in self.gtts:
            if row["id"] == int(trigger_id):
                return dict(row)
        raise KiteInputError("not found", status_code=404)

    async def get_orders(self):
        if self.orders_unreadable:
            raise httpx.ReadTimeout("order book")
        return [dict(o) for o in self.orders]

    async def get_order_history(self, order_id):
        return [dict(o) for o in self.orders if o["order_id"] == order_id]

    # ----- GTT writes
    async def place_gtt(self, **request):
        self.place_gtt_calls.append(request)
        if self.gtt_error_before_create is not None:
            raise self.gtt_error_before_create
        self.gtt_seq += 1
        self.gtts.append({
            "id": self.gtt_seq, "status": "active", "type": request["trigger_type"],
            "condition": {
                "exchange": request["exchange"],
                "tradingsymbol": request["tradingsymbol"],
                "trigger_values": list(request["trigger_values"]),
                "last_price": request["last_price"],
            },
            "orders": [dict(o) for o in request["orders"]],
        })
        if self.gtt_error_after_create is not None:
            raise self.gtt_error_after_create
        return {"trigger_id": self.gtt_seq}

    async def modify_gtt(self, trigger_id, **request):
        self.modified.append((int(trigger_id), request))
        for row in self.gtts:
            if row["id"] == int(trigger_id):
                row["condition"]["trigger_values"] = list(request["trigger_values"])
                row["orders"] = [dict(o) for o in request["orders"]]
        return {"trigger_id": int(trigger_id)}

    async def delete_gtt(self, trigger_id):
        self.deleted.append(int(trigger_id))
        self.gtts = [r for r in self.gtts if r["id"] != int(trigger_id)]
        return {"trigger_id": int(trigger_id)}

    # ----- order writes
    async def place_order(self, symbol, side, qty, **kwargs):
        self.place_order_calls.append({"symbol": symbol, "side": side, "qty": qty, **kwargs})
        if self.place_order_error_before_create is not None:
            raise self.place_order_error_before_create
        self.order_seq += 1
        filled = int(qty) if self.order_fill is None else int(self.order_fill)
        self.net_qty -= filled
        status = "COMPLETE" if filled == int(qty) else ("CANCELLED" if filled else "REJECTED")
        self.orders.append({
            "order_id": f"EXIT-{self.order_seq}", "tradingsymbol": SYMBOL,
            "exchange": "NFO", "product": "NRML", "transaction_type": "SELL",
            "status": status, "filled_quantity": filled, "quantity": int(qty),
            "average_price": 88.0 if filled else 0.0,
            "tag": kwargs.get("tag", ""),
            "order_timestamp": NOW.replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S"),
        })
        if self.place_order_error_after_create is not None:
            raise self.place_order_error_after_create
        return {"order_id": f"EXIT-{self.order_seq}"}

    async def cancel_order(self, order_id, **_kwargs):
        return {"order_id": order_id}


@pytest.fixture
def env(monkeypatch):
    """A clean runner + registry bound to one live broker double."""
    from app.services import live_safety
    from app.services.notifications import telegram_lifecycle

    runner.clear()
    runner._schedule_done.clear()
    store.reset(UID)
    broker = LiveBroker()
    alerts: list[dict] = []
    halts: list[tuple] = []
    notes: list[tuple] = []
    clock = {"ms": NOW_MS}
    record = TradeRecord()
    session = SimpleNamespace(
        cfg=GammaMoveConfig(stop_mode="both").validate(),
        signals={}, subscribed=set(), record=record,
        note=lambda kind, message: notes.append((kind, message)),
    )

    async def get_client(_uid):
        return broker

    async def subscribe(*_a, **_k):
        return None

    monkeypatch.setattr(runner, "_now", lambda: NOW)
    monkeypatch.setattr(runner, "_now_ms", lambda: clock["ms"])
    monkeypatch.setattr(runner, "_client", get_client)
    monkeypatch.setattr(runner, "session_for", lambda *_a, **_k: session)
    monkeypatch.setattr(runner, "_subscribe_watched", subscribe)
    monkeypatch.setattr(
        telegram_lifecycle, "enqueue_best_effort",
        lambda uid, **kwargs: alerts.append(kwargs),
    )
    monkeypatch.setattr(
        live_safety, "set_kill_switch",
        lambda enabled, reason="", **_k: halts.append((enabled, reason)),
    )
    yield SimpleNamespace(
        broker=broker, alerts=alerts, halts=halts, notes=notes, clock=clock,
        session=session, record=record,
    )
    store.reset(UID)
    runner.clear()
    runner._schedule_done.clear()


def _restart() -> None:
    """Forget every in-memory cache: the registry is reloaded from its durable row."""
    store.reset(UID)
    runner._exit_locks.clear()
    runner._exit_retry_at.clear()
    runner._protection_checked_at.clear()


def _reload(symbol: str = SYMBOL) -> PositionState:
    pos = store.get(UID, symbol)
    assert pos is not None
    return pos


def _live_triggers(broker: LiveBroker) -> list[dict]:
    return [g for g in broker.gtts if g["status"] == "active"]


# ===================================================================== finding 1
@pytest.mark.asyncio
async def test_timed_out_gtt_placement_is_never_retried_blindly(env):
    pos = _position()
    store.put(UID, pos)
    env.broker.gtt_error_after_create = httpx.ReadTimeout("10s")

    first = await runner._ensure_protection(UID, env.broker, pos)
    second = await runner._ensure_protection(UID, env.broker, pos)
    third = await runner._ensure_protection(UID, env.broker, pos)

    assert first is False
    # The reply was lost, the trigger exists: it is adopted, never duplicated.
    assert len(env.broker.place_gtt_calls) == 1
    assert len(_live_triggers(env.broker)) == 1
    assert second is True and third is True
    assert pos.gtt_id == env.broker.gtts[0]["id"]
    assert pos.gtt_pending is False
    assert pos.gtt_qty == 50


@pytest.mark.asyncio
async def test_pending_fence_is_durable_before_the_gtt_post(env):
    pos = _position()
    store.put(UID, pos)
    seen: dict = {}
    real = env.broker.place_gtt

    async def spy(**request):
        # What a crash at this instant would leave behind.
        _restart()
        seen["row"] = store.get(UID, SYMBOL)
        return await real(**request)

    env.broker.place_gtt = spy
    await runner._ensure_protection(UID, env.broker, pos)

    assert seen["row"].gtt_pending is True
    assert seen["row"].gtt_pending_qty == 50
    assert seen["row"].gtt_pending_trigger == pytest.approx(90.0)


@pytest.mark.asyncio
async def test_restart_between_gtt_create_and_registry_write_does_not_duplicate(env):
    pos = _position()
    store.put(UID, pos)
    env.broker.gtt_error_after_create = SystemExit("crash after the broker created it")

    with pytest.raises(SystemExit):
        await runner._ensure_protection(UID, env.broker, pos)
    _restart()
    env.broker.gtt_error_after_create = None
    restarted = _reload()

    assert restarted.gtt_pending is True and restarted.gtt_id == 0
    ok = await runner._ensure_protection(UID, env.broker, restarted)

    assert ok is True
    assert len(env.broker.place_gtt_calls) == 1
    assert len(_live_triggers(env.broker)) == 1
    assert restarted.gtt_id == env.broker.gtts[0]["id"]


@pytest.mark.asyncio
async def test_pending_fence_is_cleared_only_when_the_book_is_readable_and_empty(env):
    pos = _position()
    store.put(UID, pos)
    env.broker.gtt_error_before_create = httpx.ReadTimeout("lost before it was stored")
    assert await runner._ensure_protection(UID, env.broker, pos) is False
    assert pos.gtt_pending is True
    env.broker.gtt_error_before_create = None

    env.clock["ms"] += 120_000          # long past the in-flight grace
    env.broker.gtts_unreadable = True
    assert await runner._ensure_protection(UID, env.broker, pos) is False
    assert pos.gtt_pending is True       # unreadable book: still fenced
    assert len(env.broker.place_gtt_calls) == 1

    env.broker.gtts_unreadable = False
    assert await runner._ensure_protection(UID, env.broker, pos) is True
    assert len(env.broker.place_gtt_calls) == 2   # readable and empty: placed afresh
    assert pos.gtt_pending is False and pos.gtt_id


@pytest.mark.asyncio
async def test_in_flight_grace_holds_the_fence_even_when_the_book_is_empty(env):
    pos = _position()
    store.put(UID, pos)
    env.broker.gtt_error_before_create = httpx.ReadTimeout("still in flight")
    await runner._ensure_protection(UID, env.broker, pos)
    env.broker.gtt_error_before_create = None

    # The book is readable and empty a moment later, but the POST may still land.
    assert await runner._ensure_protection(UID, env.broker, pos) is False
    assert len(env.broker.place_gtt_calls) == 1
    assert pos.gtt_pending is True


@pytest.mark.asyncio
async def test_definitive_refusal_lifts_the_fence_so_the_next_pass_retries(env):
    pos = _position()
    store.put(UID, pos)
    env.broker.gtt_error_before_create = KiteInputError("bad trigger", status_code=400)

    assert await runner._ensure_protection(UID, env.broker, pos) is False
    assert pos.gtt_pending is False
    assert _reload().gtt_pending is False

    env.broker.gtt_error_before_create = None
    assert await runner._ensure_protection(UID, env.broker, pos) is True
    assert len(env.broker.place_gtt_calls) == 2
    assert len(_live_triggers(env.broker)) == 1


@pytest.mark.asyncio
async def test_unproven_exit_does_not_sell_over_a_trigger_that_may_exist(env):
    pos = _position()
    store.put(UID, pos)
    env.broker.gtt_error_after_create = httpx.ReadTimeout("10s")
    await runner._ensure_protection(UID, env.broker, pos)
    env.broker.gtt_error_after_create = None
    trigger_id = env.broker.gtts[0]["id"]
    env.broker.gtts_unreadable = True       # cannot tell whether a trigger rests

    ok, status = await runner._execute_exit(UID, pos, market_ltp=89.0, reason="SL")

    assert (ok, status) == (False, "protection_not_released")
    assert env.broker.place_order_calls == []

    env.broker.gtts_unreadable = False
    runner._exit_retry_at.clear()
    await runner._execute_exit(UID, pos, market_ltp=89.0, reason="SL")

    assert len(env.broker.place_order_calls) == 1       # one SELL
    assert env.broker.deleted == [trigger_id]           # the trigger was taken out first
    assert _live_triggers(env.broker) == []


@pytest.mark.asyncio
async def test_duplicate_triggers_from_an_older_build_are_reduced_to_one(env):
    pos = _position()
    store.put(UID, pos)
    env.broker.gtt_error_after_create = httpx.ReadTimeout("10s")
    await runner._ensure_protection(UID, env.broker, pos)
    env.broker.gtt_error_after_create = None
    # A second identical trigger already rests (placed by a build without the fence).
    await env.broker.place_gtt(**env.broker.place_gtt_calls[0])
    assert len(_live_triggers(env.broker)) == 2

    ok = await runner._ensure_protection(UID, env.broker, pos)

    assert ok is True
    assert len(_live_triggers(env.broker)) == 1
    assert pos.gtt_id == _live_triggers(env.broker)[0]["id"]
