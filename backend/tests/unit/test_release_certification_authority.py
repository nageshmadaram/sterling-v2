from __future__ import annotations

import pytest

from app.services import release_certification as certification


def test_security_gate_uses_the_same_environment_as_runtime_auth(monkeypatch):
    from app.core import runtime_env, security
    monkeypatch.delenv('ENVIRONMENT', raising=False)
    monkeypatch.setattr(runtime_env, 'environment', lambda: 'production')
    validated = []
    monkeypatch.setattr(security, 'validate_production_security', lambda: validated.append(True))
    assert certification._production_security_gate().status == certification.PASS
    assert validated == [True]


def test_security_gate_refuses_nonproduction_even_with_valid_keys(monkeypatch):
    from app.core import runtime_env
    monkeypatch.setattr(runtime_env, 'environment', lambda: 'development')
    assert certification._production_security_gate().status == certification.FAIL


@pytest.mark.parametrize(
    "gate",
    ["source_identity", "release_manifest", "backup_restore", "test_suites"],
)
def test_automated_gate_cannot_be_human_attested(tmp_path, gate):
    store = certification.CertificationStore(tmp_path)

    with pytest.raises(ValueError, match="automated"):
        store.attest(
            "a" * 40,
            gate,
            "PASS",
            attested_by="operator",
            evidence_ref="artifact://proof",
        )



def test_derived_gates_cannot_be_human_attested(tmp_path):
    store = certification.CertificationStore(tmp_path)

    for gate in sorted(certification._DERIVED_GATES):
        with pytest.raises(ValueError, match="derived"):
            store.attest(
                "a" * 40,
                gate,
                "PASS",
                attested_by="operator",
                evidence_ref="https://example.test/proof",
            )


def test_legacy_test_suite_pass_is_ignored_by_machine_authority(tmp_path, monkeypatch):
    sha = "a" * 40
    monkeypatch.setenv("STERLING_STATE_ROOT", str(tmp_path))
    store = certification.CertificationStore(tmp_path / "cert")
    store.directory.mkdir(parents=True, exist_ok=True)
    store._path(sha).write_text(
        """{
  "sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "gates": [
    {
      "key": "test_suites",
      "status": "PASS",
      "attested_by": "legacy-operator",
      "evidence_ref": "https://ci.example.test/old-human-pass"
    }
  ]
}
""",
        encoding="utf-8",
    )

    snapshot = type(
        "Snapshot",
        (),
        {
            "total": 0,
            "unresolved_intents": 0,
            "open_positions": 0,
            "external_positions": 0,
            "held": (),
            "external_instruments": (),
            "detail": "",
        },
    )()

    report = certification.certification_report(
        sha=sha,
        tag="candidate",
        store=store,
        exposure_snapshot_value=snapshot,
    )

    assert report.by_key["test_suites"].status == certification.UNKNOWN
    assert report.release_ready is False


