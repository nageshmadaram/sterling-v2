#!/usr/bin/env python3
"""Sterling/OpenAlgo-pattern compatibility inventory and COPY-ONLY schema ledger.

This is an additive, offline staging phase. It NEVER changes the source, real
broker sessions, positions or risk controls; it does NOT claim exact OpenAlgo
table parity, a PostgreSQL migration, or authorization to trade.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import tempfile
from urllib.parse import quote

VERSION = "sterling-openalgo-compat-v1"
LEDGER = "sterling_upgrade_ledger"
# Known operational Sterling tables, not invented OpenAlgo schema names.
DOMAINS = {
    "account_and_sessions": ("kite_accounts", "users"),
    "order_and_execution": ("kite_order_intents", "kite_order_observations",
                            "kite_fill_ledger", "kite_execution_increments"),
    "positions_and_risk": ("positions", "kite_inventory", "execution_control"),
    "settings": ("system_config",),
}
# These six tables are created by the operational db.init() baseline. The users
# table is created separately by user_store._init_table(), so absence is an
# explicit blocker for full auth parity, not proof that no users exist.
REQUIRED = frozenset({
    "kite_accounts", "kite_order_intents", "kite_fill_ledger",
    "kite_inventory", "execution_control", "positions", "system_config",
})
# Exact SQLite column declarations inspected in Sterling db.py. Additional
# columns are allowed; missing/wrong-affinity critical fields are blockers.
# This is NOT an assertion of OpenAlgo physical schema equivalence.
REQUIRED_COLUMNS = {
    "positions": {
        "id": "TEXT", "status": "TEXT", "data": "TEXT",
        "entry_ts": "INTEGER", "updated_ts": "INTEGER",
    },
    "kite_accounts": {
        "id": "TEXT", "user_id": "TEXT", "is_paper": "INTEGER",
        "is_active": "INTEGER", "access_token_enc": "TEXT",
    },
    "kite_order_intents": {
        "intent_key": "TEXT", "uid": "TEXT", "account_id": "TEXT",
        "strategy_id": "TEXT", "signal_id": "TEXT", "side": "TEXT",
        "quantity": "INTEGER", "tag": "TEXT", "state": "TEXT",
        "projection_pending": "INTEGER", "projection_version": "INTEGER",
    },
    "kite_fill_ledger": {
        "account_id": "TEXT", "order_id": "TEXT", "trade_id": "TEXT",
        "side": "TEXT", "quantity": "INTEGER", "price": "REAL",
    },
    "kite_inventory": {
        "account_id": "TEXT", "uid": "TEXT", "symbol": "TEXT",
        "net_quantity": "INTEGER", "reconciliation_required": "INTEGER",
    },
    "execution_control": {
        "scope": "TEXT", "uid": "TEXT", "account_id": "TEXT",
        "operator_state": "TEXT", "recovery_state": "TEXT",
        "revision": "INTEGER", "initialized": "INTEGER",
    },
    "system_config": {"key": "TEXT", "value": "TEXT"},
    "users": {
        "id": "TEXT", "username": "TEXT", "password_hash": "TEXT",
        "role": "TEXT", "token_version": "INTEGER",
    },
}
LEDGER_COLUMNS = ("version", "phase", "source_schema_sha256")

# Safety-significant SQLite keys observed in the operational Sterling baseline.
# These are NOT inferred OpenAlgo table definitions or claims of PostgreSQL parity.
# A missing UNIQUE/PK key would permit duplicates after a future data backfill.
FULL_PARITY_KEYS: dict[str, tuple[tuple[str, ...], ...]] = {
    "users": (("id",), ("username",)),
    "positions": (("id",),),
    "kite_accounts": (("id",),),
    "kite_order_intents": (("intent_key",), ("tag",)),
    "kite_order_observations": (("id",), ("intent_key", "fingerprint")),
    "kite_fill_ledger": (("account_id", "order_id", "trade_id"),),
    "kite_execution_increments": (
        ("id",), ("account_id", "order_id", "cumulative_quantity"),
    ),
    "kite_inventory": (("account_id", "uid", "symbol"),),
    "kite_execution_leases": (("scope", "account_id", "uid", "symbol"),),
    "kite_execution_conflicts": (("id",),),
    "execution_control": (("scope", "uid", "account_id"),),
    "system_config": (("key",),),
}



def _source(path: Path) -> Path:
    if path.is_symlink() or not path.is_file():
        raise ValueError("Source must be an existing regular, non-symlink SQLite file")
    real = path.resolve(strict=True)
    if not stat.S_ISREG(real.stat().st_mode):
        raise ValueError("Source must be a regular database")
    return real


def _read_only(path: Path) -> sqlite3.Connection:
    uri = "file:" + quote(str(path), safe="/") + "?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=10)
    conn.execute("PRAGMA query_only=ON")
    return conn


def _schema(conn: sqlite3.Connection, *, exclude_ledger: bool = False) -> dict:
    query = (
        "SELECT type, name, tbl_name, sql FROM sqlite_master "
        "WHERE type IN ('table','index','view','trigger') AND name NOT LIKE 'sqlite_%'"
    )
    params = ()
    if exclude_ledger:
        query += " AND name<>?"
        params = (LEDGER,)
    objects = conn.execute(query + " ORDER BY type, name", params).fetchall()
    return {
        "schema_sha256": hashlib.sha256(
            json.dumps(objects, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "objects": {name: {"type": kind, "owner": owner} for kind, name, owner, _ in objects},
    }


def _counts(conn: sqlite3.Connection) -> dict[str, int]:
    names = [
        name for (name,) in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    ]
    return {
        name: int(conn.execute(
            'SELECT COUNT(*) FROM "' + name.replace('"', '""') + '"'
        ).fetchone()[0])
        for name in sorted(names)
    }


def _content_fingerprints(conn: sqlite3.Connection, tables: set[str]) -> dict[str, str]:
    """Private ordered content digests: prove equal data, not just equal counts.

    Never return these digests in user-facing inventory, logs or API responses:
    low-entropy private values may be susceptible to offline guessing.
    ORDER BY all columns supports tables without rowids or primary keys.
    """
    digests: dict[str, str] = {}
    for table in sorted(tables):
        if table == LEDGER:
            continue
        quoted = '"' + table.replace('"', '""') + '"'
        columns = [
            str(row[1]) for row in conn.execute(f"PRAGMA table_info({quoted})")
        ]
        if not columns:
            raise ValueError(f"Table schema unavailable: {table}")
        ordering = ", ".join(
            '"' + name.replace('"', '""') + '"'
            for name in columns
        )
        digest = hashlib.sha256()
        cursor = conn.execute(
            f"SELECT * FROM {quoted} ORDER BY {ordering}"
        )
        while True:
            batch = cursor.fetchmany(256)
            if not batch:
                break
            for row in batch:
                encoded = repr(tuple(row)).encode("utf-8", errors="backslashreplace")
                digest.update(len(encoded).to_bytes(8, "big"))
                digest.update(encoded)
        digests[table] = digest.hexdigest()
    return digests


def _check(conn: sqlite3.Connection) -> None:
    if conn.execute("PRAGMA quick_check").fetchone() != ("ok",):
        raise ValueError("SQLite integrity check failed; do not migrate")
    if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise ValueError("Foreign key constraint violation; do not migrate")


def _column_gaps(conn: sqlite3.Connection, tables: set[str]) -> dict:
    gaps = {}
    for table, expected in REQUIRED_COLUMNS.items():
        if table not in tables:
            continue
        actual = {
            str(row[1]): str(row[2]).strip().upper()
            for row in conn.execute(
                'PRAGMA table_info("' + table.replace('"', '""') + '")'
            )
        }
        mismatches = {
            name: {"expected": kind, "actual": actual.get(name, "MISSING")}
            for name, kind in expected.items()
            if actual.get(name) != kind
        }
        if mismatches:
            gaps[table] = mismatches
    return gaps


def inventory(path: Path) -> dict:
    """Only schema metadata and row COUNTS. Never export values or secrets."""
    source = _source(Path(path))
    with closing(_read_only(source)) as conn:
        _check(conn)
        schema = _schema(conn)
        counts = _counts(conn)
        tables = set(counts)
        missing = sorted(REQUIRED - tables)
        column_gaps = _column_gaps(conn, tables)
        domains = {
            name: {
                "present": [t for t in expected if t in tables],
                "missing": [t for t in expected if t not in tables],
            }
            for name, expected in DOMAINS.items()
        }
        return {
            "status": ("BLOCKED_INCOMPLETE_SCHEMA"
                       if missing or column_gaps else "READY_FOR_COPY_ONLY"),
            "mode": "READ_ONLY_INVENTORY",
            "schema_sha256": schema["schema_sha256"],
            "table_counts": counts,
            "domain_mapping": domains,
            "missing_required": missing,
            "column_mismatches": column_gaps,
            "sqlite_user_version": int(conn.execute("PRAGMA user_version").fetchone()[0]),
            "broker_connection": "NOT_ATTEMPTED",
            "live_trading": "NOT_AUTHORIZED",
            "production_migration": "NOT_CERTIFIED",
        }



def _unique_keys(conn: sqlite3.Connection, table: str) -> set[tuple[str, ...]]:
    """Only unconditional column-only PRIMARY KEY/UNIQUE constraints count.

    A partial or expression index cannot prove global uniqueness; index names
    are not trusted as identity contracts. Never read a table's row values.
    """
    quoted = '"' + table.replace('"', '""') + '"'
    column_rows = list(conn.execute(f"PRAGMA table_info({quoted})"))
    primary = tuple(
        str(row[1]) for row in sorted(
            (r for r in column_rows if int(r[5]) > 0), key=lambda r: int(r[5])
        )
    )
    keys = {tuple(sorted(primary))} if primary else set()
    for index in conn.execute(f"PRAGMA index_list({quoted})"):
        # (seq, name, unique, origin, partial); a partial unique index only
        # protects rows satisfying its predicate, not the entire trading book.
        if not int(index[2]) or (len(index) > 4 and int(index[4])):
            continue
        name = '"' + str(index[1]).replace('"', '""') + '"'
        parts = list(conn.execute(f"PRAGMA index_xinfo({name})"))
        key_columns = [r for r in parts if int(r[5]) == 1]
        if not key_columns or any(int(r[1]) < 0 or r[2] is None for r in key_columns):
            continue
        keys.add(tuple(sorted(str(r[2]) for r in key_columns)))
    return keys


def audit_full_migration_preflight(path: Path) -> dict:
    """Read-only, exhaustive *safety-key* baseline; never authorizes migration.

    This gate intentionally has no passing full-migration state until complete
    PostgreSQL operational models, transactional data transfer, restore, broker
    reconciliation and independent approval are implemented and verified.
    Unlike the backwards-compatible copy-only inventory, it also requires the
    separately initialized user and execution evidence tables.
    """
    source = _source(Path(path))
    with closing(_read_only(source)) as conn:
        conn.execute("BEGIN")  # stable read snapshot across the entire audit
        _check(conn)
        counts = _counts(conn)
        tables = set(counts)
        missing = sorted(set(FULL_PARITY_KEYS) - tables)
        mismatches = _column_gaps(conn, tables)
        missing_keys: dict[str, list[list[str]]] = {}
        for table, expected in FULL_PARITY_KEYS.items():
            if table not in tables:
                continue
            present = _unique_keys(conn, table)
            absent = [
                list(key) for key in expected
                if tuple(sorted(key)) not in present
            ]
            if absent:
                missing_keys[table] = absent
        return {
            "status": "BLOCKED_FULL_MIGRATION",
            "mode": "READ_ONLY_FULL_PARITY_PREFLIGHT",
            "schema_sha256": _schema(conn)["schema_sha256"],
            "sqlite_user_version": int(conn.execute("PRAGMA user_version").fetchone()[0]),
            "tables_scanned": len(tables),
            "critical_tables": sorted(FULL_PARITY_KEYS),
            "missing_critical_tables": missing,
            "critical_column_mismatches": mismatches,
            "missing_safety_unique_keys": missing_keys,
            "unmapped_operational_tables": sorted(tables),
            "postgresql_operational_schema": "NOT_IMPLEMENTED",
            "postgresql_data_migration": "NOT_IMPLEMENTED",
            "restore_and_rollback_evidence": "NOT_VERIFIED",
            "independent_approval": "NOT_PROVIDED",
            "source_modified": False,
            "broker_connection": "NOT_ATTEMPTED",
            "live_trading": "NOT_AUTHORIZED",
            "production_migration": "NOT_CERTIFIED",
        }

def _ledger_status(conn: sqlite3.Connection, baseline_hash: str) -> str:
    tables = set(_counts(conn))
    if LEDGER not in tables:
        conn.execute(
            "CREATE TABLE sterling_upgrade_ledger ("
            "version TEXT PRIMARY KEY, "
            "phase TEXT NOT NULL CHECK (phase='copy_only'), "
            "source_schema_sha256 TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO sterling_upgrade_ledger(version,phase,source_schema_sha256) "
            "VALUES (?,?,?)", (VERSION, "copy_only", baseline_hash)
        )
        return "APPLIED_TO_DISPOSABLE_COPY"
    cols = tuple(
        r[1] for r in conn.execute('PRAGMA table_info("sterling_upgrade_ledger")')
    )
    if cols != LEDGER_COLUMNS:
        raise ValueError("Existing migration ledger schema mismatch")
    rows = conn.execute(
        "SELECT version,phase,source_schema_sha256 FROM sterling_upgrade_ledger"
    ).fetchall()
    if len(rows) != 1 or rows[0][0] != VERSION or rows[0][1] != "copy_only":
        raise ValueError("Unknown or conflicting migration ledger version")
    if (not isinstance(rows[0][2], str) or len(rows[0][2]) != 64
            or rows[0][2] != _schema(conn, exclude_ledger=True)["schema_sha256"]):
        raise ValueError("Existing version ledger fingerprint does not match preserved operational schema")
    return "ALREADY_APPLIED_ON_COPY"


def migrate_copy(source: Path, destination: Path, *, fail_after_ledger: bool = False) -> dict:
    """Read source, transactional additive migration to isolated copy, atomic publish.

    An existing destination is never overwritten. The failure hook exists
    exclusively to prove rollback/cleanup in offline unit tests.
    """
    src = _source(Path(source))
    # Reading a live database into a new file can export broker/session
    # credentials and a non-quiescent risk state. Require a separately created
    # and reviewed offline source snapshot; do not silently use the active
    # operator database just because the destination is disposable.
    configured = (os.environ.get("STERLING_DB_PATH") or "").strip()
    if (src.name.lower() == "sterling_paper.db"
            or (configured and src == Path(configured).resolve())):
        raise PermissionError(
            "Copy-only migration requires an offline source snapshot, "
            "not the active Sterling operational database"
        )
    dest = Path(destination).absolute()
    if dest.exists() or dest.is_symlink() or dest.resolve() == src:
        raise ValueError("Destination must be a new isolated file; no overwrite")
    if dest.parent.resolve() == src.parent:
        raise ValueError("Destination must be in a different directory from source")
    if not dest.parent.is_dir():
        raise ValueError("Destination parent must exist; will not create directories")
    scan = inventory(src)
    if scan["missing_required"] or scan["column_mismatches"]:
        raise ValueError("Source schema incomplete or incompatible; migration copy refused")
    stage = None
    published = False
    try:
        fd, raw = tempfile.mkstemp(prefix=".sterling-compat-", suffix=".sqlite",
                                  dir=str(dest.parent))
        stage = Path(raw)
        os.fchmod(fd, 0o600)
        os.close(fd)
        with closing(_read_only(src)) as original, closing(sqlite3.connect(stage, timeout=30)) as clone:
            original.backup(clone)
            # A WAL source can leave a WAL-mode output. Convert the isolated
            # staging copy to a self-contained DELETE-journal DB before
            # publication; never touch the source's WAL or live transaction.
            if clone.execute("PRAGMA journal_mode=DELETE").fetchone()[0] != "delete":
                raise ValueError("Could not create self-contained SQLite migration copy")
            _check(clone)
            original_schema = _schema(original)["schema_sha256"]
            pre_schema = _schema(clone)["schema_sha256"]
            before = _counts(clone)
            if original_schema != pre_schema or before != _counts(original):
                raise ValueError("Snapshot drift or incomplete SQLite backup")
            # Stronger than counts: a partial/incorrect backup can retain the
            # same number of rows while changing private order, risk or secret
            # values. The digests are compared only in-process, never exposed.
            before_content = _content_fingerprints(original, set(before))
            if before_content != _content_fingerprints(clone, set(before)):
                raise ValueError("SQLite snapshot content differs from source")
            clone.execute("BEGIN IMMEDIATE")
            try:
                status = _ledger_status(clone, pre_schema)
                if fail_after_ledger:
                    raise RuntimeError("synthetic interrupted migration before commit")
                clone.commit()
            except BaseException:
                clone.rollback()
                raise
            _check(clone)
            after = _counts(clone)
            if {k: v for k, v in after.items() if k != LEDGER} != {
                k: v for k, v in before.items() if k != LEDGER
            }:
                raise ValueError("Migration changed original table row counts")
            if LEDGER not in after or after[LEDGER] != 1:
                raise ValueError("Migration ledger missing or not idempotent")
            if before_content != _content_fingerprints(clone, set(before)):
                raise ValueError("Migration changed operational table content")
            # Exact original sqlite_master entries must survive, not just counts.
            remaining = clone.execute(
                "SELECT type,name,tbl_name,sql FROM sqlite_master "
                "WHERE name NOT LIKE 'sqlite_%' AND name NOT LIKE 'sqlite_autoindex_%' "
                "AND name<>? ORDER BY type,name", (LEDGER,)
            ).fetchall()
            original_objects = original.execute(
                "SELECT type,name,tbl_name,sql FROM sqlite_master "
                "WHERE name NOT LIKE 'sqlite_%' AND name NOT LIKE 'sqlite_autoindex_%' "
                "AND name<>? ORDER BY type,name", (LEDGER,)
            ).fetchall()
            if remaining != original_objects:
                raise ValueError("Migration unexpectedly altered operational schema")
        with stage.open("rb") as f:
            os.fsync(f.fileno())
        # Atomic create-without-replace, even if another process races this path.
        os.link(stage, dest)
        published = True
        directory_fd = os.open(dest.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        return {
            "status": status,
            "mode": "COPY_ONLY",
            "schema_sha256_before": pre_schema,
            "table_counts_unchanged": True,
            "source_modified": False,
            "destination_mode": oct(dest.stat().st_mode & 0o777),
            "broker_connection": "NOT_ATTEMPTED",
            "production_migration": "NOT_CERTIFIED",
        }
    except BaseException:
        if published:
            dest.unlink(missing_ok=True)
        raise
    finally:
        if stage is not None:
            stage.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path)
    parser.add_argument("--apply-to-copy", action="store_true")
    parser.add_argument("--audit-full-parity", action="store_true",
                        help="read-only safety-key audit; always blocks full migration until certified")
    args = parser.parse_args()
    if args.audit_full_parity:
        if args.apply_to_copy or args.destination is not None:
            parser.error("--audit-full-parity cannot be combined with a copy destination")
        result = audit_full_migration_preflight(args.source)
        print(json.dumps(result, sort_keys=True))
        raise SystemExit(2)  # a blocked migration must not be a green shell gate
    if args.apply_to_copy:
        if args.destination is None:
            parser.error("--apply-to-copy requires --destination")
        result = migrate_copy(args.source, args.destination)
    else:
        result = inventory(args.source)
        result["dry_run"] = True
        result["source_modified"] = False
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
