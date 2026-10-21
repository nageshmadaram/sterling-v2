"""The doctor verdict and the operator dashboard.

The rule under test throughout: a check that could not be run is not a pass.
``sterlingctl doctor`` exiting zero must mean "I looked and it is fine".
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import time

import pytest

from app.core.health import ComponentHealth, SystemHealth, compose_health
from app.core.operator_report import (
    EXIT_COULD_NOT_CHECK,
    EXIT_OK,
    EXIT_UNSAFE,
    DoctorCheck,
    DoctorReport,
    doctor_from_preflight,
    operator_dashboard,
    render_dashboard,
    run_checks,
)


@dataclass
class _Preflight:
    code: str
    passed: bool
    details: object = ""


# ── doctor verdict ────────────────────────────────────────────────────────


def test_all_passing_checks_exit_zero():
    report = DoctorReport(checks=(DoctorCheck("clock", True),))
    assert report.safe is True
    assert report.exit_code == EXIT_OK


def test_custom_live_config_does_not_require_the_frozen_experiment_hash():
    from app.core.waivers import split_doctor_checks
    report = doctor_from_preflight([_Preflight('config_identity', False, ['config_hash_mismatch'])])
    checks = {check.name: check for check in report.checks}
    assert checks['config_identity'].passed is True
    assert checks['experiment_config_identity'].passed is False
    assert split_doctor_checks(report.checks).safe is True


@pytest.mark.parametrize('reason', ['config_store_unavailable', 'snapback_disabled', 'config_load_failed:invalid'])
def test_live_config_failures_remain_blocking_alongside_experiment_drift(reason):
    from app.core.waivers import split_doctor_checks
    report = doctor_from_preflight([_Preflight('config_identity', False, ['config_hash_mismatch', reason])])
    split = split_doctor_checks(report.checks)
    assert split.safe is False
    assert split.failures[0].name == 'config_identity'
    assert reason in split.failures[0].detail


def test_a_failed_check_exits_unsafe():
    report = DoctorReport(
        checks=(DoctorCheck("clock", True), DoctorCheck("disk", False, "full"))
    )
    assert report.safe is False
    assert report.exit_code == EXIT_UNSAFE


def test_a_check_that_could_not_run_is_not_a_pass():
    report = DoctorReport(
        checks=(DoctorCheck("clock", True), DoctorCheck("broker", None, "no session"))
    )
    assert report.safe is False
    assert report.exit_code == EXIT_COULD_NOT_CHECK


def test_a_failure_outranks_an_unknown():
    report = DoctorReport(
        checks=(DoctorCheck("disk", False), DoctorCheck("broker", None))
    )
    assert report.exit_code == EXIT_UNSAFE


def test_running_nothing_at_all_is_the_worst_outcome():
    """Reporting success because nothing ran is the one unforgivable answer."""
    report = DoctorReport(checks=())
    assert report.safe is False
    assert report.exit_code == EXIT_COULD_NOT_CHECK


def test_an_exception_becomes_could_not_check_not_a_failure():
    def boom() -> tuple[bool, str]:
        raise ConnectionError("broker unreachable")

    checks = run_checks({"broker": boom, "clock": lambda: (True, "")})
    by_name = {c.name: c for c in checks}
    assert by_name["broker"].passed is None
    assert "ConnectionError" in by_name["broker"].detail
    assert by_name["clock"].passed is True


def test_every_check_runs_even_after_one_fails():
    """A doctor that stops at the first problem hides how much is wrong."""
    calls: list[str] = []

    def record(name: str, ok: bool):
        def check() -> tuple[bool, str]:
            calls.append(name)
            return ok, ""

        return check

    run_checks(
        {"a": record("a", False), "b": record("b", False), "c": record("c", True)}
    )
    assert sorted(calls) == ["a", "b", "c"]


def test_preflight_checks_convert_faithfully():
    report = doctor_from_preflight(
        [_Preflight("disk", False, {"free_bytes": 1}), _Preflight("clock", True)]
    )
    assert report.exit_code == EXIT_UNSAFE
    assert {c.name for c in report.failures} == {"disk"}


def test_the_doctor_can_carry_the_health_report():
    report = doctor_from_preflight(
        [_Preflight("clock", True)],
        components={
            name: ComponentHealth(name, SystemHealth.NORMAL)
            for name in ("broker", "market_data", "evidence", "reconciliation", "safe_mode")
        },
    )
    assert report.health is not None
    assert report.health.overall is SystemHealth.NORMAL
    assert report.as_dict()["health"]["overall"] == "normal"


# ── dashboard ─────────────────────────────────────────────────────────────


def test_the_dashboard_lists_all_ten_lanes():
    payload = operator_dashboard()
    assert len(payload["lanes"]) == 10


def test_every_blocked_lane_states_a_reason():
    payload = operator_dashboard()
    for lane_key, reason in payload["lanes_blocked"].items():
        assert reason, lane_key
    assert set(payload["lanes_originating"]) & set(payload["lanes_blocked"]) == set()


def test_supertrend_modes_are_declared_but_all_follow_the_global_kill_switch(monkeypatch):
    """A mode in LIVE_MINIMUM never escapes the independent master entry switch.

    SuperTrend now supports five selectable modes; the old swing-only dashboard
    expectation silently drifted from the registered and selectable runtime.
    """
    from app.core import lane_registry
    from app.core.horizon import HorizonMode

    declared = {f"supertrend:{mode.value}" for mode in HorizonMode}
    monkeypatch.setattr(lane_registry, "LIVE_EXECUTION_ENABLED", True)
    enabled = operator_dashboard()
    assert {key for key in enabled["lanes_originating"] if key.startswith("supertrend:")} == declared
    assert not declared.intersection(enabled["lanes_blocked"])

    monkeypatch.setattr(lane_registry, "LIVE_EXECUTION_ENABLED", False)
    disabled = operator_dashboard()
    assert not declared.intersection(disabled["lanes_originating"])
    assert {key: disabled["lanes_blocked"].get(key) for key in declared} == {
        key: "LIVE_EXECUTION_DISABLED" for key in declared
    }


def test_dashboard_reports_undefined_rules_when_a_lane_is_unqualified(monkeypatch):
    """The refusal still appears when a declaration really lacks entry rules."""
    from dataclasses import replace
    from app.core import lane_registry

    key = "supertrend:intraday"
    original = lane_registry.LANES[key]
    monkeypatch.setitem(lane_registry.LANES, key, replace(original, rules_defined=False))
    monkeypatch.setattr(lane_registry, "LIVE_EXECUTION_ENABLED", True)
    payload = operator_dashboard()
    assert payload["lanes_blocked"][key] == "LANE_RULES_UNDEFINED"
    assert "LANE_RULES_UNDEFINED" in render_dashboard(payload)


def test_the_rendered_lane_state_is_not_truncated():
    text = render_dashboard(operator_dashboard())
    assert "LIVE_MINIMUM" in text
    assert "LIVE_MINIMUMopen" not in text


def test_an_unreported_system_shows_as_recovery_required():
    payload = operator_dashboard()
    assert payload["system"]["overall"] == "recovery_required"
    assert payload["system"]["may_open_new_exposure"] is False


def test_rendering_names_the_state_and_the_reason(monkeypatch):
    from app.core import lane_registry

    monkeypatch.setattr(lane_registry, "LIVE_EXECUTION_ENABLED", False)
    healthy = compose_health(
        {
            name: ComponentHealth(name, SystemHealth.NORMAL)
            for name in ("broker", "market_data", "evidence", "reconciliation", "safe_mode")
        }
    )
    text = render_dashboard(operator_dashboard(healthy))
    assert "STERLING STATUS" in text
    assert "NORMAL" in text
    assert "snapback" not in text or "SNAPBACK" in text
    assert "LIVE_EXECUTION_DISABLED" in text
    assert "blocked (LIVE_EXECUTION_DISABLED)" in text
    # Column padding must not run the name into its value.
    assert "reconciliationNORMAL" not in text


@pytest.mark.parametrize("lane", ["ultra_scalping", "overnight", "swing"])
def test_every_mode_appears_in_the_rendered_screen(lane):
    assert lane in render_dashboard(operator_dashboard())


# ── Sterling-specific doctor checks ───────────────────────────────────────

def test_doctor_reports_actual_persisted_safe_mode(monkeypatch, tmp_path):
    from app.core.operator_report import lane_doctor_checks
    from app.services.safe_mode import SafeModeService, SafeModeTrigger

    path = tmp_path / "safe_mode.json"
    monkeypatch.setenv("STERLING_SAFE_MODE_FILE", str(path))
    service = SafeModeService(path)
    service.engage(trigger=SafeModeTrigger.OPERATOR, reason="release validation")

    check = _by_name(lane_doctor_checks())["safe_mode_state"]
    assert check.passed is False
    assert "release validation" in check.detail

    service.release(operator_ack=True, note="test complete")
    check = _by_name(lane_doctor_checks())["safe_mode_state"]
    assert check.passed is True
    assert check.detail == "NORMAL"


def _by_name(checks):
    return {c.name: c for c in checks}


def test_the_doctor_covers_the_gaps_preflight_does_not(monkeypatch):
    from app.core.operator_report import lane_doctor_checks

    names = set(_by_name(lane_doctor_checks()))
    assert names == {
        "focus_policy",
        "release_tag",
        "risk_limits",
        "lane_origination",
        "supertrend_core_frozen",
        # Post-67 go-live gaps: none of these is visible from the preflight,
        # and each one blocks real orders rather than today's shadow session.
        "static_egress",
        "account_binding",
        "release_certification",
        # Section 12: the deployment host itself. A day the lake was unplugged
        # is a day of market data that cannot be bought back.
        "lake_mount",
        "network_path",
        # Go-live section 10.1: the deployment host, the broker session, the
        # feed, the backups and the two safety states an operator acts on.
        "deployment_identity",
        "broker_session",
        "market_freshness",
        "backup_age",
        "restore_check",
        "safe_mode_state",
        "execution_control_state",
        # The broker's own answer about what it holds. Sterling's stores read
        # flat while the account held 16625 of a CDSL call.
        "broker_flatness",
    }


def test_an_unset_release_tag_is_reported_as_a_failure(monkeypatch):
    """Evidence written without one cannot name the release that produced it."""
    from app.core.operator_report import lane_doctor_checks

    monkeypatch.delenv("STERLING_RELEASE_TAG", raising=False)
    check = _by_name(lane_doctor_checks())["release_tag"]
    assert check.passed is False
    assert "unattributed" in check.detail


def test_a_set_release_tag_passes(monkeypatch):
    from app.core.operator_report import lane_doctor_checks

    monkeypatch.setenv("STERLING_RELEASE_TAG", "snapback-prospective-runtime-1.6")
    check = _by_name(lane_doctor_checks())["release_tag"]
    assert check.passed is True
    assert check.detail == "snapback-prospective-runtime-1.6"


def test_an_unconfigured_risk_hierarchy_is_reported(monkeypatch):
    """The gap the capacity layer deliberately does not invent limits for."""
    from app.core.operator_report import lane_doctor_checks
    from app.core.risk_hierarchy import ENV_VAR

    monkeypatch.delenv(ENV_VAR, raising=False)
    check = _by_name(lane_doctor_checks())["risk_limits"]
    assert check.passed is False
    assert "not enforced" in check.detail


def test_global_only_risk_hierarchy_is_incomplete_not_ready(monkeypatch):
    from app.core.operator_report import lane_doctor_checks
    from app.core.risk_hierarchy import ENV_VAR

    monkeypatch.setenv(ENV_VAR, '{"global": {"": 1000}}')
    check = _by_name(lane_doctor_checks())["risk_limits"]
    assert check.passed is False
    assert "strategy:" in check.detail
    assert "mode:" in check.detail
    assert "position:" in check.detail


def test_a_complete_live_lane_risk_hierarchy_passes(monkeypatch):
    from app.core.horizon import LaneState
    from app.core.lane_registry import all_lanes
    from app.core.operator_report import lane_doctor_checks
    from app.core.risk_hierarchy import ENV_VAR

    lanes = [
        lane for lane in all_lanes()
        if lane.state in (LaneState.LIVE_MINIMUM, LaneState.LIVE_SCALED)
        and lane.rules_defined
    ]
    limits = {
        "global": {"": 100000},
        "strategy": {lane.strategy_id: 30000 for lane in lanes},
        "mode": {lane.lane_key: 10000 for lane in lanes},
        "position": {lane.lane_key: 3000 for lane in lanes},
    }
    monkeypatch.setenv(ENV_VAR, json.dumps(limits))
    check = _by_name(lane_doctor_checks())["risk_limits"]
    assert check.passed is True
    assert "config_hash" in check.detail


def test_a_missing_live_lane_cap_fails_doctor_with_scope(monkeypatch):
    from app.core.horizon import LaneState
    from app.core.lane_registry import all_lanes
    from app.core.operator_report import lane_doctor_checks
    from app.core.risk_hierarchy import ENV_VAR

    lanes = [
        lane for lane in all_lanes()
        if lane.state in (LaneState.LIVE_MINIMUM, LaneState.LIVE_SCALED)
        and lane.rules_defined
    ]
    limits = {
        "global": {"": 100000},
        "strategy": {lane.strategy_id: 30000 for lane in lanes},
        "mode": {lane.lane_key: 10000 for lane in lanes},
        "position": {lane.lane_key: 3000 for lane in lanes},
    }
    del limits["position"]["supertrend:intraday"]
    monkeypatch.setenv(ENV_VAR, json.dumps(limits))
    check = _by_name(lane_doctor_checks())["risk_limits"]
    assert check.passed is False
    assert "position:supertrend:intraday" in check.detail


def test_a_malformed_risk_hierarchy_is_could_not_check_not_a_pass(monkeypatch):
    from app.core.operator_report import lane_doctor_checks
    from app.core.risk_hierarchy import ENV_VAR

    monkeypatch.setenv(ENV_VAR, "{not json")
    check = _by_name(lane_doctor_checks())["risk_limits"]
    assert check.passed is None
    assert check.blocking is True


def test_the_doctor_names_which_lanes_may_trade():
    from app.core.operator_report import lane_doctor_checks

    check = _by_name(lane_doctor_checks())["lane_origination"]
    assert "of 10 lanes" in check.detail


def test_future_broker_reachability_timestamp_is_not_healthy(monkeypatch):
    from app.core.operator_report import lane_doctor_checks

    monkeypatch.setenv(
        "STERLING_BROKER_REACHABLE_AT",
        (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
    )

    check = _by_name(lane_doctor_checks())["network_path"]

    assert check.passed is None
    assert "future" in check.detail
    assert "not trustworthy" in check.detail


@pytest.mark.parametrize('fresh,same_deployment,expected', [
    (True, True, True), (False, True, None), (True, False, None),
])
def test_network_path_uses_only_current_binding_proof(monkeypatch, fresh, same_deployment, expected):
    from types import SimpleNamespace
    from app.core.operator_report import lane_doctor_checks
    from app.services import account_binding_service as bindings
    monkeypatch.delenv('STERLING_BROKER_REACHABLE_AT', raising=False)
    monkeypatch.setenv('STERLING_DEPLOYMENT_ID', 'this-host')
    binding = SimpleNamespace(deployment_id='this-host' if same_deployment else 'other-host',
                              verified_at=datetime.now(timezone.utc).isoformat())
    monkeypatch.setattr(bindings.AccountBindingStore, 'active', lambda self: binding)
    monkeypatch.setattr(bindings, 'verification_is_current', lambda value: fresh)
    check = _by_name(lane_doctor_checks())['network_path']
    assert check.passed is expected


def test_future_market_tick_timestamp_is_not_healthy(monkeypatch):
    from app.core.operator_report import lane_doctor_checks
    import urllib.request

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps({
                "feed": {
                    "connected": True,
                    "last_tick_ms": int((time.time() + 300) * 1000),
                }
            }).encode("utf-8")

    monkeypatch.setattr(urllib.request, "urlopen", lambda *_a, **_k: Response())

    check = _by_name(lane_doctor_checks())["market_freshness"]

    assert check.passed is None
    assert "future" in check.detail
    assert "not trustworthy" in check.detail


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("external_exposure_stale", None),
        ("external_exposure_account_identity", None),
        ("external_broker_automation", False),
        ("external_broker_exposure", False),
    ],
)
def test_doctor_broker_flatness_reuses_canonical_admission_gate(
    monkeypatch, code, expected
):
    from app.core.operator_report import lane_doctor_checks
    from app.services import external_positions

    monkeypatch.setattr(
        external_positions,
        "admission_blocker",
        lambda: (code, "canonical broker truth blocker"),
    )

    check = _by_name(lane_doctor_checks())["broker_flatness"]

    assert check.passed is expected
    assert check.detail == "canonical broker truth blocker"


def test_the_doctor_fails_if_the_supertrend_core_drifts(monkeypatch):
    from app.core.operator_report import lane_doctor_checks
    import app.engines.sterling_kite_engine.lanes as st_lanes

    check = _by_name(lane_doctor_checks())["supertrend_core_frozen"]
    assert check.passed is True

    monkeypatch.setattr(
        st_lanes, "audit_core_parity", lambda cfg=None: [st_lanes.ParityFinding("fast", (21, 1.0), (20, 1.0))]
    )
    drifted = _by_name(lane_doctor_checks())["supertrend_core_frozen"]
    assert drifted.passed is False
    assert "fast" in drifted.detail


def test_focus_check_names_gamma_unwind_from_the_release_scope():
    """Focus covers Snapback/SuperTrend lanes only; Gamma must not read as off."""
    from app.core.operator_report import lane_doctor_checks

    detail = _by_name(lane_doctor_checks())["focus_policy"].detail
    assert "Gamma-Unwind" in detail
    assert "lane originators:" in detail


@pytest.mark.parametrize('intact_when_new,usable,expected', [
    (True, True, True),     # only old: every order re-verifies, so not a blocker
    (False, True, False),   # wrong client / deployment / tampered evidence: still fails
    (True, False, False),   # binding not usable: still fails
])
def test_account_binding_ignores_age_only(monkeypatch, intact_when_new, usable, expected):
    from types import SimpleNamespace
    from app.core.operator_report import lane_doctor_checks
    from app.services import account_binding_service as bindings
    old = '2026-10-21T00:00:00+00:00'
    binding = SimpleNamespace(usable=usable, verified_at=old)
    monkeypatch.setattr(bindings, 'binding_health', lambda: {
        'account_binding_readable': True, 'account_binding_ready': False,
        'account_binding_id': 'b1', 'legal_account_holder': 'Owner', 'account_scope': 'live',
        'account_binding_verified_at': old, 'detail': 'verification not current'})
    monkeypatch.setattr(bindings.AccountBindingStore, 'active', lambda self: binding)
    monkeypatch.setattr(bindings, 'verification_is_current',
                        lambda value, now=None: intact_when_new and now is not None)
    check = _by_name(lane_doctor_checks())['account_binding']
    assert check.passed is expected
    if expected:
        assert 're-verified before every order' in check.detail


def test_safe_mode_check_uses_the_service_path_when_env_is_unset(monkeypatch, tmp_path):
    """The service does not export STERLING_SAFE_MODE_FILE; it must not read as 'no home'."""
    from app.core.operator_report import lane_doctor_checks
    from app.services import safety_supervisor
    from app.services.safe_mode import SafeModeService, SafeModeTrigger

    path = tmp_path / 'safe_mode.json'
    service = SafeModeService(path)
    service.engage(trigger=SafeModeTrigger.OPERATOR, reason="setup")
    service.release(operator_ack=True, note="ready")
    monkeypatch.delenv('STERLING_SAFE_MODE_FILE', raising=False)
    monkeypatch.setattr(safety_supervisor, 'safe_mode_path', lambda: path)
    check = _by_name(lane_doctor_checks())['safe_mode_state']
    assert check.passed is True
    assert check.detail == 'NORMAL'
