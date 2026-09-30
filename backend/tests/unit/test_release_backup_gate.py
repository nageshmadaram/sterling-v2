from __future__ import annotations

import json
import sqlite3

from app.core.backup_manifest import (
    BackupManifest,
    RestoreStatus,
    schema_signature,
    sha256_file,
    table_row_counts,
)
from app.services import release_certification as certification


def _ignore_global_coverage_contract(monkeypatch):
    """Release-gate tests below target SHA/restore semantics, not coverage policy."""
    monkeypatch.setattr("app.core.backup_manifest.REQUIRED_ARTIFACTS", ())



def _backup(root, *, sha: str, status: RestoreStatus = RestoreStatus.PASSED):
    directory = root / "2026-09-29"
    directory.mkdir(parents=True)
    evidence_path = directory / "evidence.db"
    with sqlite3.connect(evidence_path) as conn:
        conn.execute("CREATE TABLE evidence (id INTEGER)")

    release_path = directory / "release.json"
    coverage_path = directory / "coverage.json"
    release_path.write_text(
        json.dumps({
            "build": {
                "runtime_sha": sha,
                "release_tag": "candidate",
            }
        }),
        encoding="utf-8",
    )
    coverage_path.write_text("[]", encoding="utf-8")

    manifest = BackupManifest(
        backup_id=directory.name,
        created_at="2026-09-29T12:00:00+00:00",
        release_sha=sha,
        release_tag="candidate",
        schema_versions={},
        file_checksums={
            "evidence.db": sha256_file(evidence_path),
            "release.json": sha256_file(release_path),
            "coverage.json": sha256_file(coverage_path),
        },
        row_counts={"evidence.db": table_row_counts(evidence_path)},
        schema_signatures={"evidence.db": schema_signature(evidence_path)},
        restore_test_status=status,
        restore_tested_at="2026-09-29T12:05:00+00:00",
    )
    (directory / "manifest.json").write_text(
        json.dumps(manifest.as_dict()),
        encoding="utf-8",
    )
    return directory


def test_restore_gate_refuses_pass_from_another_sha(tmp_path, monkeypatch):
    _ignore_global_coverage_contract(monkeypatch)
    expected = "a" * 40
    _backup(tmp_path, sha="b" * 40)
    monkeypatch.setenv("STERLING_BACKUP_ROOT", str(tmp_path))

    gate = certification._backup_restore_gate(expected)

    assert gate.status == "FAIL"
    assert "does not match candidate" in gate.detail


def test_restore_gate_accepts_proved_backup_on_exact_sha(tmp_path, monkeypatch):
    _ignore_global_coverage_contract(monkeypatch)
    expected = "a" * 40
    _backup(tmp_path, sha=expected)
    monkeypatch.setenv("STERLING_BACKUP_ROOT", str(tmp_path))

    gate = certification._backup_restore_gate(expected)

    assert gate.status == "PASS"
    assert expected[:12] in gate.detail


def test_restore_gate_does_not_treat_untested_exact_sha_as_pass(tmp_path, monkeypatch):
    _ignore_global_coverage_contract(monkeypatch)
    expected = "a" * 40
    _backup(tmp_path, sha=expected, status=RestoreStatus.UNTESTED)
    monkeypatch.setenv("STERLING_BACKUP_ROOT", str(tmp_path))

    gate = certification._backup_restore_gate(expected)

    assert gate.status == "FAIL"
    assert "UNTESTED" in gate.detail


def test_restore_gate_honors_runtime_snapback_backup_root(tmp_path, monkeypatch):
    _ignore_global_coverage_contract(monkeypatch)
    expected = "a" * 40
    runtime_root = tmp_path / "runtime-backups"
    _backup(runtime_root, sha=expected)
    monkeypatch.delenv("STERLING_BACKUP_ROOT", raising=False)
    monkeypatch.setenv("STERLING_SNAPBACK_BACKUP_ROOT", str(runtime_root))

    gate = certification._backup_restore_gate(expected)

    assert gate.status == "PASS"
    assert expected[:12] in gate.detail


def test_restore_gate_rejects_bytes_changed_after_proof(tmp_path, monkeypatch):
    _ignore_global_coverage_contract(monkeypatch)
    expected = "a" * 40
    directory = _backup(tmp_path, sha=expected)
    monkeypatch.setenv("STERLING_BACKUP_ROOT", str(tmp_path))

    (directory / "release.json").write_text(
        json.dumps({
            "build": {
                "runtime_sha": expected,
                "release_tag": "candidate",
                "changed_after_restore_proof": True,
            }
        }),
        encoding="utf-8",
    )

    gate = certification._backup_restore_gate(expected)

    assert gate.status == "FAIL"
    assert "checksum changed after restore proof" in gate.detail


def test_restore_gate_rechecks_database_even_if_checksum_is_rewritten(tmp_path, monkeypatch):
    _ignore_global_coverage_contract(monkeypatch)
    expected = "a" * 40
    directory = _backup(tmp_path, sha=expected)
    evidence_path = directory / "evidence.db"

    # Simulate a mutable backup directory plus a forged PASSED manifest: replace
    # the SQLite file with arbitrary bytes, then recompute the manifest checksum.
    # A checksum-only gate would accept this because the manifest and bytes agree.
    evidence_path.write_bytes(b"this is not a sqlite database")
    payload = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    payload["file_checksums"]["evidence.db"] = sha256_file(evidence_path)
    (directory / "manifest.json").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )
    monkeypatch.setenv("STERLING_BACKUP_ROOT", str(tmp_path))

    gate = certification._backup_restore_gate(expected)

    assert gate.status == "FAIL"
    assert "fresh isolated restore verification failed" in gate.detail
    assert "cannot be opened after restore" in gate.detail or "integrity_check" in gate.detail


def test_frozen_release_requires_a_fresh_post_freeze_backup(tmp_path, monkeypatch):
    _ignore_global_coverage_contract(monkeypatch)
    expected = "a" * 40
    _backup(tmp_path, sha=expected)
    monkeypatch.setenv("STERLING_BACKUP_ROOT", str(tmp_path))
    monkeypatch.setattr(
        certification,
        "read_manifest",
        lambda: {"build": {"runtime_sha": expected}},
    )

    gate = certification._backup_restore_gate(expected)

    assert gate.status == "FAIL"
    assert "current release is frozen" in gate.detail
    assert "fresh post-freeze backup" in gate.detail


def test_restore_gate_cross_checks_release_json_identity(tmp_path, monkeypatch):
    _ignore_global_coverage_contract(monkeypatch)
    expected = "a" * 40
    directory = _backup(tmp_path, sha=expected)
    release_path = directory / "release.json"
    release_path.write_text(
        json.dumps({
            "build": {
                "runtime_sha": "b" * 40,
                "release_tag": "candidate",
            }
        }),
        encoding="utf-8",
    )

    payload = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    payload["file_checksums"]["release.json"] = sha256_file(release_path)
    (directory / "manifest.json").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )
    monkeypatch.setenv("STERLING_BACKUP_ROOT", str(tmp_path))

    gate = certification._backup_restore_gate(expected)

    assert gate.status == "FAIL"
    assert "release.json SHA disagrees with backup manifest" in gate.detail
