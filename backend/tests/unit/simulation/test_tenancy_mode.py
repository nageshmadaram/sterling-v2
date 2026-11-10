"""Per-strategy mode (MANUAL / SIMULATION / AUTO): transitions, the config copy that seeds the virtual
tenant, the live readiness gate, and the reset. Every invariant is asserted against both tenants."""
from __future__ import annotations

import json
import time

import pytest
from tenancy_support import seed_legacy_immutable_review_intent, OWNER, SIM_ACCOUNT_ID, SIM_UID, unprotected_position, wipe_tenants

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



def test_simulation_reset_preflights_frozen_gamma_journal_before_any_mutation(tenants):
    """An immutable review child must never produce a partial simulation wipe."""
    mode.set_mode(OWNER, "gamma", "SIMULATION")
    get_ledger().adjust_cash(OWNER, -12_345)
    protected = seed_legacy_immutable_review_intent()
    cash = get_ledger().account(OWNER)["cash"]
    before = _modes()
    before_control = db.get_execution_control(uid=SIM_UID, account_id=SIM_ACCOUNT_ID)
    with pytest.raises(mode.ModeBlocked) as blocked:
        mode.reset_simulation(OWNER)
    assert blocked.value.code == "immutable_review_evidence"
    assert get_ledger().account(OWNER)["cash"] == cash
    assert _modes() == before and before["gamma"] == "SIMULATION"
    assert db.get_execution_control(uid=SIM_UID, account_id=SIM_ACCOUNT_ID) == before_control
    assert order_journal.get(protected.intent_key).state == "RESERVED"
    assert not order_journal.claim_submission(protected.intent_key)


def test_simulation_reset_unavailable_preflight_does_not_modify_virtual_tenant(
    tenants, monkeypatch,
):
    from app.services.kite_engine import order_journal as journal
    mode.set_mode(OWNER, "gamma", "SIMULATION")
    get_ledger().adjust_cash(OWNER, -2345)
    cash = get_ledger().account(OWNER)["cash"]
    before = _modes()

    def unavailable(_uid):
        raise RuntimeError("SYNTHETIC_DB_SECRET")

    monkeypatch.setattr(journal, "begin_simulation_reset", unavailable)
    with pytest.raises(mode.ModeUnavailable) as blocked:
        mode.reset_simulation(OWNER)
    assert blocked.value.code == "simulation_reset_preflight_unavailable"
    assert "SYNTHETIC_DB_SECRET" not in blocked.value.message
    assert get_ledger().account(OWNER)["cash"] == cash
    assert _modes() == before


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
    monkeypatch.setattr(mode, "_purge_order_books", lambda uid, *, reset_token=None: seen.append(("books", uid)))
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
    with pytest.raises(ValueError):
        journal.purge_simulation(OWNER)
    assert journal.list_trades([OWNER])["total"] == 1


def test_a_failed_journal_purge_is_reported_by_the_reset_not_swallowed(monkeypatch):
    """The stale SIMULATION rows would keep feeding the summary and the equity curve, so the reset must say so."""
    import sqlite3

    journal.record_entry(uid=SIM_UID, strategy="gamma", symbol="NIFTY24JUN24000CE", key="S1", status="OPEN",
                         entry_price=100.0, qty=75)

    def locked(*a, **k):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(journal, "_write", locked)
    result = mode.reset_simulation(OWNER)
    assert result["ok"] is False and "journal_rows" not in result["cleared"]
    assert "database is locked" in result["failed"]["journal_rows"]
    monkeypatch.undo()
    assert journal.list_trades([SIM_UID])["total"] == 1                     # nothing was cleared, and we were told
    assert mode.reset_simulation(OWNER)["ok"] is True                       # a retry finishes the job
    assert journal.list_trades([SIM_UID])["total"] == 0


def test_reset_writes_durable_fence_before_virtual_cash_mutation(tenants, monkeypatch):
    from app.services.simulation import sim_runtime
    original = sim_runtime.reset_owner
    observed = []

    def before_cash(owner, *, capital=None, reset_token=None):
        assert order_journal.simulation_reset_fence_state(SIM_UID) == 'ACTIVE'
        with pytest.raises(ValueError, match='simulation_reset_fenced'):
            order_journal.reserve(uid=SIM_UID, account_id=SIM_ACCOUNT_ID,
                strategy_id='gamma-unwind', generation_id='g', signal_id='pending',
                exchange='NFO', symbol='SYNTHETIC26NOV26000CE', side='SELL', quantity=1)
        observed.append(True)
        return original(owner, capital=capital, reset_token=reset_token)

    monkeypatch.setattr(sim_runtime, 'reset_owner', before_cash)
    assert mode.reset_simulation(OWNER)['ok']
    assert observed == [True]
    assert order_journal.simulation_reset_fence_state(SIM_UID) is None


