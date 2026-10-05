"""The start and stop sequences, and the rules they must not bend.

What is being tested here is not that eleven functions run. It is that an
unanswerable question stops the sequence, that a failed start leaves the system
refusing exposure, and that a stop will not walk away from an open position.
"""
from __future__ import annotations

import pytest

from app.services import service_lifecycle as lifecycle
from app.services.service_lifecycle import (
    LifecycleReport, StepResult, render_report, start_sequence, stop_sequence,
)


def _step(n, name, ok, detail=""):
    return lambda: StepResult(n, name, ok, detail)


class TestTheSequenceStopsAtTheFirstNonPass:
    def test_a_failure_stops_the_run(self):
        report = start_sequence([
            _step(1, "first", True),
            _step(2, "second", False, "broker said no"),
            _step(3, "third", True),
        ])
        assert [s.step for s in report.steps] == [1, 2]
        assert report.completed is False
        assert report.exit_code == 1
        assert report.stopped_at.name == "second"

    def test_unknown_stops_the_run_and_is_not_a_pass(self):
        report = start_sequence([
            _step(1, "first", True),
            _step(2, "second", None, "could not read the clock"),
            _step(3, "third", True),
        ])
        assert report.completed is False
        # Exit code 2 is the contract's "could not be checked", distinct from a
        # failure, because an operator must be able to tell them apart.
        assert report.exit_code == 2
        assert "That is not a pass" in render_report(report)

    def test_all_thirteen_passing_completes(self):
        report = start_sequence([_step(i, f"s{i}", True) for i in range(1, 14)])
        assert report.completed is True
        assert report.exit_code == 0
        assert report.stopped_at is None


class TestAFailedStartLeavesTheSystemRefusingExposure:
    def test_recovery_required_is_engaged_first_and_cleared_last(self):
        assert lifecycle.START_STEPS[0] is lifecycle._step_engage_recovery
        assert lifecycle.START_STEPS[-1] is lifecycle._step_clear_recovery

    def test_a_start_that_stops_early_never_reaches_the_clear(self, monkeypatch):
        states: list[str] = []
        monkeypatch.setattr(lifecycle, "_set_recovery",
                            lambda state, code, reason: states.append(state))
        report = start_sequence([
            lifecycle._step_engage_recovery,
            _step(2, "doctor", False, "disk full"),
            lifecycle._step_clear_recovery,
        ])
        assert states == ["RECOVERY_REQUIRED"]
        assert report.completed is False
        assert "remains in RECOVERY_REQUIRED" in render_report(report)


class TestTheStopWillNotAbandonExposure:
    def test_an_open_position_refuses_the_stop(self, monkeypatch):
        from app.services.kite_engine import order_journal, positions

        held = type("P", (), {"symbol": "NIFTY26JAN24000CE"})()
        monkeypatch.setattr(positions, "known_uids", lambda: ["family"])
        monkeypatch.setattr(positions, "open_positions", lambda uid: [held])
        monkeypatch.setattr(order_journal, "unresolved", lambda uid: [])

        result = lifecycle._step_no_abandoned_exposure()
        assert result.ok is False
        assert "NIFTY26JAN24000CE" in result.detail

    def test_an_unresolved_intent_refuses_the_stop(self, monkeypatch):
        from app.services.kite_engine import order_journal, positions

        intent = type("I", (), {"symbol": "BANKNIFTY26JAN52000PE"})()
        monkeypatch.setattr(positions, "known_uids", lambda: ["family"])
        monkeypatch.setattr(positions, "open_positions", lambda uid: [])
        monkeypatch.setattr(order_journal, "unresolved", lambda uid: [intent])

        result = lifecycle._step_no_abandoned_exposure()
        assert result.ok is False
        assert "unresolved order intent" in result.detail

    def test_an_unreadable_registry_is_unknown_not_empty(self, monkeypatch):
        from app.services.kite_engine import positions

        def _boom():
            raise RuntimeError("database is locked")

        monkeypatch.setattr(positions, "known_uids", _boom)
        result = lifecycle._step_no_abandoned_exposure()
        assert result.ok is None
        assert "database is locked" in result.detail

    def test_nothing_held_permits_the_stop(self, monkeypatch):
        from app.services.kite_engine import order_journal, positions

        monkeypatch.setattr(positions, "known_uids", lambda: ["family"])
        monkeypatch.setattr(positions, "open_positions", lambda uid: [])
        monkeypatch.setattr(order_journal, "unresolved", lambda uid: [])
        assert lifecycle._step_no_abandoned_exposure().ok is True

    def test_force_continues_past_a_refusal_without_hiding_it(self):
        report = stop_sequence([
            _step(1, "halt", True),
            _step(2, "reconcile", False, "one mismatch"),
            _step(3, "no exposure", True),
        ], force=True)
        assert [s.step for s in report.steps] == [1, 2, 3]
        # The refusal is still in the report and still in the exit code; force
        # only allows the remaining steps to run.
        assert report.exit_code == 1
        assert "FAILED" in render_report(report)


