"""Live-money audit regressions for the Gamma-Unwind runner."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

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
from app.services import gamma_move_runner as runner

IST = timezone(timedelta(hours=5, minutes=30))
NOW = datetime(2026, 10, 5, 11, 0, tzinfo=IST)
NOW_MS = int(NOW.timestamp() * 1000)


def _signal(*, at_ms: int = NOW_MS, dte: int = 1) -> GammaSignal:
    inst = InstrumentRef(
        "222", "RELIANCE26OCT3000CE", "CE", 3000,
        "2026-10-29", 10, 0.05, "NFO",
    )
    watch = WatchContract(
        "RELIANCE", 111, "BREAKOUT-CE", inst, "HOI-1",
        10_000, 1, 2990.0, 3000.0, 2.0,
        build_oi=10_000, chain_ltp=100.0, dte=dte,
    )
    gates = GateContext(
        0.2, 1.0, 8.0, 0, True, True, True, (), (),
        ("zone", "liquidity", "direction", "unwind"), "SC",
    )
    return GammaSignal(
        id="gamma:audit", at_ms=at_ms, contract=watch, ltp=100.0,
        oi_drop_pct=10.0, vol_ratio=2.0, strong=False,
        base_low=90.0, base_high=99.0, reason="E1", snapshot={},
        gates=gates, tier=TIER_ENTRY,
    )


def _position(*, hold: str = "intraday") -> PositionState:
    pos = PositionState.from_signal(
        _signal(dte=1 if hold == "intraday" else 5),
        fill=100.0, quantity=10, lots=1, at_ms=NOW_MS - 600_000,
        day="2026-10-05",
    )
    pos.status = "open"
    pos.account_id = "acct-live"
    pos.order_id = "ENTRY-1"
    return pos


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    runner.clear()
    runner._schedule_done.clear()
    monkeypatch.setattr(runner, "_now", lambda: NOW)
    yield
    runner.clear()
    runner._schedule_done.clear()


# --------------------------------------------------------------------- entry
@pytest.mark.asyncio
async def test_stale_entry_signal_is_not_executable(monkeypatch):
    sig = _signal(at_ms=NOW_MS - (runner.ENTRY_SIGNAL_MAX_AGE_S + 5) * 1000)
    session = SimpleNamespace(signals={sig.id: sig}, note=lambda *_a: None)
    cfg = GammaMoveConfig(stop_mode="both").validate()
    monkeypatch.setattr(runner, "get_config", lambda _uid=None: cfg)
    monkeypatch.setattr(runner, "session_for", lambda *_a, **_k: session)
    monkeypatch.setattr(runner.positions_store, "get", lambda *_a: None)

    result = await runner.arm("u1", sig.id)

    assert result["ok"] is False
    assert result["code"] == "signal_stale"
    assert "no longer current" in result["message"]


@pytest.mark.asyncio
async def test_future_dated_entry_signal_is_not_executable(monkeypatch):
    sig = _signal(at_ms=NOW_MS + 10 * 60_000)
    session = SimpleNamespace(signals={sig.id: sig}, note=lambda *_a: None)
    cfg = GammaMoveConfig(stop_mode="both").validate()
    monkeypatch.setattr(runner, "get_config", lambda _uid=None: cfg)
    monkeypatch.setattr(runner, "session_for", lambda *_a, **_k: session)
    monkeypatch.setattr(runner.positions_store, "get", lambda *_a: None)

    result = await runner.arm("u1", sig.id)

    assert result["ok"] is False
    assert result["code"] == "signal_stale"


# ------------------------------------------------------ TSL / broker protection
@pytest.mark.asyncio
async def test_broker_stop_is_never_moved_down(monkeypatch):
    pos = _position()
    pos.stop_mode = "both"
    pos.gtt_id = 77
    pos.gtt_at = 95.0
    pos.gtt_qty = 10
    pos.stop = 90.0
    moved = []

    class Client:
        _is_paper = False
        _account_id = "acct-live"

    from app.services.kite_engine import protective_stop

    async def move_stop(*_a, **kwargs):
        moved.append(kwargs["trigger_premium"])
        return True

    monkeypatch.setattr(protective_stop, "move_stop", move_stop)
    monkeypatch.setattr(runner.positions_store, "put", lambda _u, p: p)

    ok = await runner._ensure_protection("u1", Client(), pos)

    assert moved == []
    assert ok is True
    assert pos.gtt_at == pytest.approx(95.0)


# ------------------------------------------------------ exit serialisation
class _ExitClient:
    _is_paper = False
    _account_id = "acct-live"

    def __init__(self) -> None:
        self.sells: list[dict] = []

    async def place_order(self, symbol, side, qty, **kwargs):
        await asyncio.sleep(0)
        self.sells.append({"symbol": symbol, "side": side, "qty": qty, **kwargs})
        return {"order_id": f"EXIT-{len(self.sells)}"}


@pytest.mark.asyncio
async def test_concurrent_exit_triggers_send_one_sell(monkeypatch):
    pos = _position()
    client = _ExitClient()
    monkeypatch.setattr(runner, "session_for", lambda *_a, **_k: SimpleNamespace(note=lambda *_x: None))

    async def get_client(_uid):
        return client

    async def broker_qty(_uid, _c, _p, **_kwargs):
        await asyncio.sleep(0)
        return 10

    async def release(_uid, _c, _p):
        await asyncio.sleep(0)
        return True

    async def settle(_uid, _c, p):
        p.status = "closed"
        return True, "closed"

    monkeypatch.setattr(runner, "_client", get_client)
    monkeypatch.setattr(runner, "_broker_long_quantity", broker_qty)
    monkeypatch.setattr(runner, "_cancel_protection", release)
    monkeypatch.setattr(runner, "_settle_known_exit", settle)
    monkeypatch.setattr(runner.positions_store, "put", lambda _u, p: p)

    results = await asyncio.gather(
        runner._execute_exit("u1", pos, market_ltp=89.0, reason="SL", grade_price=90.0),
        runner._execute_exit("u1", pos, market_ltp=89.0, reason="EOD", grade_price=89.0),
        runner._execute_exit("u1", pos, market_ltp=88.5, reason="SL", grade_price=90.0),
    )

    assert len(client.sells) == 1
    assert sum(1 for ok, _ in results if ok) == 1


@pytest.mark.asyncio
async def test_failed_exit_is_throttled_instead_of_hammering_the_broker(monkeypatch):
    pos = _position()
    calls = []

    async def get_client(_uid):
        return _ExitClient()

    async def broker_qty(_uid, _c, _p, **_kwargs):
        calls.append(1)
        return None

    monkeypatch.setattr(runner, "session_for", lambda *_a, **_k: SimpleNamespace(note=lambda *_x: None))
    monkeypatch.setattr(runner, "_client", get_client)
    monkeypatch.setattr(runner, "_broker_long_quantity", broker_qty)

    first = await runner._execute_exit("u1", pos, market_ltp=89.0, reason="SL")
    second = await runner._execute_exit("u1", pos, market_ltp=88.0, reason="SL")

    assert first == (False, "broker_quantity_unverified")
    assert second == (False, "exit_retry_backoff")
    assert len(calls) == 1


# ------------------------------------------------ realised P&L -> daily-loss cap
class _SettleClient:
    def __init__(self, paper: bool) -> None:
        self._is_paper = paper
        self._account_id = "acct-live"


def _patch_settlement(monkeypatch, *, paper: bool, fill: float, status="COMPLETE"):
    from app.services import canonical_fill_reconciliation as fills

    async def resolved(**_kwargs):
        return fills.BrokerOrderResolution(
            order_id="EXIT-9", status=status, filled_quantity=10,
            average_price=fill, terminal=True, settled=True,
        )

    record = TradeRecord()
    monkeypatch.setattr(fills, "resolve_acknowledged_order", resolved)
    monkeypatch.setattr(runner.positions_store, "put", lambda _u, p: p)
    monkeypatch.setattr(runner.positions_store, "save_record", lambda _u, r: r)
    closed = []
    monkeypatch.setattr(
        runner.positions_store, "close",
        lambda *a, **k: closed.append((a, k)),
    )
    monkeypatch.setattr(
        runner, "session_for",
        lambda _u: SimpleNamespace(record=record, note=lambda *_x: None),
    )
    return record, closed


@pytest.mark.asyncio
async def test_closed_exit_feeds_the_account_daily_loss_accumulator(monkeypatch):
    from app.services.kite_engine import fill_ledger, state

    pos = _position()
    pos.exiting = True
    pos.exit_order_id = "EXIT-9"
    pos.exit_requested_qty = 10
    pos.exit_requested_price = 90.0
    pos.exit_pending_reason = "SL"
    record, closed = _patch_settlement(monkeypatch, paper=False, fill=88.0)
    booked = []
    monkeypatch.setattr(state, "record_realized_pnl_once", lambda uid, _key, pnl, **_k: (booked.append((uid, pnl)), True)[1])
    monkeypatch.setattr(fill_ledger, "inventory", lambda *_a, **_k: None)

    done, status = await runner._settle_known_exit("u1", _SettleClient(paper=True), pos)

    assert (done, status) == (True, "closed")
    assert booked == [("u1", pytest.approx(-120.0))]
    assert pos.exit_booked_order_id == "EXIT-9"
    assert record.realised_inr == pytest.approx(-120.0)
    assert closed


@pytest.mark.asyncio
@pytest.mark.parametrize("held", [10, 0])
async def test_live_exit_is_booked_to_the_accumulator_whatever_the_shared_ledger_holds(monkeypatch, held):
    """Gamma's entries are not in the ledger, and the ledger's cost basis for a
    contract is shared by every strategy. Another strategy holding the strike
    (10) or having closed it earlier (0) must neither swallow Gamma's loss nor
    have its own cost basis consumed by Gamma's SELL."""
    from app.services.kite_engine import fill_ledger, state

    pos = _position()
    pos.exiting = True
    pos.exit_order_id = "EXIT-9"
    pos.exit_requested_qty = 10
    pos.exit_requested_price = 90.0
    pos.exit_pending_reason = "SL"
    _patch_settlement(monkeypatch, paper=False, fill=88.0)
    legacy = []
    ledger = []
    monkeypatch.setattr(state, "record_realized_pnl_once", lambda uid, _key, pnl, **_k: (legacy.append(pnl), True)[1])
    monkeypatch.setattr(
        fill_ledger, "inventory",
        lambda *_a, **_k: SimpleNamespace(net_quantity=held),
    )
    monkeypatch.setattr(fill_ledger, "record", lambda **kw: ledger.append(kw))

    await runner._settle_known_exit("u1", _SettleClient(paper=False), pos)

    assert legacy == [pytest.approx(-120.0)]
    assert ledger == []
    assert pos.exit_booked_order_id == "EXIT-9"


