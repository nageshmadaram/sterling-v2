"""Control plane and admission for the virtual tenant: it behaves like AUTO (journal, idempotency,
risk approval, daily-loss halt on VIRTUAL capital), follows the global halts, and never changes them."""
from __future__ import annotations

import itertools
from types import SimpleNamespace

import pytest
from tenancy_support import OWNER, SIM_ACCOUNT_ID, SIM_UID, live_account

from app.services import db, live_safety
from app.services import external_positions as ep
from app.services.exchanges.kite import accounts as kite_accounts
from app.services.execution_service import (
    CanonicalExecutionService, ExecutionRequest, ExposureEffect, RiskApproval,
)
from app.services.kite_engine import order_journal
from app.services.kite_engine import state as engine_state
from app.services.safety_supervisor import SafetySupervisor
from app.services.simulation import tenant


@pytest.fixture(autouse=True)
def _no_snapback_family_gate(monkeypatch):
    """The Snapback gate derives live-process health; its own tests own it. Here it is let through."""
    monkeypatch.setattr("app.services.execution_service.snapback_live_gate_decision",
                        lambda **kw: SimpleNamespace(allowed=True, protective=False, reasons=[]),
                        raising=False)


@pytest.fixture(autouse=True)
def _clean_tenant_state():
    """The daily P&L, daily-loss limits and ledger rows live in the shared test database."""
    def wipe():
        from app.services.simulation.ledger import get_ledger

        engine_state._daily_pnl.pop(SIM_UID, None)
        engine_state._daily_pnl.pop(OWNER, None)
        for key in (f"kite_engine_daily_pnl_{SIM_UID}", f"kite_engine_daily_pnl_{OWNER}",
                    f"daily_loss_cfg_{SIM_UID}"):
            db.set_config(key, "")
        with db._conn() as c:
            c.execute("DELETE FROM execution_control WHERE uid=?", (SIM_UID,))
            c.execute("DELETE FROM kite_order_intents WHERE uid IN (?, ?)", (SIM_UID, OWNER))
        get_ledger().reset(OWNER)

    wipe()
    yield
    wipe()


@pytest.fixture
def tenants(allow_live_execution):
    live = live_account()
    kite_accounts.ensure_sim_account(OWNER)
    tenant.boot_tenant(OWNER)
    return live


def _control(uid=SIM_UID, account_id=SIM_ACCOUNT_ID):
    return db.get_execution_control(uid=uid, account_id=account_id)


def _may_open(uid, account_id, strategy="snapback"):
    verdict = SafetySupervisor().authorize("INCREASE_EXPOSURE", uid=uid, account_id=account_id,
                                           strategy_id=strategy)
    return verdict.allowed, verdict.code


# ── control-plane init ───────────────────────────────────────────────────────
def test_boot_creates_the_tenants_own_control_row_running_and_clean():
    kite_accounts.ensure_sim_account(OWNER)
    assert not int(_control()["initialized"])                 # uninitialised reads RECOVERY_REQUIRED
    tenant.ensure_control_plane(OWNER)
    row = _control()
    assert (row["operator_state"], row["recovery_state"], row["scope"]) == ("RUNNING", "CLEAN", "global")
    assert int(row["initialized"]) == 1 and row["actor"] == "simulation"


def test_control_init_is_idempotent_and_never_touches_other_scopes():
    live = live_account()
    kite_accounts.ensure_sim_account(OWNER)
    root_before = db.get_execution_control()
    live_before = db.get_execution_control(uid=OWNER, account_id=live.id)
    first = tenant.ensure_control_plane(OWNER)
    again = tenant.ensure_control_plane(OWNER)
    assert again["revision"] == first["revision"]              # a second boot writes nothing
    assert db.get_execution_control() == root_before           # the process-wide root is the live runtime's
    assert db.get_execution_control(uid=OWNER, account_id=live.id) == live_before


def test_an_existing_recovery_required_row_is_left_for_recovery_not_papered_over():
    kite_accounts.ensure_sim_account(OWNER)
    db.set_execution_control(operator_state="RUNNING", recovery_state="RECOVERY_REQUIRED",
                             reason_code="BROKER_TRANSPORT_UNCERTAINTY", uid=SIM_UID, account_id=SIM_ACCOUNT_ID)
    tenant.ensure_control_plane(OWNER)
    assert _control()["recovery_state"] == "RECOVERY_REQUIRED"