class TestStartupReconciliationIsFresh:
    def test_startup_runs_a_fresh_family_reconciliation(self, monkeypatch):
        from types import SimpleNamespace

        called = {"count": 0}

        async def reconcile_now():
            called["count"] += 1
            return SimpleNamespace(
                as_dict=lambda: {
                    "clean": True,
                    "mismatches": [],
                }
            )

        monkeypatch.setattr(
            "app.services.snapback_reconciliation.reconcile_family_account",
            reconcile_now,
        )

        result = lifecycle._step_reconcile()

        assert result.ok is True
        assert called["count"] == 1

    def test_fresh_reconciliation_mismatch_blocks_start(self, monkeypatch):
        from types import SimpleNamespace

        async def reconcile_now():
            return SimpleNamespace(
                as_dict=lambda: {
                    "clean": False,
                    "mismatches": [{"code": "POSITION_MISMATCH"}],
                }
            )

        monkeypatch.setattr(
            "app.services.snapback_reconciliation.reconcile_family_account",
            reconcile_now,
        )

        result = lifecycle._step_reconcile()

        assert result.ok is False
        assert "1 mismatch" in result.detail


class TestTheFeedFreshnessRule:
    def test_a_connected_socket_with_no_ticks_is_unknown(self, monkeypatch):
        monkeypatch.setattr(lifecycle, "_ops_get",
                            lambda path, timeout=5.0: {"feed": {"connected": True, "last_tick_ms": 0}})
        result = lifecycle._step_fresh_feed()
        assert result.ok is None
        assert "no tick" in result.detail

    def test_a_stale_tick_fails_even_though_the_socket_is_connected(self, monkeypatch):
        import time

        stale = int(time.time() * 1000) - (lifecycle.MAX_TICK_AGE_MS + 5_000)
        monkeypatch.setattr(lifecycle, "_ops_get",
                            lambda path, timeout=5.0: {"feed": {"connected": True, "last_tick_ms": stale}})
        assert lifecycle._step_fresh_feed().ok is False

    def test_a_fresh_tick_passes(self, monkeypatch):
        import time

        fresh = int(time.time() * 1000) - 1_000
        monkeypatch.setattr(lifecycle, "_ops_get",
                            lambda path, timeout=5.0: {"feed": {"connected": True, "last_tick_ms": fresh}})
        assert lifecycle._step_fresh_feed().ok is True

    def test_an_unreachable_service_is_unknown(self, monkeypatch):
        def _boom(path, timeout=5.0):
            raise OSError("connection refused")

        monkeypatch.setattr(lifecycle, "_ops_get", _boom)
        assert lifecycle._step_fresh_feed().ok is None


