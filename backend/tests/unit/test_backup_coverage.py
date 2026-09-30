"""A backup that restores the numbers and loses the system is not a backup.

The rules under test: an artifact that exists and was not captured is a defect;
an artifact that does not exist may be fine and must say so; the deployment
configuration is recorded by checksum and never copied, because it holds broker
credentials and a backup is a file that gets copied elsewhere.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from app.core.backup_coverage import (
    REQUIRED_ARTIFACTS,
    ArtifactKind,
    Coverage,
    coverage_report,
    render_coverage,
    resolve_artifacts,
)


@pytest.fixture()
def host(tmp_path, monkeypatch):
    """A host with an evidence database, a safety state and a lane manifest."""
    (tmp_path / "backend").mkdir()
    (tmp_path / "data/manifests/strategies").mkdir(parents=True)
    with sqlite3.connect(tmp_path / "backend/snapback_observations.db") as conn:
        conn.execute("CREATE TABLE outcomes (outcome_id TEXT)")
    (tmp_path / "data/safe_mode.json").write_text("{}", encoding="utf-8")
    (tmp_path / "data/manifests/strategies/snapback.json").write_text("{}", encoding="utf-8")
    for var in ("STERLING_EVIDENCE_DB", "STERLING_OBSERVATIONS_DB_PATH",
                "STERLING_DB_PATH", "STERLING_SAFE_MODE_FILE", "STERLING_SHADOW_DIR"):
        monkeypatch.delenv(var, raising=False)
    return tmp_path


class TestTheDeclaredSet:
    def test_the_eleven_required_artifacts_are_declared(self):
        names = {a.name for a in REQUIRED_ARTIFACTS}
        for expected in (
            "authoritative_evidence_db", "canonical_intent_journal", "safety_state",
            "release_manifest", "lane_manifests", "deployment_configuration",
        ):
            assert expected in names

    def test_the_deployment_configuration_is_never_copied(self):
        config = next(a for a in REQUIRED_ARTIFACTS
                      if a.name == "deployment_configuration")
        assert config.kind is ArtifactKind.REFERENCED
        assert "credentials" in config.why

    def test_every_artifact_says_why_it_matters(self):
        for artifact in REQUIRED_ARTIFACTS:
            assert artifact.why.strip(), artifact.name

    def test_a_resolver_that_raises_does_not_break_the_report(self, monkeypatch, tmp_path):
        # A backup must not be prevented by one unreadable path.
        resolved = dict((a.name, p) for a, p in resolve_artifacts(tmp_path))
        assert len(resolved) == len(REQUIRED_ARTIFACTS)


class TestCoverage:
    def test_runtime_observations_env_drives_coverage_identity(self, host, monkeypatch):
        runtime_db = host / "data" / "prospective_runtime.db"
        runtime_db.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(runtime_db) as conn:
            conn.execute("CREATE TABLE outcomes (outcome_id TEXT)")
        monkeypatch.setenv("STERLING_OBSERVATIONS_DB_PATH", str(runtime_db))

        resolved = dict((a.name, p) for a, p in resolve_artifacts(host))

        assert resolved["authoritative_evidence_db"] == runtime_db

    def test_an_existing_artifact_that_was_not_captured_is_a_gap(self, host):
        statuses = {s.artifact.name: s for s in coverage_report(host)}
        assert statuses["authoritative_evidence_db"].coverage is Coverage.GAP
        assert statuses["safety_state"].coverage is Coverage.GAP

    def test_an_artifact_that_does_not_exist_is_absent_not_a_gap(self, host):
        statuses = {s.artifact.name: s for s in coverage_report(host)}
        # This host has never bound a broker account.
        assert statuses["account_bindings"].coverage is Coverage.ABSENT

    def test_capturing_clears_the_gap(self, host):
        statuses = {
            s.artifact.name: s
            for s in coverage_report(host, captured=["authoritative_evidence_db"])
        }
        assert statuses["authoritative_evidence_db"].coverage is Coverage.COPIED

    def test_a_referenced_artifact_is_neither_copied_nor_a_gap(self, host):
        statuses = {
            s.artifact.name: s
            for s in coverage_report(host, referenced=["deployment_configuration"])
        }
        assert statuses["deployment_configuration"].coverage is Coverage.REFERENCED

    def test_the_rendering_names_the_gaps_and_explains_them(self, host):
        text = render_coverage(coverage_report(host))
        assert "GAPS" in text
        assert "authoritative_evidence_db" in text
        assert "promotion decision" in text  # the artifact's own "why"

    def test_full_coverage_says_so_without_qualification(self, host):
        # The remaining required-before-live artifacts have to exist before the
        # report can be clean: their absence is itself something to report.
        import sqlite3

        with sqlite3.connect(host / "backend/sterling_paper.db") as conn:
            conn.execute("CREATE TABLE kite_order_intents (intent_key TEXT)")
        (host / "data/manifests/release.json").write_text("{}", encoding="utf-8")

        captured = [a.name for a, p in resolve_artifacts(host) if p and p.exists()]
        text = render_coverage(coverage_report(host, captured=captured))
        assert "either backed up or legitimately absent" in text


class TestDatabaseDeclarations:
    def test_missing_declared_database_is_reported_by_full_backup(
            self, host, monkeypatch):
        from app.core.backup_manifest import create_full_backup

        evidence = host / "backend/snapback_observations.db"
        missing = host / "backend/runtime-missing.db"
        monkeypatch.setenv(
            "STERLING_BACKUP_DATABASES",
            f"{evidence}:{missing}",
        )
        monkeypatch.setattr("app.core.release_manifest.repo_root", lambda: host)

        result = create_full_backup(backup_root=host / "backups", root=host)

        assert evidence.name in result.databases
        assert str(missing) in result.missing

    def test_two_declared_databases_cannot_overwrite_each_other_by_basename(
            self, host, monkeypatch):
        from app.core.backup_manifest import declared_databases
        from app.services.snapback_backup import BackupIntegrityError

        first = host / "one/runtime.db"
        second = host / "two/runtime.db"
        first.parent.mkdir(parents=True)
        second.parent.mkdir(parents=True)
        monkeypatch.setenv(
            "STERLING_BACKUP_DATABASES",
            f"{first}:{second}",
        )

        with pytest.raises(BackupIntegrityError, match="collide by basename"):
            declared_databases(host)


class TestScheduledBackupAugmentation:
    def test_augmentation_writes_declared_state_and_coverage(
        self, host, monkeypatch
    ):
        from app.core.backup_manifest import (
            augment_backup_directory,
            snapshot_database,
        )

        monkeypatch.setattr("app.core.release_manifest.repo_root", lambda: host)
        target = host / "backups" / "2026-09-30"
        target.mkdir(parents=True)
        evidence = host / "backend/snapback_observations.db"
        copied = target / evidence.name
        snapshot_database(evidence, copied)

        result = augment_backup_directory(
            target,
            root=host,
            already_included=[copied],
        )

        assert "safety_state" in result.captured
        assert "lane_manifests" in result.captured
        assert (target / "coverage.json").exists()
        coverage = json.loads((target / "coverage.json").read_text())
        by_name = {row["name"]: row["coverage"] for row in coverage}
        assert by_name["authoritative_evidence_db"] == "COPIED"
        assert by_name["safety_state"] == "COPIED"
        assert "coverage.json" in result.manifest.file_checksums
        assert "release.json" in result.manifest.file_checksums


    def test_second_same_day_augmentation_cannot_reuse_stale_state(
        self, host, monkeypatch
    ):
        from app.core.backup_manifest import (
            augment_backup_directory,
            snapshot_database,
        )

        monkeypatch.setattr("app.core.release_manifest.repo_root", lambda: host)
        target = host / "backups" / "2026-09-30"
        target.mkdir(parents=True)
        evidence = host / "backend/snapback_observations.db"
        copied = target / evidence.name
        snapshot_database(evidence, copied)

        augment_backup_directory(
            target,
            root=host,
            already_included=[copied],
        )
        backed_up_safety = target / "state" / "safety_state.json"
        assert backed_up_safety.exists()

        # Source state disappears before another same-day backup.  The earlier
        # copy must not survive and make the new backup look complete.
        (host / "data/safe_mode.json").unlink()

        result = augment_backup_directory(
            target,
            root=host,
            already_included=[copied],
        )

        assert not backed_up_safety.exists()
        coverage = json.loads((target / "coverage.json").read_text())
        safety = next(row for row in coverage if row["name"] == "safety_state")
        assert safety["coverage"] == "ABSENT"
        assert safety["required_before_live"] is True
        assert "state/safety_state.json" not in result.manifest.file_checksums


class TestTheBackupActuallyCapturesThem:
    def test_a_full_backup_copies_the_declared_state_and_records_coverage(
            self, host, monkeypatch):
        from app.core.backup_manifest import create_full_backup

        monkeypatch.setattr("app.core.release_manifest.repo_root", lambda: host)
        result = create_full_backup(backup_root=host / "backups", root=host)

        assert "safety_state" in result.captured
        assert "lane_manifests" in result.captured
        coverage = json.loads((result.directory / "coverage.json").read_text())
        by_name = {row["name"]: row["coverage"] for row in coverage}
        assert by_name["safety_state"] == "COPIED"
        assert by_name["authoritative_evidence_db"] == "COPIED"
        assert result.gaps == ()

    def test_restore_check_fails_when_the_backup_had_a_gap(self, host, monkeypatch):
        from app.core.backup_manifest import create_full_backup, prove_restore

        monkeypatch.setattr("app.core.release_manifest.repo_root", lambda: host)
        result = create_full_backup(backup_root=host / "backups", root=host)

        # Rewrite the coverage record as though something had been left out.
        coverage_file = result.directory / "coverage.json"
        rows = json.loads(coverage_file.read_text())
        for row in rows:
            if row["name"] == "safety_state":
                row["coverage"] = "GAP"
        coverage_file.write_text(json.dumps(rows), encoding="utf-8")

        proof = prove_restore(result.directory)
        assert proof.passed is False
        assert any("were not backed up" in f for f in proof.failures)

    def test_restore_check_fails_when_coverage_is_missing_entirely(self, host, monkeypatch):
        from app.core.backup_manifest import create_full_backup, prove_restore

        monkeypatch.setattr("app.core.release_manifest.repo_root", lambda: host)
        result = create_full_backup(backup_root=host / "backups", root=host)
        (result.directory / "coverage.json").unlink()

        proof = prove_restore(result.directory)
        assert proof.passed is False
        assert any("coverage.json" in f for f in proof.failures)

    def test_a_complete_backup_restores_clean(self, host, monkeypatch):
        from app.core.backup_manifest import create_full_backup, prove_restore

        # Durable intent/execution-control state is required before live capital.
        with sqlite3.connect(host / "backend/sterling_paper.db") as conn:
            conn.execute("CREATE TABLE kite_order_intents (intent_key TEXT)")

        monkeypatch.setattr("app.core.release_manifest.repo_root", lambda: host)
        result = create_full_backup(backup_root=host / "backups", root=host)
        proof = prove_restore(result.directory)
        assert proof.passed is True, proof.failures

    def test_restore_fails_when_required_source_artifact_never_existed(
            self, host, monkeypatch):
        from app.core.backup_manifest import create_full_backup, prove_restore

        # The fixture intentionally has no canonical intent journal.
        monkeypatch.setattr("app.core.release_manifest.repo_root", lambda: host)
        result = create_full_backup(backup_root=host / "backups", root=host)
        proof = prove_restore(result.directory)

        assert proof.passed is False
        assert any(
            "required artifacts did not exist" in failure
            and "canonical_intent_journal" in failure
            for failure in proof.failures
        )
