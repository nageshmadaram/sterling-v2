from __future__ import annotations

import pytest

from app.services import release_certification as certification


@pytest.mark.parametrize(
    "gate",
    ["source_identity", "release_manifest", "backup_restore"],
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


def test_human_pass_requires_evidence_reference(tmp_path):
    store = certification.CertificationStore(tmp_path)

    with pytest.raises(ValueError, match="evidence reference"):
        store.attest(
            "a" * 40,
            "test_suites",
            "PASS",
            attested_by="operator",
        )


def test_human_pass_rejects_uninspectable_evidence_reference(tmp_path):
    store = certification.CertificationStore(tmp_path)

    with pytest.raises(ValueError, match="existing local file or http"):
        store.attest(
            "a" * 40,
            "test_suites",
            "PASS",
            attested_by="operator",
            evidence_ref="artifact://not-verifiable-here",
        )


def test_human_pass_accepts_existing_local_evidence_file(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    evidence = root / "artifacts" / "tests.txt"
    evidence.parent.mkdir()
    evidence.write_text("backend/frontend/e2e passed", encoding="utf-8")
    monkeypatch.setenv("STERLING_ROOT", str(root))

    store = certification.CertificationStore(tmp_path / "cert")
    result = store.attest(
        "a" * 40,
        "test_suites",
        "PASS",
        attested_by="operator",
        evidence_ref="artifacts/tests.txt",
    )

    assert result.status == certification.PASS
    assert result.evidence_ref == "artifacts/tests.txt"
    assert len(result.evidence_sha256) == 64

    evidence.write_text("edited after attestation", encoding="utf-8")
    reread = store.read("a" * 40)["test_suites"]

    assert reread.status == certification.UNKNOWN
    assert "changed since attestation" in reread.detail


def test_legacy_pass_without_evidence_is_downgraded_to_unknown(tmp_path):
    sha = "a" * 40
    store = certification.CertificationStore(tmp_path)
    store._path(sha).write_text(
        """{
  "sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "gates": [
    {
      "key": "test_suites",
      "status": "PASS",
      "attested_by": "operator",
      "evidence_ref": "",
      "detail": "old-format record"
    }
  ]
}
""",
        encoding="utf-8",
    )

    loaded = store.read(sha)

    assert loaded["test_suites"].status == certification.UNKNOWN
    assert "re-attestation required" in loaded["test_suites"].detail


def test_certification_file_renamed_from_other_sha_satisfies_nothing(tmp_path):
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
      "attested_by": "operator",
      "evidence_ref": "artifact://tests"
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
