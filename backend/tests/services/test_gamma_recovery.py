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


# ------------------------------------------------------------ shared exit helpers
async def _armed(env, *, qty: int = 50) -> PositionState:
    pos = _position(qty=qty)
    env.broker.net_qty = qty
    store.put(UID, pos)
    assert await runner._ensure_protection(UID, env.broker, pos) is True
    assert pos.gtt_id and pos.gtt_qty == qty
    return pos


def _capture_booking(monkeypatch) -> list:
    from app.services.kite_engine import fill_ledger, state

    booked: list = []
    monkeypatch.setattr(state, "record_realized_pnl",
                        lambda uid, pnl, **_k: booked.append(pnl))
    monkeypatch.setattr(fill_ledger, "inventory", lambda *_a, **_k: None)
    return booked


# ===================================================================== finding 5
@pytest.mark.asyncio
async def test_partial_exit_is_settled_once_when_reconcile_overlaps_it(env, monkeypatch):
    booked = _capture_booking(monkeypatch)
    pos = await _armed(env, qty=50)
    env.broker.order_fill = 20           # a MARKET exit that part-fills, then is cancelled
    real_history = env.broker.get_order_history

    async def slow_history(order_id):
        await asyncio.sleep(0.03)        # the poll window a 5s reconcile cycle overlaps
        return await real_history(order_id)

    env.broker.get_order_history = slow_history

    async def reconcile_mid_exit():
        await asyncio.sleep(0.01)
        return await runner.reconcile(UID)

    await asyncio.gather(
        runner._execute_exit(UID, pos, market_ltp=88.0, reason="SL"),
        reconcile_mid_exit(),
        reconcile_mid_exit(),
    )

    assert pos.quantity == 30                      # 50 - 20, subtracted once
    assert pos.lots == 3
    assert booked == [pytest.approx(-240.0)]       # booked once
    assert env.record.realised_inr == pytest.approx(-240.0)
    assert len(env.broker.place_order_calls) == 1
    live = _live_triggers(env.broker)
    assert len(live) == 1 and live[0]["orders"][0]["quantity"] == 30


@pytest.mark.asyncio
async def test_settle_does_not_mutate_after_another_path_already_settled(env, monkeypatch):
    booked = _capture_booking(monkeypatch)
    pos = await _armed(env, qty=50)
    env.broker.order_fill = 20
    ok, status = await runner._execute_exit(UID, pos, market_ltp=88.0, reason="SL")
    assert status == "partial_exit" and pos.quantity == 30

    # A late caller still holding the pre-settlement identity must be a no-op.
    pos.exiting = True
    pos.exit_order_id = "EXIT-1"
    pos.exit_requested_qty = 50
    from app.services import canonical_fill_reconciliation as fills

    real = fills.resolve_acknowledged_order

    async def settle_elsewhere(**kwargs):
        resolution = await real(**kwargs)
        pos.exiting = False              # the competing path finished while we awaited
        pos.exit_order_id = ""
        return resolution

    monkeypatch.setattr(fills, "resolve_acknowledged_order", settle_elsewhere)
    done, status = await runner._settle_known_exit(UID, env.broker, pos)

    assert (done, status) == (False, "exit_already_settled")
    assert pos.quantity == 30
    assert booked == [pytest.approx(-240.0)]


# ===================================================================== finding 6
def _find_sell_orders(env) -> list[dict]:
    return [o for o in env.broker.orders if o["transaction_type"] == "SELL"]


@pytest.mark.asyncio
async def test_exit_cancelled_mid_submit_is_found_by_tag_after_restart(env, monkeypatch):
    booked = _capture_booking(monkeypatch)
    pos = await _armed(env, qty=50)
    env.broker.place_order_error_after_create = asyncio.CancelledError()

    task = asyncio.create_task(runner._execute_exit(UID, pos, market_ltp=88.0, reason="SL"))
    with pytest.raises(asyncio.CancelledError):
        await task
    env.broker.place_order_error_after_create = None

    durable = _reload()
    assert durable.exiting is True
    assert durable.exit_order_id == runner.EXIT_SUBMITTING
    assert durable.exit_tag and durable.exit_tag != "gamma-unwind-exit"
    assert env.broker.place_order_calls[0]["tag"] == durable.exit_tag

    _restart()
    result = await runner.reconcile(UID)

    assert store.get(UID, SYMBOL).status == "closed"
    assert len(env.broker.place_order_calls) == 1       # never a second SELL
    assert booked == [pytest.approx(-600.0)]
    assert "vanished" in result


@pytest.mark.asyncio
async def test_exit_with_a_lost_reply_is_resolved_not_resent(env, monkeypatch):
    _capture_booking(monkeypatch)
    pos = await _armed(env, qty=50)
    env.broker.place_order_error_after_create = httpx.ReadTimeout("10s")

    ok, status = await runner._execute_exit(UID, pos, market_ltp=88.0, reason="SL")
    env.broker.place_order_error_after_create = None

    assert len(env.broker.place_order_calls) == 1
    assert (ok, status) == (True, "closed")             # found in the book by its tag
    assert store.get(UID, SYMBOL).status == "closed"


