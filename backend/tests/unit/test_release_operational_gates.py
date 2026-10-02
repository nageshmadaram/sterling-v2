from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

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
    monkeypatch.setenv("STERLING_DEPLOYMENT_ID", "prod-shadow-1")
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


def _active_shadow_binding(tmp_path, monkeypatch, *, stale=False):
    from app.services.account_binding_service import AccountBindingStore

    monkeypatch.setenv("STERLING_STATE_ROOT", str(tmp_path))
    monkeypatch.setenv("STERLING_DEPLOYMENT_ID", "prod-shadow-1")
    monkeypatch.setenv("STERLING_FAMILY_ACCOUNT_ID", "KITE-1")

    evidence_dir = tmp_path / "data/manifests/account_binding_verifications"
    evidence_dir.mkdir(parents=True, exist_ok=True)

    store = AccountBindingStore()
    binding = store.declare(
        broker="zerodha",
        legal_account_holder="Holder",
        client_id="AB1234",
        account_scope="shadow",
        deployment_id="prod-shadow-1",
        static_egress_profile="home-static",
    )
    observed = (
        datetime.now(timezone.utc) - timedelta(hours=1)
        if stale
        else datetime.now(timezone.utc)
    ).isoformat()
    evidence_path = evidence_dir / "proof.json"
    evidence_path.write_text(
        json.dumps(
            {
                "binding_id": binding.binding_id,
                "profile_client_id": "AB1234",
                "application_account_id": "KITE-1",
                "deployment_id": "prod-shadow-1",
                "positions_read": True,
                "orders_read": True,
                "protective_orders_read": True,
            }
        ),
        encoding="utf-8",
    )
    verified = binding.verified(
        profile_client_id="AB1234",
        application_account_id="KITE-1",
        deployment_id="prod-shadow-1",
        inventory_read=True,
        evidence_ref=str(evidence_path),
        when=observed,
    )
    active = verified.activated()
    store.save(active)
    return store, active


def test_account_binding_gate_accepts_fresh_active_shadow_binding(tmp_path, monkeypatch):
    _active_shadow_binding(tmp_path, monkeypatch)
    gate = cert._account_binding_gate()
    assert gate.status == "PASS"
    assert gate.evidence_sha256


def test_account_binding_gate_rejects_stale_shadow_proof(tmp_path, monkeypatch):
    _active_shadow_binding(tmp_path, monkeypatch, stale=True)
    gate = cert._account_binding_gate()
    assert gate.status == "FAIL"
    assert "fresh" in gate.detail


def test_account_binding_gate_rejects_missing_evidence_file(tmp_path, monkeypatch):
    store, binding = _active_shadow_binding(tmp_path, monkeypatch)
    from app.core.broker_account_binding import binding_from_row

    missing = binding_from_row(
        {**binding.as_row(), "verification_evidence_ref": str(tmp_path / "missing.json")}
    )
    store.save(missing)

    gate = cert._account_binding_gate()
    assert gate.status == "UNKNOWN"
    assert "missing" in gate.detail


@pytest.mark.parametrize(
    "gate",
    ["production_security", "deployment_identity", "account_binding"],
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
