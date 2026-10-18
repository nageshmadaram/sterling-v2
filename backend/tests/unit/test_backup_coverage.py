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
    # This fixture models an older checkout with backend/*.db (not an
    # explicitly relocated STERLING_STATE_ROOT/data layout).
    for var in ("STERLING_STATE_ROOT", "STERLING_EVIDENCE_DB",
                "STERLING_OBSERVATIONS_DB_PATH", "STERLING_DB_PATH",
                "STERLING_KITE_ENGINE_DB", "STERLING_SAFE_MODE_FILE",
                "STERLING_SHADOW_DIR"):
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

    def test_engaged_family_halt_is_part_of_backup_coverage(self, host, monkeypatch):
        halt = host / "data" / "snapback" / "new_trades_halt.json"
        halt.parent.mkdir(parents=True, exist_ok=True)
        halt.write_text('{"halted": true, "reason": "operator stop"}', encoding="utf-8")
        monkeypatch.setenv("STERLING_NEW_TRADES_HALT_PATH", str(halt))

        statuses = {s.artifact.name: s for s in coverage_report(host)}
        assert statuses["family_new_trades_halt"].coverage is Coverage.GAP

        copied = {
            s.artifact.name: s
            for s in coverage_report(host, captured=["family_new_trades_halt"])
        }
        assert copied["family_new_trades_halt"].coverage is Coverage.COPIED

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

    def test_foreign_same_named_db_cannot_count_as_canonical_journal(
            self, host, monkeypatch):
        from app.core.backup_manifest import create_full_backup

        canonical = host / "backend" / "sterling_paper.db"
        with sqlite3.connect(canonical) as conn:
            conn.execute("CREATE TABLE genuine_orders (id INTEGER)")

        foreign = host / "foreign" / "sterling_paper.db"
        foreign.parent.mkdir()
        with sqlite3.connect(foreign) as conn:
            conn.execute("CREATE TABLE unrelated (id INTEGER)")

        result = create_full_backup(
            backup_root=host / "backups",
            root=host,
            databases=[host / "backend/snapback_observations.db", foreign],
        )

        rows = json.loads((result.directory / "coverage.json").read_text())
        coverage = {row["name"]: row["coverage"] for row in rows}
        assert coverage["authoritative_evidence_db"] == "COPIED"
        assert coverage["canonical_intent_journal"] == "GAP"
        assert "canonical_intent_journal" in result.gaps


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


def test_two_full_backups_same_day_are_isolated(host, monkeypatch):
    from datetime import datetime, timezone

    from app.core.backup_manifest import create_full_backup

    monkeypatch.setattr("app.core.release_manifest.repo_root", lambda: host)
    evidence = host / "backend/snapback_observations.db"
    extra = host / "backend/extra-runtime.db"
    with sqlite3.connect(extra) as conn:
        conn.execute("CREATE TABLE state (id INTEGER)")

    first = create_full_backup(
        backup_root=host / "backups",
        root=host,
        databases=[evidence, extra],
        now=datetime(2026, 9, 30, 10, 0, tzinfo=timezone.utc),
    )

    extra.unlink()

    second = create_full_backup(
        backup_root=host / "backups",
        root=host,
        databases=[evidence],
        now=datetime(2026, 9, 30, 10, 5, tzinfo=timezone.utc),
    )

    assert first.directory != second.directory
    assert (first.directory / "extra-runtime.db").exists()
    assert not (second.directory / "extra-runtime.db").exists()
    assert first.manifest.backup_id != second.manifest.backup_id