def test_booking_is_idempotent_per_exit_order(monkeypatch):
    from app.services.kite_engine import fill_ledger, state

    pos = _position()
    legacy = []
    monkeypatch.setattr(state, "record_realized_pnl_once", lambda uid, _key, pnl, **_k: (legacy.append(pnl), True)[1])
    monkeypatch.setattr(fill_ledger, "inventory", lambda *_a, **_k: None)

    first = runner._book_exit_pnl(
        "u1", _SettleClient(paper=True), pos,
        order_id="EXIT-1", quantity=10, price=95.0, pnl=-50.0,
    )
    pos.exit_booked_order_id = "EXIT-1"
    second = runner._book_exit_pnl(
        "u1", _SettleClient(paper=True), pos,
        order_id="EXIT-1", quantity=10, price=95.0, pnl=-50.0,
    )

    assert first == runner.BOOK_LEGACY
    assert second == runner.BOOK_ALREADY
    assert legacy == [-50.0]


# -------------------------------------------- broker-executed (GTT) stop exits
def _live_reconcile_env(
    monkeypatch, pos, *, orders, verdict, cancel=None,
    gtt_child_id="GTT-CHILD-1", history=None,
):
    from app.services import live_safety
    from app.services.kite_engine import protective_stop as pstop, state

    halts: list = []
    closes: list = []
    booked: list = []
    notes: list = []

    class Client:
        _is_paper = False
        _account_id = "acct-live"

        async def get_orders(self):
            return orders

        async def get_gtt(self, trigger_id):
            assert int(trigger_id) == int(pos.gtt_id)
            return {
                "id": int(trigger_id),
                "orders": [{
                    "result": {
                        "order_result": {"order_id": gtt_child_id}
                    }
                }],
            }

        async def get_order_history(self, order_id):
            if history is None:
                return []
            return [dict(row) for row in history if row.get("order_id") == order_id]

    async def client(_uid):
        return Client()

    record = TradeRecord()
    session = SimpleNamespace(
        cfg=GammaMoveConfig(stop_mode="both").validate(),
        signals={}, subscribed=set(), record=record,
        note=lambda kind, message: notes.append((kind, message)),
    )

    async def broker_qty(_uid, _c, _p, **_kwargs):
        return 0

    async def subscribe(*_a, **_k):
        return None

    async def stop_status(*_a, **_k):
        return verdict

    async def cancel_result(_c, _gid):
        return cancel or pstop.GONE

    monkeypatch.setattr(runner, "session_for", lambda *_a, **_k: session)
    monkeypatch.setattr(runner, "_client", client)
    monkeypatch.setattr(runner, "_broker_long_quantity", broker_qty)
    monkeypatch.setattr(runner, "_subscribe_watched", subscribe)
    monkeypatch.setattr(runner.positions_store, "open_positions", lambda _u: [pos])
    monkeypatch.setattr(runner.positions_store, "put", lambda _u, p: p)
    monkeypatch.setattr(runner.positions_store, "save_record", lambda _u, r: r)
    monkeypatch.setattr(
        runner.positions_store, "close",
        lambda *a, **k: closes.append((a, k)),
    )
    monkeypatch.setattr(pstop, "stop_status", stop_status)
    monkeypatch.setattr(pstop, "cancel_stop_result", cancel_result)
    monkeypatch.setattr(state, "record_realized_pnl_once", lambda uid, _key, pnl, **_k: (booked.append(pnl), True)[1])
    from app.services.kite_engine import fill_ledger
    monkeypatch.setattr(fill_ledger, "inventory", lambda *_a, **_k: None)
    monkeypatch.setattr(
        live_safety, "set_kill_switch",
        lambda enabled, reason="", **_k: halts.append((enabled, reason)),
    )
    return SimpleNamespace(
        halts=halts, closes=closes, booked=booked, notes=notes, record=record,
    )