class TestTheBrokerBindingRule:
    def test_an_authenticated_account_that_is_not_the_binding_fails(self, monkeypatch):
        monkeypatch.setattr(lifecycle, "_ops_get", lambda path, timeout=5.0: {
            "broker": {"connected": True, "account_id": "AB1234",
                       "bound_client_id": "ZZ9999", "binding_matches": False}})
        result = lifecycle._step_broker_connected()
        assert result.ok is False
        assert "AB1234" in result.detail and "ZZ9999" in result.detail

    def test_no_active_binding_is_unknown_not_permission(self, monkeypatch):
        monkeypatch.setattr(lifecycle, "_ops_get", lambda path, timeout=5.0: {
            "broker": {"connected": True, "account_id": "AB1234",
                       "bound_client_id": None, "binding_matches": None}})
        assert lifecycle._step_broker_connected().ok is None

    def test_a_matching_binding_passes(self, monkeypatch):
        monkeypatch.setattr(lifecycle, "_ops_get", lambda path, timeout=5.0: {
            "broker": {"connected": True, "account_id": "AB1234",
                       "bound_client_id": "AB1234", "binding_matches": True}})
        assert lifecycle._step_broker_connected().ok is True


class TestTheOperatorCanReadIt:
    def test_the_report_names_the_step_that_stopped_it(self):
        report = LifecycleReport(action="start", steps=[
            StepResult(1, "engage RECOVERY_REQUIRED", True),
            StepResult(2, "pre-start doctor", False, "disk 96% full"),
        ])
        text = render_report(report)
        assert "pre-start doctor" in text
        assert "disk 96% full" in text
        assert "STOPPED at step 2" in text

    def test_a_clean_run_says_so_without_qualification(self):
        report = LifecycleReport(action="start",
                                 steps=[StepResult(i, f"s{i}", True) for i in range(1, 14)])
        assert "completed; every step ran and passed" in render_report(report)


def test_the_documented_step_counts_match_the_spec():
    """Thirteen start steps and five stop steps, as the runbook promises.

    The go-live specification adds production-secret validation and the account
    binding, and moves release identity ahead of starting the service.
    """
    assert len(lifecycle.START_STEPS) == 13
    assert len(lifecycle.STOP_STEPS) == 5
    assert [s().step for s in ()] == []  # no accidental empty-sequence pass
    assert start_sequence([]).completed is False


