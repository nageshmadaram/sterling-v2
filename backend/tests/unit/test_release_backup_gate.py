from __future__ import annotations

import json
import sqlite3

from app.core.backup_manifest import BackupManifest, RestoreStatus
from app.services import release_certification as certification


def _backup(root, *, sha: str, status: RestoreStatus = RestoreStatus.PASSED):
    directory = root / "2026-09-29"
    directory.mkdir(parents=True)
    with sqlite3.connect(directory / "evidence.db") as conn:
        conn.execute("CREATE TABLE evidence (id INTEGER)")
    manifest = BackupManifest(
        backup_id=directory.name,
        created_at="2026-09-29T12:00:00+00:00",
        release_sha=sha,
        release_tag="candidate",
        schema_versions={},
        file_checksums={},
        row_counts={},
        schema_signatures={},
        restore_test_status=status,
        restore_tested_at="2026-09-29T12:05:00+00:00",
    )
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


def test_restore_gate_does_not_treat_untested_exact_sha_as_pass(tmp_path, monkeypatch):
    expected = "a" * 40
    _backup(tmp_path, sha=expected, status=RestoreStatus.UNTESTED)
    monkeypatch.setenv("STERLING_BACKUP_ROOT", str(tmp_path))

    gate = certification._backup_restore_gate(expected)

    assert gate.status == "FAIL"
    assert "UNTESTED" in gate.detail
