"""A Simulation tenant can halt ITSELF; it can never engage the process-wide kill switch that stops real trading.

Each engine site that can run for the virtual tenant engages the switch through ``simulation.halt``; the tests
drive those sites with a simulation uid and make ``live_safety.set_kill_switch`` fail the test if it is reached.
"""
from types import SimpleNamespace

import pytest

from app.services import db, live_safety
from app.services.simulation import halt
from app.services.simulation.identity import sim_account_id, sim_uid

OWNER = "default"
SIM = sim_uid(OWNER)
SIM_ACCOUNT = sim_account_id(OWNER)


@pytest.fixture(autouse=True)
def _clean():
    def wipe():
        with db._conn() as c:
            c.execute("DELETE FROM execution_control WHERE uid=?", (SIM,))
        live_safety._KILL_SWITCH.update({"enabled": False, "reason": "", "set_ts_ms": 0})
    wipe()
    yield
    wipe()


@pytest.fixture
def no_global_switch(monkeypatch):
    """Any engagement of the process-wide kill switch fails the test."""
    def boom(*a, **k):
        raise AssertionError(f"the global kill switch was engaged by a simulation: {a!r}")
    monkeypatch.setattr(live_safety, "set_kill_switch", boom)


def _control():
    return db.get_execution_control(uid=SIM, account_id=SIM_ACCOUNT)


def test_a_live_uid_engages_the_global_kill_switch_exactly_as_before(monkeypatch):
    calls = []
    monkeypatch.setattr(live_safety, "set_kill_switch", lambda *a, **k: calls.append((a, k)))
    halt.engage_kill_switch("default", "exit unresolved")
    assert calls == [((True, "exit unresolved"), {})]


def test_a_simulation_uid_halts_only_its_own_control_row(no_global_switch):
    before = db.get_execution_control()
    halt.engage_kill_switch(SIM, "virtual stop unconfirmed")
    row = _control()
    assert (row["operator_state"], row["reason_code"], row["actor"]) == ("HALTED", halt.SIM_HALT_CODE, "simulation")
    assert row["reason"] == "virtual stop unconfirmed"
    assert db.get_execution_control() == before                       # the process-wide root is untouched
    assert live_safety.kill_switch_state()["enabled"] is False        # and so is the in-memory global switch


def test_a_halted_simulation_tenant_can_be_resumed_but_a_live_uid_cannot_be_cleared_here(no_global_switch):
    halt.engage_kill_switch(SIM, "x")
    halt.clear_halt(SIM)
    assert _control()["operator_state"] == "RUNNING"
    with pytest.raises(ValueError):
        halt.clear_halt("default")


# ── the engine sites ──────────────────────────────────────────────────────────────────────
def test_snapback_engage_kill_switch_is_tenant_scoped_for_a_simulation_uid(no_global_switch):
    from app.services import snapback_live_wiring as wiring
    wiring.engage_kill_switch("Snapback protection not confirmed", SIM)
    assert _control()["operator_state"] == "HALTED"
    assert live_safety.kill_switch_state()["enabled"] is False


def test_snapback_engage_kill_switch_still_engages_the_global_switch_for_live(monkeypatch):
    from app.services import snapback_live_wiring as wiring
    seen = []
    monkeypatch.setattr(live_safety, "set_kill_switch", lambda enabled, reason="", **k: seen.append((enabled, reason)))
    wiring.engage_kill_switch("Snapback protection not confirmed", "default")
    assert seen == [(True, "Snapback protection not confirmed")]


def test_an_orphan_gamma_entry_in_the_simulation_does_not_halt_live_trading(no_global_switch):
    from app.services.kite_engine import execution_lifecycle as life
    life._flag_orphan_entry(SIM, SimpleNamespace(symbol="NIFTY2611325000CE", filled_quantity=75))
    assert _control()["operator_state"] == "HALTED"


def test_a_supertrend_protection_failure_in_the_simulation_halts_only_the_tenant(no_global_switch):
    """The owner's halt-on-unprotected-holding rule applies to the virtual tenant too, scoped to itself."""
    from app.services.kite_engine import execution_lifecycle as life
    virtual = SimpleNamespace(_is_paper=True, _is_simulated=True)
    life._halt_new_entries_after_protection_failure(
        virtual, SimpleNamespace(uid=SIM, symbol="NIFTY2611325000CE"), "initial protective GTT was not confirmed")
    row = _control()
    assert row["operator_state"] == "HALTED" and "SuperTrend broker protection unavailable" in row["reason"]


def test_no_lifecycle_site_that_can_run_for_the_simulation_calls_the_global_switch_directly():
    """The Supertrend lifecycle, its state module and Snapback's wiring reach the switch only through the helper."""
    from app.services import snapback_live_wiring
    from app.services.kite_engine import execution_lifecycle, state
    for module in (execution_lifecycle, state, snapback_live_wiring):
        source = open(module.__file__).read()
        assert "live_safety.set_kill_switch" not in source.replace("from app.services import live_safety", ""), module.__name__


def test_a_persistence_failure_while_booking_simulated_pnl_does_not_halt_live_trading(no_global_switch, monkeypatch):
    from app.services.kite_engine import state
    monkeypatch.setattr(state, "_persist_daily_pnl", lambda uid: (_ for _ in ()).throw(OSError("disk")))
    with pytest.raises(RuntimeError):
        state.record_realized_pnl(SIM, -100.0)
    assert _control()["operator_state"] == "HALTED"
    state._daily_pnl.pop(SIM, None)


def test_resetting_the_virtual_books_lifts_the_tenants_own_halt_and_nothing_else(ledger, no_global_switch):
    from app.services.simulation import sim_runtime as rt
    halt.engage_kill_switch(SIM, "virtual stop unconfirmed")
    rt.reset_owner(OWNER, ledger=ledger)
    assert _control()["operator_state"] == "RUNNING"
    # a halt with another reason (an operator's, say) is not the simulation's to lift
    db.set_execution_control(operator_state="HALTED", reason_code="OPERATOR", reason="hold", actor="operator",
                             uid=SIM, account_id=SIM_ACCOUNT)
    rt.reset_owner(OWNER, ledger=ledger)
    assert _control()["operator_state"] == "HALTED"
