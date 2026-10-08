"""Family live gate: no Snapback exposure-increasing live order without RiskEngine
approval, a HEALTHY system and a reconciled broker. The evidence verdict and the
LIVE_ELIGIBLE promotion flag are waived for this release: they are reported as
advisories and never refuse an order. Protective actions stay permitted while halted.
"""

from __future__ import annotations

import pytest

from app.services.snapback_family_gate import (
    LiveIntent,
    evaluate_live_gate,
    is_exposure_increasing,
)


def _ok(**over):
    base = dict(
        intent=LiveIntent.ENTER,
        evidence_verdict="PASSED",
        live_eligible=True,
        risk_approved=True,
        system_status="HEALTHY",
        broker_reconciled=True,
        operator_override=False,
    )
    base.update(over)
    return base


def test_all_conditions_met_allows_entry():
    decision = evaluate_live_gate(**_ok())

    assert decision.allowed is True
    assert decision.reasons == []


def test_unproven_evidence_is_advisory_and_does_not_block_entry():
    # Evidence is waived: INCONCLUSIVE evidence and an unpromoted state are shown,
    # never enforced.
    decision = evaluate_live_gate(
        **_ok(evidence_verdict="INCONCLUSIVE", live_eligible=False)
    )

    assert decision.allowed is True
    assert decision.reasons == []
    assert "evidence_not_passed" in decision.advisories
    assert "not_live_eligible" in decision.advisories


def test_failed_evidence_is_advisory_too():
    decision = evaluate_live_gate(**_ok(evidence_verdict="FAILED"))

    assert decision.allowed is True
    assert decision.advisories == ["evidence_not_passed"]


def test_waived_evidence_never_hides_a_real_blocker():
    decision = evaluate_live_gate(
        **_ok(evidence_verdict="FAILED", live_eligible=False, risk_approved=False)
    )

    assert decision.allowed is False
    assert decision.reasons == ["risk_engine_denied"]
    assert "evidence_not_passed" not in decision.reasons


def test_risk_engine_denial_blocks():
    decision = evaluate_live_gate(**_ok(risk_approved=False))

    assert decision.allowed is False
    assert "risk_engine_denied" in decision.reasons


def test_degraded_system_blocks():
    decision = evaluate_live_gate(**_ok(system_status="DEGRADED"))

    assert decision.allowed is False
    assert "system_not_healthy" in decision.reasons


def test_unreconciled_broker_blocks():
    decision = evaluate_live_gate(**_ok(broker_reconciled=False))

    assert decision.allowed is False
    assert "broker_not_reconciled" in decision.reasons


def test_manual_override_cannot_bypass_the_gate():
    decision = evaluate_live_gate(
        **_ok(
            system_status="DEGRADED",
            broker_reconciled=False,
            operator_override=True,
        )
    )

    assert decision.allowed is False
    assert "system_not_healthy" in decision.reasons
    assert "broker_not_reconciled" in decision.reasons


@pytest.mark.parametrize("intent", [LiveIntent.EXIT, LiveIntent.FLATTEN, LiveIntent.RECONCILE])
def test_protective_actions_remain_permitted_while_halted(intent):
    decision = evaluate_live_gate(
        intent=intent,
        evidence_verdict="INCONCLUSIVE",
        live_eligible=False,
        risk_approved=False,
        system_status="HALTED",
        broker_reconciled=False,
        operator_override=False,
    )

    assert decision.allowed is True
    assert decision.protective is True


def test_exposure_classification():
    assert is_exposure_increasing(LiveIntent.ENTER) is True
    assert is_exposure_increasing(LiveIntent.ADD) is True
    assert is_exposure_increasing(LiveIntent.EXIT) is False
    assert is_exposure_increasing(LiveIntent.FLATTEN) is False
    assert is_exposure_increasing(LiveIntent.RECONCILE) is False


def test_unknown_intent_fails_closed():
    decision = evaluate_live_gate(**_ok(intent="something_new"))

    assert decision.allowed is False
    assert "unknown_intent" in decision.reasons


def test_stop_switch_blocks_entries_but_not_exits(tmp_path, monkeypatch):
    """The runner must skip the entry phase while halted, and still run risk/EOD."""
    import app.services.snapback_family_ops as fam

    monkeypatch.setenv("STERLING_NEW_TRADES_HALT_PATH", str(tmp_path / "halt.json"))

    assert fam.new_trades_halted() is False
    fam.set_new_trades_halted(True, reason="drill")
    assert fam.new_trades_halted() is True

    import inspect

    from app.services import snapback_runner

    source = inspect.getsource(snapback_runner.tick)

    # Entries are gated; the risk monitor and EOD phases are not.
    assert "entries_halted" in source
    assert "process_prospective_intraday_risk" in source
    # The end-of-day phase is now the durable finalizer, not the fetching wrapper.
    assert "finalize_eod_session" in source

    fam.set_new_trades_halted(False, reason="drill end")
    assert fam.new_trades_halted() is False


def test_halt_state_default_is_bound_to_persistent_state_root_not_cwd(
    tmp_path, monkeypatch
):
    import app.services.snapback_family_ops as fam

    state = tmp_path / "persistent"
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.setenv("STERLING_STATE_ROOT", str(state))
    monkeypatch.delenv("STERLING_NEW_TRADES_HALT_PATH", raising=False)
    monkeypatch.chdir(elsewhere)

    fam.set_new_trades_halted(True, reason="operator stop")

    expected = state / "data" / "snapback" / "new_trades_halt.json"
    assert expected.is_file()
    assert fam._state_path() == expected
    assert not (elsewhere / "data" / "snapback" / "new_trades_halt.json").exists()
    assert fam.new_trades_halted() is True


def test_unreadable_halt_state_fails_closed(tmp_path, monkeypatch):
    import app.services.snapback_family_ops as fam

    bad = tmp_path / "halt.json"
    bad.write_text("{not json", encoding="utf-8")
    monkeypatch.setenv("STERLING_NEW_TRADES_HALT_PATH", str(bad))

    assert fam.new_trades_halted() is True