def test_crashed_simulation_reset_fence_blocks_reset_and_rearming(tenants):
    mode.set_mode(OWNER, 'gamma', 'SIMULATION')
    get_ledger().adjust_cash(OWNER, -123)
    cash = get_ledger().account(OWNER)['cash']
    token = order_journal.begin_simulation_reset(SIM_UID)
    with pytest.raises(mode.ModeBlocked) as refused:
        mode.reset_simulation(OWNER)
    assert refused.value.code == 'simulation_reset_in_progress'
    with pytest.raises(mode.ModeBlocked) as refused_arm:
        mode.set_mode(OWNER, 'gamma', 'SIMULATION')
    assert refused_arm.value.code == 'simulation_reset_fenced'
    assert get_ledger().account(OWNER)['cash'] == cash
    assert order_journal.simulation_reset_fence_state(SIM_UID) == 'ACTIVE'
    order_journal.finish_simulation_reset(SIM_UID, token, success=False)


def test_failed_simulation_reset_retains_fence_and_recovery_state(tenants, monkeypatch):
    from app.services import trade_journal as journal
    mode.set_mode(OWNER, 'gamma', 'SIMULATION')
    original = journal.purge_simulation

    def fail(_uid):
        raise RuntimeError('synthetic failure')

    monkeypatch.setattr(journal, 'purge_simulation', fail)
    result = mode.reset_simulation(OWNER)
    assert not result['ok'] and 'journal_rows' in result['failed']
    assert order_journal.simulation_reset_fence_state(SIM_UID) == 'FAILED'
    control = db.get_execution_control(uid=SIM_UID, account_id=SIM_ACCOUNT_ID)
    assert control['recovery_state'] == 'RECOVERY_REQUIRED'
    with pytest.raises(mode.ModeBlocked, match='Simulation reset recovery'):
        mode.set_mode(OWNER, 'gamma', 'SIMULATION')
    monkeypatch.setattr(journal, 'purge_simulation', original)
    assert mode.reset_simulation(OWNER)['ok']
    assert order_journal.simulation_reset_fence_state(SIM_UID) is None
    assert db.get_execution_control(uid=SIM_UID, account_id=SIM_ACCOUNT_ID)['recovery_state'] == 'CLEAN'


def test_unexpected_reset_interruption_persists_failed_fence_and_halts_tenant(tenants, monkeypatch):
    from app.services.simulation import sim_runtime
    mode.set_mode(OWNER, 'gamma', 'SIMULATION')
    original = sim_runtime.reset_owner

    def interrupted(*_args, **_kw):
        raise RuntimeError('synthetic_private_reset_failure')

    monkeypatch.setattr(sim_runtime, 'reset_owner', interrupted)
    with pytest.raises(mode.ModeUnavailable) as error:
        mode.reset_simulation(OWNER)
    assert error.value.code == 'simulation_reset_interrupted'
    assert 'synthetic_private' not in error.value.message
    assert order_journal.simulation_reset_fence_state(SIM_UID) == 'FAILED'
    control = db.get_execution_control(uid=SIM_UID, account_id=SIM_ACCOUNT_ID)
    assert control['recovery_state'] == 'RECOVERY_REQUIRED'
    monkeypatch.setattr(sim_runtime, 'reset_owner', original)
    assert mode.reset_simulation(OWNER)['ok'] is True
    assert order_journal.simulation_reset_fence_state(SIM_UID) is None


def test_unconfirmed_reset_finalize_never_claims_clean_control_plane(tenants, monkeypatch):
    original = order_journal.finish_simulation_reset

    def failed_finalize(*_args, **_kw):
        raise RuntimeError('synthetic_private_finalize_failure')

    monkeypatch.setattr(order_journal, 'finish_simulation_reset', failed_finalize)
    with pytest.raises(mode.ModeUnavailable) as error:
        mode.reset_simulation(OWNER)
    assert error.value.code == 'simulation_reset_finalize_unavailable'
    assert 'synthetic_private' not in error.value.message
    assert order_journal.simulation_reset_fence_state(SIM_UID) == 'ACTIVE'
    assert db.get_execution_control(uid=SIM_UID, account_id=SIM_ACCOUNT_ID)['recovery_state'] == 'RECOVERY_REQUIRED'
    monkeypatch.setattr(order_journal, 'finish_simulation_reset', original)
    with pytest.raises(mode.ModeBlocked) as blocked:
        mode.reset_simulation(OWNER)
    assert blocked.value.code == 'simulation_reset_in_progress'


