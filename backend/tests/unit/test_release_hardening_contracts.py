"""Release regressions: exact ownership, durable protection and send-time safety."""
import asyncio
import time
from dataclasses import replace
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

import pytest

from app.services import db, gamma_move_runner, protection_health, snapback_live_exits
from app.services.execution_service import CanonicalExecutionService, ExecutionRequest, RiskApproval
from app.services.exchanges.kite.client import KiteClient
from app.services.exchanges.kite.errors import KiteError
from app.services.kite_engine import positions, monitor
from app.services.snapback_family_mode import (
    INTENT_REDUCE, canonical_broker_capability, guard_broker_reduction, guard_broker_write,
)


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_DB_PATH", str(tmp_path / "hardening.db"))
    db.init()
    positions._positions.clear()
    yield
    positions._positions.clear()


@pytest.mark.parametrize("origin", ["sterling-kite", "gamma_move", "snapback", "price_rules"])
def test_protection_fault_blocks_every_origin_and_survives_reload(isolated, origin):
    uid, account = "hardening-owner", "hardening-account"
    p = positions.OpenPosition(uid=uid, symbol="OPT", exchange="NFO", qty=40,
        account_id=account, status=positions.OPEN, stop_premium=80, gtt_id=91,
        protection_pending=True)
    positions._positions[uid] = {p.symbol: p}
    positions.persist_strict(uid)
    positions._positions.clear()  # restart, with no in-memory halt
    allowed, reason = CanonicalExecutionService().is_trading_allowed(
        uid=uid, account_id=account, strategy_id=origin)
    assert allowed is False
    assert "protection" in reason.lower()
    assert protection_health.admission_blocker(uid, account)[0] == "protection_integrity"
    # Repair on the exact owner removes the mechanical fence.
    loaded = positions.get(uid, "OPT")
    loaded.protection_pending = False
    positions.persist_strict(uid)
    assert protection_health.admission_blocker(uid, account) == ("", "")


@pytest.mark.asyncio
@pytest.mark.parametrize("quantity", [-50, float("nan"), 1.5, None])
async def test_gamma_invalid_signed_quantity_never_becomes_flat(monkeypatch, quantity):
    p = NS(account_id="a", quantity=40, product="NRML",
           instrument=NS(tradingsymbol="OPT", exchange="NFO"))
    client = NS(_is_paper=False, _account_id="a", get_positions_raw=AsyncMock(return_value={
        "net": [{"tradingsymbol":"OPT", "exchange":"NFO", "product":"NRML", "quantity":quantity}]}))
    monkeypatch.setattr("app.services.snapback_reconciliation._canonical_live_positions", lambda *_: [])
    assert await gamma_move_runner._broker_long_quantity("u", client, p, current_registered=False) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("remaining", [40, 25, 0])
async def test_restored_snapback_stop_never_adopts_surplus(monkeypatch, remaining):
    p = NS(position_id="p", uid="u", account_id="a", option_qty=40,
           option_exit_qty=40-remaining, protection_state="NONE")
    protection = NS(ensure=AsyncMock())
    manager = snapback_live_exits.SnapbackExitManager(
        positions=NS(get=lambda _: p), protection=protection)
    monkeypatch.setattr(snapback_live_exits, "_broker_net", AsyncMock(return_value={}))
    monkeypatch.setattr(manager, "_held_beyond_siblings", lambda *_: 60)
    await manager._restore_stop_over_held(NS(_is_paper=False), p)
    if remaining:
        assert protection.ensure.await_args.args[2] == remaining
    else:
        protection.ensure.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("lookup", ["missing", "unknown", "raises", "in-flight"])
async def test_forced_snapback_exit_cannot_bypass_unknown_stop(monkeypatch, lookup):
    p = NS(position_id="p", uid="u", option_symbol="OPT", option_qty=40, exit_reason="FLATTEN")
    value = None if lookup == "unknown" else NS(unclear=False, in_flight=True, ours=[])
    protection = NS() if lookup == "missing" else NS(unrecorded_stops=AsyncMock(
        side_effect=TimeoutError() if lookup == "raises" else None, return_value=value))
    manager = snapback_live_exits.SnapbackExitManager(protection=protection)
    monkeypatch.setattr(snapback_live_exits, "alert", lambda *a, **k: None)
    assert await manager._release_unrecorded_stop(NS(), p, forced=True) == "stop_not_released"


