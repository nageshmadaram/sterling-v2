from __future__ import annotations

import pytest
from types import SimpleNamespace

from app.core.backup_manifest import (
    BackupManifest,
    RestoreProof,
    RestoreStatus,
    prove_restore,
    record_restore_proof,
)


def _ignore_global_coverage_contract(monkeypatch):
    monkeypatch.setattr("app.core.backup_manifest.REQUIRED_ARTIFACTS", ())


def _manifest(backup_id: str = "backup-a") -> BackupManifest:
    return BackupManifest(
        backup_id=backup_id,
        created_at="2026-09-30T00:00:00+00:00",
        release_sha="a" * 40,
        release_tag="release-test",
        schema_versions={},
        file_checksums={},
        row_counts={},
        schema_signatures={},
    )


def test_restore_proof_must_belong_to_the_same_backup():
    proof = RestoreProof(
        backup_id="backup-b",
        passed=True,
        checked_at="2026-09-30T00:01:00+00:00",
    )

    with pytest.raises(ValueError, match="restore_proof_backup_mismatch"):
        record_restore_proof(_manifest("backup-a"), proof)


def test_passed_restore_proof_cannot_carry_failures():
    proof = RestoreProof(
        backup_id="backup-a",
        passed=True,
        checked_at="2026-09-30T00:01:00+00:00",
        failures=("checksum mismatch",),
    )

    with pytest.raises(ValueError, match="restore_proof_pass_contains_failures"):
        record_restore_proof(_manifest(), proof)


def test_restore_proof_requires_check_timestamp():
    proof = RestoreProof(
        backup_id="backup-a",
        passed=False,
        checked_at="",
        failures=("restore failed",),
    )

    with pytest.raises(ValueError, match="restore_proof_checked_at_missing"):
        record_restore_proof(_manifest(), proof)


def test_matching_pass_is_stamped_on_manifest():
    proof = RestoreProof(
        backup_id="backup-a",
        passed=True,
        checked_at="2026-09-30T00:01:00+00:00",
    )

    stamped = record_restore_proof(_manifest(), proof)

    assert stamped.backup_id == "backup-a"
    assert stamped.restore_test_status is RestoreStatus.PASSED
    assert stamped.restore_tested_at == proof.checked_at
    assert stamped.restore_failure is None


def test_restore_proof_refuses_database_omitted_from_manifest(tmp_path, monkeypatch):
    _ignore_global_coverage_contract(monkeypatch)
    db_path = tmp_path / "evidence.db"
    import sqlite3

    with sqlite3.connect(db_path) as conn:
        conn.execute("CREATE TABLE evidence (id INTEGER)")

    (tmp_path / "release.json").write_text(
        '{"build":{"runtime_sha":"' + ("a" * 40) + '","release_tag":"release-test"}}',
        encoding="utf-8",
    )
    (tmp_path / "coverage.json").write_text("[]", encoding="utf-8")

    proof = prove_restore(tmp_path, _manifest())

    assert proof.passed is False
    assert "evidence.db: database missing checksum evidence" in proof.failures
    assert "evidence.db: database missing row-count evidence" in proof.failures
    assert "evidence.db: database missing schema evidence" in proof.failures


def test_restore_proof_refuses_shrunk_coverage_declaration_set(tmp_path, monkeypatch):
    declared = SimpleNamespace(
        name="authoritative_evidence_db",
        required_before_live=True,
        kind=SimpleNamespace(value="database"),
    )
    monkeypatch.setattr(
        "app.core.backup_manifest.REQUIRED_ARTIFACTS",
        (declared,),
    )
    (tmp_path / "release.json").write_text(
        '{"build":{"runtime_sha":"' + ("a" * 40) + '","release_tag":"release-test"}}',
        encoding="utf-8",
    )
    (tmp_path / "coverage.json").write_text("[]", encoding="utf-8")

    proof = prove_restore(tmp_path, _manifest())

    assert proof.passed is False
    assert any(
        "missing required artifact declarations: authoritative_evidence_db" in item
        for item in proof.failures
    )


@pytest.mark.parametrize(
    "coverage_rows, expected_fragment",
    [
        ([{"name": "authoritative_evidence_db", "kind": "database",
           "required_before_live": True, "coverage": "MYSTERY"}],
         "unknown coverage state MYSTERY"),
        ([42], "artifact row must be an object"),
    ],
)
def test_restore_proof_fails_closed_on_malformed_coverage_rows(
    tmp_path, monkeypatch, coverage_rows, expected_fragment
):
    declared = SimpleNamespace(
        name="authoritative_evidence_db",
        required_before_live=True,
        kind=SimpleNamespace(value="database"),
    )
    monkeypatch.setattr(
        "app.core.backup_manifest.REQUIRED_ARTIFACTS",
        (declared,),
    )
    (tmp_path / "release.json").write_text(
        '{"build":{"runtime_sha":"' + ("a" * 40) + '","release_tag":"release-test"}}',
        encoding="utf-8",
    )
    import json
    (tmp_path / "coverage.json").write_text(
        json.dumps(coverage_rows),
        encoding="utf-8",
    )

    proof = prove_restore(tmp_path, _manifest())

    assert proof.passed is False
    assert any(expected_fragment in item for item in proof.failures)


def test_restore_proof_rejects_release_identity_mismatch(tmp_path, monkeypatch):
    _ignore_global_coverage_contract(monkeypatch)
    manifest = _manifest()
    (tmp_path / "release.json").write_text(
        '{"build":{"runtime_sha":"' + ("b" * 40) + '","release_tag":"other"}}',
        encoding="utf-8",
    )
    (tmp_path / "coverage.json").write_text("[]", encoding="utf-8")

    proof = prove_restore(tmp_path, manifest)

    assert proof.passed is False
    assert any("runtime_sha does not match backup manifest" in item for item in proof.failures)
    assert any("release_tag does not match backup manifest" in item for item in proof.failures)


def test_restore_proof_accepts_matching_release_identity(tmp_path, monkeypatch):
    _ignore_global_coverage_contract(monkeypatch)
    manifest = _manifest()
    (tmp_path / "release.json").write_text(
        '{"build":{"runtime_sha":"' + ("a" * 40) + '","release_tag":"release-test"}}',
        encoding="utf-8",
    )
    (tmp_path / "coverage.json").write_text("[]", encoding="utf-8")

    proof = prove_restore(tmp_path, manifest)

    assert proof.passed is True
    assert proof.failures == ()