@pytest.mark.parametrize('fence_state', ['ACTIVE', 'FAILED'])
def test_recovery_and_boot_refuse_to_reactivate_fenced_tenant(tenants, monkeypatch, fence_state):
    import asyncio
    token = order_journal.begin_simulation_reset(SIM_UID)
    if fence_state == 'FAILED':
        order_journal.finish_simulation_reset(SIM_UID, token, success=False)
    with pytest.raises(ValueError, match='simulation_reset_fenced'):
        tenant.boot_tenant(OWNER)
    assert tenant._needs_recovery(OWNER) is False

    async def check():
        from app.services.exchanges.kite import accounts as ka
        called = []
        async def disallowed(_account):
            called.append('client')
            raise AssertionError('should never fetch client while fenced')
        monkeypatch.setattr(ka, 'acquire_client', disallowed)
        return await tenant.recover_tenant(OWNER)

    result = asyncio.run(check())
    assert result['status'] == 'simulation_reset_fenced'
    assert result['fence_state'] == fence_state
    assert order_journal.simulation_reset_fence_state(SIM_UID) == fence_state


def test_reset_internal_boot_requires_exact_durable_reset_token(tenants):
    token = order_journal.begin_simulation_reset(SIM_UID)
    with pytest.raises(ValueError, match='simulation_reset_fenced'):
        tenant.boot_tenant(OWNER)
    with pytest.raises(ValueError, match='simulation_reset_fenced'):
        tenant.boot_tenant(OWNER, reset_token='invalid')
    tenant.boot_tenant(OWNER, reset_token=token)
    assert order_journal.simulation_reset_fence_state(SIM_UID) == 'ACTIVE'
    order_journal.finish_simulation_reset(SIM_UID, token, success=False)
    with pytest.raises(ValueError, match='simulation_reset_fenced'):
        tenant.boot_tenant(OWNER, reset_token=token)


def test_direct_runtime_capital_write_refuses_durable_fence(tenants):
    from app.services.simulation import sim_runtime
    original = get_ledger().account(OWNER)['capital']
    token = order_journal.begin_simulation_reset(SIM_UID)
    with pytest.raises(ValueError, match='simulation_reset_fenced'):
        sim_runtime.set_capital(OWNER, original + 1)
    assert get_ledger().account(OWNER)['capital'] == original
    order_journal.finish_simulation_reset(SIM_UID, token, success=False)


@pytest.mark.asyncio
@pytest.mark.parametrize('fence_state', ['ACTIVE', 'FAILED'])
@pytest.mark.parametrize('session_open', [True, False])
async def test_virtual_runtime_pass_never_evaluates_fenced_owner(tenants, fence_state, session_open):
    from app.services.simulation import sim_runtime
    token = order_journal.begin_simulation_reset(SIM_UID)
    if fence_state == 'FAILED':
        order_journal.finish_simulation_reset(SIM_UID, token, success=False)
    evaluated = []
    report = await sim_runtime.run_pass(
        owners=[OWNER], force=False,
        session_open=lambda _now: session_open,
        exchange_for=lambda _owner: evaluated.append('built') or None,
    )
    assert evaluated == []
    assert report['owners'] == []
    assert report['snapshots'] == 0
    assert report['errors'][OWNER] == 'simulation_reset_fenced'


@pytest.mark.asyncio
async def test_virtual_runtime_corrupt_fence_fails_closed_without_evaluation(tenants):
    from app.services.simulation import sim_runtime
    with db._conn() as conn:
        conn.execute('INSERT INTO system_config(key,value) VALUES (?,?)',
                     ('simulation_reset_fence_v1:' + SIM_UID, 'PRIVATE_CORRUPT_MARKER'))
    evaluated = []
    report = await sim_runtime.run_pass(
        owners=[OWNER], force=True,
        session_open=lambda _now: True,
        exchange_for=lambda _owner: evaluated.append('built') or None,
    )
    assert evaluated == [] and report['snapshots'] == 0
    assert report['errors'][OWNER] == 'simulation_reset_fence_unavailable'
    assert 'PRIVATE_CORRUPT_MARKER' not in str(report)


@pytest.mark.asyncio
async def test_fenced_runtime_keeps_other_virtual_owner_running(tenants):
    from app.services.simulation import sim_runtime
    token = order_journal.begin_simulation_reset(SIM_UID)
    evaluated = []

    class FakeExchange:
        async def evaluate(self):
            return {'done': True}

    report = await sim_runtime.run_pass(owners=[OWNER, 'unrelated-owner'], force=True,
        session_open=lambda _now: True,
        exchange_for=lambda owner: evaluated.append(owner) or FakeExchange())
    assert report['errors'].get(OWNER) == 'simulation_reset_fenced'
    assert 'unrelated-owner' in evaluated and OWNER not in evaluated
    assert [item['owner'] for item in report['owners']] == ['unrelated-owner']
    order_journal.finish_simulation_reset(SIM_UID, token, success=False)


