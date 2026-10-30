"""Isolated OpenAlgo-pattern compatibility migration: no broker or live DB."""
from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys

import pytest

from upgrade import sterling_openalgo as migration


def synthetic_db(tmp_path: Path, *, missing: str = "") -> Path:
    path = tmp_path / "operator-src" / "sterling.db"
    path.parent.mkdir(parents=True)
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        for table in sorted(migration.REQUIRED - {missing}):
            conn.execute(
                f'CREATE TABLE "{table}" (id INTEGER PRIMARY KEY, value TEXT NOT NULL)'
            )
            # Synthetic sensitive content must not appear in the inventory JSON.
            conn.execute(
                f'INSERT INTO "{table}" (id,value) VALUES (1, ?)',
                ("synthetic-secret-should-not-appear",),
            )
        conn.execute("PRAGMA user_version=7")
        conn.execute(
            'INSERT INTO "positions"(id,value) VALUES(2,?)',
            ("WAL-committed-last-row",),
        ) if missing != "positions" else None
    return path


def test_inventory_is_read_only_and_redacts_values(tmp_path):
    source = synthetic_db(tmp_path)
    before = source.stat().st_mtime_ns
    report = migration.inventory(source)
    assert report["status"] == "READY_FOR_COPY_ONLY"
    assert report["sqlite_user_version"] == 7
    assert report["domain_mapping"]["account_and_sessions"]["missing"] == ["users"]
    assert report["table_counts"]["positions"] == 2
    assert report["broker_connection"] == "NOT_ATTEMPTED"
    assert "synthetic-secret" not in json.dumps(report)
    assert source.stat().st_mtime_ns == before
    with sqlite3.connect(source) as conn:
        assert "sterling_upgrade_ledger" not in migration._counts(conn)


def test_atomic_copy_preserves_every_original_table_and_wal_row(tmp_path):
    source = synthetic_db(tmp_path)
    original_bytes = source.read_bytes()
    target = tmp_path / "isolated" / "migrated.db"
    target.parent.mkdir()
    result = migration.migrate_copy(source, target)
    assert result["status"] == "APPLIED_TO_DISPOSABLE_COPY"
    assert result["production_migration"] == "NOT_CERTIFIED"
    assert result["source_modified"] is False
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert source.read_bytes() == original_bytes
    assert migration.inventory(source)["status"] == "READY_FOR_COPY_ONLY"
    with sqlite3.connect(target) as conn:
        assert conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        row = conn.execute(
            "SELECT version,phase,source_schema_sha256 FROM sterling_upgrade_ledger"
        ).fetchone()
        assert row[0] == migration.VERSION and row[1] == "copy_only"
        assert row[2] == migration.inventory(source)["schema_sha256"]
        assert conn.execute("SELECT value FROM positions WHERE id=2").fetchone()[0] == "WAL-committed-last-row"
    assert not list(target.parent.glob(".sterling-compat-*"))


def test_second_copy_is_idempotent_and_never_duplicates_ledger(tmp_path):
    source = synthetic_db(tmp_path)
    one = tmp_path / "isolated" / "one.db"
    two = tmp_path / "another-isolated" / "two.db"
    one.parent.mkdir()
    two.parent.mkdir()
    assert migration.migrate_copy(source, one)["status"] == "APPLIED_TO_DISPOSABLE_COPY"
    result = migration.migrate_copy(one, two)
    assert result["status"] == "ALREADY_APPLIED_ON_COPY"
    with sqlite3.connect(two) as conn:
        assert conn.execute("SELECT COUNT(*) FROM sterling_upgrade_ledger").fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM positions").fetchone()[0] == 2


def test_injected_migration_interruption_rolls_back_and_cleans_temp(tmp_path):
    source = synthetic_db(tmp_path)
    target = tmp_path / "isolated" / "interrupted.db"
    target.parent.mkdir()
    original = source.read_bytes()
    with pytest.raises(RuntimeError, match="synthetic interrupted"):
        migration.migrate_copy(source, target, fail_after_ledger=True)
    assert not target.exists()
    assert not list(target.parent.iterdir())
    assert source.read_bytes() == original


