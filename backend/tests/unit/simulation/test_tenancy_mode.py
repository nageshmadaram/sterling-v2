"""Per-strategy mode (MANUAL / SIMULATION / AUTO): transitions, the config copy that seeds the virtual
tenant, the live readiness gate, and the reset. Every invariant is asserted against both tenants."""
from __future__ import annotations

import json
import time

import pytest
from tenancy_support import OWNER, SIM_ACCOUNT_ID, SIM_UID, unprotected_position, wipe_tenants

from app.services import db, gamma_move, snapback
from app.services import trade_journal as journal
from app.services.exchanges.kite import accounts as kite_accounts
from app.services.kite_engine import fill_ledger, order_journal, positions
from app.services.kite_engine import state as engine_state
from app.services.simulation import mode, tenant
from app.services.simulation.ledger import get_ledger

STRATEGIES = mode.STRATEGIES


@pytest.fixture(autouse=True)
def _clean():
    wipe_tenants()
    yield
    wipe_tenants()


@pytest.fixture
def tenants():
    kite_accounts.ensure_sim_account(OWNER)
    tenant.boot_tenant(OWNER)


def _mode(strategy):
    return mode.strategy_state(OWNER, strategy)["mode"]


def _modes():
    return {s: _mode(s) for s in STRATEGIES}


def _stored(key):
    raw = db.get_config(key)
    return json.loads(raw) if raw else None


# ── reading ──────────────────────────────────────────────────────────────────
def test_a_fresh_install_is_manual_everywhere(tenants):
    assert _modes() == {"supertrend": "MANUAL", "snapback": "MANUAL", "gamma": "MANUAL"}
    state = mode.strategy_state(OWNER, "supertrend")
    assert state["live"]["armed"] is False and state["simulation"]["armed"] is False
    assert state["simulation"]["configured"] is False and state["conflict"] is False


def test_the_mode_is_read_from_the_three_real_switches(tenants):
    engine_state.set_config(OWNER, engine_state.get_config(OWNER).model_copy(update={"auto_execute": True}))
    assert _mode("supertrend") == "AUTO"                                  # supertrend flag defaults on
    assert _mode("gamma") == "MANUAL" and _mode("snapback") == "MANUAL"    # their own flags default off
    gamma_move.set_config({"auto_execute": True}, uid=OWNER)
    snapback.set_config({"auto_execute": True}, uid=OWNER)
    assert _modes() == {"supertrend": "AUTO", "snapback": "AUTO", "gamma": "AUTO"}
    engine_state.set_config(OWNER, engine_state.get_config(OWNER).model_copy(update={"auto_execute": False}))
    assert set(_modes().values()) == {"MANUAL"}                            # the shared master gates all three


def test_both_tenants_armed_reads_as_auto_and_is_flagged(tenants):
    mode.set_mode(OWNER, "gamma", "SIMULATION")
    gamma_move.set_config({"auto_execute": True}, uid=OWNER)
    engine_state.set_config(OWNER, engine_state.get_config(OWNER).model_copy(update={"auto_execute": True}))
    state = mode.strategy_state(OWNER, "gamma")
    assert state["mode"] == "AUTO" and state["conflict"] is True          # real money wins the label
    assert state["live"]["armed"] and state["simulation"]["armed"]


@pytest.mark.parametrize("bad", ["", "momentum", "paper", "adaptive_edge"])
def test_unknown_strategies_are_refused(bad):
    with pytest.raises(mode.ModeError) as err:
        mode.set_mode(OWNER, bad, "SIMULATION")
    assert err.value.code == "invalid_strategy"
    assert _db_rows_for(SIM_UID) == []


def test_names_and_modes_are_case_and_space_insensitive():
    assert mode.check_strategy(" Gamma ") == "gamma" and mode.check_mode(" simulation") == "SIMULATION"


@pytest.mark.parametrize("bad", ["", "PAPER", "live", "ON"])
def test_unknown_modes_are_refused_and_change_nothing(bad):
    with pytest.raises(mode.ModeError) as err:
        mode.set_mode(OWNER, "gamma", bad)
    assert err.value.code == "invalid_mode"
    assert _db_rows_for(SIM_UID) == []


def _db_rows_for(uid):
    return [k for k in (p.format(u=uid) for p in mode._CONFIG_KEYS) if db.get_config(k)]