@pytest.mark.asyncio
async def test_exit_that_never_reached_the_broker_rearms_after_the_grace(env, monkeypatch):
    _capture_booking(monkeypatch)
    pos = await _armed(env, qty=50)
    env.broker.place_order_error_before_create = asyncio.CancelledError()
    task = asyncio.create_task(runner._execute_exit(UID, pos, market_ltp=88.0, reason="SL"))
    with pytest.raises(asyncio.CancelledError):
        await task
    env.broker.place_order_error_before_create = None
    assert env.broker.orders == []
    _restart()

    await runner.reconcile(UID)                         # inside the grace: wait, do nothing
    held = _reload()
    assert held.exiting is True and not _live_triggers(env.broker)
    assert len(env.broker.place_order_calls) == 1

    env.clock["ms"] += 120_000
    await runner.reconcile(UID)

    freed = _reload()
    assert freed.exiting is False and freed.exit_tag == ""
    assert len(_live_triggers(env.broker)) == 1         # the stop is back
    assert len(env.broker.place_order_calls) == 1       # and no SELL was invented


@pytest.mark.asyncio
async def test_unreadable_order_book_keeps_the_exit_fenced_and_alerts(env, monkeypatch):
    _capture_booking(monkeypatch)
    pos = await _armed(env, qty=50)
    env.broker.place_order_error_after_create = httpx.ReadTimeout("10s")
    env.broker.orders_unreadable = True
    await runner._execute_exit(UID, pos, market_ltp=88.0, reason="SL")
    env.broker.place_order_error_after_create = None
    env.clock["ms"] += 120_000

    done, status = await runner._settle_known_exit(UID, env.broker, pos)

    assert done is False
    assert pos.exiting is True and pos.exit_order_id == runner.EXIT_SUBMITTING
    assert any(a["event_type"] == "EXIT UNRESOLVED" for a in env.alerts)
    assert env.halts and env.halts[-1][0] is True
    assert len(env.broker.place_order_calls) == 1


@pytest.mark.asyncio
async def test_legacy_exit_row_without_an_order_id_is_resolved_by_the_old_tag(env, monkeypatch):
    booked = _capture_booking(monkeypatch)
    pos = await _armed(env, qty=50)
    # What an older build persisted: exiting, no order id, the constant exit tag.
    await runner._cancel_protection(UID, env.broker, pos)
    pos.exiting = True
    pos.exit_order_id = ""
    pos.exit_requested_qty = 50
    pos.exit_requested_price = 88.0
    pos.exit_pending_reason = "SL"
    store.put(UID, pos)
    await env.broker.place_order(
        f"NFO:{SYMBOL}", "sell", 50, tag="gamma-unwind-exit", order_type="market_order")
    _restart()

    await runner.reconcile(UID)

    assert store.get(UID, SYMBOL).status == "closed"
    assert booked == [pytest.approx(-600.0)]
    assert len(env.broker.place_order_calls) == 1


@pytest.mark.asyncio
async def test_definitive_exit_refusal_still_rearms_the_stop(env):
    pos = await _armed(env, qty=50)
    env.broker.place_order_error_before_create = KiteInputError("rejected", status_code=400)

    ok, status = await runner._execute_exit(UID, pos, market_ltp=88.0, reason="SL")

    assert (ok, status) == (False, "submit_failed")
    assert pos.exiting is False and pos.exit_tag == ""
    assert len(_live_triggers(env.broker)) == 1


# ===================================================================== finding 7
@pytest.mark.asyncio
async def test_exit_sells_the_held_quantity_when_the_broker_holds_less(env, monkeypatch):
    _capture_booking(monkeypatch)
    pos = await _armed(env, qty=50)
    env.broker.net_qty = 30             # a manual partial square-off in Kite

    ok, status = await runner._execute_exit(UID, pos, market_ltp=88.0, reason="SL")

    assert env.broker.place_order_calls[0]["qty"] == 30
    assert (ok, status) == (True, "closed")
    assert store.get(UID, SYMBOL).pnl_reconciliation_required is True
    assert any(a.get("severity") == "CRITICAL" and "30" in a["detail"] for a in env.alerts)
    assert _live_triggers(env.broker) == []


@pytest.mark.asyncio
async def test_exit_still_refuses_when_broker_quantity_cannot_be_read(env):
    pos = await _armed(env, qty=50)

    async def broken():
        raise httpx.ReadTimeout("positions")

    env.broker.get_positions = broken
    ok, status = await runner._execute_exit(UID, pos, market_ltp=88.0, reason="SL")

    assert (ok, status) == (False, "broker_quantity_unverified")
    assert env.broker.place_order_calls == []


@pytest.mark.asyncio
async def test_reconcile_resizes_registry_and_stop_to_the_broker_quantity(env):
    pos = await _armed(env, qty=50)
    env.broker.net_qty = 30

    await runner.reconcile(UID)

    assert pos.quantity == 30 and pos.lots == 3
    assert pos.pnl_reconciliation_required is True
    assert env.broker.modified and env.broker.modified[-1][1]["orders"][0]["quantity"] == 30
    assert pos.gtt_qty == 30
    assert any(a.get("severity") == "CRITICAL" for a in env.alerts)
    assert env.broker.place_order_calls == []


@pytest.mark.asyncio
async def test_reconcile_waits_when_the_stop_has_fired_and_its_child_is_working(env, monkeypatch):
    from app.services.kite_engine import protective_stop as pstop

    pos = await _armed(env, qty=50)
    env.broker.net_qty = 30             # the fired stop's child part-filled

    async def fired(*_a, **_k):
        return pstop.STOP_TRIGGERED_RESTING

    monkeypatch.setattr(pstop, "stop_status", fired)
    await runner.reconcile(UID)

    assert pos.quantity == 50           # the child is still selling the rest
    assert env.broker.modified == []