class TestTheGoLiveOrdering:
    """Section 9.1: identity and secrets are verified before the service runs."""

    def test_release_identity_and_secrets_precede_starting_the_unit(self):
        names = [f.__name__ for f in lifecycle.START_STEPS]
        assert names.index("_step_release_identity") < names.index("_step_start_unit")
        assert names.index("_step_production_security") < names.index("_step_start_unit")
        assert names.index("_step_account_binding") < names.index("_step_start_unit")

    def test_the_broker_is_only_reached_after_health(self):
        names = [f.__name__ for f in lifecycle.START_STEPS]
        assert names.index("_step_wait_health") < names.index("_step_broker_connected")

    def test_a_development_environment_is_unknown_not_a_pass(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "development")
        result = lifecycle._step_production_security()
        # "We were not in production" is not a security verdict.
        assert result.ok is None
        assert "development" in result.detail

    def test_a_refused_production_secret_fails_the_step(self, monkeypatch):
        monkeypatch.setenv("ENVIRONMENT", "production")

        def _refuse():
            raise RuntimeError("Production security error: STERLING_SECRET_KEY too short")

        monkeypatch.setattr("app.core.security.validate_production_security", _refuse)
        result = lifecycle._step_production_security()
        assert result.ok is False
        assert "STERLING_SECRET_KEY" in result.detail

    def test_no_active_binding_fails_the_step(self, monkeypatch):
        monkeypatch.setattr(
            "app.services.account_binding_service.binding_health",
            lambda: {
                "account_binding_readable": True,
                "account_binding_ready": False,
                "account_binding_id": None,
                "detail": "no ACTIVE binding",
            },
        )
        result = lifecycle._step_account_binding()
        assert result.ok is False
        assert "no ACTIVE binding" in result.detail

    def test_active_live_ready_binding_passes(self, monkeypatch):
        monkeypatch.setenv("STERLING_DEPLOYMENT_ID", "prod-live-1")
        monkeypatch.setattr(
            "app.services.account_binding_service.binding_health",
            lambda: {
                "account_binding_readable": True,
                "account_binding_ready": True,
                "account_binding_live_ready": True,
                "account_binding_status": "ACTIVE",
                "account_scope": "live",
                "deployment_id": "prod-live-1",
                "account_binding_id": "kite-ab123-20260930",
            },
        )

        result = lifecycle._step_account_binding()

        assert result.ok is True
        assert result.detail == "kite-ab123-20260930"

    def test_a_binding_that_is_not_live_ready_refuses_start(self, monkeypatch):
        """Identity binding is a safety control: a binding with no live scope or
        no named static-egress profile cannot carry a real order."""
        monkeypatch.setenv("STERLING_DEPLOYMENT_ID", "prod-live-1")
        monkeypatch.setattr(
            "app.services.account_binding_service.binding_health",
            lambda: {
                "account_binding_readable": True,
                "account_binding_ready": True,
                "account_binding_live_ready": False,
                "account_binding_status": "ACTIVE",
                "account_scope": "read-only",
                "deployment_id": "prod-live-1",
                "account_binding_id": "kite-ab123-20260930",
            },
        )

        result = lifecycle._step_account_binding()

        assert result.ok is False
        assert "live-ready" in result.detail

    def test_a_binding_for_another_deployment_refuses_start(self, monkeypatch):
        monkeypatch.setenv("STERLING_DEPLOYMENT_ID", "prod-live-1")
        monkeypatch.setattr(
            "app.services.account_binding_service.binding_health",
            lambda: {
                "account_binding_readable": True,
                "account_binding_ready": True,
                "account_binding_live_ready": True,
                "account_binding_status": "ACTIVE",
                "account_scope": "live",
                "deployment_id": "somewhere-else",
                "account_binding_id": "kite-ab123-20260930",
            },
        )

        result = lifecycle._step_account_binding()

        assert result.ok is False
        assert "does not match" in result.detail

    def test_verified_but_not_active_binding_refuses_start(self, monkeypatch):
        monkeypatch.setenv("STERLING_DEPLOYMENT_ID", "prod-live-1")
        monkeypatch.setattr(
            "app.services.account_binding_service.binding_health",
            lambda: {
                "account_binding_readable": True,
                "account_binding_ready": True,
                "account_binding_status": "VERIFIED",
                "account_scope": "live",
                "deployment_id": "prod-live-1",
                "account_binding_id": "kite-ab123-20260930",
            },
        )

        result = lifecycle._step_account_binding()

        assert result.ok is False
        assert "ACTIVE" in result.detail

    def test_an_unreadable_binding_store_is_unknown(self, monkeypatch):
        def _boom():
            raise RuntimeError("manifest directory missing")

        monkeypatch.setattr("app.services.account_binding_service.binding_health", _boom)
        assert lifecycle._step_account_binding().ok is None