# ── SIMULATION ───────────────────────────────────────────────────────────────
@pytest.mark.parametrize("strategy", STRATEGIES)
def test_simulation_arms_the_virtual_tenant_and_leaves_live_off(strategy):
    result = mode.set_mode(OWNER, strategy, "SIMULATION")
    assert result["mode"] == "SIMULATION" and result["changed"] and result["previous"] == "MANUAL"
    assert result["live"]["armed"] is False and result["simulation"]["armed"] is True
    assert result["simulation"]["master"] is True and result["simulation"]["configured"] is True
    others = [s for s in STRATEGIES if s != strategy]
    assert all(_mode(s) == "MANUAL" for s in others)                      # nothing else got armed


def test_simulation_boots_the_tenant_so_the_engines_can_scan_it():
    mode.set_mode(OWNER, "supertrend", "SIMULATION")
    account = kite_accounts.get_active(SIM_UID)
    assert account is not None and account.id == SIM_ACCOUNT_ID
    control = db.get_execution_control(uid=SIM_UID, account_id=SIM_ACCOUNT_ID)
    assert (control["operator_state"], control["recovery_state"]) == ("RUNNING", "CLEAN")


def test_simulation_never_changes_the_live_master_or_another_live_strategy(tenants):
    engine_state.set_config(OWNER, engine_state.get_config(OWNER).model_copy(update={"auto_execute": True}))
    gamma_move.set_config({"auto_execute": True}, uid=OWNER)
    live_before = engine_state.get_config(OWNER).model_dump()
    mode.set_mode(OWNER, "snapback", "SIMULATION")
    live_after = engine_state.get_config(OWNER).model_dump()
    assert live_after == live_before                                       # master, flags, everything
    assert _mode("gamma") == "AUTO" and _mode("supertrend") == "AUTO"
    assert _mode("snapback") == "SIMULATION"


def test_auto_to_simulation_turns_live_off_first(tenants):
    mode.set_mode(OWNER, "gamma", "AUTO")
    assert _mode("gamma") == "AUTO"
    result = mode.set_mode(OWNER, "gamma", "SIMULATION")
    assert result["live"]["armed"] is False and result["simulation"]["armed"] is True
    assert gamma_move.get_config(OWNER).auto_execute is False


def test_simulation_is_idempotent(tenants):
    mode.set_mode(OWNER, "gamma", "SIMULATION")
    again = mode.set_mode(OWNER, "gamma", "SIMULATION")
    assert again["mode"] == "SIMULATION" and again["changed"] is False


def test_simulation_enables_a_strategy_the_owner_had_disabled():
    snapback.set_config({"enabled": False}, uid=OWNER)
    mode.set_mode(OWNER, "snapback", "SIMULATION")
    assert snapback.get_config(OWNER).enabled is False                    # live setting untouched
    assert snapback.get_config(SIM_UID).enabled is True                   # choosing SIMULATION means run it


# ── the config copy ──────────────────────────────────────────────────────────
def test_the_owners_engine_config_is_copied_once_with_safe_values():
    owner_cfg = engine_state.get_config(OWNER).model_copy(update={
        "risk_pct": 0.5, "max_lots": 3, "stop_mode": "monitor", "max_daily_loss_pct": None, "adx_min": 22.0,
        "auto_execute": True, "supertrend_auto_execute": True})
    engine_state.set_config(OWNER, owner_cfg)
    mode.set_mode(OWNER, "supertrend", "SIMULATION")
    sim = engine_state.get_config(SIM_UID)
    assert (sim.risk_pct, sim.max_lots, sim.adx_min) == (0.5, 3, 22.0)     # behaves like the owner's AUTO
    assert sim.stop_mode == "both"                                        # the virtual broker holds the stop
    assert sim.max_daily_loss_pct == mode.SEED_MAX_DAILY_LOSS_PCT          # a missing cap would refuse entries
    assert sim.engine_enabled is True
    assert sim.auto_execute is True and sim.supertrend_auto_execute is True   # only because it was switched on
    live = engine_state.get_config(OWNER)
    assert (live.risk_pct, live.stop_mode, live.max_daily_loss_pct) == (0.5, "monitor", None)   # live untouched


def test_the_owners_own_daily_loss_cap_is_kept():
    engine_state.set_config(OWNER, engine_state.get_config(OWNER).model_copy(update={"max_daily_loss_pct": 1.25}))
    mode.set_mode(OWNER, "supertrend", "SIMULATION")
    assert engine_state.get_config(SIM_UID).max_daily_loss_pct == 1.25


