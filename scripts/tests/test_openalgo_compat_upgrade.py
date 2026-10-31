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


def synthetic_db(
    tmp_path: Path, *, missing: str = "",
    altered_column: tuple[str, str, str] | None = None,
) -> Path:
    path = tmp_path / "operator-src" / "sterling.db"
    path.parent.mkdir(parents=True)
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        for table in sorted(migration.REQUIRED - {missing}):
            definitions = []
            for name, declared_type in migration.REQUIRED_COLUMNS[table].items():
                if altered_column and (table, name) == altered_column[:2]:
                    if altered_column[2] == "MISSING":
                        continue
                    declared_type = altered_column[2]
                definitions.append(
                    f'"{name}" {declared_type}' + (" PRIMARY KEY" if name == "id" else "")
                )
            if "id" not in migration.REQUIRED_COLUMNS[table]:
                definitions.append('"id" INTEGER PRIMARY KEY')
            definitions.append('"synthetic_payload" TEXT NOT NULL')
            conn.execute(f'CREATE TABLE "{table}" (' + ", ".join(definitions) + ")")
            # Synthetic sensitive content must not appear in the inventory JSON.
            conn.execute(
                f'INSERT INTO "{table}" (id,synthetic_payload) VALUES (1, ?)',
                ("synthetic-secret-should-not-appear",),
            )
        conn.execute("PRAGMA user_version=7")
        conn.execute(
            'INSERT INTO "positions"(id,synthetic_payload) VALUES(2,?)',
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
        assert conn.execute("SELECT synthetic_payload FROM positions WHERE id=2").fetchone()[0] == "WAL-committed-last-row"
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


@pytest.mark.parametrize("altered", [
    ("kite_order_intents", "quantity", "TEXT"),
    ("kite_order_intents", "projection_pending", "MISSING"),
    ("kite_inventory", "net_quantity", "TEXT"),
    ("execution_control", "revision", "MISSING"),
    ("kite_accounts", "access_token_enc", "MISSING"),
])
def test_incompatible_critical_columns_prevent_copy_and_are_reported(tmp_path, altered):
    source = synthetic_db(tmp_path, altered_column=altered)
    report = migration.inventory(source)
    assert report["status"] == "BLOCKED_INCOMPLETE_SCHEMA"
    assert altered[1] in report["column_mismatches"][altered[0]]
    target = tmp_path / "isolated" / "refused.db"
    target.parent.mkdir()
    with pytest.raises(ValueError, match="incomplete or incompatible"):
        migration.migrate_copy(source, target)
    assert not target.exists()


def test_same_row_count_corruption_in_staged_migration_is_detected(tmp_path, monkeypatch):
    """Copy may have identical row counts yet altered broker/risk content."""
    source = synthetic_db(tmp_path)
    dest = tmp_path / "isolated" / "no-corrupt-copy.db"
    dest.parent.mkdir()
    before = source.read_bytes()
    real_ledger = migration._ledger_status

    def corrupt_after_ledger(conn, original_schema_hash):
        status = real_ledger(conn, original_schema_hash)
        conn.execute(
            "UPDATE positions SET synthetic_payload='tampered in staging' WHERE id='2'"
        )
        return status

    monkeypatch.setattr(migration, "_ledger_status", corrupt_after_ledger)
    with pytest.raises(ValueError, match="operational table content"):
        migration.migrate_copy(source, dest)
    assert not dest.exists()
    assert not list(dest.parent.iterdir())
    assert source.read_bytes() == before


def test_live_named_or_configured_source_is_refused_even_with_new_destination(tmp_path, monkeypatch):
    source = synthetic_db(tmp_path)
    dest = tmp_path / "isolated" / "copy.db"
    dest.parent.mkdir()
    monkeypatch.setenv("STERLING_DB_PATH", str(source))
    with pytest.raises(PermissionError, match="offline source snapshot"):
        migration.migrate_copy(source, dest)
    monkeypatch.delenv("STERLING_DB_PATH")
    named_live = source.with_name("sterling_paper.db")
    source.rename(named_live)
    with pytest.raises(PermissionError, match="offline source snapshot"):
        migration.migrate_copy(named_live, dest)
    assert not dest.exists()


def test_full_parity_audit_reads_without_modifying_or_exporting_private_rows(tmp_path):
    source = synthetic_db(tmp_path)
    before = source.read_bytes()
    report = migration.audit_full_migration_preflight(source)
    assert report["status"] == "BLOCKED_FULL_MIGRATION"
    assert report["mode"] == "READ_ONLY_FULL_PARITY_PREFLIGHT"
    assert report["missing_critical_tables"]
    assert "users" in report["missing_critical_tables"]
    assert "kite_order_intents" in report["missing_safety_unique_keys"]
    assert ["tag"] in report["missing_safety_unique_keys"]["kite_order_intents"]
    assert report["postgresql_operational_schema"] == "NOT_IMPLEMENTED"
    assert report["independent_approval"] == "NOT_PROVIDED"
    assert report["source_modified"] is False
    assert "synthetic-secret" not in json.dumps(report)
    assert source.read_bytes() == before


def test_full_parity_audit_recognizes_global_unique_key_but_not_partial(tmp_path):
    source = synthetic_db(tmp_path)
    with sqlite3.connect(source) as conn:
        conn.execute(
            "CREATE UNIQUE INDEX ix_partial_tag ON kite_order_intents(tag) "
            "WHERE tag IS NOT NULL"
        )
    report = migration.audit_full_migration_preflight(source)
    assert ["tag"] in report["missing_safety_unique_keys"]["kite_order_intents"]
    with sqlite3.connect(source) as conn:
        conn.execute("CREATE UNIQUE INDEX ux_exact_tag ON kite_order_intents(tag)")
    after = migration.audit_full_migration_preflight(source)
    assert "kite_order_intents" not in after["missing_safety_unique_keys"] or (
        ["tag"] not in after["missing_safety_unique_keys"]["kite_order_intents"]
    )
    assert after["status"] == "BLOCKED_FULL_MIGRATION"


def test_full_parity_audit_detects_missing_primary_key_after_same_row_count(tmp_path):
    source = synthetic_db(tmp_path)
    with sqlite3.connect(source) as conn:
        conn.execute("CREATE TABLE weak_control(scope TEXT, uid TEXT, account_id TEXT)")
        conn.execute("INSERT INTO weak_control VALUES('global','u','a')")
        conn.execute("DROP TABLE execution_control")
        conn.execute("ALTER TABLE weak_control RENAME TO execution_control")
    report = migration.audit_full_migration_preflight(source)
    assert ["scope", "uid", "account_id"] in report[
        "missing_safety_unique_keys"
    ]["execution_control"]
    assert report["tables_scanned"] >= len(migration.REQUIRED)
    assert report["status"] == "BLOCKED_FULL_MIGRATION"


def test_full_parity_cli_emits_redacted_json_and_nonzero_blocked_exit(tmp_path):
    source = synthetic_db(tmp_path)
    command = [sys.executable, "-m", "upgrade.sterling_openalgo",
               "--source", str(source), "--audit-full-parity"]
    proc = subprocess.run(command, capture_output=True, text=True)
    assert proc.returncode == 2
    report = json.loads(proc.stdout)
    assert report["status"] == "BLOCKED_FULL_MIGRATION"
    assert report["broker_connection"] == "NOT_ATTEMPTED"
    assert "synthetic-secret" not in proc.stdout
    assert not proc.stderr


def test_full_parity_cli_never_combines_read_only_report_with_write(tmp_path):
    source = synthetic_db(tmp_path)
    destination = tmp_path / "other" / "copy.db"
    proc = subprocess.run(
        [sys.executable, "-m", "upgrade.sterling_openalgo",
         "--source", str(source), "--destination", str(destination),
         "--apply-to-copy", "--audit-full-parity"],
        capture_output=True, text=True,
    )
    assert proc.returncode == 2
    assert "cannot be combined" in proc.stderr
    assert not destination.exists()


def test_full_parity_audit_explicitly_flags_nullable_sqlite_text_primary_keys(tmp_path):
    source = synthetic_db(tmp_path)
    result = migration.audit_full_migration_preflight(source)
    # The synthetic operational positions schema deliberately declares
    # id TEXT PRIMARY KEY without NOT NULL. SQLite rowid tables admit NULL.
    assert "id" in result["nullable_identity_columns"]["positions"]
    assert result["status"] == "BLOCKED_FULL_MIGRATION"


def test_sqlite_nullable_unique_index_does_not_establish_pg_identity_parity(tmp_path):
    source = synthetic_db(tmp_path)
    with sqlite3.connect(source) as conn:
        conn.execute("CREATE UNIQUE INDEX ux_nullable_tag ON kite_order_intents(tag)")
    result = migration.audit_full_migration_preflight(source)
    assert "tag" in result["nullable_identity_columns"]["kite_order_intents"]
    assert result["status"] == "BLOCKED_FULL_MIGRATION"


def test_sqlite_identity_nullability_distinguishes_rowid_alias_and_without_rowid():
    with sqlite3.connect(":memory:") as conn:
        conn.execute("CREATE TABLE weak_identity (id TEXT PRIMARY KEY)")
        conn.execute("CREATE TABLE rowid_identity (id INTEGER PRIMARY KEY)")
        conn.execute("CREATE TABLE strong_identity (id TEXT PRIMARY KEY) WITHOUT ROWID")
        assert migration._nullable_identity_columns(
            conn, "weak_identity", (("id",),)
        ) == ["id"]
        assert migration._nullable_identity_columns(
            conn, "rowid_identity", (("id",),)
        ) == []
        assert migration._nullable_identity_columns(
            conn, "strong_identity", (("id",),)
        ) == []
        # Confirm this is not only a PRAGMA inference: SQLite permits duplicate
        # NULL TEXT primary keys on an ordinary rowid table.
        conn.execute("INSERT INTO weak_identity(id) VALUES(NULL)")
        conn.execute("INSERT INTO weak_identity(id) VALUES(NULL)")
        assert conn.execute(
            "SELECT COUNT(*) FROM weak_identity WHERE id IS NULL"
        ).fetchone()[0] == 2


def test_full_parity_tracks_auth_policy_and_exact_once_pnl_identity_contracts(tmp_path):
    source = synthetic_db(tmp_path)
    report = migration.audit_full_migration_preflight(source)
    critical = set(report["critical_tables"])
    assert {
        "platform_admin",
        "account_policy_revisions",
        "user_feature_grants",
        "user_workspace_visibility",
        "auth_audit_events",
        "auth_event_outbox",
        "account_schema_versions",
        "kite_realized_pnl_events",
    } <= critical
    assert "kite_realized_pnl_events" in report["missing_critical_tables"]
    # "Unmapped" means not covered by FULL_PARITY_KEYS, not ALL tables.
    assert not (set(report["unmapped_operational_tables"]) & critical)
    assert not (set(report["unmapped_operational_tables"]) & set(report["critical_tables"]))
    assert report["status"] == "BLOCKED_FULL_MIGRATION"


def test_full_parity_refuses_auth_grants_without_compound_identity_key(tmp_path):
    source = synthetic_db(tmp_path)
    with sqlite3.connect(source) as conn:
        conn.execute("""CREATE TABLE user_feature_grants (
            user_id TEXT NOT NULL, feature TEXT NOT NULL,
            enabled INTEGER NOT NULL, actor_id TEXT NOT NULL,
            updated_at_ms INTEGER NOT NULL
        )""")
        conn.execute("INSERT INTO user_feature_grants VALUES('u','kite',1,'owner',1)")
    result = migration.audit_full_migration_preflight(source)
    assert ["user_id", "feature"] in result["missing_safety_unique_keys"]["user_feature_grants"]
    assert "user_feature_grants" not in result["critical_column_mismatches"]
    assert result["status"] == "BLOCKED_FULL_MIGRATION"


def test_full_parity_refuses_pnl_dedup_loss_and_wrong_numeric_type(tmp_path):
    source = synthetic_db(tmp_path)
    with sqlite3.connect(source) as conn:
        conn.execute("""CREATE TABLE kite_realized_pnl_events (
            uid TEXT NOT NULL, event_key TEXT NOT NULL,
            day_iso TEXT NOT NULL, pnl TEXT NOT NULL,
            created_ms INTEGER NOT NULL
        )""")
        conn.execute("INSERT INTO kite_realized_pnl_events VALUES('u','trade1','2026-10-31','999',1)")
    result = migration.audit_full_migration_preflight(source)
    assert ["uid", "event_key"] in result["missing_safety_unique_keys"]["kite_realized_pnl_events"]
    assert result["critical_column_mismatches"]["kite_realized_pnl_events"]["pnl"] == {
        "expected": "REAL", "actual": "TEXT"
    }
    assert result["status"] == "BLOCKED_FULL_MIGRATION"


def test_full_parity_readonly_orphan_counts_without_leaking_principal_values(tmp_path):
    source = synthetic_db(tmp_path)
    with sqlite3.connect(source) as conn:
        conn.execute("""CREATE TABLE users (
            id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL, role TEXT NOT NULL,
            is_active INTEGER NOT NULL, token_version INTEGER NOT NULL
        )""")
        conn.execute(
            "INSERT INTO users VALUES('known-user','public-name','private-secret','user',1,1)"
        )
        conn.execute(
            "UPDATE kite_accounts SET user_id='orphan-secret-id'"
        )
    before = source.read_bytes()
    first = migration.audit_full_migration_preflight(source)
    assert first["orphan_identity_row_counts"]["kite_accounts"] == 1
    assert "orphan-secret-id" not in json.dumps(first)
    assert "private-secret" not in json.dumps(first)
    assert source.read_bytes() == before
    with sqlite3.connect(source) as conn:
        conn.execute(
            "UPDATE kite_accounts SET user_id='known-user'"
        )
    second = migration.audit_full_migration_preflight(source)
    assert "kite_accounts" not in second["orphan_identity_row_counts"]
    assert second["source_modified"] is False


def test_full_parity_auth_policy_integer_slot_and_unique_designation(tmp_path):
    source = synthetic_db(tmp_path)
    with sqlite3.connect(source) as conn:
        conn.execute("""CREATE TABLE platform_admin (
            slot INTEGER PRIMARY KEY,
            user_id TEXT NOT NULL UNIQUE
        )""")
        conn.execute("INSERT INTO platform_admin VALUES(1,'u')")
    report = migration.audit_full_migration_preflight(source)
    assert "platform_admin" not in report["missing_safety_unique_keys"]
    assert "platform_admin" not in report["critical_column_mismatches"]
    assert "platform_admin" not in report["nullable_identity_columns"]
    assert report["status"] == "BLOCKED_FULL_MIGRATION"
