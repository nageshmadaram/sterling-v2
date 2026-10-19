from types import SimpleNamespace
import pytest
from pydantic import ValidationError
from app.services import db, shared_risk as risk


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_DB_PATH", str(tmp_path / "risk.db"))
    monkeypatch.setattr(db, "_available", False)
    assert db.init()
    return risk


def profile(store, **values):
    return store.save_config(
        "owner",
        risk.SharedRiskConfig(enabled=True, strategies=["supertrend"], **values),
    )


def request(strategy="sterling-kite"):
    return SimpleNamespace(
        uid="owner",
        account_id="account",
        strategy_id=strategy,
        generation_id="g1",
        signal_id="s1",
        exchange="NFO",
        symbol="NIFTY26OCT23000CE",
        side="BUY",
        quantity=25,
        price=100,
        payload={},
    )


def test_no_defaults_or_other_tenant_inheritance(store):
    assert store.get_config("owner").enabled is False
    profile(store, allocation_inr=10000)
    assert store.get_config("other").enabled is False
    assert store.selected("owner", "snapback") is None
    assert store.selected("owner", "sterling-kite").allocation_inr == 10000


@pytest.mark.parametrize(
    "values",
    [
        {"allocation_inr": 0},
        {"per_trade_risk_inr": float("nan")},
        {"allocation_inr": float("inf")},
        {"strategies": ["unknown"]},
        {"enabled": True},
        {"allocation_inr": 100, "per_trade_risk_inr": 101},
    ],
)
def test_invalid_or_missing_limits_are_refused(values):
    with pytest.raises(ValidationError):
        risk.SharedRiskConfig(**values)


def test_stale_save_is_rejected_without_losing_old_values(store):
    old = store.get_config("owner")
    new = profile(store, allocation_inr=10000)
    with pytest.raises(risk.RevisionConflict):
        store.save_config("owner", old)
    assert store.get_config("owner") == new


def test_effective_config_does_not_mutate_or_persist_strategy_values(store):
    from app.engines.sterling_kite_engine.schemas import EngineConfigModel
    from app.engines.snapback.config import SnapbackConfig

    base = EngineConfigModel(
        risk_pct=1, max_daily_loss_pct=2, allow_min_lot_over_risk=True
    )
    profile(
        store, per_trade_risk_inr=500, max_daily_loss_inr=1000, allocation_inr=10000
    )
    effective = store.supertrend_config("owner", base)
    assert base.max_daily_loss_pct == 2 and base.allow_min_lot_over_risk is True
    assert (
        effective.max_daily_loss_pct == 10
        and effective.allow_min_lot_over_risk is False
    )
    assert store.sizing_inputs("owner", 20000, 1) == (10000, 5)
    assert store.daily_loss_limit("owner", 20000, 2) == 1000
    assert store.daily_loss_limit("other", 20000, 2) == 400
    snap = SnapbackConfig(capital_inr=100000, premium_pct_of_capital=2)
    assert store.snapback_config("owner", snap) is snap
    cfg = store.get_config("owner")
    store.save_config("owner", cfg.model_copy(update={"strategies": ["snapback"]}))
    applied = store.snapback_config("owner", snap)
    assert applied.capital_inr == 10000 and applied.premium_pct_of_capital == 5
    assert snap.capital_inr == 100000 and snap.premium_pct_of_capital == 2


def test_whole_lot_sizing_never_rounds_over_the_shared_cap(store):
    profile(store, per_trade_risk_inr=2500)
    assert store.bounded_quantity("owner", "supertrend", 150, 100, 25) == 25
    assert store.bounded_quantity("owner", "supertrend", 150, 101, 25) == 0
    assert store.bounded_quantity("owner", "gamma_unwind", 150, 101, 25) == 150


def test_entry_checks_loss_and_unreadable_state_not_zero(store, monkeypatch):
    from app.services import live_minimum_gate as gate

    profile(
        store, allocation_inr=10000, max_daily_loss_inr=1000, per_trade_risk_inr=3000
    )
    monkeypatch.setattr(gate, "_read_inventory", lambda r: [])
    monkeypatch.setattr(gate, "_daily_loss", lambda uid: (0, 1500))
    assert store.entry_refusal(request(), 2500) is None
    assert "Per-trade".lower() in store.entry_refusal(request(), 4000).lower()
    monkeypatch.setattr(gate, "_daily_loss", lambda uid: (1000, 1500))
    assert "daily loss" in store.entry_refusal(request(), 2500)
    monkeypatch.setattr(gate, "_daily_loss", lambda uid: (None, None))
    assert "unreadable" in store.entry_refusal(request(), 2500)
    assert store.entry_refusal(request("gamma_unwind"), 25000) is None


def test_pending_reservations_and_final_recheck_exclude_only_current(
    store, monkeypatch
):
    from app.services import live_minimum_gate as gate
    from app.services.kite_engine import order_journal

    profile(store, allocation_inr=10000)
    monkeypatch.setattr(gate, "_read_inventory", lambda r: [])
    intent = order_journal.reserve(
        uid="owner",
        account_id="account",
        strategy_id="sterling-kite",
        generation_id="g1",
        signal_id="other",
        exchange="NFO",
        symbol="OTHER",
        side="BUY",
        quantity=25,
        capital_required=9000,
        available_capital=20000,
    )
    assert "allocation" in store.entry_refusal(request(), 2500)
    # A changed operator limit is re-read, not cached in the approval.
    cfg = store.get_config("owner")
    store.save_config("owner", cfg.model_copy(update={"allocation_inr": 20000}))
    assert store.entry_refusal(request(), 2500) is None
    cfg = store.get_config("owner")
    store.save_config("owner", cfg.model_copy(update={"allocation_inr": 10000}))
    assert (
        store.entry_refusal(request(), 9000, ignore_intent_key=intent.intent_key)
        is None
    )