def test_gamma_and_snapback_configs_are_copied_with_nothing_armed_but_the_chosen_one():
    gamma_move.set_config({"execution_lots": 4, "stop_mode": "monitor", "auto_execute": True}, uid=OWNER)
    snapback.set_config({"max_open_positions": 7, "auto_execute": True, "stop_mode": "monitor"}, uid=OWNER)
    mode.set_mode(OWNER, "gamma", "SIMULATION")
    g = gamma_move.get_config(SIM_UID)
    assert (g.execution_lots, g.stop_mode, g.auto_execute) == (4, "both", True)
    assert _stored(f"snapback_config:{SIM_UID}") is None                  # snapback was not asked for
    mode.set_mode(OWNER, "snapback", "SIMULATION")
    s = snapback.get_config(SIM_UID)
    assert (s.max_open_positions, s.stop_mode, s.auto_execute, s.enabled) == (7, "both", True, True)
    # the owner's live config was never edited by any of this
    assert gamma_move.get_config(OWNER).stop_mode == "monitor" and snapback.get_config(OWNER).stop_mode == "monitor"


def test_the_copy_is_made_only_when_the_tenant_has_no_config():
    gamma_move.set_config({"execution_lots": 4}, uid=OWNER)
    mode.set_mode(OWNER, "gamma", "SIMULATION")
    gamma_move.set_config({"execution_lots": 9}, uid=SIM_UID)             # the owner tuned the simulation
    gamma_move.set_config({"execution_lots": 2}, uid=OWNER)
    mode.set_mode(OWNER, "gamma", "MANUAL")
    mode.set_mode(OWNER, "gamma", "SIMULATION")
    assert gamma_move.get_config(SIM_UID).execution_lots == 9             # not overwritten by the owner's
    assert mode.seed_strategy(OWNER, "gamma") is False


def test_the_simulation_does_not_inherit_the_owners_legacy_global_config():
    db.set_config("gamma_move_config", json.dumps({"execution_lots": 6}))
    assert gamma_move.get_config(OWNER).execution_lots == 6
    assert gamma_move.get_config(SIM_UID).execution_lots != 6
    mode.set_mode(OWNER, "gamma", "SIMULATION")
    assert gamma_move.get_config(SIM_UID).execution_lots == 6             # copied on purpose, with the owner's view


# ── the engines' own AUTO gates agree with the mode ───────────────────────────
def _engine_gates(uid):
    """What each strategy's production origination gate says for ``uid`` (not this module's reading)."""
    from app.services import gamma_move_runner, snapback_dispatcher
    from app.services.kite_engine import service

    return {"supertrend": service.supertrend_auto_entry_armed(engine_state.get_config(uid)),
            "snapback": snapback_dispatcher.auto_execute(uid),
            "gamma": gamma_move_runner.auto_execute(uid)}


@pytest.mark.parametrize("strategy", STRATEGIES)
def test_the_production_gates_follow_the_mode_for_both_tenants(strategy, tenants):
    off = {s: False for s in STRATEGIES}
    assert _engine_gates(OWNER) == off and _engine_gates(SIM_UID) == off
    mode.set_mode(OWNER, strategy, "SIMULATION")
    assert _engine_gates(SIM_UID) == {**off, strategy: True} and _engine_gates(OWNER) == off
    mode.set_mode(OWNER, strategy, "AUTO")
    assert _engine_gates(OWNER) == {**off, strategy: True} and _engine_gates(SIM_UID) == off
    mode.set_mode(OWNER, strategy, "MANUAL")
    assert _engine_gates(OWNER) == off and _engine_gates(SIM_UID) == off


def test_all_three_strategies_can_run_in_different_modes_at_once(tenants):
    mode.set_mode(OWNER, "supertrend", "AUTO")
    mode.set_mode(OWNER, "gamma", "SIMULATION")
    mode.set_mode(OWNER, "snapback", "MANUAL")
    assert _modes() == {"supertrend": "AUTO", "gamma": "SIMULATION", "snapback": "MANUAL"}
    assert _engine_gates(OWNER) == {"supertrend": True, "gamma": False, "snapback": False}
    assert _engine_gates(SIM_UID) == {"supertrend": False, "gamma": True, "snapback": False}