def test_latest_backup_uses_manifest_time_across_mixed_directory_formats(tmp_path):
    from app.core.backup_manifest import BackupManifest, latest_backup_dir

    root = tmp_path / "backups"
    earlier = root / "2026-09-30T100000.000000Z"
    later = root / "2026-09-30"
    earlier.mkdir(parents=True)
    later.mkdir(parents=True)

    for directory, created in (
        (earlier, "2026-09-30T10:00:00+00:00"),
        (later, "2026-09-30T23:00:00+00:00"),
    ):
        (directory / "evidence.db").write_bytes(b"db")
        manifest = BackupManifest(
            backup_id=directory.name,
            created_at=created,
            release_sha="a" * 40,
            release_tag="candidate",
            schema_versions={},
            file_checksums={},
            row_counts={},
            schema_signatures={},
        )
        (directory / "manifest.json").write_text(
            json.dumps(manifest.as_dict()),
            encoding="utf-8",
        )

    assert latest_backup_dir(root) == later


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


def test_same_day_augmentation_refreshes_existing_runtime_db(host, monkeypatch):
    from app.core.backup_manifest import augment_backup_directory, snapshot_database

    source = host / "backend/sterling_paper.db"
    with sqlite3.connect(source) as conn:
        conn.execute("CREATE TABLE intents (id INTEGER)")
        conn.execute("INSERT INTO intents VALUES (1)")
        conn.execute("INSERT INTO intents VALUES (2)")

    evidence = host / "backend/snapback_observations.db"
    target = host / "backups" / "2026-10-18"
    target.mkdir(parents=True)
    verified = target / evidence.name
    snapshot_database(evidence, verified)

    # This previous same-day backup is stale but has an apparently valid name.
    stale = target / source.name
    with sqlite3.connect(stale) as conn:
        conn.execute("CREATE TABLE intents (id INTEGER)")
        conn.execute("INSERT INTO intents VALUES (999)")

    result = augment_backup_directory(
        target, root=host, already_included=[verified]
    )

    assert result.manifest.row_counts["sterling_paper.db"]["intents"] == 2
    with sqlite3.connect(stale) as conn:
        assert conn.execute("SELECT id FROM intents ORDER BY id").fetchall() == [
            (1,), (2,),
        ]


def test_augmentation_rejects_unverified_already_included_path(host):
    from app.core.backup_manifest import augment_backup_directory
    from app.services.snapback_backup import BackupIntegrityError

    outside = host / "backend/snapback_observations.db"
    target = host / "backups" / "2026-10-18"

    with pytest.raises(BackupIntegrityError, match="verified snapshot within"):
        augment_backup_directory(
            target, root=host, already_included=[outside],
        )


def test_augmentation_defaults_to_persistent_state_root(tmp_path, monkeypatch):
    from app.core.backup_manifest import (
        augment_backup_directory, manifest_path
    )

    persistent = tmp_path / "persisted"
    evidence = persistent / "data" / "snapback_observations.db"
    evidence.parent.mkdir(parents=True)
    with sqlite3.connect(evidence) as conn:
        conn.execute("CREATE TABLE events (id INTEGER)")
        conn.execute("INSERT INTO events VALUES (13)")
    monkeypatch.setenv("STERLING_STATE_ROOT", str(persistent))
    monkeypatch.setenv("STERLING_EVIDENCE_DB", str(evidence))
    monkeypatch.setenv("STERLING_BACKUP_DATABASES", str(evidence))

    target = persistent / "backups" / "2026-10-18"
    result = augment_backup_directory(target)

    assert result.manifest.row_counts[evidence.name]["events"] == 1
    assert manifest_path(result.manifest) == (
        persistent / "data/backups/manifests/2026-10-18.json"
    )



def test_full_backup_does_not_follow_symlink_to_external_file(host, monkeypatch):
    from app.core.backup_manifest import create_full_backup

    external = host / "confidential.txt"
    external.write_text("private-not-for-backup", encoding="utf-8")
    safety = host / "data/safe_mode.json"
    safety.unlink()
    safety.symlink_to(external)
    result = create_full_backup(backup_root=host / "backups", root=host)

    assert "safety_state" in result.gaps
    assert not (result.directory / "state/safety_state.json").exists()
    assert all(
        "private-not-for-backup" not in p.read_text(errors="replace")
        for p in (result.directory / "state").rglob("*.json")
    )


