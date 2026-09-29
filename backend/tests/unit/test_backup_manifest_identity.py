from __future__ import annotations

import pytest

from app.core.backup_manifest import (
    BackupManifest,
    RestoreProof,
    RestoreStatus,
    record_restore_proof,
)


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