@pytest.mark.asyncio
@pytest.mark.parametrize("foreign", [True, False])
async def test_supertrend_leaves_foreign_and_unclaimed_children_untouched(monkeypatch, foreign):
    p = NS(account_id="a", symbol="OPT", exchange="NFO", direction="long", protection_pending=True)
    monkeypatch.setattr(monitor, "_resting_sell_triggers", AsyncMock(return_value=[{"id":91}]))
    monkeypatch.setattr("app.services.external_positions.owned_gtt_ids", lambda _: frozenset({"91"}) if foreign else frozenset())
    monkeypatch.setattr(monitor, "_settle_fired_child", AsyncMock())
    monkeypatch.setattr(monitor, "_critical", lambda *a: None)
    monkeypatch.setattr(positions, "persist_strict", lambda _: None)
    result = await monitor._clear_unknown_protection(NS(), "u", p, "NRML", forced=True)
    assert result == ("clear" if foreign else "blocked")
    monitor._settle_fired_child.assert_not_awaited()


def test_live_reduction_requires_bound_and_raw_stop_mutations_refuse(monkeypatch):
    monkeypatch.delenv("STERLING_FAMILY_MODE", raising=False)
    with canonical_broker_capability("close", intent=INTENT_REDUCE):
        with pytest.raises(PermissionError, match="bound"):
            guard_broker_reduction(exchange="NFO", tradingsymbol="OPT", transaction_type="SELL", quantity=40)
    for operation in ("place_gtt", "modify_gtt", "cancel_gtt", "cancel_order"):
        with pytest.raises(PermissionError):
            guard_broker_write(operation)


@pytest.mark.asyncio
@pytest.mark.parametrize("timestamp", [0, -1, "future", "stale"])
async def test_invalid_risk_clock_sends_nothing(timestamp, monkeypatch):
    monkeypatch.setattr("app.core.lane_registry.entry_transport_allowed", lambda _: True)
    now = int(time.time()*1000)
    stamp = now+60000 if timestamp == "future" else now-61000 if timestamp == "stale" else timestamp
    req = ExecutionRequest(uid="u", account_id="a", strategy_id="sterling-kite", generation_id="g",
                           signal_id="s", exchange="NFO", symbol="OPT", side="BUY", quantity=40)
    proof = RiskApproval(approval_id="p", uid="u", account_id="a", strategy_id="sterling-kite",
                        generation_id="g", signal_id="s", symbol="OPT", side="BUY", quantity=40,
                        timestamp_ms=stamp, available_capital=100000)
    broker = NS(_account_id="a", place_order=AsyncMock())
    result = await CanonicalExecutionService().submit_order(req, broker_client=broker, risk_approval=proof)
    assert not result.success and "timestamp" in result.error
    broker.place_order.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("after_first_send", [False, True])
async def test_halt_during_limiter_or_429_stops_next_send(monkeypatch, after_first_send):
    from app.services.exchanges.kite import client as transport
    from app.services.exchanges.kite import constants as K
    blocked = False
    broker = KiteClient.__new__(KiteClient)
    broker._is_paper = False
    async def acquire():
        nonlocal blocked
        if not after_first_send:
            blocked = True
    async def send(*args):
        nonlocal blocked
        blocked = True
        raise KiteError("rate limit", status_code=429)
    broker._auth_post = AsyncMock(side_effect=send)
    monkeypatch.setattr(transport, "_GLOBAL_KITE_ORDER_LIMITER", NS(acquire=acquire))
    def check():
        if blocked:
            raise PermissionError("halted")
    with canonical_broker_capability("entry", before_send=check):
        with pytest.raises(PermissionError, match="halted"):
            await broker._place(variety=K.VARIETY_REGULAR, exchange="NFO", tradingsymbol="OPT",
                transaction_type="BUY", quantity=40, product="NRML", order_type="LIMIT", price=100,
                allow_amo=False)
    assert broker._auth_post.await_count == int(after_first_send)