def _sell(order_id="GTT-CHILD-1", qty=10, avg=89.5, ts=NOW):
    return {
        "order_id": order_id, "tradingsymbol": "RELIANCE26OCT3000CE",
        "exchange": "NFO", "product": "NRML", "transaction_type": "SELL",
        "status": "COMPLETE", "filled_quantity": qty, "average_price": avg,
        "order_timestamp": ts.replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S"),
    }


@pytest.mark.asyncio
async def test_broker_gtt_stop_fill_closes_position_books_loss_and_does_not_halt(monkeypatch):
    from app.services.kite_engine import protective_stop as pstop

    pos = _position()
    pos.stop_mode = "both"
    pos.gtt_id = 55
    pos.gtt_at = 90.0
    pos.gtt_qty = 10
    env = _live_reconcile_env(
        monkeypatch, pos, orders=[_sell()], verdict=pstop.STOP_TRIGGERED,
    )

    result = await runner.reconcile("u1")

    assert env.halts == []
    assert result["restored"] == 0
    assert len(env.closes) == 1
    args, kwargs = env.closes[0]
    assert args[1] == "RELIANCE26OCT3000CE"
    assert args[2] == "SL"
    assert kwargs["exit_price"] == pytest.approx(89.5)
    assert kwargs["quantity_closed"] == 10
    assert kwargs["pnl_inr"] == pytest.approx(-105.0)
    assert env.booked == [pytest.approx(-105.0)]
    assert env.record.trades == 1
    assert env.record.realised_inr == pytest.approx(-105.0)
    assert pos.exit_booked_order_id == "GTT-CHILD-1"


