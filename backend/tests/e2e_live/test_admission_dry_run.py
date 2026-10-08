"""End-to-end admission dry run: a valid signal -> a broker order, on a FRESH install.

One live account, a clean database, no operator environment beyond the account itself,
the Snapback evidence runner NOT running (its preflight fails exactly as on a machine with
no experiment configured). Every test boots the application through ``main.lifespan`` and
drives a REAL production entry path with a synthetic signal. The broker is an offline
Kite REST backend behind the production ``KiteClient``; see ``tests/e2e_live/machine.py``.

A test fails with the FIRST refusal the signal met, in the refusal's own words, so a wiring
gap reads as "<strategy> was refused: <code/reason>" rather than as an assertion on state.
"""
from __future__ import annotations

import pytest

from tests.e2e_live import scenarios as S
from tests.e2e_live.machine import UID


async def _fresh_live_machine(machine):
    """Day-two boot: the account was added and logged in yesterday, the process restarts."""
    machine.fresh_process()
    machine.provision_account()
    await machine.restart()
    await machine.run_live_runtime_once()
    return machine


def _explain(outcome: "S.Outcome") -> str:
    return f"{outcome}\n" + "\n".join(f"  {line}" for line in outcome.activity[-8:])


# ---------------------------------------------------------------------------------- boot

@pytest.mark.asyncio
async def test_fresh_machine_boots_into_an_open_admission(machine):
    await _fresh_live_machine(machine)

    live_runtime = machine.m("app.services.live_runtime")
    status = live_runtime.live_status()
    assert status["safe_mode"]["state"] == "NORMAL", status["safe_mode"]
    assert status["recovery"]["overall"] == "CLEAN", status["recovery"]
    assert status["exposure"]["fresh"] and status["exposure"]["readable"], status["exposure"]
    verdict = machine.admission()
    assert verdict["allowed"], verdict

    # The retired strategies' loops exist (maintenance-only) and so do the three live ones.
    names = machine.task_names()
    assert names.count("auto_scan_loop") >= 5, names       # kite engine, gamma, snapback board,
    assert "snapback-live-dispatcher" in names, names       # adaptive edge, intraday
    assert "live-recovery" in names and "live-exposure" in names, names


@pytest.mark.asyncio
async def test_snapback_evidence_runner_is_not_running_and_does_not_gate_live_entries(machine):
    """The machine this suite models cannot run the prospective evidence runner."""
    await _fresh_live_machine(machine)

    preflight = machine.m("app.services.snapback_preflight").run_preflight()
    assert not preflight.may_start_runner, "the evidence runner must be unable to start here"
    health = machine.m("app.services.snapback_health")
    assert "prospective_runner_unavailable" in health.get_prospective_health(UID)["unresolved_errors"]

    # A dispatcher cycle is what records the live heartbeat; after one, nothing about the dead
    # evidence runner may be reported as a blocker for live Snapback.
    dispatcher = machine.m("app.services.snapback_dispatcher").get_dispatcher()
    await dispatcher.cycle(UID)
    readiness = machine.m("app.services.snapback_readiness")
    assert readiness.gate_blockers(UID) == [], readiness.readiness_state(UID)


# ------------------------------------------------------------------------- the three paths

@pytest.mark.asyncio
async def test_supertrend_signal_reaches_the_broker_and_is_protected(machine):
    await _fresh_live_machine(machine)
    S.configure_supertrend(machine)
    outcome = await S.drive_supertrend(machine)

    assert outcome.ordered, f"Supertrend was refused: {_explain(outcome)}"
    assert outcome.protected, f"Supertrend entry is not protected: {_explain(outcome)}"
    gtt = machine.exchange.gtt_posts
    assert len(gtt) == 1
    # A priced LIMIT sell leg under the trigger, NRML (Kite refuses MIS and unpriced GTTs).
    assert '"order_type": "LIMIT"' in gtt[0]["orders"] and '"product": "NRML"' in gtt[0]["orders"]


@pytest.mark.asyncio
async def test_gamma_entry_reaches_the_broker_and_is_protected(machine):
    await _fresh_live_machine(machine)
    S.configure_gamma(machine)
    outcome = await S.drive_gamma(machine)

    assert outcome.ordered, f"Gamma Unwind was refused: {_explain(outcome)}"
    assert outcome.protected, f"Gamma entry is not protected: {_explain(outcome)}"
    assert len(machine.exchange.gtt_posts) == 1


@pytest.mark.asyncio
async def test_snapback_swing_entry_reaches_the_broker_with_stop_and_hedge(machine):
    await _fresh_live_machine(machine)
    S.configure_snapback(machine)
    outcome = await S.drive_snapback(machine)

    assert outcome.ordered, f"Snapback was refused: {_explain(outcome)}"
    assert outcome.protected, f"Snapback entry is not protected: {_explain(outcome)}"
    positions = outcome.detail["positions"]
    assert positions and positions[0]["status"] == "OPEN", positions
    assert positions[0]["hedge_qty"] > 0 and positions[0]["hedge_entry_order"], positions
    symbols = [(b["tradingsymbol"], b["transaction_type"]) for b in machine.exchange.order_posts]
    assert (S.SNAPBACK_OPTION, "BUY") in symbols and (S.SNAPBACK_FUTURE, "BUY") in symbols, symbols