# ── turning a master on must never arm something nobody asked for ─────────────
def test_arming_one_strategy_live_does_not_arm_supertrend_through_its_default_flag(tenants):
    assert engine_state.get_config(OWNER).supertrend_auto_execute is True   # the default that makes this a hazard
    assert engine_state.get_config(OWNER).auto_execute is False
    mode.set_mode(OWNER, "gamma", "AUTO")
    assert engine_state.get_config(OWNER).auto_execute is True
    assert _modes() == {"supertrend": "MANUAL", "snapback": "MANUAL", "gamma": "AUTO"}


def test_arming_one_strategy_in_the_simulation_does_not_arm_the_others_there():
    gamma_move.set_config({"auto_execute": True}, uid=OWNER)               # dormant: master is off
    snapback.set_config({"auto_execute": True}, uid=OWNER)
    mode.set_mode(OWNER, "supertrend", "SIMULATION")
    assert _modes() == {"supertrend": "SIMULATION", "snapback": "MANUAL", "gamma": "MANUAL"}
    assert gamma_move.get_config(SIM_UID).auto_execute is False
    assert snapback.get_config(SIM_UID).auto_execute is False


def test_a_live_strategy_that_was_dormant_stays_off_when_another_is_armed_live(tenants):
    gamma_move.set_config({"auto_execute": True}, uid=OWNER)               # master off: dormant
    mode.set_mode(OWNER, "snapback", "AUTO")
    assert _mode("gamma") == "MANUAL"                                      # not silently promoted to AUTO


# ── AUTO keeps today's readiness blockers ─────────────────────────────────────
def test_auto_is_refused_over_an_unguarded_live_book_and_nothing_changes(tenants):
    unprotected_position()
    before = engine_state.get_config(OWNER).model_dump()
    with pytest.raises(mode.ModeBlocked) as err:
        mode.set_mode(OWNER, "supertrend", "AUTO")
    assert err.value.status == 409 and err.value.code == "auto_execute_blocked"
    assert any("NO stop" in reason for reason in err.value.reasons)
    assert engine_state.get_config(OWNER).model_dump() == before
    assert _modes() == {"supertrend": "MANUAL", "snapback": "MANUAL", "gamma": "MANUAL"}


def test_auto_runs_the_same_check_the_config_routes_run(tenants, monkeypatch):
    from app.services.kite_engine import service

    asked = []
    monkeypatch.setattr(service, "autoexec_enable_blockers",
                        lambda uid, *, was_on, now_on: asked.append((uid, was_on, now_on)) or [])
    mode.set_mode(OWNER, "gamma", "AUTO")
    assert asked == [(OWNER, False, True)]                                # the live book, off -> on


def test_an_unreadable_readiness_check_blocks_auto(tenants, monkeypatch):
    from app.services.kite_engine import service

    def broken(*args, **kwargs):
        raise RuntimeError("journal unreadable")

    monkeypatch.setattr(service, "autoexec_enable_blockers", broken)
    with pytest.raises(mode.ModeBlocked) as err:
        mode.set_mode(OWNER, "gamma", "AUTO")
    assert "journal unreadable" in err.value.reasons[0]
    assert _mode("gamma") == "MANUAL"


def test_a_live_blocker_does_not_stop_simulation_and_standing_down_is_never_gated(tenants):
    mode.set_mode(OWNER, "gamma", "AUTO")
    unprotected_position()                                            # now the live book is unguarded
    assert mode.set_mode(OWNER, "gamma", "SIMULATION")["mode"] == "SIMULATION"
    assert mode.set_mode(OWNER, "gamma", "MANUAL")["mode"] == "MANUAL"


def test_simulation_is_gated_by_its_own_books_not_the_live_ones(tenants):
    unprotected_position(SIM_UID)
    with pytest.raises(mode.ModeBlocked) as err:
        mode.set_mode(OWNER, "gamma", "SIMULATION")
    assert any("NO stop" in reason for reason in err.value.reasons)
    assert _mode("gamma") == "MANUAL"
    assert gamma_move.get_config(OWNER).auto_execute is False


def test_the_simulation_view_cannot_arm_live_money(tenants):
    with pytest.raises(mode.ModeForbidden) as err:
        mode.set_mode(OWNER, "gamma", "AUTO", allow_live=False)
    assert err.value.status == 403 and err.value.message == "Not available in Simulation view"
    assert _mode("gamma") == "MANUAL"
    assert engine_state.get_config(OWNER).auto_execute is False
    assert mode.set_mode(OWNER, "gamma", "SIMULATION", allow_live=False)["mode"] == "SIMULATION"
    assert mode.set_mode(OWNER, "gamma", "MANUAL", allow_live=False)["mode"] == "MANUAL"