def test_missing_critical_table_blocks_copy_without_creating_target(tmp_path):
    source = synthetic_db(tmp_path, missing="kite_order_intents")
    target = tmp_path / "isolated" / "out.db"
    target.parent.mkdir()
    assert migration.inventory(source)["status"] == "BLOCKED_INCOMPLETE_SCHEMA"
    with pytest.raises(ValueError, match="incomplete"):
        migration.migrate_copy(source, target)
    assert not target.exists()


def test_source_and_destination_guards_never_overwrite(tmp_path):
    source = synthetic_db(tmp_path)
    with pytest.raises(ValueError, match="isolated"):
        migration.migrate_copy(source, source)
    with pytest.raises(ValueError, match="different directory"):
        migration.migrate_copy(source, source.parent / "other.db")
    target = tmp_path / "isolated" / "existing.db"
    target.parent.mkdir()
    target.write_text("DO NOT TOUCH", encoding="utf-8")
    with pytest.raises(ValueError, match="isolated"):
        migration.migrate_copy(source, target)
    assert target.read_text() == "DO NOT TOUCH"
    link = tmp_path / "link.db"
    link.symlink_to(source)
    with pytest.raises(ValueError, match="non-symlink"):
        migration.inventory(link)


def test_corrupted_database_refused_without_output(tmp_path):
    source = tmp_path / "corrupt.db"
    source.write_bytes(b"not a sqlite database")
    dest = tmp_path / "isolated" / "bad.db"
    dest.parent.mkdir()
    with pytest.raises(sqlite3.DatabaseError):
        migration.inventory(source)
    with pytest.raises(sqlite3.DatabaseError):
        migration.migrate_copy(source, dest)
    assert not dest.exists()


def test_unknown_preexisting_ledger_is_not_adopted(tmp_path):
    source = synthetic_db(tmp_path)
    with sqlite3.connect(source) as conn:
        conn.execute(
            "CREATE TABLE sterling_upgrade_ledger("
            "version TEXT PRIMARY KEY, phase TEXT, source_schema_sha256 TEXT)"
        )
        conn.execute(
            "INSERT INTO sterling_upgrade_ledger VALUES('future-unknown','copy_only',?)",
            ("a" * 64,),
        )
    dest = tmp_path / "isolated" / "no.db"
    dest.parent.mkdir()
    with pytest.raises(ValueError, match="Unknown or conflicting"):
        migration.migrate_copy(source, dest)
    assert not dest.exists()


def test_cli_defaults_dry_run_and_never_creates_copy(tmp_path):
    source = synthetic_db(tmp_path)
    target = tmp_path / "isolated" / "not-created.db"
    cmd = [sys.executable, "-m", "upgrade.sterling_openalgo",
           "--source", str(source), "--destination", str(target)]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    out = json.loads(result.stdout)
    assert out["mode"] == "READ_ONLY_INVENTORY"
    assert out["dry_run"] and not target.exists()
    assert "synthetic-secret" not in result.stdout


def test_existing_version_ledger_rejects_schema_drift(tmp_path):
    source = synthetic_db(tmp_path)
    copy = tmp_path / "isolated" / "first.db"
    next_copy = tmp_path / "isolated-next" / "second.db"
    copy.parent.mkdir()
    next_copy.parent.mkdir()
    migration.migrate_copy(source, copy)
    with sqlite3.connect(copy) as c:
        c.execute("CREATE TABLE unexpected_new_table(value INTEGER)")
    with pytest.raises(ValueError, match="fingerprint"):
        migration.migrate_copy(copy, next_copy)
    assert not next_copy.exists()


def test_copy_has_no_wal_sidecar_and_can_be_reopened_independently(tmp_path):
    source = synthetic_db(tmp_path)
    dest = tmp_path / "isolated" / "portable.db"
    dest.parent.mkdir()
    migration.migrate_copy(source, dest)
    assert not (dest.parent / "portable.db-wal").exists()
    assert not (dest.parent / "portable.db-shm").exists()
    with sqlite3.connect(dest) as c:
        assert c.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        assert c.execute("SELECT COUNT(*) FROM positions").fetchone()[0] == 2