@pytest.mark.asyncio
async def test_gamma_stop_ignores_field_identical_sell_owned_by_another_strategy(monkeypatch):
    from app.services.kite_engine import protective_stop as pstop

    pos = _position()
    pos.stop_mode = "both"
    pos.gtt_id = 55
    foreign = _sell(order_id="OTHER-STRATEGY-SELL", avg=89.5)
    env = _live_reconcile_env(
        monkeypatch,
        pos,
        orders=[foreign],
        verdict=pstop.STOP_TRIGGERED,
        gtt_child_id="GAMMA-CHILD-55",
    )

    await runner.reconcile("u1")

    assert env.closes == []
    assert env.booked == []
    assert pos.exit_booked_order_id == ""


@pytest.mark.asyncio
async def test_gamma_stop_selects_exact_gtt_child_when_foreign_sell_also_matches(monkeypatch):
    from app.services.kite_engine import protective_stop as pstop

    pos = _position()
    pos.stop_mode = "both"
    pos.gtt_id = 55
    foreign = _sell(order_id="OTHER-STRATEGY-SELL", avg=91.0)
    own = _sell(order_id="GAMMA-CHILD-55", avg=89.5)
    env = _live_reconcile_env(
        monkeypatch,
        pos,
        orders=[foreign, own],
        verdict=pstop.STOP_TRIGGERED,
        gtt_child_id="GAMMA-CHILD-55",
    )

    await runner.reconcile("u1")

    assert len(env.closes) == 1
    assert env.closes[0][1]["exit_price"] == pytest.approx(89.5)
    assert env.booked == [pytest.approx(-105.0)]
    assert pos.exit_booked_order_id == "GAMMA-CHILD-55"


