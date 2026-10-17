"""Section 14.1: a backup holds the evidence AND runtime databases, plus the
release identity that wrote them.

Backing up only the evidence store leaves the durable intent record — the thing
restart recovery reads to decide whether an order was ever sent — outside the
backup, and leaves a restored directory unable to say which build produced it.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from app.core.backup_manifest import (
    BackupIntegrityError,
    create_full_backup,
    declared_databases,
    prove_restore,
    snapshot_database,
)


def _make_db(path, rows: int = 3, table: str = "t"):
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY)")
        conn.executemany(f"INSERT INTO {table} (id) VALUES (?)", [(i,) for i in range(rows)])
        conn.commit()
    finally:
        conn.close()
    return path


@pytest.fixture()
def sources(tmp_path):
    evidence = _make_db(tmp_path / "snapback_observations.db", rows=5)
    runtime = _make_db(tmp_path / "intents.db", rows=2, table="intents")
    return evidence, runtime


def test_every_declared_database_is_included(tmp_path, sources):
    evidence, runtime = sources

    result = create_full_backup(
        backup_root=tmp_path / "backups", root=tmp_path, databases=[evidence, runtime]
    )

    assert set(result.databases) == {"snapback_observations.db", "intents.db"}
    assert result.manifest.row_counts["snapback_observations.db"]["t"] == 5
    assert result.manifest.row_counts["intents.db"]["intents"] == 2


def test_the_release_identity_travels_with_the_bytes(tmp_path, sources):
    evidence, _ = sources

    result = create_full_backup(
        backup_root=tmp_path / "backups", root=tmp_path, databases=[evidence]
    )

    # Asking the repository later gives the CURRENT build, which is the wrong
    # answer about a backup taken months ago.
    stored = json.loads((result.directory / "release.json").read_text())
    assert stored["lane_count"] == 10
    assert "release.json" in result.manifest.file_checksums


def test_a_declared_database_that_does_not_exist_is_reported_not_silent(tmp_path, sources):
    evidence, _ = sources

    result = create_full_backup(
        backup_root=tmp_path / "backups",
        root=tmp_path,
        databases=[evidence, tmp_path / "never_created.db"],
    )

    assert result.databases == ("snapback_observations.db",)
    assert any("never_created.db" in name for name in result.missing)



def test_explicit_same_basename_databases_cannot_overwrite_each_other(tmp_path):
    first = tmp_path / "primary" / "sterling_paper.db"
    second = tmp_path / "secondary" / "sterling_paper.db"
    first.parent.mkdir()
    second.parent.mkdir()
    _make_db(first, rows=2)
    _make_db(second, rows=7)

    destination = tmp_path / "backups"
    with pytest.raises(BackupIntegrityError, match="basename collision"):
        create_full_backup(
            backup_root=destination,
            root=tmp_path,
            databases=[first, second],
        )
    assert not destination.exists(), "invalid backup must not create a partial directory"


def test_explicit_repeated_database_path_is_snapshotted_once(tmp_path):
    path = _make_db(tmp_path / "sterling_paper.db", rows=3)

    result = create_full_backup(
        backup_root=tmp_path / "backups",
        root=tmp_path,
        databases=[path, path],
    )

    assert result.databases == ("sterling_paper.db",)
    assert result.manifest.row_counts["sterling_paper.db"]["t"] == 3



def test_a_backup_with_nothing_to_back_up_fails_loudly(tmp_path):
    with pytest.raises(FileNotFoundError):
        create_full_backup(
            backup_root=tmp_path / "backups",
            root=tmp_path,
            databases=[tmp_path / "absent.db"],
        )


def test_wal_sidecars_never_reach_the_manifest(tmp_path, sources):
    # A -wal file describes a connection that no longer exists. Checksumming it
    # would fail every later restore-check for no real reason.
    evidence, _ = sources
    result = create_full_backup(
        backup_root=tmp_path / "backups", root=tmp_path, databases=[evidence]
    )

    assert not [
        name
        for name in result.manifest.file_checksums
        if name.endswith(("-wal", "-shm", "-journal"))
    ]
    assert not list(result.directory.glob("*-wal"))


def test_a_full_backup_proves_its_own_restore(tmp_path, sources, monkeypatch):
    evidence, runtime = sources
    # Model a host that has every artifact required before live capital. The
    # release manifest is deliberately excluded: the runbook proves backup
    # before manifest freeze.
    safe = tmp_path / "data" / "safe_mode.json"
    lanes = tmp_path / "data" / "manifests" / "strategies"
    safe.parent.mkdir(parents=True, exist_ok=True)
    lanes.mkdir(parents=True, exist_ok=True)
    safe.write_text("{}", encoding="utf-8")
    (lanes / "snapback.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("STERLING_OBSERVATIONS_DB_PATH", str(evidence))
    monkeypatch.setenv("STERLING_DB_PATH", str(runtime))

    result = create_full_backup(
        backup_root=tmp_path / "backups", root=tmp_path, databases=[evidence, runtime]
    )

    proof = prove_restore(result.directory)

    assert proof.passed, proof.failures
    assert set(proof.restored_row_counts) == {"snapback_observations.db", "intents.db"}


def test_a_snapshot_is_consistent_while_the_source_is_open(tmp_path):
    source = _make_db(tmp_path / "live.db", rows=4)
    holder = sqlite3.connect(str(source))
    holder.execute("PRAGMA journal_mode=WAL")
    holder.execute("INSERT INTO t (id) VALUES (99)")
    holder.commit()

    try:
        checksum = snapshot_database(source, tmp_path / "copy.db")
    finally:
        holder.close()

    assert len(checksum) == 64
    conn = sqlite3.connect(str(tmp_path / "copy.db"))
    try:
        assert conn.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 5
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        conn.close()



def test_missing_source_snapshot_does_not_create_empty_database(tmp_path):
    missing = tmp_path / "missing-source.db"
    destination = tmp_path / "snapshot.db"

    with pytest.raises(FileNotFoundError, match="source database does not exist"):
        snapshot_database(missing, destination)

    assert not missing.exists()
    assert not destination.exists()


def test_snapshot_handles_source_path_containing_question_mark(tmp_path):
    source = _make_db(tmp_path / "observations?safe.db", rows=3)
    destination = tmp_path / "safe-snapshot.db"

    snapshot_database(source, destination)

    with sqlite3.connect(destination) as conn:
        assert conn.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 3



class TestDeclaredDatabases:
    def test_runtime_observations_path_is_the_default_evidence_database(
        self, tmp_path, monkeypatch
    ):
        observations = _make_db(tmp_path / "prospective_runtime.db")
        monkeypatch.delenv("STERLING_EVIDENCE_DB", raising=False)
        monkeypatch.delenv("STERLING_BACKUP_DATABASES", raising=False)
        monkeypatch.setenv("STERLING_OBSERVATIONS_DB_PATH", str(observations))

        declared = declared_databases(tmp_path)

        assert observations in declared

    def test_explicit_evidence_override_wins_over_runtime_alias(
        self, tmp_path, monkeypatch
    ):
        observations = _make_db(tmp_path / "prospective_runtime.db")
        override = _make_db(tmp_path / "override.db")
        monkeypatch.delenv("STERLING_BACKUP_DATABASES", raising=False)
        monkeypatch.setenv("STERLING_OBSERVATIONS_DB_PATH", str(observations))
        monkeypatch.setenv("STERLING_EVIDENCE_DB", str(override))

        declared = declared_databases(tmp_path)

        assert override in declared
        assert observations not in declared

    def test_persistent_layout_honours_runtime_database_environment(
        self, tmp_path, monkeypatch
    ):
        data = tmp_path / "data"
        data.mkdir()
        evidence = _make_db(data / "snapback_observations.db")
        runtime = _make_db(data / "sterling_paper.db")
        intent = _make_db(data / "intents.db")

        monkeypatch.delenv("STERLING_BACKUP_DATABASES", raising=False)
        monkeypatch.delenv("STERLING_EVIDENCE_DB", raising=False)
        monkeypatch.setenv("STERLING_STATE_ROOT", str(tmp_path))
        monkeypatch.setenv("STERLING_OBSERVATIONS_DB_PATH", str(evidence))
        monkeypatch.setenv("STERLING_DB_PATH", str(runtime))
        monkeypatch.setenv("STERLING_INTENT_DB", str(intent))

        declared = declared_databases(tmp_path)

        assert evidence in declared
        assert runtime in declared
        assert intent in declared
        assert tmp_path / "backend/sterling_paper.db" not in declared

    def test_the_environment_can_name_the_set(self, tmp_path, monkeypatch):
        a = _make_db(tmp_path / "a.db")
        b = _make_db(tmp_path / "b.db")
        monkeypatch.setenv("STERLING_BACKUP_DATABASES", f"{a}:{b}")

        assert [p.name for p in declared_databases(tmp_path)] == ["a.db", "b.db"]

    def test_absent_paths_stay_declared_so_the_backup_can_report_them(
        self, tmp_path, monkeypatch
    ):
        a = _make_db(tmp_path / "a.db")
        monkeypatch.setenv("STERLING_BACKUP_DATABASES", f"{a}:{tmp_path / 'gone.db'}")

        # Dropping a missing database would silently shrink the backup set;
        # keeping it lets create_full_backup say exactly what it could not copy.
        assert [p.name for p in declared_databases(tmp_path)] == ["a.db", "gone.db"]

    def test_the_same_database_is_never_listed_twice(self, tmp_path, monkeypatch):
        a = _make_db(tmp_path / "a.db")
        monkeypatch.setenv("STERLING_BACKUP_DATABASES", f"{a}:{a}")

        assert len(declared_databases(tmp_path)) == 1