# ── MANUAL and SIMULATION -> AUTO ─────────────────────────────────────────────
def test_manual_turns_both_tenants_off_and_settles_the_virtual_master(tenants):
    mode.set_mode(OWNER, "gamma", "SIMULATION")
    mode.set_mode(OWNER, "snapback", "AUTO")
    mode.set_mode(OWNER, "gamma", "MANUAL")
    mode.set_mode(OWNER, "snapback", "MANUAL")
    assert _modes() == {"supertrend": "MANUAL", "snapback": "MANUAL", "gamma": "MANUAL"}
    assert engine_state.get_config(SIM_UID).auto_execute is False         # nothing armed there: inert


def test_manual_on_a_never_seeded_simulation_writes_nothing_there():
    mode.set_mode(OWNER, "gamma", "MANUAL")
    assert _db_rows_for(SIM_UID) == []


def test_simulation_to_auto_turns_the_virtual_side_off_first(tenants):
    mode.set_mode(OWNER, "snapback", "SIMULATION")
    result = mode.set_mode(OWNER, "snapback", "AUTO")
    assert result["live"]["armed"] and not result["simulation"]["armed"]
    assert snapback.get_config(SIM_UID).auto_execute is False
    assert engine_state.get_config(SIM_UID).auto_execute is False          # last strategy out: master settles off


def test_the_virtual_master_stays_on_while_another_simulated_strategy_runs(tenants):
    mode.set_mode(OWNER, "gamma", "SIMULATION")
    mode.set_mode(OWNER, "snapback", "SIMULATION")
    mode.set_mode(OWNER, "snapback", "MANUAL")
    assert engine_state.get_config(SIM_UID).auto_execute is True
    assert _mode("gamma") == "SIMULATION"


# ── failure order: never armed in both places ─────────────────────────────────
def test_a_failed_switch_leaves_the_strategy_off_not_armed_twice(tenants, monkeypatch):
    mode.set_mode(OWNER, "gamma", "AUTO")
    real_arm = mode._arm

    def failing_arm(uid, strategy):
        if uid == SIM_UID:
            raise RuntimeError("disk full")
        return real_arm(uid, strategy)

    monkeypatch.setattr(mode, "_arm", failing_arm)
    with pytest.raises(mode.ModeError) as err:
        mode.set_mode(OWNER, "gamma", "SIMULATION")
    assert err.value.code == "mode_change_failed"
    state = mode.strategy_state(OWNER, "gamma")
    assert state["live"]["armed"] is False and state["simulation"]["armed"] is False and not state["conflict"]


def test_a_failed_live_arming_leaves_the_virtual_side_already_off(tenants, monkeypatch):
    mode.set_mode(OWNER, "gamma", "SIMULATION")
    real_arm = mode._arm

    def failing_arm(uid, strategy):
        if uid == OWNER:
            raise RuntimeError("disk full")
        return real_arm(uid, strategy)

    monkeypatch.setattr(mode, "_arm", failing_arm)
    with pytest.raises(mode.ModeError):
        mode.set_mode(OWNER, "gamma", "AUTO")
    state = mode.strategy_state(OWNER, "gamma")
    assert state["live"]["armed"] is False and state["simulation"]["armed"] is False


def test_the_shared_master_goes_on_last_so_a_failed_flag_write_arms_nothing(tenants, monkeypatch):
    def refuse(values, uid=None):
        raise RuntimeError("disk full")

    monkeypatch.setattr(gamma_move, "set_config", refuse)
    with pytest.raises(mode.ModeFailed):
        mode.set_mode(OWNER, "gamma", "AUTO")
    assert engine_state.get_config(OWNER).auto_execute is False             # the master never went on
    assert set(_modes().values()) == {"MANUAL"}


# ── the Snapback freeze protects the live sample only ─────────────────────────
def test_the_snapback_freeze_does_not_stop_the_simulation_but_still_guards_live(tenants, monkeypatch):
    monkeypatch.setattr(snapback, "prospective_experiment_active", lambda: True)
    assert mode.set_mode(OWNER, "snapback", "SIMULATION")["mode"] == "SIMULATION"
    assert snapback.get_config(SIM_UID).auto_execute is True
    with pytest.raises(snapback.FrozenExperimentConfigError):
        snapback.set_config({"max_open_positions": 3}, uid=OWNER)
    with pytest.raises(mode.ModeError) as err:                             # live AUTO while frozen: refused, fail closed
        mode.set_mode(OWNER, "snapback", "AUTO")
    assert err.value.code == "mode_change_failed"
    state = mode.strategy_state(OWNER, "snapback")                         # the virtual side was disarmed first, so
    assert state["live"]["armed"] is False and state["simulation"]["armed"] is False   # off, never armed twice
    assert snapback.get_config(OWNER).auto_execute is False