def test_full_backup_rejects_symlink_inside_strategy_directory(host, monkeypatch):
    from app.core.backup_manifest import create_full_backup

    external = host / "confidential.txt"
    external.write_text("private-not-for-backup", encoding="utf-8")
    (host / "data/manifests/strategies/secret.json").symlink_to(external)
    result = create_full_backup(backup_root=host / "backups", root=host)

    assert "lane_manifests" in result.gaps
    assert not (result.directory / "state/lane_manifests/secret.json").exists()


def test_scheduled_backup_rejects_symlinked_existing_metadata(host):
    from app.core.backup_manifest import (
        BackupIntegrityError, augment_backup_directory,
    )

    target = host / "backups" / "2026-10-18"
    target.mkdir(parents=True)
    outside = host / "operator-state.json"
    outside.write_text("do not overwrite operator state", encoding="utf-8")
    (target / "release.json").symlink_to(outside)

    with pytest.raises(BackupIntegrityError, match="metadata must not be a symlink"):
        augment_backup_directory(target, root=host)

    assert outside.read_text(encoding="utf-8") == "do not overwrite operator state"


def test_scheduled_backup_rejects_symlinked_target_directory(host):
    from app.core.backup_manifest import (
        BackupIntegrityError, augment_backup_directory,
    )

    outside = host / "operator-live"
    outside.mkdir()
    sentinel = outside / "do-not-delete.txt"
    sentinel.write_text("preserve", encoding="utf-8")
    target = host / "linked-backup"
    target.symlink_to(outside, target_is_directory=True)

    with pytest.raises(BackupIntegrityError, match="target must not be a symlink"):
        augment_backup_directory(target, root=host)

    assert sentinel.read_text(encoding="utf-8") == "preserve"


def test_backup_discards_partial_directory_if_late_symlink_appears(
    tmp_path, monkeypatch
):
    import shutil
    from types import SimpleNamespace

    from app.core.backup_manifest import _capture_declared_artifacts
    from app.core import backup_coverage

    live = tmp_path / "data/manifests/strategies"
    live.mkdir(parents=True)
    (live / "snapshot.json").write_text("{}", encoding="utf-8")
    secret = tmp_path / "private-key.txt"
    secret.write_text("DO NOT EXPORT", encoding="utf-8")
    target = tmp_path / "archives" / "today"
    target.mkdir(parents=True)

    artifact = SimpleNamespace(
        name="lane_manifests", kind=ArtifactKind.DIRECTORY
    )
    monkeypatch.setattr(
        backup_coverage, "resolve_artifacts",
        lambda _root: [(artifact, live)],
    )
    real_copytree = shutil.copytree

    def simulate_source_change(source, destination, **kwargs):
        real_copytree(source, destination, **kwargs)
        (destination / "late-symlink").symlink_to(secret)

    monkeypatch.setattr(shutil, "copytree", simulate_source_change)

    captured, referenced = _capture_declared_artifacts(tmp_path, target)

    assert captured == []
    assert referenced == []
    assert not (target / "state/lane_manifests").exists()
    assert secret.read_text(encoding="utf-8") == "DO NOT EXPORT"


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

    def test_restore_check_fails_when_copied_artifact_is_not_checksummed(
            self, host, monkeypatch):
        from app.core.backup_manifest import create_full_backup, prove_restore

        with sqlite3.connect(host / "backend/sterling_paper.db") as conn:
            conn.execute("CREATE TABLE kite_order_intents (intent_key TEXT)")

        monkeypatch.setattr("app.core.release_manifest.repo_root", lambda: host)
        result = create_full_backup(backup_root=host / "backups", root=host)

        manifest_path = result.directory / "manifest.json"
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        payload["file_checksums"].pop("state/safety_state.json", None)
        manifest_path.write_text(json.dumps(payload), encoding="utf-8")

        proof = prove_restore(result.directory)

        assert proof.passed is False
        assert any(
            "safety_state: copied artifact is not checksummed" in failure
            for failure in proof.failures
        )

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