def test_certification_store_still_refuses_cross_sha_legacy_records(tmp_path):
    old_sha = "b" * 40
    new_sha = "a" * 40
    store = certification.CertificationStore(tmp_path)
    store._path(old_sha).write_text(
        """{
  "sha": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
  "gates": [
    {
      "key": "test_suites",
      "status": "PASS",
      "attested_by": "legacy-operator",
      "evidence_ref": "https://ci.example.test/old-human-pass"
    }
  ]
}
""",
        encoding="utf-8",
    )
    store._path(new_sha).write_text(
        store._path(old_sha).read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    assert store.read(new_sha) == {}

def test_corrupt_release_manifest_is_unknown_not_exception(monkeypatch):
    def explode():
        raise ValueError("bad json")

    monkeypatch.setattr(certification, "read_manifest", explode)

    gate = certification._release_manifest_gate()

    assert gate.status == certification.UNKNOWN
    assert "unreadable" in gate.detail


def test_corrupt_ci_and_drill_stores_are_unknown(monkeypatch):
    monkeypatch.setattr(
        "app.core.ci_certification.ci_report",
        lambda _sha: (_ for _ in ()).throw(ValueError("bad ci json")),
    )
    monkeypatch.setattr(
        "app.core.failure_drills.drill_report",
        lambda _sha: (_ for _ in ()).throw(ValueError("bad drill json")),
    )

    ci_gate = certification._remote_ci_gate("a" * 40)
    drill_gate = certification._failure_drills_gate("a" * 40)

    assert ci_gate.status == certification.UNKNOWN
    assert "bad ci json" in ci_gate.detail
    assert drill_gate.status == certification.UNKNOWN
    assert "bad drill json" in drill_gate.detail


class _UnreadableStore:
    def read(self, _sha):
        raise ValueError("bad certification json")


def test_corrupt_human_attestation_store_does_not_crash_report(monkeypatch):
    monkeypatch.setattr(
        certification,
        "_source_identity",
        lambda *_a, **_k: certification.GateResult("source_identity"),
    )
    monkeypatch.setattr(
        certification,
        "_release_manifest_gate",
        lambda: certification.GateResult("release_manifest"),
    )
    monkeypatch.setattr(
        certification,
        "_backup_restore_gate",
        lambda _sha: certification.GateResult("backup_restore"),
    )
    monkeypatch.setattr(
        certification,
        "_failure_drills_gate",
        lambda _sha: certification.GateResult("failure_drills"),
    )
    monkeypatch.setattr(
        certification,
        "_remote_ci_gate",
        lambda _sha: certification.GateResult("remote_ci"),
    )

    snapshot = type(
        "Snapshot",
        (),
        {
            "total": 0,
            "unresolved_intents": 0,
            "open_positions": 0,
            "external_positions": 0,
            "held": (),
            "external_instruments": (),
            "detail": "",
        },
    )()

    report = certification.certification_report(
        sha="a" * 40,
        tag="candidate",
        store=_UnreadableStore(),
        exposure_snapshot_value=snapshot,
    )

    assert report.release_ready is False
    assert report.by_key["test_suites"].status == certification.UNKNOWN


class _ForgedStore:
    def read(self, _sha):
        return {
            "backup_restore": certification.GateResult(
                "backup_restore",
                certification.PASS,
                "human said backup was fine",
                attested_by="operator",
                evidence_ref="artifact://forged",
            )
        }


def test_automated_unknown_cannot_be_replaced_by_stored_pass(monkeypatch):
    monkeypatch.setattr(
        certification,
        "_source_identity",
        lambda *_a, **_k: certification.GateResult("source_identity"),
    )
    monkeypatch.setattr(
        certification,
        "_release_manifest_gate",
        lambda: certification.GateResult("release_manifest"),
    )
    monkeypatch.setattr(
        certification,
        "_backup_restore_gate",
        lambda _sha: certification.GateResult(
            "backup_restore",
            certification.UNKNOWN,
            "backup could not be verified",
        ),
    )
    monkeypatch.setattr(
        certification,
        "_failure_drills_gate",
        lambda _sha: certification.GateResult("failure_drills"),
    )
    monkeypatch.setattr(
        certification,
        "_remote_ci_gate",
        lambda _sha: certification.GateResult("remote_ci"),
    )

    snapshot = type(
        "Snapshot",
        (),
        {
            "total": 0,
            "unresolved_intents": 0,
            "open_positions": 0,
            "external_positions": 0,
            "held": (),
            "external_instruments": (),
            "detail": "",
        },
    )()

    report = certification.certification_report(
        sha="a" * 40,
        tag="candidate",
        store=_ForgedStore(),
        exposure_snapshot_value=snapshot,
    )

    gate = report.by_key["backup_restore"]
    assert gate.status == certification.UNKNOWN
    assert gate.detail == "backup could not be verified"


def test_strict_release_verdict_requires_every_gate_and_known_zero_exposure():
    """The CLI strict path consumes this verdict, not advisory/waiver text."""
    all_pass = tuple(
        certification.GateResult(g.key, certification.PASS, "verified")
        for g in certification.RELEASE_GATES
    )
    report = certification.CertificationReport(
        sha="a" * 40,
        tag="candidate",
        results=all_pass,
        unresolved_exposure=0,
    )
    assert report.release_ready is True

    unknown = certification.CertificationReport(
        sha=report.sha,
        tag=report.tag,
        results=all_pass[:-1],
        unresolved_exposure=0,
    )
    assert unknown.release_ready is False

    unresolved = certification.CertificationReport(
        sha=report.sha,
        tag=report.tag,
        results=all_pass,
        unresolved_exposure=None,
    )
    assert unresolved.release_ready is False

    stale_exposure = certification.CertificationReport(
        sha=report.sha,
        tag=report.tag,
        results=all_pass,
        unresolved_exposure=1,
    )
    assert stale_exposure.release_ready is False


def test_cli_offers_fail_closed_certify_strict_while_advisory_is_separate():
    from pathlib import Path

    cli = Path(__file__).resolve().parents[3] / "scripts" / "sterlingctl"
    content = cli.read_text(encoding="utf-8")
    assert 'if [ "${2:-}" = "strict" ]; then' in content
    assert "if report.release_ready:" in content
    assert "STRICT CERTIFICATION: BLOCKED" in content
    assert "sys.exit(1)" in content