@pytest.mark.asyncio
@pytest.mark.parametrize("leg", ["option", "hedge"])
async def test_snapback_projection_ack_failure_replays_without_counting_fill_twice(monkeypatch, leg):
    from app.services import canonical_fill_reconciliation as fills
    from app.services.snapback_live_positions import LivePosition
    p = LivePosition(position_id="p", uid="u", account_id="a", signal_id="s", lane="swing",
        underlying="U", side="fade_up", option_type="PE", option_exchange="NFO",
        option_symbol="OPT", option_qty=40, option_entry_price=100, hedge_symbol="FUT",
        hedge_exchange="NFO", hedge_side="SELL", hedge_qty=40, hedge_entry_price=200,
        option_exit_order_id="exit", option_exit_requested=40,
        hedge_exit_order_id="exit", hedge_exit_requested=40)
    def mutate(_id, fn):
        fn(p)
        return p
    resolution = NS(filled_quantity=40, average_price=90, settled=True,
        reconciliation_required=False, projection_version=1, intent_key="i", exchange_ts_ms=1234)
    manager = snapback_live_exits.SnapbackExitManager(positions=NS(mutate=mutate),
        resolver=AsyncMock(return_value=resolution))
    monkeypatch.setattr(manager, "_book_exit", Mock(return_value=True))
    monkeypatch.setattr(fills, "mark_projected", Mock(side_effect=[OSError("disk"), True]))
    assert await manager._resolve_exit(NS(), p, leg=leg, order_id="exit") == "projection_pending"
    assert getattr(p, f"{leg}_exit_order_id") == "exit"
    assert getattr(p, f"{leg}_exit_qty") == 40
    assert await manager._resolve_exit(NS(), p, leg=leg, order_id="exit") == "done"
    assert getattr(p, f"{leg}_exit_qty") == 40
    assert getattr(p, f"{leg}_exit_order_id") == ""
    assert manager._book_exit.call_args.kwargs["exchange_ts_ms"] == 1234


@pytest.mark.asyncio
async def test_snapback_ledger_failure_keeps_exit_identity_and_projection_pending(monkeypatch):
    from app.services import canonical_fill_reconciliation as fills
    p = NS(uid="u", account_id="a", option_exit_requested=40, option_entry_price=100)
    resolution = NS(filled_quantity=40, average_price=90, exchange_ts_ms=1234)
    manager = snapback_live_exits.SnapbackExitManager(resolver=AsyncMock(return_value=resolution))
    monkeypatch.setattr(manager, "_book_exit", Mock(return_value=False))
    monkeypatch.setattr(fills, "mark_projected", Mock())
    assert await manager._resolve_exit(NS(), p, leg="option", order_id="exit") == "accounting_pending"
    fills.mark_projected.assert_not_called()


def test_gamma_accounting_fault_blocks_other_strategies_even_when_halt_write_failed(monkeypatch):
    from app.services import gamma_move_positions
    p = NS(account_id="a", pnl_reconciliation_required=True, is_open=True, pnl_flagged_day="")
    monkeypatch.setattr(gamma_move_positions, "load", lambda _: {"OPT":p})
    monkeypatch.setattr(positions, "unreadable_reason", lambda _: "")
    from app.services.protection_health import admission_blocker
    assert admission_blocker("u", "a")[0] == "accounting_integrity"
    assert admission_blocker("u", "foreign")[0] != "accounting_integrity"

@pytest.mark.asyncio
async def test_failed_recovery_compare_and_set_never_reports_clean(isolated, monkeypatch):
    def refuse(**kwargs):
        raise RuntimeError("revision changed")
    monkeypatch.setattr(db, "set_recovery_state", refuse)
    result = await CanonicalExecutionService().startup_recovery(uid="u", account_id="a")
    assert result["recovery_state"] == "RECOVERY_REQUIRED"
    assert result["recovery_write_error"] is True


@pytest.mark.asyncio
async def test_account_transition_waits_until_canonical_submit_finishes(isolated, monkeypatch):
    from app.services.execution_identity_lock import account_lock
    service = CanonicalExecutionService()
    reached = asyncio.Event()
    release = asyncio.Event()
    moved = asyncio.Event()
    async def submit(*args, **kwargs):
        reached.set()
        await release.wait()
        return NS(success=False, status="REJECTED", error="test")
    monkeypatch.setattr(service, "_submit_order", submit)
    monkeypatch.setattr("app.services.notifications.system_alerts.execution_result", lambda *a, **k: None, raising=False)
    req = ExecutionRequest(uid="u", account_id="a", strategy_id="sterling-kite", generation_id="g",
                           signal_id="s", exchange="NFO", symbol="OPT", side="BUY", quantity=40)
    task = asyncio.create_task(service.submit_order(req))
    await reached.wait()
    async def transition():
        async with account_lock("u"):
            moved.set()
    change = asyncio.create_task(transition())
    await asyncio.sleep(0)
    assert not moved.is_set()
    release.set()
    await asyncio.gather(task, change)
    assert moved.is_set()