@pytest.mark.asyncio
async def test_gamma_stop_can_reconcile_exact_child_from_order_history(monkeypatch):
    from app.services.kite_engine import protective_stop as pstop

    pos = _position()
    pos.stop_mode = "both"
    pos.gtt_id = 55
    own = _sell(order_id="GAMMA-CHILD-HIST", avg=88.0)
    env = _live_reconcile_env(
        monkeypatch,
        pos,
        orders=[],
        history=[own],
        verdict=pstop.STOP_TRIGGERED,
        gtt_child_id="GAMMA-CHILD-HIST",
    )

    await runner.reconcile("u1")

    assert len(env.closes) == 1
    assert env.closes[0][1]["exit_price"] == pytest.approx(88.0)
    assert pos.exit_booked_order_id == "GAMMA-CHILD-HIST"


@pytest.mark.asyncio
async def test_broker_trailing_stop_fill_is_labelled_trail(monkeypatch):
    from app.services.kite_engine import protective_stop as pstop

    pos = _position()
    pos.stop_mode = "both"
    pos.gtt_id = 55
    pos.trade_state = "T1_TRAIL"
    pos.stop = 100.0
    env = _live_reconcile_env(
        monkeypatch, pos, orders=[_sell(avg=100.0)], verdict=pstop.STOP_TRIGGERED,
    )

    await runner.reconcile("u1")

    assert env.closes[0][0][2] == "TRAIL"
    assert env.record.wins == 1  # exactly at entry is not a loss


@pytest.mark.asyncio
async def test_triggered_stop_without_a_findable_fill_stays_open_and_does_not_halt(monkeypatch):
    from app.services.kite_engine import protective_stop as pstop

    pos = _position()
    pos.stop_mode = "both"
    pos.gtt_id = 55
    env = _live_reconcile_env(
        monkeypatch, pos, orders=[], verdict=pstop.STOP_TRIGGERED,
    )

    await runner.reconcile("u1")

    assert env.closes == []
    assert env.halts == []
    assert env.booked == []


@pytest.mark.asyncio
async def test_unverifiable_stop_with_zero_inventory_still_halts(monkeypatch):
    from app.services.kite_engine import protective_stop as pstop

    pos = _position()
    pos.stop_mode = "both"
    pos.gtt_id = 55
    env = _live_reconcile_env(
        monkeypatch, pos, orders=[], verdict=pstop.STOP_UNVERIFIED,
    )

    await runner.reconcile("u1")

    assert env.closes == []
    assert env.halts and env.halts[-1][0] is True


@pytest.mark.asyncio
async def test_manual_close_cancels_resting_stop_then_forgets_position(monkeypatch):
    from app.services.kite_engine import protective_stop as pstop

    pos = _position()
    pos.stop_mode = "both"
    pos.gtt_id = 55
    env = _live_reconcile_env(
        monkeypatch, pos, orders=[], verdict=pstop.STOP_ABSENT,
        cancel=pstop.CANCELLED,
    )

    result = await runner.reconcile("u1")

    assert result["vanished"] == ["RELIANCE26OCT3000CE"]
    assert env.halts == []
    assert env.closes[0][0][2] == "not at broker on reconcile"
    assert pos.gtt_id == 0