def test_fenced_tick_path_refuses_to_queue_runtime_evaluation(tenants, monkeypatch):
    from app.services.simulation import sim_runtime
    token = order_journal.begin_simulation_reset(SIM_UID)
    sim_runtime._watch[OWNER] = {1001: [('down', 100.0)]}
    assert sim_runtime.on_ticks(OWNER, [{'instrument_token': 1001, 'last_price': 90.0}]) is False
    assert OWNER not in sim_runtime._inflight
    sim_runtime._watch.pop(OWNER, None)
    order_journal.finish_simulation_reset(SIM_UID, token, success=False)


def test_direct_capital_write_checks_durable_fence_inside_ledger_writer_lock(tenants, monkeypatch):
    """An earlier clean preflight cannot authorize a later fenced cash change."""
    from app.services.simulation import sim_runtime
    old = get_ledger().account(OWNER)['capital']
    token = order_journal.begin_simulation_reset(SIM_UID)
    # Model a stale preflight return: the transactional writer check must still
    # observe the on-disk fence, not trust the earlier observation.
    monkeypatch.setattr(order_journal, 'simulation_reset_fence_state', lambda _uid: None)
    with pytest.raises(ValueError, match='simulation_reset_fenced'):
        sim_runtime.set_capital(OWNER, old + 9)
    assert get_ledger().account(OWNER)['capital'] == old
    order_journal.finish_simulation_reset(SIM_UID, token, success=False)


@pytest.mark.parametrize('mutation', [
    'account', 'cash', 'capital', 'setting', 'order', 'history',
    'gtt', 'mark', 'fill', 'snapshot', 'reset', 'owned_transaction',
])
def test_durable_reset_fence_refuses_individual_virtual_ledger_writers(tenants, mutation):
    """Direct virtual-ledger writers must not bypass a crash-safe reset fence."""
    from app.services.simulation.charges import compute_charges
    from app.services.simulation.ledger import get_ledger

    led = get_ledger()
    order = {
        'order_id': 'synthetic-order-1', 'exchange': 'NFO',
        'tradingsymbol': 'SYNTHETIC26NOV26000CE', 'product': 'NRML',
        'transaction_type': 'BUY', 'status': 'OPEN',
        'exchange_update_timestamp': '2026-11-10 10:00:00',
        '_sim': {'day': '2026-11-10'},
    }
    gtt = {
        'id': 998877, 'status': 'active',
        'condition': {'exchange': 'NFO', 'tradingsymbol': 'SYNTHETIC26NOV26000CE'},
    }
    view = led.account_view(OWNER, '2026-11-10')
    charges = compute_charges(exchange='NFO', tradingsymbol='SYNTHETIC26NOV26000CE',
                              side='BUY', quantity=1, price=100, instrument_type='CE')
    writers = {
        'account': lambda: led.ensure_account(OWNER),
        'cash': lambda: led.adjust_cash(OWNER, -10),
        'capital': lambda: led.set_capital(OWNER, 900_000),
        'setting': lambda: led.set_setting(OWNER, 'slippage_ticks', 2),
        'order': lambda: led.put_order(OWNER, order),
        'history': lambda: led.add_history(OWNER, order),
        'gtt': lambda: led.put_gtt(OWNER, gtt),
        'mark': lambda: led.set_mark(OWNER, 'NFO', 'SYNTHETIC26NOV26000CE', 'NRML', 99),
        'fill': lambda: led.record_fill(OWNER, order=order, quantity=1, price=100,
                                       charges=charges,
                                       meta={'instrument_type':'CE', 'tradingsymbol':order['tradingsymbol']},
                                       day='2026-11-10'),
        'snapshot': lambda: led.add_snapshot(OWNER, view),
        'reset': lambda: led.reset(OWNER),
        'owned_transaction': lambda: _in_owned_transaction(led, OWNER),
    }
    before = led.account(OWNER)
    token = order_journal.begin_simulation_reset(SIM_UID)
    with pytest.raises(ValueError, match='simulation_reset_fenced'):
        writers[mutation]()
    assert led.account(OWNER) == before
    assert led.orders(OWNER) == [] and led.gtts(OWNER) == []
    order_journal.finish_simulation_reset(SIM_UID, token, success=False)


def _in_owned_transaction(ledger, owner):
    with ledger.transaction(owner=owner):
        ledger.next_id('order')


def test_virtual_reset_coordinator_requires_matching_token_to_clear_books(tenants):
    led = get_ledger()
    led.adjust_cash(OWNER, -123)
    token = order_journal.begin_simulation_reset(SIM_UID)
    with pytest.raises(ValueError, match='simulation_reset_fenced'):
        led.reset(OWNER, reset_token='wrong')
    result = led.reset(OWNER, reset_token=token)
    assert result['cash'] == result['capital'] == 1_000_000
    assert order_journal.simulation_reset_fence_state(SIM_UID) == 'ACTIVE'
    order_journal.finish_simulation_reset(SIM_UID, token, success=False)
    with pytest.raises(ValueError, match='simulation_reset_fenced'):
        led.reset(OWNER, reset_token=token)