# ── reset ────────────────────────────────────────────────────────────────────
def _journal_counts(uid):
    return (journal.list_trades([uid])["total"], journal.list_skips([uid])["total"])


def _dirty_the_tenant():
    from app.services.simulation import sim_runtime

    mode.set_mode(OWNER, "gamma", "SIMULATION")
    mode.set_mode(OWNER, "snapback", "SIMULATION")
    sim_runtime.set_capital(OWNER, 500_000)
    get_ledger().adjust_cash(OWNER, -1234.5)
    positions.register(positions.OpenPosition(
        uid=SIM_UID, symbol="NIFTY24JUN24000CE", exchange="NFO", token=777, qty=75, entry_premium=120.0,
        stop_premium=90.0, order_id="V1", status=positions.OPEN, direction="long",
        red_count_ms=int(time.time() * 1000)))
    journal.record_entry(uid=SIM_UID, strategy="gamma", symbol="NIFTY24JUN24000CE", key="k1", status="OPEN",
                         entry_price=120.0, qty=75)
    journal.record_skip(uid=SIM_UID, strategy="gamma", code="gate", reason="test", signal_id="s1", symbol="X")
    db.set_config(f"gamma_move_record_{SIM_UID}", json.dumps({"x": 1}))
    assert _journal_counts(SIM_UID) == (1, 1)


def test_reset_wipes_the_virtual_tenant_and_starts_it_fresh():
    _dirty_the_tenant()
    result = mode.reset_simulation(OWNER)
    assert result["ok"] is True and result["failed"] == {}
    assert result["capital"] == 500_000 and result["cash"] == 500_000       # capital kept, cash back to it
    assert positions.open_positions(SIM_UID) == []
    assert _db_rows_for(SIM_UID) == []                                      # configs, positions, records
    assert _journal_counts(SIM_UID) == (0, 0)                               # trades, timelines and skips
    assert _modes() == {"supertrend": "MANUAL", "snapback": "MANUAL", "gamma": "MANUAL"}
    assert engine_state.get_config(SIM_UID).auto_execute is False
    assert get_ledger().account(OWNER)["cash"] == 500_000
    control = db.get_execution_control(uid=SIM_UID, account_id=SIM_ACCOUNT_ID)
    assert (control["operator_state"], control["recovery_state"]) == ("RUNNING", "CLEAN")


def test_reset_keeps_the_virtual_capital_limits_not_the_live_default():
    from app.services import live_safety

    _dirty_the_tenant()
    mode.reset_simulation(OWNER)
    cfg = live_safety.daily_loss_config(SIM_UID)
    assert cfg.hard_halt_inr == -round(500_000 * tenant.DAILY_LOSS_HARD_PCT / 100.0, 2)


def test_reset_can_change_the_capital():
    mode.set_mode(OWNER, "gamma", "SIMULATION")
    result = mode.reset_simulation(OWNER, capital=2_000_000)
    assert result["capital"] == 2_000_000 and get_ledger().account(OWNER)["cash"] == 2_000_000


def test_reset_never_touches_the_live_tenant():
    mode.set_mode(OWNER, "gamma", "AUTO")
    gamma_move.set_config({"execution_lots": 5}, uid=OWNER)
    engine_state.set_config(OWNER, engine_state.get_config(OWNER).model_copy(update={"risk_pct": 0.77}))
    db.set_config(f"gamma_move_record_{OWNER}", json.dumps({"keep": True}))
    positions.register(positions.OpenPosition(
        uid=OWNER, symbol="BANKNIFTY24JUN50000CE", exchange="NFO", token=888, qty=30, entry_premium=200.0,
        stop_premium=150.0, order_id="L1", status=positions.OPEN, direction="long",
        red_count_ms=int(time.time() * 1000)))
    journal.record_entry(uid=OWNER, strategy="gamma", symbol="BANKNIFTY24JUN50000CE", key="L", status="OPEN",
                         entry_price=200.0, qty=30)
    journal.record_skip(uid=OWNER, strategy="gamma", code="gate", reason="live", signal_id="s9", symbol="Y")
    mode.set_mode(OWNER, "snapback", "SIMULATION")
    mode.reset_simulation(OWNER)
    assert gamma_move.get_config(OWNER).execution_lots == 5 and _mode("gamma") == "AUTO"
    assert engine_state.get_config(OWNER).risk_pct == 0.77
    assert [p.symbol for p in positions.open_positions(OWNER)] == ["BANKNIFTY24JUN50000CE"]
    assert json.loads(db.get_config(f"gamma_move_record_{OWNER}")) == {"keep": True}
    assert _journal_counts(OWNER) == (1, 1)                                 # the live journal is untouched