def test_boot_tenant_creates_account_ledger_control_and_limits(tenants):
    account = kite_accounts.sim_account_for(OWNER)
    assert account.is_sim and _control()["recovery_state"] == "CLEAN"
    assert tenant.virtual_capital(OWNER) == 1_000_000.0
    assert live_safety.has_daily_loss_override(SIM_UID)
    assert tenant.boot_tenant(OWNER) is account                # idempotent


# ── risk limits scale to virtual capital ─────────────────────────────────────
def test_daily_loss_limits_are_a_share_of_virtual_capital_and_live_keeps_its_own(tenants):
    cfg = live_safety.daily_loss_config(SIM_UID)
    assert (cfg.hard_halt_inr, cfg.soft_warn_inr) == (-20_000.0, -10_000.0)
    assert live_safety.daily_loss_config(OWNER) == live_safety.daily_loss_config(None)   # untouched
    assert not live_safety.has_daily_loss_override(OWNER)


def test_limits_follow_a_capital_change(tenants):
    tenant.apply_capital_limits(OWNER, 500_000.0)
    cfg = live_safety.daily_loss_config(SIM_UID)
    assert (cfg.hard_halt_inr, cfg.soft_warn_inr) == (-10_000.0, -5_000.0)


def test_non_positive_capital_is_refused(tenants):
    with pytest.raises(ValueError):
        tenant.apply_capital_limits(OWNER, 0)


def test_the_sim_daily_loss_halt_blocks_only_the_sim_tenant(tenants):
    live = tenants
    engine_state.record_realized_pnl(SIM_UID, -19_000.0)               # inside the 2% limit
    assert _may_open(SIM_UID, SIM_ACCOUNT_ID) == (True, "")
    engine_state.record_realized_pnl(SIM_UID, -2_000.0)                # -21,000 breaches -20,000
    allowed, code = _may_open(SIM_UID, SIM_ACCOUNT_ID)
    assert (allowed, code) == (False, "daily_loss_halt")
    assert _may_open(OWNER, live.id)[0] is True                        # the live tenant is unaffected
    assert engine_state.daily_realized_pnl_strict(OWNER) == 0.0


def test_the_shipped_live_limit_would_have_halted_the_sim_on_a_trivial_loss(tenants):
    """Why the scaling exists: -1,500 (the live default) is nothing on ₹10 lakh."""
    engine_state.record_realized_pnl(SIM_UID, -1_800.0)
    assert live_safety.daily_loss_config(None).hard_halt_inr == -1500.0
    assert _may_open(SIM_UID, SIM_ACCOUNT_ID) == (True, "")


# ── global halts bind the sim; the sim never moves them ──────────────────────
def test_the_live_kill_switch_stops_sim_entries_but_not_sim_exits(tenants):
    live_safety.set_kill_switch(True, "drill")
    assert _may_open(SIM_UID, SIM_ACCOUNT_ID) == (False, "kill_switch")
    reduce = SafetySupervisor().authorize("CLOSE_POSITION", uid=SIM_UID, account_id=SIM_ACCOUNT_ID)
    assert reduce.allowed


def test_safe_mode_stops_sim_entries(tenants, monkeypatch, tmp_path):
    from app.services.safe_mode import SafeModeService, SafeModeTrigger

    path = tmp_path / "safe.json"
    monkeypatch.setenv("STERLING_SAFE_MODE_FILE", str(path))
    SafeModeService(path).initialise(operator_ack=True, note="t")
    SafeModeService(path).engage(trigger=SafeModeTrigger.EXECUTION_DISCREPANCY, reason="test")
    allowed, code = _may_open(SIM_UID, SIM_ACCOUNT_ID)
    assert (allowed, code) == (False, "safe_mode")


def test_the_sims_own_recovery_fence_never_halts_the_live_tenant(tenants):
    live = tenants
    root_before, live_before = db.get_execution_control(), db.get_execution_control(uid=OWNER, account_id=live.id)
    db.set_recovery_state("RECOVERY_REQUIRED", reason_code="BROKER_TRANSPORT_UNCERTAINTY",
                          uid=SIM_UID, account_id=SIM_ACCOUNT_ID)
    assert _may_open(SIM_UID, SIM_ACCOUNT_ID)[0] is False
    assert _may_open(OWNER, live.id)[0] is True
    assert db.get_execution_control() == root_before
    assert db.get_execution_control(uid=OWNER, account_id=live.id) == live_before
    assert live_safety.kill_switch_state()["enabled"] is False