def test_fenced_virtual_ledger_blocks_one_owner_not_others(tenants):
    led = get_ledger()
    token = order_journal.begin_simulation_reset(SIM_UID)
    assert led.ensure_account('independent-virtual-owner')['cash'] == 1_000_000
    led.put_order('independent-virtual-owner', {
        'order_id': 'other1', 'exchange': 'NFO',
        'tradingsymbol': 'SYNTHETIC26NOV26000CE', 'status': 'OPEN',
        '_sim': {'day':'2026-11-10'},
    })
    assert len(led.orders('independent-virtual-owner')) == 1
    assert order_journal.simulation_reset_fence_state(SIM_UID) == 'ACTIVE'
    order_journal.finish_simulation_reset(SIM_UID, token, success=False)


@pytest.mark.asyncio
async def test_virtual_exchange_order_and_gtt_refuse_an_active_reset_before_order_id(tenants, clock):
    """The actual virtual-exchange routes acquire the owned fence before ids or fills."""
    from app.services.simulation.virtual_exchange import VirtualExchange
    from tests.unit.simulation.sim_fakes import FakeReal, quote_source
    from tests.unit.simulation.test_sim_virtual_exchange import mkt, oco

    real = FakeReal(owner=OWNER, clock=clock)
    ex = VirtualExchange(OWNER, get_ledger(), quote_source(real, clock), clock=clock)
    token = order_journal.begin_simulation_reset(SIM_UID)
    for invoke in (lambda: mkt(ex, 'BUY'), lambda: oco(ex)):
        with pytest.raises(ValueError, match='simulation_reset_fenced'):
            await invoke()
    assert get_ledger().orders(OWNER) == []
    assert get_ledger().gtts(OWNER) == []
    assert real.writes == []
    order_journal.finish_simulation_reset(SIM_UID, token, success=False)


def test_virtual_ledger_write_txn_finishes_before_competing_reset_fence(tenants):
    """A committed owner write precedes the competing reset, or is refused after it."""
    import threading

    led = get_ledger()
    entered, release, reset_done = threading.Event(), threading.Event(), threading.Event()
    result = {'errors': []}
    doc = {'order_id':'fence-race-1', 'exchange':'NFO',
           'tradingsymbol':'SYNTHETIC26NOV26000CE', 'status':'OPEN',
           '_sim':{'day':'2026-11-10'}}

    def writer():
        try:
            with led.transaction(owner=OWNER):
                entered.set()
                assert release.wait(timeout=12)
                led.put_order(OWNER, doc)
        except BaseException as exc:
            result['errors'].append(('writer', str(exc)))

    def resetter():
        try:
            result['token'] = order_journal.begin_simulation_reset(SIM_UID)
        except BaseException as exc:
            result['errors'].append(('resetter', str(exc)))
        finally:
            reset_done.set()

    first = threading.Thread(target=writer, daemon=True)
    second = threading.Thread(target=resetter, daemon=True)
    first.start()
    assert entered.wait(timeout=6)
    second.start()
    try:
        assert not reset_done.wait(timeout=0.15)
    finally:
        release.set()
        first.join(timeout=12)
        second.join(timeout=12)
    assert not first.is_alive() and not second.is_alive()
    assert result['errors'] == []
    assert reset_done.is_set()
    assert get_ledger().get_order(OWNER, 'fence-race-1')['status'] == 'OPEN'
    with pytest.raises(ValueError, match='simulation_reset_fenced'):
        led.put_order(OWNER, {**doc, 'order_id':'fence-race-2'})
    order_journal.finish_simulation_reset(SIM_UID, result['token'], success=False)


def test_unscoped_ledger_connection_is_read_only_on_real_application_db(tenants):
    """Read helpers must not expose a silent raw owner-write bypass."""
    import sqlite3
    ledger = get_ledger()
    before = ledger.account(OWNER)
    with pytest.raises(sqlite3.OperationalError):
        with ledger._cx() as conn:
            conn.execute("UPDATE sim_accounts SET cash=0 WHERE owner=?", (OWNER,))
    assert ledger.account(OWNER) == before


def test_unscoped_ledger_transaction_refused_for_real_application_db(tenants):
    """A generic BEGIN IMMEDIATE has no tenant authorization context."""
    ledger = get_ledger()
    before = ledger.account(OWNER)
    with pytest.raises(ValueError, match="simulation_owner_scope_required"):
        with ledger.transaction():
            with ledger._cx() as conn:
                conn.execute("UPDATE sim_accounts SET cash=0 WHERE owner=?", (OWNER,))
    assert ledger.account(OWNER) == before