# ------------------------------------------------------------- AUTO visibility
def test_default_stop_mode_is_broker_plus_monitor():
    assert GammaMoveConfig().validate().stop_mode == "both"


@pytest.mark.asyncio
async def test_auto_entry_that_cannot_go_live_alerts_the_operator(monkeypatch):
    sig = _signal(dte=5)  # swing: not live-eligible
    alerts = []
    session = SimpleNamespace(
        signals={}, note=lambda *_a: None,
    )

    async def blocked(_uid, _sid):
        return {
            "ok": False, "code": "reference_swing_protection_unavailable",
            "message": "Swing hold (expiry more than 1 trading day away).",
        }

    from app.services.notifications import telegram_lifecycle
    monkeypatch.setattr(runner, "session_for", lambda *_a, **_k: session)
    monkeypatch.setattr(runner, "auto_execute", lambda _u: True)
    monkeypatch.setattr(runner, "arm", blocked)
    monkeypatch.setattr(runner, "_persist_signals", lambda _s: None)
    monkeypatch.setattr(
        telegram_lifecycle, "enqueue_best_effort",
        lambda uid, **kwargs: alerts.append((uid, kwargs)),
    )

    await runner._register_signals("u1", [sig])

    assert len(alerts) == 1
    kwargs = alerts[0][1]
    assert kwargs["event_type"] == "ENTRY NOT TAKEN"
    assert kwargs["title"] == "RELIANCE26OCT3000CE"
    assert "SWING" in kwargs["detail"] and "expiry more than 1 trading day" in kwargs["detail"]


# ------------------------------------------------- reconcile cost and alerts
def test_synchronised_broker_stop_is_not_rewritten_every_scheduler_pass():
    pos = _position()
    pos.stop_mode = "both"
    pos.gtt_id = 55
    pos.gtt_qty = 10
    pos.gtt_at = pos.stop

    assert runner._protection_due("u1", pos) is True   # first look
    assert runner._protection_due("u1", pos) is False  # inside the recheck window
    pos.quantity = 6                                    # resized -> must re-sync now
    assert runner._protection_due("u1", pos) is True


def test_unprotected_position_is_always_due():
    pos = _position()
    pos.stop_mode = "both"
    assert runner._protection_due("u1", pos) is True
    assert runner._protection_due("u1", pos) is True


def test_unresolved_order_alert_is_critical_and_states_no_retry(monkeypatch):
    from app.services.notifications import telegram_lifecycle

    sent = []
    monkeypatch.setattr(
        telegram_lifecycle, "enqueue_best_effort",
        lambda uid, **kwargs: sent.append(kwargs),
    )
    runner._alert_unresolved("u1", _position(), what="EXIT", oid="X1")

    assert sent[0]["event_type"] == "EXIT UNRESOLVED"
    assert sent[0]["severity"] == "CRITICAL"
    assert "no retry sent" in sent[0]["detail"]


def test_new_gamma_lifecycle_events_render_in_the_shared_vocabulary():
    from app.services.notifications import lifecycle_format as fmt

    for event, detail in (
        ("ENTRY NOT TAKEN", "INTRADAY · This E1 signal is no longer current."),
        ("PNL UNRECONCILED", "Exit X1 realised P&L not booked · Qty 10"),
        ("EXIT UNRESOLVED", "Order X1 outcome unknown · Qty 10 · no retry sent"),
        ("POSITION CLOSED", "Qty 10 · Exit ₹89.50 · SL · P&L ₹-105.00"),
    ):
        text = fmt.render_lifecycle(
            strategy="Gamma-Unwind", event_type=event,
            title="RELIANCE26OCT3000CE", detail_text=detail,
        )
        assert "Gamma Unwind" in text
        assert event in text.upper()
    closed = fmt.canonicalize_detail("POSITION CLOSED", "Qty 10 · Exit ₹89.50 · SL · P&L ₹-105.00")
    assert closed == "Exit ₹89.50 · P&L -₹105.00 · Qty 10 · Reason SL"