@pytest.mark.asyncio
async def test_missing_protection_ack_cannot_clear_the_durable_fence(isolated, monkeypatch):
    from app.services.kite_engine import protective_stop
    p = positions.OpenPosition(uid="u", account_id="a", symbol="OPT", exchange="NFO", qty=40,
        order_id="entry", status=positions.OPEN, fill_price=100, stop_premium=80, product="NRML")
    positions._positions["u"] = {"OPT":p}
    positions.persist_strict("u")
    monkeypatch.setattr(protective_stop, "verify_protection_inventory", AsyncMock(return_value=(True,"", "NRML")))
    monkeypatch.setattr(protective_stop, "_reference_price", AsyncMock(return_value=100))
    broker = NS(_is_paper=False, _account_id="a", place_gtt=AsyncMock(return_value={}))
    result = await CanonicalExecutionService().place_protection(uid="u", account_id="a", position_id="entry",
        protection_params={"symbol":"OPT"}, broker_client=broker)
    assert result.status == "UNKNOWN" and not result.success
    positions._positions.clear()
    assert positions.get("u", "OPT").protection_pending is True
    assert protection_health.admission_blocker("u", "a")[0] == "protection_integrity"


@pytest.mark.asyncio
async def test_cancel_timeout_does_not_claim_rejection_or_completion(isolated):
    from app.services.kite_engine import order_journal
    intent = order_journal.reserve(uid="u", account_id="a", strategy_id="sterling-kite", generation_id="g",
        signal_id="s", exchange="NFO", symbol="OPT", side="BUY", quantity=40, payload={"intent_type":"ENTRY"})
    order_journal.claim_submission(intent.intent_key)
    order_journal.acknowledge(intent.intent_key, "entry")
    broker = NS(_is_paper=False, _account_id="a", cancel_order=AsyncMock(side_effect=TimeoutError("lost reply")))
    result = await CanonicalExecutionService().cancel_order(uid="u", account_id="a", order_id="entry", broker_client=broker)
    assert result.status == "UNKNOWN" and not result.success
    assert order_journal.get(intent.intent_key).state == "SUBMITTED"

@pytest.mark.asyncio
async def test_owned_gamma_stop_fill_retains_prior_exchange_day(monkeypatch):
    p = NS(quantity=40, product="NRML", instrument=NS(tradingsymbol="OPT", exchange="NFO"))
    order = dict(order_id="child", tradingsymbol="OPT", exchange="NFO", product="NRML",
        transaction_type="SELL", status="COMPLETE", quantity=40, filled_quantity=40,
        average_price=90, exchange_timestamp="2026-10-09 15:29:00")
    broker = NS(get_orders=AsyncMock(return_value=[order]))
    monkeypatch.setattr(gamma_move_runner, "_gtt_child_order_id", AsyncMock(return_value="child"))
    days = {}
    assert await gamma_move_runner._find_triggered_exit_fills(broker, p, execution_days=days) == [("child",40,90)]
    assert days == {"child":"2026-10-09"}


def test_snapback_entry_ledger_uses_durable_exchange_time(monkeypatch):
    from app.services.snapback_live_wiring import SnapbackExecutionAdapter
    from app.services.kite_engine import fill_ledger
    broker = SnapbackExecutionAdapter(plan_store=NS())
    monkeypatch.setattr(broker, "_record_leg", Mock())
    record = Mock(return_value=NS(reconciliation_required=False))
    monkeypatch.setattr(fill_ledger, "record", record)
    request = NS(account_id="a", uid="u", symbol="OPT", exchange="NFO", side="BUY", payload={})
    assert broker._book_fill(request, NS(exchange_ts_ms=1234), "entry", 40, 100)
    assert record.call_args.kwargs["exchange_ts_ms"] == 1234
    assert broker._record_leg.call_args.kwargs["exchange_ts_ms"] == 1234
