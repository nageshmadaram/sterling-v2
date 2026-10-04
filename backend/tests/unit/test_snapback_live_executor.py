"""1.5 P0-LIVE: ARMED plans become orders through one ordered, resumable path.

Before this, arming transitioned a durable plan to ARMED and nothing consumed it.
submit_plan() existed with no production caller, submitted only the option leg,
and passed no RiskApproval — so the canonical service would have refused it
anyway. "Live execution complete" was too strong a claim.

The ordering here is the whole safety argument:

    revalidate → reconcile → risk approve → option fill
    → hedge sized to the CONFIRMED fill → protection → OPEN

Every step is gated on the previous one having actually happened, not on its
acknowledgement. Nothing below places an order itself; the executor asks the
canonical execution service, which is the only thing that talks to a broker.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List

import pytest


# ------------------------------------------------------------ the broker spy


class BrokerSpy:
    """Records every order the executor asks for, in order."""

    def __init__(self, *, fills=None, reject=(), unknown=()):
        self.submitted: List[Dict[str, Any]] = []
        self.fills = fills or {}
        self.reject = set(reject)
        self.unknown = set(unknown)
        self.protection: List[Dict[str, Any]] = []

    async def submit_order(self, request, broker_client=None, risk_approval=None,
                           risk_approved=False):
        from app.services.execution_service import ExecutionResult

        self.submitted.append({
            "symbol": request.symbol,
            "side": request.side,
            "quantity": request.quantity,
            "exchange": request.exchange,
            "risk_approval": risk_approval,
            "broker_client": broker_client,
            "exposure_effect": getattr(request, "exposure_effect", None),
            "signal_id": request.signal_id,
        })

        if request.symbol in self.reject:
            return ExecutionResult(success=False, status="REJECTED",
                                   error="broker rejected")
        if request.symbol in self.unknown:
            return ExecutionResult(success=False, status="UNKNOWN")

        filled = self.fills.get(request.symbol, request.quantity)
        return ExecutionResult(
            success=True, status="FILLED", order_id=f"ORD-{len(self.submitted)}",
            filled_quantity=filled, filled_price=100.0,
        )

    async def place_protection(self, **kwargs):
        self.protection.append(kwargs)
        return {"state": "ACTIVE", "protection_id": "GTT-1"}


@pytest.fixture
def plan_store(tmp_path):
    from app.services.snapback_plan_store import PlanStore

    store = PlanStore(db_path=str(tmp_path / "plans.db"))
    store.publish(
        plan_id="PLAN-1", opportunity_id="OPP-1", account_id="KITE-FAMILY",
        option_symbol="NIFTY26OCT25000PE", option_quantity=150,
        futures_symbol="NIFTY26OCTFUT", target_futures_quantity=75,
        max_option_price=120.0, hedge_price_limit=25100.0,
        risk_amount=9000.0, cash_required=9000.0, margin_required=140000.0,
        policy_snapshot_hash="pol-1", runtime_build_sha="build-1",
        option_exchange="NFO", futures_exchange="NFO",
    )
    store.arm("PLAN-1", expected_revision=1, idempotency_key="k1", armed_by="u1")
    return store


def _executor(plan_store, broker, **over):
    from app.services.snapback_live_executor import SnapbackLiveExecutor

    defaults = dict(
        plan_store=plan_store,
        execution_service=broker,
        reconciliation_fn=lambda account_id: {"clean": True, "fresh": True},
        risk_approval_fn=lambda **k: _approval(**k),
        protection_fn=broker.place_protection,
        revalidate_fn=lambda plan: {"ok": True, "reasons": []},
    )
    defaults.update(over)
    return SnapbackLiveExecutor(**defaults)


def _approval(**kwargs):
    from app.services.execution_service import RiskApproval

    return RiskApproval(
        approval_id="RISK-1", uid=kwargs.get("uid", "default"),
        account_id=kwargs.get("account_id", "KITE-FAMILY"),
        symbol=kwargs.get("symbol", ""), side=kwargs.get("side", "BUY"),
        quantity=int(kwargs.get("quantity", 0)),
        generation_id=kwargs.get("generation_id", "gen-1"),
        approved=True,
        strategy_id=kwargs.get("strategy_id", ""),
        signal_id=kwargs.get("signal_id", ""),
    )


# --------------------------------------------------------------- happy path


def test_the_full_sequence_runs_in_order(plan_store):
    broker = BrokerSpy()
    result = asyncio.run(_executor(plan_store, broker).execute("PLAN-1"))

    assert result.status == "OPEN"
    symbols = [o["symbol"] for o in broker.submitted]
    assert symbols == ["NIFTY26OCT25000PE", "NIFTY26OCTFUT"]
    assert [o["side"] for o in broker.submitted] == ["BUY", "BUY"]
    assert broker.protection, "protection was never established"


def test_exact_plan_account_client_is_passed_to_canonical_execution(plan_store):
    broker = BrokerSpy()
    sentinel_client = object()
    seen = []

    async def resolve(uid, account_id):
        seen.append((uid, account_id))
        return sentinel_client

    result = asyncio.run(
        _executor(plan_store, broker, broker_client_fn=resolve).execute("PLAN-1")
    )

    assert result.status == "OPEN"
    assert seen == [("default", "KITE-FAMILY")]
    assert broker.submitted
    assert all(o["broker_client"] is sentinel_client for o in broker.submitted)


def test_missing_exact_plan_account_client_refuses_before_submit(plan_store):
    broker = BrokerSpy()

    async def missing(uid, account_id):
        return None

    result = asyncio.run(
        _executor(plan_store, broker, broker_client_fn=missing).execute("PLAN-1")
    )

    assert result.status == "REFUSED"
    assert result.reasons == ["broker_client_unavailable"]
    assert broker.submitted == []


def test_every_order_carries_a_risk_approval(plan_store):
    """The canonical service refuses an exposure increase without one, so an
    executor that omits it fails closed — but silently, at the broker boundary."""
    broker = BrokerSpy()
    asyncio.run(_executor(plan_store, broker).execute("PLAN-1"))

    for order in broker.submitted:
        assert order["risk_approval"] is not None, order["symbol"]


def test_the_plan_is_consumed_exactly_once(plan_store):
    broker = BrokerSpy()
    executor = _executor(plan_store, broker)

    asyncio.run(executor.execute("PLAN-1"))
    second = asyncio.run(executor.execute("PLAN-1"))

    assert second.status in ("ALREADY_CONSUMED", "REFUSED")
    assert len([o for o in broker.submitted if o["side"] == "BUY"]) == 2


def test_only_an_armed_plan_executes(plan_store):
    broker = BrokerSpy()
    plan_store.cancel("PLAN-1", reason="superseded")

    result = asyncio.run(_executor(plan_store, broker).execute("PLAN-1"))

    assert result.status == "REFUSED"
    assert broker.submitted == []


def test_durable_intent_failure_refuses_before_any_broker_submit(plan_store):
    broker = BrokerSpy()

    class BrokenIntentStore:
        def reserve(self, intent):
            raise OSError("disk full")

    result = asyncio.run(
        _executor(plan_store, broker, intent_store=BrokenIntentStore()).execute("PLAN-1")
    )

    assert result.status == "REFUSED"
    assert result.reasons == ["durable_intent_reservation_failed"]
    assert result.stop_new_entries is True
    assert broker.submitted == []


# -------------------------------------------------------- gates before order


def test_a_dirty_book_stops_before_any_order(plan_store):
    broker = BrokerSpy()
    executor = _executor(
        plan_store, broker,
        reconciliation_fn=lambda account_id: {"clean": False, "fresh": True},
    )

    result = asyncio.run(executor.execute("PLAN-1"))

    assert result.status == "REFUSED"
    assert broker.submitted == []


def test_a_stale_reconciliation_stops_before_any_order(plan_store):
    broker = BrokerSpy()
    executor = _executor(
        plan_store, broker,
        reconciliation_fn=lambda account_id: {"clean": True, "fresh": False},
    )

    result = asyncio.run(executor.execute("PLAN-1"))

    assert result.status == "REFUSED"
    assert broker.submitted == []


def test_a_failed_revalidation_stops_before_any_order(plan_store):
    """A plan armed minutes ago may no longer be the right trade."""
    broker = BrokerSpy()
    executor = _executor(
        plan_store, broker,
        revalidate_fn=lambda plan: {"ok": False, "reasons": ["quote stale"]},
    )

    result = asyncio.run(executor.execute("PLAN-1"))

    assert result.status == "REFUSED"
    assert broker.submitted == []


def test_a_denied_risk_approval_stops_before_any_order(plan_store):
    broker = BrokerSpy()

    def denied(**kwargs):
        from dataclasses import replace

        return replace(_approval(**kwargs), approved=False, reason="margin")

    result = asyncio.run(_executor(plan_store, broker, risk_approval_fn=denied)
                         .execute("PLAN-1"))

    assert result.status == "REFUSED"
    assert broker.submitted == []


# ------------------------------------------------------------- partial fills


def test_the_hedge_is_sized_to_the_confirmed_option_fill(plan_store):
    """Half the option filled means half the hedge. Sizing to the intended
    quantity would leave a naked futures leg."""
    broker = BrokerSpy(fills={"NIFTY26OCT25000PE": 75})   # half of 150

    asyncio.run(_executor(plan_store, broker).execute("PLAN-1"))

    hedge = next(o for o in broker.submitted if o["symbol"] == "NIFTY26OCTFUT")
    assert hedge["quantity"] == 38   # ceil(75/150 * 75)


def test_fade_down_call_uses_short_futures_hedge(tmp_path):
    from app.services.snapback_live_executor import _hedge_side
    from app.services.snapback_plan_store import PlanStore

    store = PlanStore(db_path=str(tmp_path / "fade-down-plans.db"))
    store.publish(
        plan_id="PLAN-DOWN", opportunity_id="OPP-DOWN",
        account_id="KITE-FAMILY",
        option_symbol="NIFTY26OCT25000CE", option_quantity=150,
        futures_symbol="NIFTY26OCTFUT", target_futures_quantity=75,
        max_option_price=120.0, hedge_price_limit=25100.0,
        risk_amount=9000.0, cash_required=9000.0, margin_required=140000.0,
        policy_snapshot_hash="pol-down", runtime_build_sha="build-1",
        option_exchange="NFO", futures_exchange="NFO",
        signal_side="fade_down", option_type="CE",
    )
    store.arm(
        "PLAN-DOWN", expected_revision=1,
        idempotency_key="down-1", armed_by="u1",
    )
    broker = BrokerSpy()

    result = asyncio.run(_executor(store, broker).execute("PLAN-DOWN"))

    assert _hedge_side(store.get("PLAN-DOWN")) == "SELL"
    assert result.status == "OPEN"
    hedge = next(o for o in broker.submitted if o["symbol"] == "NIFTY26OCTFUT")
    assert hedge["side"] == "SELL"


def test_conflicting_signal_side_and_option_type_fail_closed():
    from app.services.snapback_live_executor import _hedge_side

    assert _hedge_side({
        "signal_side": "fade_up",
        "option_type": "CE",
        "option_symbol": "NIFTY26OCT25000CE",
    }) == ""


def test_rejected_hedge_never_reports_open_but_option_is_protected(plan_store):
    broker = BrokerSpy(reject={"NIFTY26OCTFUT"})

    result = asyncio.run(_executor(plan_store, broker).execute("PLAN-1"))

    assert result.status == "HEDGE_FAILED"
    assert result.stop_new_entries is True
    assert result.option_filled == 150
    assert result.hedge_filled == 0
    assert result.protection_state == "ACTIVE"
    assert broker.protection and broker.protection[0]["quantity"] == 150
    assert plan_store.get("PLAN-1")["status"] == "RECONCILING"


def test_rejected_hedge_retry_gets_a_new_durable_attempt_identity(plan_store):
    broker = BrokerSpy(reject={"NIFTY26OCTFUT"})

    first = asyncio.run(_executor(plan_store, broker).execute("PLAN-1"))
    second = asyncio.run(_executor(plan_store, broker).execute("PLAN-1"))
    third = asyncio.run(_executor(plan_store, broker).execute("PLAN-1"))

    assert first.status == second.status == third.status == "HEDGE_FAILED"
    hedge_orders = [o for o in broker.submitted if o["symbol"] == "NIFTY26OCTFUT"]
    assert [o["quantity"] for o in hedge_orders] == [75, 75, 75]
    assert hedge_orders[0]["signal_id"] == "OPP-1"
    assert hedge_orders[1]["signal_id"].startswith("OPP-1:hedge-recovery:")
    assert hedge_orders[2]["signal_id"].startswith("OPP-1:hedge-recovery:")
    assert hedge_orders[1]["signal_id"] != hedge_orders[2]["signal_id"]
    for order in hedge_orders:
        assert order["risk_approval"].strategy_id == "snapback"
        assert order["risk_approval"].signal_id == order["signal_id"]
    assert plan_store.get("PLAN-1")["status"] == "RECONCILING"
    recovery_events = [
        e for e in plan_store.events("PLAN-1") if e["event"] == "RECOVERY_ATTEMPT"
    ]
    assert len(recovery_events) == 2
    assert recovery_events[0]["revision"] < recovery_events[1]["revision"]

def test_partial_hedge_requires_reconciliation_and_never_reports_open(plan_store):
    broker = BrokerSpy(fills={"NIFTY26OCTFUT": 25})

    result = asyncio.run(_executor(plan_store, broker).execute("PLAN-1"))

    assert result.status == "RECONCILING"
    assert result.reconcile_first is True
    assert result.stop_new_entries is True
    assert result.hedge_filled == 25
    assert "hedge_fill_mismatch" in result.reasons[0]
    assert result.protection_state == "ACTIVE"
    assert plan_store.get("PLAN-1")["status"] == "RECONCILING"


def test_unknown_hedge_submission_requires_reconciliation_after_protection(plan_store):
    broker = BrokerSpy(unknown={"NIFTY26OCTFUT"})

    result = asyncio.run(_executor(plan_store, broker).execute("PLAN-1"))

    assert result.status == "RECONCILING"
    assert result.reconcile_first is True
    assert result.stop_new_entries is True
    assert result.reasons == ["hedge_submission_unknown"]
    assert result.protection_state == "ACTIVE"
    assert broker.protection
    assert plan_store.get("PLAN-1")["status"] == "RECONCILING"


def test_a_zero_option_fill_places_no_hedge(plan_store):
    broker = BrokerSpy(fills={"NIFTY26OCT25000PE": 0})

    result = asyncio.run(_executor(plan_store, broker).execute("PLAN-1"))

    assert [o["symbol"] for o in broker.submitted] == ["NIFTY26OCT25000PE"]
    assert result.status == "RECONCILING"
    assert result.reconcile_first is True
    assert plan_store.get("PLAN-1")["status"] == "RECONCILING"


def test_a_rejected_option_entry_places_no_hedge(plan_store):
    broker = BrokerSpy(reject={"NIFTY26OCT25000PE"})

    result = asyncio.run(_executor(plan_store, broker).execute("PLAN-1"))

    assert len(broker.submitted) == 1
    assert result.status == "REJECTED"


def test_an_unknown_option_submission_does_not_hedge_or_resubmit(plan_store):
    """UNKNOWN means the order may be working. Hedging against a position that
    may not exist, or resubmitting, are both worse than stopping."""
    broker = BrokerSpy(unknown={"NIFTY26OCT25000PE"})

    result = asyncio.run(_executor(plan_store, broker).execute("PLAN-1"))

    assert len(broker.submitted) == 1
    assert result.status == "RECONCILING"
    assert plan_store.get("PLAN-1")["status"] == "RECONCILING"


def test_reconciling_plan_is_resumable_without_revalidating_fresh_entry(plan_store):
    broker = BrokerSpy(reject={"NIFTY26OCTFUT"})
    first = asyncio.run(_executor(plan_store, broker).execute("PLAN-1"))
    assert first.status == "HEDGE_FAILED"
    assert plan_store.get("PLAN-1")["status"] == "RECONCILING"

    calls = []
    resumed_broker = BrokerSpy()
    resumed = _executor(plan_store, resumed_broker)
    resumed.revalidate_fn = lambda plan: calls.append(plan["plan_id"]) or {"ok": False}

    second = asyncio.run(resumed.execute("PLAN-1"))

    assert calls == [], "recovery must not be treated as a fresh-entry revalidation"
    assert second.status in {"OPEN", "RECONCILING", "HEDGE_FAILED"}

# --------------------------------------------------------------- protection


def test_protection_covers_the_confirmed_fill(plan_store):
    broker = BrokerSpy(fills={"NIFTY26OCT25000PE": 75})

    asyncio.run(_executor(plan_store, broker).execute("PLAN-1"))

    assert broker.protection[0]["quantity"] == 75


def test_a_position_is_not_open_until_protection_is_acknowledged(plan_store):
    broker = BrokerSpy()

    async def never_acknowledges(**kwargs):
        return {"state": "SUBMITTING"}

    result = asyncio.run(
        _executor(plan_store, broker, protection_fn=never_acknowledges)
        .execute("PLAN-1")
    )

    assert result.status != "OPEN"
    assert result.protection_state == "SUBMITTING"
    assert plan_store.get("PLAN-1")["status"] == "RECONCILING"


def test_a_failed_protection_triggers_the_deadline_policy(plan_store):
    broker = BrokerSpy()

    async def refuses(**kwargs):
        return {"state": "REQUIRED"}

    result = asyncio.run(
        _executor(plan_store, broker, protection_fn=refuses).execute("PLAN-1")
    )

    assert result.status == "PROTECTION_FAILED"
    assert result.emergency_exit_quantity == 150
    assert plan_store.get("PLAN-1")["status"] == "RECONCILING"


def test_an_unknown_protection_response_reconciles_rather_than_selling(plan_store):
    broker = BrokerSpy()

    async def unknown(**kwargs):
        return {"state": "RECONCILING"}

    result = asyncio.run(
        _executor(plan_store, broker, protection_fn=unknown).execute("PLAN-1")
    )

    assert result.emergency_exit_quantity == 0
    assert result.reconcile_first is True
    assert plan_store.get("PLAN-1")["status"] == "RECONCILING"


# ------------------------------------------------------------ wiring proof


def test_the_arm_endpoint_does_not_execute_inline():
    """Arming authorises; it must not place orders on the request thread."""
    import inspect

    from app.api.v1.endpoints import snapback_plans

    source = inspect.getsource(snapback_plans.arm_plan)

    assert "SnapbackLiveExecutor" not in source
    assert "submit_order" not in source


def test_the_executor_is_the_only_snapback_caller_of_submit_order():
    from pathlib import Path

    backend = Path(__file__).resolve().parents[2]
    offenders = []
    for path in (backend / "app" / "services").glob("snapback*.py"):
        text = path.read_text(encoding="utf-8")
        if "submit_order(" in text and path.name != "snapback_live_executor.py":
            offenders.append(path.name)

    assert offenders == [], f"Snapback places orders outside the executor: {offenders}"
