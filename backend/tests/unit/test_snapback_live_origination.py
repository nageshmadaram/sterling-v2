"""Package D: Snapback originates LIVE orders through the canonical execution path.

These tests run the real chain -- plan producer, dispatcher, live executor,
canonical execution service, order journal, fill ledger, protective stop -- against
a broker double that behaves like a live Kite transport. What they pin:

* a fresh swing signal becomes ONE hedged, protected position;
* a duplicate / restarted signal never becomes a second order;
* an unknown submission outcome is never resent and engages recovery;
* an unconfirmed broker stop engages the kill switch and is retried;
* the kill switch, a stale row, a daily-loss halt or any SafetySupervisor refusal
  stops new entries without touching exits.
"""
from __future__ import annotations

import asyncio
import itertools
import uuid
from types import SimpleNamespace

import pytest

from tests.snapback_live_harness import (
    FUTURE_SYMBOL, OPTION_SYMBOL, SCALP_NOW, SWING_NOW, LiveBroker, scalp_row, stub_beta,
    swing_row,
)

_counter = itertools.count(1)


def _signal_id(prefix: str = "snapback:fade_up:NIFTY") -> str:
    return f"{prefix}:{uuid.uuid4().hex[:12]}"


@pytest.fixture
def env(tmp_path, monkeypatch, allow_live_execution):
    from app.core.state_paths import state_root  # noqa: F401
    from app.services import live_safety, snapback
    from app.engines.snapback import SnapbackConfig
    from app.services.snapback_live_positions import LivePositionStore
    from app.services.snapback_plan_store import PlanStore

    monkeypatch.setenv("STERLING_INTENT_DB", str(tmp_path / "intents.db"))
    monkeypatch.setenv("STERLING_PLAN_DB_PATH", str(tmp_path / "plans.db"))
    monkeypatch.setenv("STERLING_SNAPBACK_LIVE_DB", str(tmp_path / "live.db"))
    monkeypatch.setenv("STERLING_NEW_TRADES_HALT_PATH", str(tmp_path / "halt.json"))

    # A known-good control plane: earlier tests in a session may have left the
    # shared database in RECOVERY_REQUIRED or the kill switch engaged.
    from app.services import db

    db.init()
    db.set_execution_control(operator_state="RUNNING", recovery_state="CLEAN",
                             reason_code="test_session", reason="snapback live tests",
                             actor="tests")
    live_safety.set_kill_switch(False, "test start")

    uid = f"sbuser-{next(_counter)}-{uuid.uuid4().hex[:6]}"
    cfg = SnapbackConfig(enabled=True, auto_execute=True, trading_mode="swing")
    monkeypatch.setattr(snapback, "get_config", lambda u=None: cfg)
    # The shared master AUTO switch is on.
    from app.services.kite_engine import state as engine_state

    monkeypatch.setattr(engine_state, "get_config",
                        lambda u: SimpleNamespace(auto_execute=True))
    # System health and reconciliation are the family gate's own inputs. The suite
    # states a healthy, reconciled system; tests below flip it to prove it blocks.
    from app.services import snapback_readiness

    ctx_state = {"state": "LIVE_ELIGIBLE", "status": "HEALTHY", "reconciled": True}

    def readiness(u="default"):
        return snapback_readiness.ReadinessState(
            state=ctx_state["state"], system_status=ctx_state["status"],
            evidence_verdict="INCONCLUSIVE", broker_reconciled=ctx_state["reconciled"])

    monkeypatch.setattr(snapback_readiness, "readiness_state", readiness)

    broker = LiveBroker(account_id=f"KITE-{uid}")
    plan_store = PlanStore(db_path=str(tmp_path / "plans.db"))
    positions = LivePositionStore(tmp_path / "live.db")
    ctx = SimpleNamespace(uid=uid, cfg=cfg, broker=broker, plan_store=plan_store,
                          positions=positions, rows=[], clock=SWING_NOW,
                          monkeypatch=monkeypatch, tmp_path=tmp_path, health=ctx_state)

    def build_dispatcher():
        from app.services.snapback_dispatcher import SnapbackDispatcher
        from app.services.snapback_live_exits import SnapbackExitManager
        from app.services.snapback_live_wiring import SnapbackProtection

        manager = SnapbackExitManager(
            positions=positions, protection=SnapbackProtection(positions=positions),
            clock=lambda: ctx.clock)

        async def noscan(_uid):
            return None

        return SnapbackDispatcher(
            plan_store=plan_store, positions=positions, exit_manager=manager,
            client_factory=lambda _uid: broker, rows_provider=lambda _uid: list(ctx.rows),
            scanner=noscan, clock=lambda: ctx.clock)

    ctx.dispatcher = build_dispatcher()
    ctx.build_dispatcher = build_dispatcher
    # Entry hedge sizing should not need daily candles.
    from app.services import snapback_plan_producer as producer

    original = producer.build_plan

    async def build_with_beta(row, c):
        if c.beta_for is None:
            c.beta_for = stub_beta()
        return await original(row, c)

    monkeypatch.setattr("app.services.snapback_dispatcher.build_plan", build_with_beta, raising=False)
    monkeypatch.setattr(producer, "build_plan", build_with_beta)
    yield ctx
    try:
        live_safety.set_kill_switch(False, "test reset")
    except Exception:
        pass


def run(coro):
    return asyncio.run(coro)


def _cycle(env):
    return run(env.dispatcher.cycle(env.uid))


# ---------------------------------------------------------------- happy path


def test_a_fresh_swing_signal_becomes_one_hedged_protected_position(env):
    env.rows = [swing_row(signal_id=_signal_id())]

    status = _cycle(env)

    assert status["state"] == "ready", status
    assert status["entries"] == 1, env.dispatcher.status(env.uid)
    placed = [(o["symbol"], o["side"], o["qty"]) for o in env.broker.placed]
    assert placed == [(OPTION_SYMBOL, "BUY", 75), (FUTURE_SYMBOL, "BUY", 75)]
    pos = env.positions.open_positions(env.uid)[0]
    assert pos.status == "OPEN"
    assert pos.option_qty == 75 and pos.hedge_qty == 75
    assert pos.protection_state == "ACTIVE" and pos.gtt_id in env.broker.gtts
    # the stop is derived from the CONFIRMED fill, not the quote
    assert pos.option_entry_price == pytest.approx(300.0)
    assert pos.stop_premium == pytest.approx(195.0)
    # the entry went out as a marketable limit, in NRML
    option_order = env.broker.placed[0]
    assert option_order["order_type"].lower().startswith("limit")
    assert option_order["limit_price"] >= 300.0
    assert option_order["product"] == "NRML"
