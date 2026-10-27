from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.services import release_certification as cert


SHA = "a" * 40


def test_production_security_gate_requires_production(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")
    gate = cert._production_security_gate()
    assert gate.status == "FAIL"
    assert "ENVIRONMENT=development" in gate.detail


def test_production_security_gate_passes_only_after_validator(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    called = {"count": 0}

    def validate():
        called["count"] += 1

    monkeypatch.setattr("app.core.security.validate_production_security", validate)
    gate = cert._production_security_gate()

    assert gate.status == "PASS"
    assert called["count"] == 1


def test_deployment_identity_gate_requires_exact_sha(tmp_path, monkeypatch):
    from app.core.deployment_identity import DeploymentIdentityStore

    monkeypatch.setenv("STERLING_STATE_ROOT", str(tmp_path))
    monkeypatch.setenv("STERLING_DEPLOYMENT_ID", "prod-live-1")
    monkeypatch.setenv("STERLING_STATIC_EGRESS_IP", "203.0.113.7")
    DeploymentIdentityStore().record(
        host_id="prod-host",
        outbound_ip="203.0.113.7",
        runtime_sha=SHA,
        observed_at=datetime.now(timezone.utc).isoformat(),
    )

    assert cert._deployment_identity_gate(SHA).status == "PASS"
    wrong = cert._deployment_identity_gate("b" * 40)
    assert wrong.status == "FAIL"
    assert "not candidate" in wrong.detail


def test_deployment_identity_rejects_non_ip_egress(tmp_path, monkeypatch):
    from app.core.deployment_identity import DeploymentIdentityStore

    monkeypatch.setenv("STERLING_STATE_ROOT", str(tmp_path))
    monkeypatch.setenv("STERLING_DEPLOYMENT_ID", "prod-live-1")

    with pytest.raises(ValueError, match="valid IPv4 or IPv6"):
        DeploymentIdentityStore().record(
            host_id="prod-host",
            outbound_ip="operator-entered-label",
            runtime_sha=SHA,
        )

    monkeypatch.setenv("STERLING_STATIC_EGRESS_IP", "operator-entered-label")
    gate = cert._deployment_identity_gate(SHA)
    assert gate.status == "FAIL"
    assert "not a valid IP address" in gate.detail


def test_legacy_egress_contract_rejects_matching_non_ip_values():
    from app.services.continuity_doctor import (
        EXPECTED_EGRESS_ENV,
        OBSERVED_AT_ENV,
        OBSERVED_EGRESS_ENV,
        egress_status,
    )

    status = egress_status(
        env={
            EXPECTED_EGRESS_ENV: "operator-entered-label",
            OBSERVED_EGRESS_ENV: "operator-entered-label",
            OBSERVED_AT_ENV: datetime.now(timezone.utc).isoformat(),
        }
    )
    assert status.verified is False
    assert "not a valid IPv4 or IPv6 address" in status.reason


def test_deployment_identity_rejects_future_observation(tmp_path):
    from app.core.deployment_identity import (
        DeploymentIdentityStore,
        verify_deployment_identity,
    )

    now = datetime.now(timezone.utc)
    store = DeploymentIdentityStore(tmp_path / "identity.json")
    store.record(
        host_id="prod-host",
        outbound_ip="203.0.113.7",
        runtime_sha=SHA,
        observed_at=(now + timedelta(minutes=1)).isoformat(),
    )

    verdict = verify_deployment_identity(
        expected_ip="203.0.113.7",
        store=store,
        now=now,
    )
    assert verdict.verified is None
    assert "future" in verdict.reason


def test_legacy_egress_contract_rejects_future_timestamp():
    from app.services.continuity_doctor import (
        EXPECTED_EGRESS_ENV,
        OBSERVED_AT_ENV,
        OBSERVED_EGRESS_ENV,
        egress_status,
    )

    now = datetime.now(timezone.utc)
    status = egress_status(
        env={
            EXPECTED_EGRESS_ENV: "203.0.113.7",
            OBSERVED_EGRESS_ENV: "203.0.113.7",
            OBSERVED_AT_ENV: (now + timedelta(minutes=1)).isoformat(),
        },
        now=lambda: now,
    )
    assert status.verified is False
    assert "future" in status.reason


def test_legacy_egress_rejects_timezone_ambiguous_timestamp():
    from app.services.continuity_doctor import (
        EXPECTED_EGRESS_ENV, OBSERVED_AT_ENV, OBSERVED_EGRESS_ENV,
        egress_status,
    )

    naive = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
    status = egress_status(env={
        EXPECTED_EGRESS_ENV: "203.0.113.7",
        OBSERVED_EGRESS_ENV: "203.0.113.7",
        OBSERVED_AT_ENV: naive,
    })
    assert status.verified is False
    assert "no timezone" in status.reason


def test_documented_egress_record_store_is_the_default_static_egress_truth(
    tmp_path, monkeypatch
):
    from app.core.deployment_identity import DeploymentIdentityStore
    from app.services.continuity_doctor import egress_status

    monkeypatch.setenv("STERLING_STATE_ROOT", str(tmp_path))
    monkeypatch.setenv("STERLING_STATIC_EGRESS_IP", "203.0.113.7")
    now = datetime.now(timezone.utc)
    DeploymentIdentityStore().record(
        host_id="prod-host",
        outbound_ip="203.0.113.7",
        runtime_sha=SHA,
        observed_at=now.isoformat(),
    )

    status = egress_status(now=lambda: now)

    assert status.verified is True
    assert status.expected == "203.0.113.7"
    assert status.observed == "203.0.113.7"


def _active_live_binding(tmp_path, monkeypatch, *, stale=False):
    from app.services.account_binding_service import AccountBindingStore

    monkeypatch.setenv("STERLING_STATE_ROOT", str(tmp_path))
    monkeypatch.setenv("STERLING_DEPLOYMENT_ID", "prod-live-1")
    monkeypatch.setenv("STERLING_FAMILY_ACCOUNT_ID", "KITE-1")

    store = AccountBindingStore()
    binding = store.declare(
        broker="zerodha",
        legal_account_holder="Holder",
        client_id="AB1234",
        account_scope="live",
        deployment_id="prod-live-1",
        static_egress_profile="home-static",
    )
    evidence_dir = (
        tmp_path
        / "data/manifests/account_binding_verifications"
        / binding.binding_id
    )
    evidence_dir.mkdir(parents=True, exist_ok=True)
    observed = (
        datetime.now(timezone.utc) - timedelta(hours=1)
        if stale
        else datetime.now(timezone.utc)
    ).isoformat()
    evidence_path = evidence_dir / "proof.json"
    evidence_bytes = json.dumps(
        {
            "schema_version": 1,
            "binding_id": binding.binding_id,
            "broker": binding.broker,
            "profile_client_id": "AB1234",
            "application_account_id": "KITE-1",
            "deployment_id": "prod-live-1",
            "account_scope": binding.account_scope,
            "observed_at": observed,
            "positions_read": True,
            "orders_read": True,
            "protective_orders_read": True,
        }
    ).encode("utf-8")
    evidence_path.write_bytes(evidence_bytes)
    evidence_sha256 = hashlib.sha256(evidence_bytes).hexdigest()
    verified = binding.verified(
        profile_client_id="AB1234",
        application_account_id="KITE-1",
        deployment_id="prod-live-1",
        inventory_read=True,
        evidence_ref=str(evidence_path),
        evidence_sha256=evidence_sha256,
        when=observed,
    )
    active = verified.activated()
    store.save(active)
    return store, active


def test_account_binding_gate_accepts_fresh_active_live_binding(tmp_path, monkeypatch):
    _active_live_binding(tmp_path, monkeypatch)
    gate = cert._account_binding_gate()
    assert gate.status == "PASS"
    assert gate.evidence_sha256


def test_account_binding_gate_rejects_stale_live_proof(tmp_path, monkeypatch):
    _active_live_binding(tmp_path, monkeypatch, stale=True)
    gate = cert._account_binding_gate()
    assert gate.status == "FAIL"
    assert "fresh" in gate.detail


def test_account_binding_gate_rejects_missing_evidence_file(tmp_path, monkeypatch):
    _store, binding = _active_live_binding(tmp_path, monkeypatch)

    # Keep the verifier-owned canonical path in the durable binding, then make
    # the evidence disappear. Redirecting the binding elsewhere tests provenance
    # violation instead and correctly returns FAIL.
    Path(binding.verification_evidence_ref).unlink()

    gate = cert._account_binding_gate()
    assert gate.status == "UNKNOWN"
    assert "missing" in gate.detail


def test_account_binding_gate_rejects_evidence_timestamp_mismatch(tmp_path, monkeypatch):
    store, binding = _active_live_binding(tmp_path, monkeypatch)
    evidence_path = binding.verification_evidence_ref
    payload = json.loads(open(evidence_path, encoding="utf-8").read())
    payload["observed_at"] = (
        datetime.now(timezone.utc) - timedelta(minutes=5)
    ).isoformat()
    with open(evidence_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)

    gate = cert._account_binding_gate()
    assert gate.status == "FAIL"
    assert "bytes changed after verification" in gate.detail


def test_account_binding_gate_rejects_byte_tampering_even_when_semantics_match(
    tmp_path, monkeypatch
):
    _store, binding = _active_live_binding(tmp_path, monkeypatch)
    evidence_path = Path(binding.verification_evidence_ref)
    payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    evidence_path.write_text(
        json.dumps(payload, indent=4, sort_keys=True),
        encoding="utf-8",
    )

    gate = cert._account_binding_gate()

    assert gate.status == "FAIL"
    assert "bytes changed after verification" in gate.detail


def test_account_binding_gate_rejects_evidence_scope_mismatch(tmp_path, monkeypatch):
    store, binding = _active_live_binding(tmp_path, monkeypatch)
    evidence_path = binding.verification_evidence_ref
    payload = json.loads(open(evidence_path, encoding="utf-8").read())
    payload["account_scope"] = "read-only"
    with open(evidence_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle)

    gate = cert._account_binding_gate()
    assert gate.status == "FAIL"
    assert "bytes changed after verification" in gate.detail


def test_account_binding_gate_rejects_arbitrary_evidence_path(tmp_path, monkeypatch):
    store, binding = _active_live_binding(tmp_path, monkeypatch)
    from app.core.broker_account_binding import binding_from_row

    canonical = Path(binding.verification_evidence_ref)
    arbitrary = tmp_path / "operator-supplied-proof.json"
    arbitrary.write_bytes(canonical.read_bytes())

    redirected = binding_from_row(
        {**binding.as_row(), "verification_evidence_ref": str(arbitrary)}
    )
    store.save(redirected)

    gate = cert._account_binding_gate()
    assert gate.status == "FAIL"
    assert "outside the canonical verifier directory" in gate.detail


@pytest.mark.parametrize(
    "gate",
    ["production_security", "deployment_identity", "account_binding", "test_suites"],
)
def test_operational_release_gates_cannot_be_human_attested(tmp_path, gate):
    with pytest.raises(ValueError, match="automated"):
        cert.CertificationStore(tmp_path).attest(
            SHA,
            gate,
            "PASS",
            attested_by="operator",
            evidence_ref="https://example.test/fake-proof",
        )


def test_test_suites_gate_is_derived_from_exact_sha_ci(tmp_path, monkeypatch):
    from app.core.ci_certification import CIRecordStore

    monkeypatch.setenv("STERLING_STATE_ROOT", str(tmp_path))
    store = CIRecordStore()
    for context in cert._TEST_SUITE_CONTEXTS:
        store.record(
            runtime_sha=SHA,
            context=context,
            conclusion="success",
            run_id="12345", job_id=1, runner_id=1, executed_steps=1,
            observed_at=datetime.now(timezone.utc).isoformat(),
            url="https://github.example.test/run/12345",
        )

    gate = cert._test_suites_gate(SHA)
    assert gate.status == "PASS"
    assert "7 backend/frontend/E2E suite contexts" in gate.detail

    wrong_sha = cert._test_suites_gate("b" * 40)
    assert wrong_sha.status == "UNKNOWN"
    assert "no record" in wrong_sha.detail


def test_test_suites_gate_fails_when_exact_sha_suite_failed(tmp_path, monkeypatch):
    from app.core.ci_certification import CIRecordStore

    monkeypatch.setenv("STERLING_STATE_ROOT", str(tmp_path))
    store = CIRecordStore()
    for context in cert._TEST_SUITE_CONTEXTS:
        store.record(
            runtime_sha=SHA,
            context=context,
            conclusion=("failure" if context == "Frontend Tests (Vitest)" else "success"),
            run_id="12345", job_id=1, runner_id=1, executed_steps=1,
            observed_at=datetime.now(timezone.utc).isoformat(),
        )

    gate = cert._test_suites_gate(SHA)
    assert gate.status == "FAIL"
    assert "Frontend Tests (Vitest)" in gate.detail