def test_the_external_exposure_gate_does_not_apply_to_the_virtual_account(tenants, monkeypatch):
    live = tenants
    from datetime import datetime, timezone

    record = {"observed_at": datetime.now(timezone.utc).isoformat(), "readable": True, "count": 0,
              "instruments": [], "automation": [], "accounts": ["SOMEONE-ELSE"],
              "by_account": {"SOMEONE-ELSE": {"readable": True, "count": 0, "instruments": [], "automation": []}}}
    monkeypatch.setattr(ep, "last_observation", lambda: record)
    assert _may_open(OWNER, live.id)[1] == "external_exposure_unreadable"      # live: refused as before
    assert _may_open(SIM_UID, SIM_ACCOUNT_ID) == (True, "")                    # virtual: nothing to contradict


# ── the canonical path, end to end, with a virtual broker ─────────────────────
class _VirtualBroker:
    """The surface the canonical service uses; records what it was asked to place."""

    _is_paper = True
    _account_id = SIM_ACCOUNT_ID
    _ids = itertools.count(1)               # one exchange: order ids are never reused

    def __init__(self, *, fail=None):
        self.placed, self.fail, self.orders = [], fail, []

    async def place_order(self, **kwargs):
        if self.fail:
            raise self.fail
        self.placed.append(kwargs)
        return {"order_id": f"SIM-{next(self._ids)}"}

    async def get_margins(self, segment=None):
        return {"equity": {"available": {"live_balance": 1_000_000.0}}}

    async def get_orders(self):
        return list(self.orders)

    async def get_order_history(self, order_id):
        return []


def _request(strategy="gamma_move", signal="sig-1", qty=75, uid=SIM_UID, account=SIM_ACCOUNT_ID,
             symbol="NIFTY26OCT24500CE"):
    req = ExecutionRequest(uid=uid, account_id=account, strategy_id=strategy, generation_id="g1",
                           signal_id=signal, exchange="NFO", symbol=symbol, side="BUY", quantity=qty,
                           tag="t", capital_required=50_000.0)
    approval = RiskApproval(approval_id="a1", uid=uid, account_id=account, symbol=symbol, side="BUY",
                            quantity=qty, generation_id="g1", strategy_id=strategy, signal_id=signal,
                            available_capital=1_000_000.0, capital_required=50_000.0)
    return req, approval


async def test_a_sim_entry_is_journalled_under_the_sim_tenant_and_is_idempotent(tenants):
    broker, service = _VirtualBroker(), CanonicalExecutionService()
    req, approval = _request()
    first = await service.submit_order(req, broker_client=broker, risk_approval=approval)
    assert (first.success, first.status) == (True, "ACKNOWLEDGED") and len(broker.placed) == 1
    again = await service.submit_order(req, broker_client=broker, risk_approval=approval)
    assert again.status in ("ACKNOWLEDGED", "SUBMITTED") and len(broker.placed) == 1   # same intent, one order
    intents = order_journal.unresolved(SIM_UID, account_id=SIM_ACCOUNT_ID) + \
        [i for i in order_journal.unresolved(SIM_UID)]
    assert intents and all(i.uid == SIM_UID and i.account_id == SIM_ACCOUNT_ID for i in intents)
    assert order_journal.unresolved(OWNER) == []                     # nothing under the live tenant


async def test_a_sim_entry_without_a_risk_approval_is_rejected(tenants):
    broker = _VirtualBroker()
    req, _ = _request()
    result = await CanonicalExecutionService().submit_order(req, broker_client=broker)
    assert result.status == "REJECTED" and broker.placed == []


async def test_a_sim_entry_for_another_tenants_account_is_rejected_at_identity(tenants):
    live = tenants
    broker = _VirtualBroker()                    # the virtual client...
    req, approval = _request(uid=OWNER, account=live.id)            # ...asked to trade for the LIVE account
    result = await CanonicalExecutionService().submit_order(req, broker_client=broker, risk_approval=approval)
    assert result.success is False and broker.placed == []
    assert "identity" in result.error.lower()


