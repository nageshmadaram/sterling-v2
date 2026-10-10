from __future__ import annotations

import json
import sqlite3
from dataclasses import replace

from app.core.backup_manifest import (
    RestoreStatus, build_backup_manifest, prove_restore, record_restore_proof,
)
from app.core.backup_coverage import REQUIRED_ARTIFACTS
from app.services import release_certification as certification


def _backup(root, *, sha: str, status: RestoreStatus = RestoreStatus.PASSED):
    directory = root / "2026-09-29"
    directory.mkdir(parents=True)
    with sqlite3.connect(directory / "evidence.db") as conn:
        conn.execute("CREATE TABLE evidence (id INTEGER)")
    with sqlite3.connect(directory / "intents.db") as conn:
        conn.execute("CREATE TABLE intents (id INTEGER)")
    (directory / "state/lane_manifests").mkdir(parents=True)
    (directory / "state/lane_manifests/lane.json").write_text("{}")
    (directory / "state/safety_state.json").write_text("{}")
    paths = {
        "authoritative_evidence_db": "evidence.db",
        "canonical_intent_journal": "intents.db",
        "safety_state": "safe_mode.json",
        "lane_manifests": "strategies",
    }
    (directory / "coverage.json").write_text(json.dumps([
        {"name": artifact.name, "kind": artifact.kind.value,
         "required_before_live": artifact.required_before_live,
         "coverage": "COPIED" if artifact.name in paths else "ABSENT",
         "path": paths.get(artifact.name)}
        for artifact in REQUIRED_ARTIFACTS
    ]))
    manifest = replace(
        build_backup_manifest(directory),
        release_sha=sha,
        release_tag="candidate",
    )
    if status is RestoreStatus.PASSED:
        proof = prove_restore(directory, manifest)
        assert proof.passed, proof.failures
        manifest = record_restore_proof(manifest, proof)
    else:
        manifest = replace(manifest, restore_test_status=status)
    (directory / "manifest.json").write_text(
        json.dumps(manifest.as_dict()),
        encoding="utf-8",
    )
    return directory


def test_restore_gate_refuses_pass_from_another_sha(tmp_path, monkeypatch):
    expected = "a" * 40
    _backup(tmp_path, sha="b" * 40)
    monkeypatch.setenv("STERLING_BACKUP_ROOT", str(tmp_path))

    gate = certification._backup_restore_gate(expected)

    assert gate.status == "FAIL"
    assert "does not match candidate" in gate.detail


def test_restore_gate_accepts_proved_backup_on_exact_sha(tmp_path, monkeypatch):
    expected = "a" * 40
    _backup(tmp_path, sha=expected)
    monkeypatch.setenv("STERLING_BACKUP_ROOT", str(tmp_path))

    gate = certification._backup_restore_gate(expected)

    assert gate.status == "PASS"
    assert expected[:12] in gate.detail


def test_restore_gate_invalidates_pass_when_backup_bytes_change(tmp_path, monkeypatch):
    expected = "a" * 40
    directory = _backup(tmp_path, sha=expected)
    (directory / "state/safety_state.json").write_text('{"changed": true}')
    monkeypatch.setenv("STERLING_BACKUP_ROOT", str(tmp_path))

    gate = certification._backup_restore_gate(expected)

    assert gate.status == "FAIL"
    assert "checksum mismatch" in gate.detail


def test_restore_gate_rejects_status_with_no_database_proof(tmp_path, monkeypatch):
    expected = "a" * 40
    directory = _backup(tmp_path, sha=expected)
    path = directory / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest.update(file_checksums={}, row_counts={}, schema_signatures={})
    path.write_text(json.dumps(manifest))
    monkeypatch.setenv("STERLING_BACKUP_ROOT", str(tmp_path))

    gate = certification._backup_restore_gate(expected)

    assert gate.status == "FAIL"
    assert "no database row-count evidence" in gate.detail


def test_restore_gate_does_not_treat_untested_exact_sha_as_pass(tmp_path, monkeypatch):
    expected = "a" * 40
    _backup(tmp_path, sha=expected, status=RestoreStatus.UNTESTED)
    monkeypatch.setenv("STERLING_BACKUP_ROOT", str(tmp_path))

    gate = certification._backup_restore_gate(expected)

    assert gate.status == "FAIL"
    assert "UNTESTED" in gate.detail


def test_restore_gate_honors_runtime_snapback_backup_root(tmp_path, monkeypatch):
    expected = "a" * 40
    runtime_root = tmp_path / "runtime-backups"
    _backup(runtime_root, sha=expected)
    monkeypatch.delenv("STERLING_BACKUP_ROOT", raising=False)
    monkeypatch.setenv("STERLING_SNAPBACK_BACKUP_ROOT", str(runtime_root))

    gate = certification._backup_restore_gate(expected)

    assert gate.status == "PASS"
    assert expected[:12] in gate.detail