def test_reset_clears_unsettled_order_intents_and_the_recovery_fence():
    """An order left SUBMITTING fences the tenant (RECOVERY_REQUIRED); a reset empties the books and the fence."""
    mode.set_mode(OWNER, "gamma", "SIMULATION")
    db.set_execution_control(operator_state="RUNNING", recovery_state="RECOVERY_REQUIRED", reason_code="x",
                             reason="unsettled", actor="test", uid=SIM_UID, account_id=SIM_ACCOUNT_ID)
    with db._conn() as c:
        c.execute("DELETE FROM kite_order_intents WHERE uid=?", (SIM_UID,))
    result = mode.reset_simulation(OWNER)
    assert result["ok"] is True
    control = db.get_execution_control(uid=SIM_UID, account_id=SIM_ACCOUNT_ID)
    assert control["recovery_state"] == "CLEAN"
    assert order_journal.unresolved(SIM_UID, account_id=SIM_ACCOUNT_ID) == []
    assert fill_ledger.holdings(SIM_UID) == []


def test_reset_lifts_the_tenants_own_halt_and_never_touches_the_global_one(monkeypatch):
    """A virtual stop that would not confirm halts the SIMULATION tenant (its own row); a reset clears that."""
    from app.services import live_safety

    mode.set_mode(OWNER, "gamma", "SIMULATION")
    db.set_operator_state(operator_state="HALTED", reason_code="SIM_HALT", reason="virtual stop unconfirmed",
                          actor="simulation", uid=SIM_UID, account_id=SIM_ACCOUNT_ID)
    assert db.get_execution_control(uid=SIM_UID, account_id=SIM_ACCOUNT_ID)["operator_state"] == "HALTED"
    switched = []
    monkeypatch.setattr(live_safety, "set_kill_switch", lambda *a, **k: switched.append(a))
    result = mode.reset_simulation(OWNER)
    assert result["ok"] is True
    control = db.get_execution_control(uid=SIM_UID, account_id=SIM_ACCOUNT_ID)
    assert (control["operator_state"], control["recovery_state"]) == ("RUNNING", "CLEAN")
    assert switched == []                                                   # nothing global was written


def test_reset_only_ever_names_the_sim_tenant(monkeypatch):
    """The wipe is built from tenant.scope_ids(owner): a real uid/account id can never be passed through it."""
    seen = []
    monkeypatch.setattr(mode, "_purge_order_books", lambda uid: seen.append(("books", uid)))
    monkeypatch.setattr(mode, "_purge_snapback_books", lambda uid, account: seen.append(("snap", uid, account)))
    monkeypatch.setattr(mode, "_purge_config_rows", lambda uid: seen.append(("cfg", uid)))
    monkeypatch.setattr(mode, "_forget_memory", lambda uid: seen.append(("mem", uid)))
    purged = []
    monkeypatch.setattr(journal, "purge_simulation", lambda uid: purged.append(uid) or 0)
    mode.reset_simulation(OWNER)
    mode.reset_simulation(SIM_UID)                                         # a sim uid names the same tenant
    assert {entry[1] for entry in seen} == {SIM_UID}
    assert {entry[2] for entry in seen if entry[0] == "snap"} == {SIM_ACCOUNT_ID}
    assert set(purged) == {SIM_UID}


def test_the_journal_purge_refuses_a_live_uid():
    journal.record_entry(uid=OWNER, strategy="gamma", symbol="NIFTY24JUN24000CE", key="L2", status="OPEN",
                         entry_price=100.0, qty=75)
    assert journal.purge_simulation(OWNER) == 0
    assert journal.list_trades([OWNER])["total"] == 1