def test_global_sequence_cannot_advance_without_owner_scope(tenants):
    ledger = get_ledger()
    with pytest.raises(ValueError, match="simulation_owner_scope_required"):
        ledger.next_id("order")
    token = order_journal.begin_simulation_reset(SIM_UID)
    with pytest.raises(ValueError, match="simulation_reset_fenced"):
        ledger.next_id("order", owner=OWNER)
    assert order_journal.simulation_reset_fence_state(SIM_UID) == 'ACTIVE'
    # Other tenant may still reserve a global id after owner-scoped validation.
    allowed = ledger.next_id('order', owner='independent-sequence-owner')
    assert type(allowed) is int and allowed > 0
    order_journal.finish_simulation_reset(SIM_UID, token, success=False)


def test_nested_owner_transaction_cannot_switch_tenant_scope(tenants):
    ledger = get_ledger()
    original = ledger.account(OWNER)
    with ledger.transaction(owner=OWNER):
        with pytest.raises(ValueError, match="simulation_owner_scope_mismatch"):
            with ledger.transaction(owner='another-virtual-owner'):
                ledger.adjust_cash('another-virtual-owner', -10)
        # A refused scope change must not poison the outer valid owner transaction.
        ledger.adjust_cash(OWNER, -15)
    assert ledger.account(OWNER)['cash'] == original['cash'] - 15
    assert ledger.account('another-virtual-owner') is None


def test_owner_scoped_sequence_inside_existing_owner_transaction(tenants):
    ledger = get_ledger()
    with ledger.transaction(owner=OWNER):
        a = ledger.next_id('order')
        b = ledger.next_id('order', owner=OWNER)
    assert b == a + 1


@pytest.mark.parametrize('kind', ['account_update', 'account_delete', 'order_insert',
                                  'order_update', 'order_delete', 'gtt_insert'])
@pytest.mark.parametrize('marker_state', ['ACTIVE', 'FAILED'])
def test_raw_sql_cannot_mutate_fenced_virtual_books(tenants, kind, marker_state):
    """The DB must refuse normal raw SQLite DML after reset admission or crash."""
    import sqlite3
    led = get_ledger()
    order = {'order_id': 'existing', 'exchange': 'NFO', 'tradingsymbol': 'SYNTHETIC26NOV26000CE',
             'status': 'OPEN', '_sim': {'day': '2026-11-10'}}
    led.put_order(OWNER, order)
    baseline = led.account(OWNER)
    token = order_journal.begin_simulation_reset(SIM_UID)
    if marker_state == 'FAILED':
        order_journal.finish_simulation_reset(SIM_UID, token, success=False)
    statements = {
        'account_update': ('UPDATE sim_accounts SET cash=0 WHERE owner=?', (OWNER,)),
        'account_delete': ('DELETE FROM sim_accounts WHERE owner=?', (OWNER,)),
        'order_insert': ("INSERT INTO sim_orders(owner,order_id,day,status,exchange,tradingsymbol,placed_ms,updated_ms,doc) VALUES(?,?,?,?,?,?,?,?,?)",
                         (OWNER, 'intruder', '2026-11-10', 'OPEN', 'NFO', 'SYNTHETIC26NOV26000CE', 1, 1, '{}')),
        'order_update': ("UPDATE sim_orders SET status='COMPLETE' WHERE owner=?", (OWNER,)),
        'order_delete': ('DELETE FROM sim_orders WHERE owner=?', (OWNER,)),
        'gtt_insert': ("INSERT INTO sim_gtts(owner,trigger_id,status,exchange,tradingsymbol,created_ms,updated_ms,doc) VALUES(?,?,?,?,?,?,?,?)",
                       (OWNER, 4983, 'active', 'NFO', 'SYNTHETIC26NOV26000CE', 1, 1, '{}')),
    }
    sql, args = statements[kind]
    with pytest.raises(sqlite3.DatabaseError):
        with db._conn() as conn:
            conn.execute(sql, args)
    assert led.account(OWNER) == baseline
    assert [o['order_id'] for o in led.orders(OWNER)] == ['existing']
    assert led.gtts(OWNER) == []


def test_raw_sql_fence_guard_installed_for_all_tenant_owned_tables(tenants):
    """Every direct INSERT/UPDATE/DELETE into a virtual tenant table is checked."""
    with db._conn() as conn:
        actual = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'sim_reset_guard_%'")}
    from app.services.simulation.ledger import _OWNED_TABLES
    expected = {f'sim_reset_guard_{table}_{op}'
                for table in ('sim_accounts', *_OWNED_TABLES)
                for op in ('insert', 'update', 'delete')}
    assert expected <= actual


def test_fenced_owner_raw_dml_does_not_block_another_tenant(tenants):
    led = get_ledger()
    led.ensure_account('independent-owner')
    token = order_journal.begin_simulation_reset(SIM_UID)
    with db._conn() as conn:
        conn.execute('UPDATE sim_accounts SET cash=555 WHERE owner=?', ('independent-owner',))
    assert led.account('independent-owner')['cash'] == 555
    assert order_journal.simulation_reset_fence_state(SIM_UID) == 'ACTIVE'
    order_journal.finish_simulation_reset(SIM_UID, token, success=False)


