from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

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



def _backup(
    root,
    *,
    sha: str,
    status: RestoreStatus = RestoreStatus.PASSED,
    created_at: str | None = None,
    restore_tested_at: str | None = None,
):
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

    now = datetime.now(timezone.utc)
    created_at = created_at or (now - timedelta(minutes=1)).isoformat()
    restore_tested_at = restore_tested_at or now.isoformat()

    manifest = BackupManifest(
        backup_id=directory.name,
        created_at=created_at,
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
        restore_tested_at=restore_tested_at,
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


def test_restore_gate_rejects_stale_backup_on_same_sha(tmp_path, monkeypatch):
    _ignore_global_coverage_contract(monkeypatch)
    expected = "a" * 40
    now = datetime.now(timezone.utc)
    _backup(
        tmp_path,
        sha=expected,
        created_at=(now - timedelta(hours=25)).isoformat(),
        restore_tested_at=(now - timedelta(hours=24, minutes=55)).isoformat(),
    )
    monkeypatch.setenv("STERLING_BACKUP_ROOT", str(tmp_path))

    gate = certification._backup_restore_gate(expected)

    assert gate.status == "FAIL"
    assert "release limit is 24h" in gate.detail


def test_restore_gate_rejects_future_backup_timestamp(tmp_path, monkeypatch):
    _ignore_global_coverage_contract(monkeypatch)
    expected = "a" * 40
    future = datetime.now(timezone.utc) + timedelta(minutes=5)
    _backup(
        tmp_path,
        sha=expected,
        created_at=future.isoformat(),
        restore_tested_at=(future + timedelta(minutes=1)).isoformat(),
    )
    monkeypatch.setenv("STERLING_BACKUP_ROOT", str(tmp_path))

    gate = certification._backup_restore_gate(expected)

    assert gate.status == "UNKNOWN"
    assert "future" in gate.detail


def test_restore_gate_rejects_proof_that_predates_backup(tmp_path, monkeypatch):
    _ignore_global_coverage_contract(monkeypatch)
    expected = "a" * 40
    now = datetime.now(timezone.utc)
    _backup(
        tmp_path,
        sha=expected,
        created_at=(now - timedelta(minutes=5)).isoformat(),
        restore_tested_at=(now - timedelta(minutes=10)).isoformat(),
    )
    monkeypatch.setenv("STERLING_BACKUP_ROOT", str(tmp_path))

    gate = certification._backup_restore_gate(expected)

    assert gate.status == "FAIL"
    assert "predates the backup" in gate.detail


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


def _make_post_freeze_backup(directory, frozen):
    state_dir = directory / "state"
    state_dir.mkdir(exist_ok=True)
    frozen_copy = state_dir / "release_manifest.json"
    frozen_copy.write_text(json.dumps(frozen), encoding="utf-8")

    lane_dir = state_dir / "lane_manifests"
    lane_dir.mkdir()
    for lane in frozen.get("lanes", []):
        lane_copy = dict(lane)
        lane_copy["build"] = frozen["build"]
        lane_copy["generated_at"] = frozen.get("generated_at")
        name = lane["lane_key"].replace(":", "__") + ".json"
        (lane_dir / name).write_text(json.dumps(lane_copy), encoding="utf-8")

    coverage_path = directory / "coverage.json"
    coverage_path.write_text(
        json.dumps([{
            "name": "release_manifest",
            "kind": "file",
            "required_before_live": False,
            "coverage": "COPIED",
            "path": "data/manifests/release.json",
        }, {
            "name": "lane_manifests",
            "kind": "directory",
            "required_before_live": True,
            "coverage": "COPIED",
            "path": "data/manifests/strategies",
        }]),
        encoding="utf-8",
    )

    manifest_path = directory / "manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["file_checksums"]["coverage.json"] = sha256_file(coverage_path)
    payload["file_checksums"]["state/release_manifest.json"] = sha256_file(frozen_copy)
    for lane_file in lane_dir.glob("*.json"):
        payload["file_checksums"][lane_file.relative_to(directory).as_posix()] = (
            sha256_file(lane_file)
        )
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")


def test_post_freeze_backup_passes_only_when_frozen_manifest_is_captured(
    tmp_path, monkeypatch
):
    expected = "a" * 40
    directory = _backup(tmp_path, sha=expected)
    frozen = {"build": {"runtime_sha": expected, "release_tag": "candidate"}}
    _make_post_freeze_backup(directory, frozen)

    monkeypatch.setattr(
        "app.core.backup_manifest.REQUIRED_ARTIFACTS",
        (
            SimpleNamespace(
                name="release_manifest",
                kind=SimpleNamespace(value="file"),
                required_before_live=False,
            ),
            SimpleNamespace(
                name="lane_manifests",
                kind=SimpleNamespace(value="directory"),
                required_before_live=True,
            ),
        ),
    )
    monkeypatch.setenv("STERLING_BACKUP_ROOT", str(tmp_path))
    monkeypatch.setattr(certification, "read_manifest", lambda: frozen)

    gate = certification._backup_restore_gate(expected)

    assert gate.status == "PASS"
    assert "fresh isolated restore verified" in gate.detail


def test_post_freeze_backup_rejects_different_frozen_manifest_bytes(
    tmp_path, monkeypatch
):
    expected = "a" * 40
    directory = _backup(tmp_path, sha=expected)
    current = {"build": {"runtime_sha": expected, "release_tag": "candidate"}}
    backed_up = {
        "build": {
            "runtime_sha": expected,
            "release_tag": "candidate",
            "different_contract": True,
        }
    }
    _make_post_freeze_backup(directory, backed_up)

    monkeypatch.setattr(
        "app.core.backup_manifest.REQUIRED_ARTIFACTS",
        (
            SimpleNamespace(
                name="release_manifest",
                kind=SimpleNamespace(value="file"),
                required_before_live=False,
            ),
            SimpleNamespace(
                name="lane_manifests",
                kind=SimpleNamespace(value="directory"),
                required_before_live=True,
            ),
        ),
    )
    monkeypatch.setenv("STERLING_BACKUP_ROOT", str(tmp_path))
    monkeypatch.setattr(certification, "read_manifest", lambda: current)

    gate = certification._backup_restore_gate(expected)

    assert gate.status == "FAIL"
    assert "does not match the currently frozen release" in gate.detail


def test_post_freeze_gate_refuses_rechecksummed_stale_lane_file(
    tmp_path, monkeypatch
):
    expected = "a" * 40
    directory = _backup(tmp_path, sha=expected)
    frozen = {
        "build": {"runtime_sha": expected, "release_tag": "candidate"},
        "generated_at": "2026-10-18T00:00:00+00:00",
        "lanes": [{"lane_key": "snapback:scalping", "note": "frozen rules"}],
    }
    _make_post_freeze_backup(directory, frozen)
    lane_file = directory / "state" / "lane_manifests" / "snapback__scalping.json"
    lane = json.loads(lane_file.read_text(encoding="utf-8"))
    lane["note"] = "stale rules"
    lane_file.write_text(json.dumps(lane), encoding="utf-8")

    # The backup manifest now attests to the tampered bytes. Semantic release
    # identity must still refuse the backup; no checksum-only false PASS.
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["file_checksums"]["state/lane_manifests/snapback__scalping.json"] = (
        sha256_file(lane_file)
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    monkeypatch.setattr(
        "app.core.backup_manifest.REQUIRED_ARTIFACTS",
        (
            SimpleNamespace(
                name="release_manifest",
                kind=SimpleNamespace(value="file"),
                required_before_live=False,
            ),
            SimpleNamespace(
                name="lane_manifests",
                kind=SimpleNamespace(value="directory"),
                required_before_live=True,
            ),
        ),
    )
    monkeypatch.setenv("STERLING_BACKUP_ROOT", str(tmp_path))
    monkeypatch.setattr(certification, "read_manifest", lambda: frozen)

    gate = certification._backup_restore_gate(expected)
    assert gate.status == "FAIL"
    assert "frozen lane manifests" in gate.detail


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