def test_corrupt_config_and_unavailable_db_fail_closed(store):
    db.set_config_strict("shared_risk:owner", "broken")
    with pytest.raises(ValidationError):
        store.entry_refusal(request(), 100)
    db._available = False
    with pytest.raises(RuntimeError):
        store.entry_refusal(request(), 100)


def test_protection_keeps_local_config_when_shared_state_is_corrupt(store):
    from app.engines.sterling_kite_engine.schemas import EngineConfigModel
    from app.engines.snapback.config import SnapbackConfig

    db.set_config_strict("shared_risk:owner", "broken")
    engine = EngineConfigModel()
    snap = SnapbackConfig()
    assert store.supertrend_config("owner", engine) is engine
    assert store.snapback_config("owner", snap) is snap
    with pytest.raises(ValidationError):
        store.entry_refusal(request(), 2500)


def test_only_proven_protective_hedge_is_exempt_even_on_corrupt_shared_state(
    store, monkeypatch
):
    from app.services import protection_health, snapback_live_positions

    marker = object()
    monkeypatch.setattr(snapback_live_positions, "open_positions", lambda uid: [marker])
    monkeypatch.setattr(
        protection_health,
        "_protected_snapback_hedge",
        lambda p, r: p is marker and r.symbol == "PROVEN_HEDGE",
    )
    db.set_config_strict("shared_risk:owner", "broken")
    r = request("snapback")
    r.symbol = "PROVEN_HEDGE"
    assert store.entry_refusal(r, 999999) is None
    r.symbol = "SPOOFED_HEDGE"
    with pytest.raises(ValidationError):
        store.entry_refusal(r, 999999)


@pytest.mark.asyncio
async def test_canonical_refuses_tightened_policy_at_final_send_without_uncertainty(
    store, monkeypatch
):
    from app.services.execution_service import (
        CanonicalExecutionService,
        ExecutionRequest,
        RiskApproval,
    )
    from app.services.kite_engine import order_journal

    profile(store, per_trade_risk_inr=3000)
    monkeypatch.setattr(
        CanonicalExecutionService, "is_trading_allowed", lambda *a, **kw: (True, "")
    )
    calls = []

    class Broker:
        _is_paper = True
        _account_id = "account"

        async def place_order(self, **kwargs):
            current = store.get_config("owner")
            store.save_config(
                "owner", current.model_copy(update={"per_trade_risk_inr": 1000})
            )
            from app.services.snapback_family_mode import recheck_before_send

            recheck_before_send()
            calls.append(kwargs)
            return {"order_id": "should-not-be-sent"}

    req = ExecutionRequest(
        uid="owner",
        account_id="account",
        strategy_id="sterling-kite",
        generation_id="g",
        signal_id="s",
        exchange="NFO",
        symbol="NIFTY26OCT23000CE",
        side="BUY",
        quantity=25,
        capital_required=2500,
        available_capital=100000,
    )
    approval = RiskApproval(
        approval_id="a",
        uid=req.uid,
        account_id=req.account_id,
        strategy_id=req.strategy_id,
        signal_id=req.signal_id,
        symbol=req.symbol,
        side=req.side,
        quantity=req.quantity,
        generation_id=req.generation_id,
        available_capital=100000,
        capital_required=2500,
    )
    result = await CanonicalExecutionService().submit_order(
        req, broker_client=Broker(), risk_approval=approval
    )
    assert not result.success and "per-trade" in result.error
    assert calls == []
    assert result.status != "UNKNOWN"
    assert order_journal.get(result.intent_key).reconciliation_required is False


def test_futures_margin_cannot_understate_the_notional_cap(store):
    profile(store, per_trade_risk_inr=1000)
    req = request()
    req.symbol = "NIFTY26OCTFUT"
    assert "per-trade" in store.entry_refusal(req, 250).lower()


def test_pending_futures_keep_their_notional_reservation(store, monkeypatch):
    from app.services import live_minimum_gate as gate
    from app.services.kite_engine import order_journal

    profile(store, allocation_inr=10000)
    monkeypatch.setattr(gate, "_read_inventory", lambda r: [])
    order_journal.reserve(
        uid="owner",
        account_id="account",
        strategy_id="sterling-kite",
        generation_id="g1",
        signal_id="future",
        exchange="NFO",
        symbol="NIFTY26OCTFUT",
        side="BUY",
        quantity=25,
        capital_required=1000,
        available_capital=20000,
        payload={"_shared_committed_value": 9000},
    )
    assert "allocation" in store.entry_refusal(request(), 2500)


def test_legacy_futures_reservation_needs_reconciliation(store, monkeypatch):
    from app.services import live_minimum_gate as gate
    from app.services.kite_engine import order_journal

    profile(store, allocation_inr=10000)
    monkeypatch.setattr(gate, "_read_inventory", lambda r: [])
    order_journal.reserve(
        uid="owner",
        account_id="account",
        strategy_id="sterling-kite",
        generation_id="g1",
        signal_id="legacy-future",
        exchange="NFO",
        symbol="NIFTY26OCTFUT",
        side="BUY",
        quantity=25,
        capital_required=1000,
        available_capital=20000,
    )
    assert "unreadable" in store.entry_refusal(request(), 2500)


def test_future_without_execution_price_cannot_use_margin_as_notional(store):
    profile(store, per_trade_risk_inr=10000)
    req = request()
    req.symbol = "NIFTY26OCTFUT"
    req.price = 0
    assert "unavailable" in store.entry_refusal(req, 250)
