"""Waived gates are advisory everywhere; real safety facts still block.

Release certification, the forward-sample verdict, identity drift and the
rehearsal gate are waived for this release. These tests defend the other half of
that decision: nothing real was waived with them. A failing safety supervisor,
an unusable broker binding or a kill switch still refuses an entry, and the
report says why.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core import waivers
from app.core.operator_report import DoctorCheck
from app.services import capital_permission_report as report

REPO = Path(__file__).resolve().parents[3]
STERLINGCTL = REPO / "scripts" / "sterlingctl"


# --------------------------------------------------------------------------
# The waiver list
# --------------------------------------------------------------------------
class TestDoctorSplit:
    def test_a_failing_waived_check_is_advisory_and_the_doctor_stays_safe(self):
        split = waivers.split_doctor_checks([
            DoctorCheck("clock", True),
            DoctorCheck("release_certification", False, "gates not passed"),
            DoctorCheck("evidence_meta", None, "unreadable"),
        ])

        assert split.safe is True
        assert split.exit_code == 0
        assert [c.name for c in split.advisories] == ["release_certification", "evidence_meta"]

    @pytest.mark.parametrize("name", [
        "safe_mode_state", "execution_control_state", "account_binding", "static_egress",
        "broker_session", "database", "clock", "disk", "backup_age", "restore_check",
        "config_identity", "family_account", "allocation_capital", "risk_limits",
    ])
    def test_real_safety_checks_are_never_waived(self, name):
        assert waivers.is_advisory_doctor_check(name) is False
        failed = waivers.split_doctor_checks([DoctorCheck(name, False)])
        assert failed.exit_code == 1 and [c.name for c in failed.failures] == [name]
        unreadable = waivers.split_doctor_checks([DoctorCheck(name, None)])
        assert unreadable.exit_code == 2 and [c.name for c in unreadable.unknowns] == [name]

    def test_a_failure_outranks_an_unreadable_check(self):
        split = waivers.split_doctor_checks([
            DoctorCheck("static_egress", None), DoctorCheck("safe_mode_state", False),
        ])
        assert split.exit_code == 1

    def test_nothing_run_is_never_safe(self):
        split = waivers.split_doctor_checks([])
        assert split.safe is False and split.exit_code == 2

    def test_every_certification_gate_is_advisory(self):
        from app.services.release_certification import RELEASE_GATES

        assert {g.key for g in RELEASE_GATES} == set(waivers.ADVISORY_RELEASE_GATES)
        assert all(waivers.is_advisory_release_gate(g.key) for g in RELEASE_GATES)


# --------------------------------------------------------------------------
# The permission report reads the real safety state
# --------------------------------------------------------------------------
def _snapshot(*blockers):
    return SimpleNamespace(
        blockers=tuple(blockers),
        may_increase_exposure=not blockers,
    )


@pytest.fixture()
def quiet_report(monkeypatch):
    """Everything real passes; the waived gates are all unproven."""
    monkeypatch.setattr(report, "_binding_live_ready", lambda: True)
    monkeypatch.setattr(report, "_release_certified", lambda: False)
    monkeypatch.setattr(report.lane_gate_inputs, "lane_promotion_passed", lambda *_a, **_k: False)
    monkeypatch.setattr(report.lane_gate_inputs, "lane_identity_matches_frozen", lambda *_a: None)
    monkeypatch.setattr(report.lane_gate_inputs, "shadow_gate_passed", lambda *_a, **_k: False)
    monkeypatch.setattr("app.services.safety_supervisor.snapshot", lambda **_k: _snapshot())
    monkeypatch.setattr(
        "app.core.lane_registry.LIVE_EXECUTION_ENABLED", True
    )


class TestLanePermission:
    def test_a_live_lane_is_permitted_with_every_waived_gate_unproven(self, quiet_report):
        verdict = report.lane_permission("snapback:swing")

        assert verdict.allowed is True
        assert verdict.blockers == ()
        assert set(verdict.advisories) == {
            "RELEASE_NOT_CERTIFIED", "LANE_PROMOTION_NOT_PASSED",
            "LANE_IDENTITY_DRIFT", "REHEARSAL_GATE_NOT_PASSED",
        }
        assert "waived (advisory, never blocking)" in verdict.render()
        assert "PERMITTED" in verdict.render()

    def test_each_live_lane_the_engines_run_is_permitted(self, quiet_report):
        for key in ("snapback:scalping", "snapback:swing", "supertrend:swing"):
            assert report.lane_permission(key).allowed is True, key

    def test_a_research_lane_is_refused_for_its_state_alone(self, quiet_report):
        verdict = report.lane_permission("supertrend:intraday")
        assert verdict.allowed is False
        assert verdict.blockers == ("LANE_NOT_LIVE_STATE",)

    def test_the_kill_switch_blocks_and_says_so(self, quiet_report, monkeypatch):
        monkeypatch.setattr("app.core.lane_registry.LIVE_EXECUTION_ENABLED", False)
        verdict = report.lane_permission("snapback:swing")
        assert verdict.allowed is False
        assert verdict.blockers == ("GLOBAL_LIVE_DISABLED",)
        assert "STERLING_LIVE_EXECUTION" in verdict.reasons[0]

    def test_a_binding_that_is_not_live_ready_blocks(self, quiet_report, monkeypatch):
        monkeypatch.setattr(report, "_binding_live_ready", lambda: False)
        assert report.lane_permission("snapback:swing").blockers == ("ACCOUNT_BINDING_NOT_READY",)

    def test_an_unreadable_binding_is_unknown_and_blocks(self, quiet_report, monkeypatch):
        monkeypatch.setattr(report, "_binding_live_ready", lambda: None)
        verdict = report.lane_permission("snapback:swing")
        assert verdict.allowed is False
        assert "account_binding_ready" in verdict.unknowns


class TestTheRiskAndExposureGatesAreWiredToTheSafetySupervisor:
    def test_a_daily_loss_halt_fails_the_risk_gate_and_the_overall_gate(self, quiet_report, monkeypatch):
        monkeypatch.setattr(
            "app.services.safety_supervisor.snapshot", lambda **_k: _snapshot("daily_loss_halt")
        )
        verdict = report.lane_permission("snapback:swing")
        assert verdict.allowed is False
        assert "RISK_GATE_NOT_PASSED" in verdict.blockers
        assert "SAFETY_BLOCKS_EXPOSURE" in verdict.blockers
        assert "EXPOSURE_GATE_NOT_PASSED" not in verdict.blockers

    @pytest.mark.parametrize("code", [
        "recovery_required", "external_exposure_stale",
        "external_exposure_never_observed", "external_exposure_unreadable",
    ])
    def test_unexplained_or_unresolved_exposure_fails_the_exposure_gate(
        self, quiet_report, monkeypatch, code
    ):
        monkeypatch.setattr(
            "app.services.safety_supervisor.snapshot", lambda **_k: _snapshot(code)
        )
        verdict = report.lane_permission("snapback:swing")
        assert verdict.allowed is False
        assert "EXPOSURE_GATE_NOT_PASSED" in verdict.blockers
        assert "RISK_GATE_NOT_PASSED" not in verdict.blockers

    def test_safe_mode_blocks_through_the_supervisor(self, quiet_report, monkeypatch):
        monkeypatch.setattr(
            "app.services.safety_supervisor.snapshot", lambda **_k: _snapshot("safe_mode")
        )
        verdict = report.lane_permission("snapback:swing")
        assert verdict.allowed is False
        assert verdict.blockers == ("SAFETY_BLOCKS_EXPOSURE",)

    def test_an_unreadable_supervisor_is_unknown_for_all_three_and_blocks(
        self, quiet_report, monkeypatch
    ):
        def _boom(**_k):
            raise RuntimeError("safety store down")

        monkeypatch.setattr("app.services.safety_supervisor.snapshot", _boom)
        verdict = report.lane_permission("snapback:swing")
        assert verdict.allowed is False
        assert {"safety_may_increase_exposure", "risk_gate_passed",
                "exposure_gate_passed"} <= set(verdict.unknowns)


class TestGammaUnwindIsRepresentedFromTheReleaseScope:
    def test_it_is_permitted_when_the_real_checks_pass(self, quiet_report):
        verdict = report.originator_permission("gamma_move")
        assert verdict.allowed is True
        assert verdict.lane_key == "gamma_move"

    def test_it_follows_the_kill_switch(self, quiet_report, monkeypatch):
        monkeypatch.setattr("app.core.lane_registry.LIVE_EXECUTION_ENABLED", False)
        assert report.originator_permission("gamma_move").blockers == ("GLOBAL_LIVE_DISABLED",)

    def test_a_strategy_outside_the_release_scope_is_refused(self, quiet_report):
        verdict = report.originator_permission("adaptive_edge")
        assert verdict.allowed is False
        assert "LANE_NOT_LIVE_STATE" in verdict.blockers

    def test_the_full_report_lists_every_lane_and_gamma_once(self, quiet_report):
        rows = report.all_lane_permissions()
        keys = [r["lane_key"] for r in rows]
        assert len(keys) == len(set(keys)) == 12
        assert {"gamma_move", "price_rules"} <= set(keys)
        assert all(r["advisory_status"] == "waived" for r in rows)

    def test_the_rendered_report_never_calls_the_state_shadow_or_refused(self, quiet_report):
        text = report.render_permissions()
        assert "REAL-MONEY ENTRY REFUSED" not in text
        assert "shadow" not in text.lower()
        assert "paper" not in text.lower()


# --------------------------------------------------------------------------
# sterlingctl states the contract, and the commands run
# --------------------------------------------------------------------------
def _slice(text: str, start: str, end: str) -> str:
    return text[text.index(start):text.index(end)]


class TestSterlingctlSource:
    def test_acceptance_is_not_refused_because_live_entry_is_enabled(self):
        text = STERLINGCTL.read_text(encoding="utf-8")
        acceptance = _slice(text, "  acceptance)", "  evidence-start)")
        assert "REFUSING acceptance" not in acceptance
        assert "must remain false" not in acceptance
        assert "Production Shadow" not in acceptance
        # Still read-only: no order placement anywhere in the acceptance arm.
        for forbidden in ("place_order", "submit_order", "CanonicalExecutionService"):
            assert forbidden not in acceptance

    def test_certify_never_sets_the_exit_code_from_release_readiness(self):
        text = STERLINGCTL.read_text(encoding="utf-8")
        certify = _slice(text, "  certify)", "  bindings)")
        assert "sys.exit(0 if report.release_ready else 1)" not in certify
        assert "WAIVER_NOTE" in certify

    def test_the_doctor_separates_waived_from_blocking(self):
        text = STERLINGCTL.read_text(encoding="utf-8")
        doctor = _slice(text, "cmd_doctor() {", 'case "${1:-}" in')
        assert "split_doctor_checks" in doctor
        assert "waived" in doctor
        assert "sys.exit(split.exit_code)" in doctor

    def test_no_stale_status_wording_survives(self):
        text = STERLINGCTL.read_text(encoding="utf-8")
        for stale in ("Production Shadow", "PRODUCTION_SHADOW", "REFUSING acceptance",
                      "REAL ENTRY ORDERS", "ELIGIBLE_LANES",
                      "why each lane may not send a real-money entry"):
            assert stale not in text, stale


def _ctl(*args: str, env_extra: dict[str, str] | None = None, tmp: Path) -> subprocess.CompletedProcess:
    import os

    env = {**os.environ, "STERLING_STATE_ROOT": str(tmp), "ENVIRONMENT": "development"}
    env.pop("STERLING_LIVE_EXECUTION", None)
    env.update(env_extra or {})
    return subprocess.run(
        [str(STERLINGCTL), *args], capture_output=True, text=True, env=env, timeout=240,
    )


class TestSterlingctlRuns:
    def test_lanes_show_the_live_lanes_and_gamma_by_default(self, tmp_path):
        done = _ctl("lanes", tmp=tmp_path)
        assert done.returncode == 0, done.stderr
        rows = {line.split()[0]: line for line in done.stdout.splitlines() if line.strip()}
        for live in ("snapback:scalping", "snapback:swing", "supertrend:swing"):
            assert " yes" in rows[live], rows[live]
        assert " no" in rows["supertrend:intraday"]
        assert "gamma_move" in rows and " yes" in rows["gamma_move"]
        assert "Live entries: ENABLED" in done.stdout

    def test_the_kill_switch_turns_every_row_off_and_says_so(self, tmp_path):
        done = _ctl("lanes", env_extra={"STERLING_LIVE_EXECUTION": "off"}, tmp=tmp_path)
        assert done.returncode == 0, done.stderr
        assert "Live entries: HALTED" in done.stdout
        rows = {line.split()[0]: line for line in done.stdout.splitlines() if line.strip()}
        for key in ("snapback:swing", "supertrend:swing"):
            assert " no" in rows[key], rows[key]
        assert "LIVE_EXECUTION_DISABLED" in rows["gamma_move"]

    def test_permission_reports_blocking_and_waived_apart(self, tmp_path):
        done = _ctl("permission", tmp=tmp_path)
        assert done.returncode == 0, done.stderr
        out = done.stdout
        assert "waived (advisory, never blocking)" in out
        assert "gamma_move" in out
        assert "REAL-MONEY ENTRY REFUSED" not in out
        assert "shadow" not in out.lower()