def test_reset_coordinator_is_only_writer_permitted_during_active_raw_sql_fence(tenants):
    led = get_ledger()
    led.put_order(OWNER, {'order_id': 'cleanup-test', 'exchange': 'NFO',
                          'tradingsymbol': 'SYNTHETIC26NOV26000CE', 'status': 'OPEN',
                          '_sim': {'day': '2026-11-10'}})
    token = order_journal.begin_simulation_reset(SIM_UID)
    with pytest.raises(ValueError, match='simulation_reset_fenced'):
        led.reset(OWNER, reset_token='incorrect')
    reset = led.reset(OWNER, reset_token=token)
    assert reset['cash'] == reset['capital']
    assert led.orders(OWNER) == []
    assert order_journal.simulation_reset_fence_state(SIM_UID) == 'ACTIVE'
    order_journal.finish_simulation_reset(SIM_UID, token, success=False)
    with pytest.raises(ValueError, match='simulation_reset_fenced'):
        led.reset(OWNER, reset_token=token)


def test_raw_sql_reset_fence_survives_new_python_process(tenants):
    """Durable guards remain effective after process restart, not only in memory."""
    import os
    import subprocess
    import sys

    led = get_ledger()
    before = led.account(OWNER)
    token = order_journal.begin_simulation_reset(SIM_UID)
    env = dict(os.environ, STERLING_DB_PATH=str(db._DB_PATH), STERLING_UNDER_TEST='1')
    code = '''
from app.services import db
with db._conn() as conn:
    conn.execute("UPDATE sim_accounts SET cash=12345 WHERE owner=?", ("default",))
'''
    for state in ('ACTIVE', 'FAILED'):
        if state == 'FAILED':
            order_journal.finish_simulation_reset(SIM_UID, token, success=False)
        result = subprocess.run([sys.executable, '-c', code], env=env,
                                text=True, capture_output=True, timeout=20)
        assert result.returncode != 0
        assert 'simulation_reset_fenced' in result.stderr
        assert led.account(OWNER) == before


def test_raw_sql_trigger_is_fail_closed_on_corrupt_marker(tenants):
    import sqlite3
    led = get_ledger()
    before = led.account(OWNER)
    with db._conn() as conn:
        conn.execute('INSERT INTO system_config(key,value) VALUES (?,?)',
                     ('simulation_reset_fence_v1:' + SIM_UID, 'CORRUPTED_PRIVACY_MARKER'))
    with pytest.raises(sqlite3.DatabaseError):
        with db._conn() as conn:
            conn.execute('UPDATE sim_accounts SET cash=0 WHERE owner=?', (OWNER,))
    assert led.account(OWNER) == before


def test_unmanaged_raw_sqlite_connection_is_fail_closed_even_without_fence(tenants):
    """Bypassing the registered app connection is not a supported write path."""
    import sqlite3
    led = get_ledger()
    old = led.account(OWNER)
    with pytest.raises(sqlite3.DatabaseError):
        with sqlite3.connect(db._DB_PATH, timeout=15) as conn:
            conn.execute('UPDATE sim_accounts SET cash=? WHERE owner=?', (old['cash'] - 1, OWNER))
    assert led.account(OWNER) == old
    # The legitimate application connection keeps ordinary unfenced writes.
    with db._conn() as conn:
        conn.execute('UPDATE sim_accounts SET cash=? WHERE owner=?', (old['cash'] - 1, OWNER))
    assert led.account(OWNER)['cash'] == old['cash'] - 1


def test_simulation_reset_persists_confirmed_stage_and_safe_completion(tenants):
    result = mode.reset_simulation(OWNER)
    assert result['ok'] is True
    checkpoint = order_journal.simulation_reset_checkpoint_state(SIM_UID)
    assert checkpoint == {'state': 'COMPLETE', 'stage': 'complete'}
    assert order_journal.simulation_reset_fence_state(SIM_UID) is None
    assert 'token' not in str(checkpoint).lower()


def test_failed_cross_store_reset_keeps_last_confirmed_checkpoint(tenants, monkeypatch):
    from app.services import trade_journal
    def fail(_uid):
        raise RuntimeError('PRIVATE_BACKEND_FAILURE')
    with monkeypatch.context() as patch:
        patch.setattr(trade_journal, 'purge_simulation', fail)
        result = mode.reset_simulation(OWNER)
    assert result['ok'] is False
    checkpoint = order_journal.simulation_reset_checkpoint_state(SIM_UID)
    assert checkpoint == {'state': 'FAILED', 'stage': 'virtual_ledger'}
    assert order_journal.simulation_reset_fence_state(SIM_UID) == 'FAILED'
    assert 'PRIVATE_BACKEND_FAILURE' not in str(checkpoint)