class TestReleaseCertificationIsAdvisoryAtStartup:
    """Release certification is waived: it is reported, and it never stops a start."""

    def _manifest(self):
        return {
            "build": {
                "runtime_sha": "a" * 40,
                "release_tag": "sterling-family-runtime-1.0",
            }
        }

    def _matching(self, monkeypatch):
        from types import SimpleNamespace

        monkeypatch.setattr("app.core.release_manifest.read_manifest", self._manifest)
        monkeypatch.setattr(
            "app.core.release_manifest.verify_manifest",
            lambda stored: SimpleNamespace(matches=True, drift=(), missing_lanes=()),
        )

    def test_gates_not_passing_are_warned_about_but_do_not_refuse_start(self, monkeypatch):
        from types import SimpleNamespace

        self._matching(monkeypatch)
        blocked = SimpleNamespace(
            release_ready=False,
            unresolved_exposure=0,
            results=(
                SimpleNamespace(key="remote_ci", status="PASS", passed=True),
                SimpleNamespace(key="kite_live_acceptance", status="UNKNOWN", passed=False),
            ),
        )
        monkeypatch.setattr(
            "app.services.release_certification.certification_report",
            lambda **kwargs: blocked,
        )

        result = lifecycle._step_release_identity()

        assert result.ok is True
        assert "kite_live_acceptance=UNKNOWN" in result.advisory
        assert "remote_ci" not in result.advisory

    def test_a_missing_manifest_is_a_warning_not_a_refusal(self, monkeypatch):
        from types import SimpleNamespace

        monkeypatch.setattr("app.core.release_manifest.read_manifest", lambda: None)
        ready = SimpleNamespace(release_ready=True, unresolved_exposure=0, results=())
        monkeypatch.setattr(
            "app.services.release_certification.certification_report",
            lambda **kwargs: ready,
        )

        result = lifecycle._step_release_identity()

        assert result.ok is True
        assert "no frozen release manifest" in result.advisory

    def test_manifest_drift_is_a_warning_not_a_refusal(self, monkeypatch):
        from types import SimpleNamespace

        monkeypatch.setattr("app.core.release_manifest.read_manifest", self._manifest)
        monkeypatch.setattr(
            "app.core.release_manifest.verify_manifest",
            lambda stored: SimpleNamespace(
                matches=False, drift=("runtime_sha moved",), missing_lanes=("snapback:swing",)
            ),
        )
        ready = SimpleNamespace(release_ready=True, unresolved_exposure=0, results=())
        monkeypatch.setattr(
            "app.services.release_certification.certification_report",
            lambda **kwargs: ready,
        )

        result = lifecycle._step_release_identity()

        assert result.ok is True
        assert "manifest drift" in result.advisory

    def test_unreadable_release_evidence_is_a_warning_not_a_refusal(self, monkeypatch):
        def _boom():
            raise RuntimeError("manifest store offline")

        monkeypatch.setattr("app.core.release_manifest.read_manifest", _boom)

        result = lifecycle._step_release_identity()

        assert result.ok is True
        assert "unreadable" in result.advisory

    def test_matching_manifest_with_release_ready_passes_identity_step(self, monkeypatch):
        from types import SimpleNamespace

        self._matching(monkeypatch)
        ready = SimpleNamespace(release_ready=True, unresolved_exposure=0, results=())
        monkeypatch.setattr(
            "app.services.release_certification.certification_report",
            lambda **kwargs: ready,
        )

        result = lifecycle._step_release_identity()
        assert result.ok is True
        assert result.advisory == ""
        assert "sterling-family-runtime-1.0" in result.detail

    def test_the_advisory_is_rendered_next_to_a_passing_step(self):
        report = LifecycleReport(
            action="start",
            steps=[StepResult(3, "release identity", True, "tag", advisory="remote_ci=UNKNOWN")],
        )
        text = render_report(report)
        assert "waived (advisory, never blocking): remote_ci=UNKNOWN" in text
        assert report.completed is True


class TestThePreflightStepSeparatesBlockingFromWaived:
    def _doctor(self, monkeypatch, checks):
        from types import SimpleNamespace

        from app.core.operator_report import DoctorCheck, DoctorReport

        report = DoctorReport(checks=tuple(DoctorCheck(*c) for c in checks))
        monkeypatch.setattr(
            "app.services.snapback_preflight.run_preflight",
            lambda: SimpleNamespace(checks=[]),
        )
        monkeypatch.setattr(
            "app.core.operator_report.doctor_from_preflight", lambda *a, **k: report
        )
        monkeypatch.setattr("app.core.operator_report.lane_doctor_checks", lambda: ())

    def test_a_waived_failure_does_not_stop_the_start(self, monkeypatch):
        self._doctor(monkeypatch, [
            ("clock", True, ""),
            ("release_certification", False, "gates not passed"),
            ("evidence_meta", None, "unreadable"),
        ])

        result = lifecycle._step_preflight()

        assert result.ok is True
        assert "release_certification" in result.advisory
        assert "evidence_meta" in result.advisory

    def test_a_real_safety_failure_still_stops_the_start(self, monkeypatch):
        self._doctor(monkeypatch, [
            ("clock", True, ""),
            ("safe_mode_state", False, "SAFE_MODE engaged"),
            ("release_certification", False, "gates not passed"),
        ])

        result = lifecycle._step_preflight()

        assert result.ok is False
        assert "safe_mode_state" in result.detail
        assert "release_certification" in result.advisory

    def test_an_unreadable_blocking_check_is_unknown_not_a_pass(self, monkeypatch):
        self._doctor(monkeypatch, [("static_egress", None, "could not read")])

        assert lifecycle._step_preflight().ok is None