async def test_the_release_scope_still_admits_exactly_the_three_strategies(tenants, monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setattr("app.core.runtime_env.is_production", lambda: True)
    service = CanonicalExecutionService()
    for strategy in ("sterling-kite", "snapback", "gamma_move"):
        broker = _VirtualBroker()
        req, approval = _request(strategy=strategy, signal=f"s-{strategy}")
        result = await service.submit_order(req, broker_client=broker, risk_approval=approval)
        assert result.status == "ACKNOWLEDGED", (strategy, result)
    broker = _VirtualBroker()
    req, approval = _request(strategy="intraday", signal="s-intraday")
    result = await service.submit_order(req, broker_client=broker, risk_approval=approval)
    assert result.status == "REJECTED_OUTSIDE_RELEASE_SCOPE" and broker.placed == []


async def test_the_live_minimum_envelope_is_a_live_only_gate(tenants, monkeypatch):
    calls = []

    async def evaluate_entry(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(allowed=True, message=lambda: "")

    monkeypatch.setattr("app.services.live_minimum_gate.evaluate_entry", evaluate_entry)
    req, approval = _request()
    await CanonicalExecutionService().submit_order(req, broker_client=_VirtualBroker(), risk_approval=approval)
    assert calls == []                                                # not asked for a virtual client


async def test_an_unknown_sim_outcome_fences_only_the_sim_tenant_until_recovery_settles_it(tenants, monkeypatch):
    from app.services.kite_engine import execution_lifecycle

    monkeypatch.setattr(execution_lifecycle, "_NO_EVIDENCE_SETTLE_AFTER_S", 0)
    live = tenants
    service = CanonicalExecutionService()
    broker = _VirtualBroker(fail=RuntimeError("virtual exchange hiccup"))
    req, approval = _request(signal="sig-unknown")
    result = await service.submit_order(req, broker_client=broker, risk_approval=approval)
    assert result.status == "UNKNOWN"
    assert _control()["recovery_state"] == "RECOVERY_REQUIRED"
    assert _may_open(SIM_UID, SIM_ACCOUNT_ID)[0] is False
    assert _may_open(OWNER, live.id)[0] is True                       # live admission untouched
    assert db.get_execution_control()["recovery_state"] == "CLEAN"    # the process-wide root too

    # the exchange never saw the order: recovery settles it as not sent and reopens the tenant
    outcome = await tenant.recover_tenant(OWNER, service=service, client=_VirtualBroker())
    assert outcome["status"] == "success"
    assert _control()["recovery_state"] == "CLEAN"
    assert _may_open(SIM_UID, SIM_ACCOUNT_ID)[0] is True


async def test_recovery_loop_reconciles_a_fenced_tenant_without_raising(tenants, monkeypatch):
    db.set_recovery_state("RECOVERY_REQUIRED", reason_code="X", uid=SIM_UID, account_id=SIM_ACCOUNT_ID)
    calls = []

    async def fake_recover(owner, **kw):
        calls.append(owner)
        db.set_recovery_state("CLEAN", reason_code="X", uid=SIM_UID, account_id=SIM_ACCOUNT_ID)
        return {"status": "success"}

    monkeypatch.setattr(tenant, "recover_tenant", fake_recover)
    await tenant.recovery_loop(OWNER, interval_s=0, max_cycles=3)
    assert calls == [OWNER]                                           # recovered once, then left alone


async def test_recovery_loop_survives_a_failing_cycle(tenants, monkeypatch):
    db.set_recovery_state("RECOVERY_REQUIRED", reason_code="X", uid=SIM_UID, account_id=SIM_ACCOUNT_ID)

    async def broken(owner, **kw):
        raise RuntimeError("exchange down")

    monkeypatch.setattr(tenant, "recover_tenant", broken)
    await tenant.recovery_loop(OWNER, interval_s=0, max_cycles=2)     # does not raise
    assert _control()["recovery_state"] == "RECOVERY_REQUIRED"


async def test_generic_order_modification_needs_the_tenants_control_row(tenants):
    """modify_order reads the tenant's own control row; an absent one reads RECOVERY_REQUIRED."""
    class _Broker(_VirtualBroker):
        async def modify_order(self, **kwargs):
            self.modified = kwargs

    service = CanonicalExecutionService()
    broker = _Broker()
    ok = await service.modify_order(SIM_UID, SIM_ACCOUNT_ID, "SIM-9", {"price": 101.0}, broker_client=broker)
    assert ok.success and broker.modified["order_id"] == "SIM-9"

    with db._conn() as c:                                           # a tenant that was never initialised
        c.execute("DELETE FROM execution_control WHERE uid=?", (SIM_UID,))
    blocked = await service.modify_order(SIM_UID, SIM_ACCOUNT_ID, "SIM-9", {"price": 102.0}, broker_client=_Broker())
    assert blocked.status == "HALTED"