def test_reset_checkpoint_is_monotone_and_token_bound(tenants):
    token = order_journal.begin_simulation_reset(SIM_UID)
    assert order_journal.simulation_reset_checkpoint_state(SIM_UID) == {'state': 'ACTIVE', 'stage': 'fenced'}
    with pytest.raises(ValueError, match='simulation_reset_fence_mismatch'):
        order_journal.checkpoint_simulation_reset(SIM_UID, 'wrong', 'journal_rows')
    with pytest.raises(ValueError, match='invalid_simulation_reset_stage'):
        order_journal.checkpoint_simulation_reset(SIM_UID, token, 'unknown_stage')
    order_journal.checkpoint_simulation_reset(SIM_UID, token, 'virtual_ledger')
    with pytest.raises(ValueError, match='simulation_reset_checkpoint_regression'):
        order_journal.checkpoint_simulation_reset(SIM_UID, token, 'disarmed')
    assert order_journal.simulation_reset_checkpoint_state(SIM_UID) == {'state': 'ACTIVE', 'stage': 'virtual_ledger'}
    order_journal.finish_simulation_reset(SIM_UID, token, success=False)
    assert order_journal.simulation_reset_checkpoint_state(SIM_UID) == {'state': 'FAILED', 'stage': 'virtual_ledger'}
    with pytest.raises(ValueError, match='simulation_reset_fence_mismatch'):
        order_journal.checkpoint_simulation_reset(SIM_UID, token, 'journal_rows')


def test_checkpoint_survives_process_death_without_releasing_active_fence(tenants):
    import os
    import subprocess
    import sys
    code = '''
from app.services import db
from app.services.kite_engine import order_journal
assert db.init()
uid='sim:default'
tok=order_journal.begin_simulation_reset(uid)
order_journal.checkpoint_simulation_reset(uid, tok, 'virtual_ledger')
import os
os._exit(79)
'''
    env = dict(os.environ, STERLING_DB_PATH=str(db._DB_PATH), STERLING_UNDER_TEST='1')
    result = subprocess.run([sys.executable, '-c', code], env=env,
                            text=True, capture_output=True, timeout=25)
    assert result.returncode == 79
    assert order_journal.simulation_reset_checkpoint_state(SIM_UID) == {'state': 'ACTIVE', 'stage': 'virtual_ledger'}
    assert order_journal.simulation_reset_fence_state(SIM_UID) == 'ACTIVE'
    with pytest.raises(mode.ModeBlocked, match='An earlier simulation reset remains active'):
        mode.reset_simulation(OWNER)


def test_late_cleanup_failure_cannot_advance_past_missing_stage(tenants, monkeypatch):
    def fail(_uid, _account_id):
        raise RuntimeError('PRIVATE_LATE_STAGE_FAILURE')
    with monkeypatch.context() as patch:
        patch.setattr(mode, '_purge_snapback_books', fail)
        result = mode.reset_simulation(OWNER)
    assert result['ok'] is False and 'snapback_books' in result['failed']
    assert 'config_rows' in result['cleared']  # cleanup continued, but does not certify completeness
    assert order_journal.simulation_reset_checkpoint_state(SIM_UID) == {
        'state': 'FAILED', 'stage': 'ema_trend_books',
    }
    assert order_journal.simulation_reset_fence_state(SIM_UID) == 'FAILED'
    assert mode.reset_simulation(OWNER)['ok'] is True  # explicit retry replaces generation
    assert order_journal.simulation_reset_checkpoint_state(SIM_UID) == {
        'state': 'COMPLETE', 'stage': 'complete',
    }


def test_inconsistent_or_missing_checkpoint_never_claims_clean(tenants):
    import json
    token = order_journal.begin_simulation_reset(SIM_UID)
    with db._conn() as conn:
        conn.execute('DELETE FROM system_config WHERE key=?',
                     ('simulation_reset_fence_v1:' + SIM_UID,))
    with pytest.raises(ValueError, match='simulation_reset_checkpoint_inconsistent'):
        order_journal.simulation_reset_checkpoint_state(SIM_UID)
    with pytest.raises(ValueError, match='simulation_reset_checkpoint_inconsistent'):
        order_journal.begin_simulation_reset(SIM_UID)
    with db._conn() as conn:
        conn.execute('INSERT INTO system_config(key,value) VALUES (?,?)',
                     ('simulation_reset_fence_v1:' + SIM_UID,
                      json.dumps({'state': 'ACTIVE', 'token': token})))
        conn.execute('DELETE FROM system_config WHERE key=?',
                     ('simulation_reset_checkpoint_v1:' + SIM_UID,))
    assert order_journal.simulation_reset_checkpoint_state(SIM_UID) == {
        'state': 'UNKNOWN', 'stage': 'unknown',
    }
    assert order_journal.simulation_reset_fence_state(SIM_UID) == 'ACTIVE'
