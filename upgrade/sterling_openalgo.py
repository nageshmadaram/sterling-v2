#!/usr/bin/env python3
"""Sterling/OpenAlgo-pattern compatibility inventory and COPY-ONLY schema ledger.

This is an additive, offline staging phase. It NEVER changes the source, real
broker sessions, positions or risk controls; it does NOT claim exact OpenAlgo
table parity, a PostgreSQL migration, or authorization to trade.
"""
from __future__ import annotations

import argparse
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
LEDGER_COLUMNS = ("version", "phase", "source_schema_sha256")


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


def _schema(conn: sqlite3.Connection) -> dict:
    objects = conn.execute(
        "SELECT type, name, tbl_name, sql FROM sqlite_master "
        "WHERE type IN ('table','index','view','trigger') AND name NOT LIKE 'sqlite_%' "
        "ORDER BY type, name"
    ).fetchall()
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


def _check(conn: sqlite3.Connection) -> None:
    if conn.execute("PRAGMA quick_check").fetchone() != ("ok",):
        raise ValueError("SQLite integrity check failed; do not migrate")
    if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise ValueError("Foreign key constraint violation; do not migrate")


def inventory(path: Path) -> dict:
    """Only schema metadata and row COUNTS. Never export values or secrets."""
    source = _source(Path(path))
    with _read_only(source) as conn:
        _check(conn)
        schema = _schema(conn)
        counts = _counts(conn)
        tables = set(counts)
        missing = sorted(REQUIRED - tables)
        domains = {
            name: {
                "present": [t for t in expected if t in tables],
                "missing": [t for t in expected if t not in tables],
            }
            for name, expected in DOMAINS.items()
        }
        return {
            "status": "BLOCKED_INCOMPLETE_SCHEMA" if missing else "READY_FOR_COPY_ONLY",
            "mode": "READ_ONLY_INVENTORY",
            "schema_sha256": schema["schema_sha256"],
            "table_counts": counts,
            "domain_mapping": domains,
            "missing_required": missing,
            "sqlite_user_version": int(conn.execute("PRAGMA user_version").fetchone()[0]),
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
    if not isinstance(rows[0][2], str) or len(rows[0][2]) != 64:
        raise ValueError("Corrupt migration schema fingerprint")
    return "ALREADY_APPLIED_ON_COPY"


def migrate_copy(source: Path, destination: Path, *, fail_after_ledger: bool = False) -> dict:
    """Read source, transactional additive migration to isolated copy, atomic publish.

    An existing destination is never overwritten. The failure hook exists
    exclusively to prove rollback/cleanup in offline unit tests.
    """
    src = _source(Path(source))
    dest = Path(destination).absolute()
    if dest.exists() or dest.is_symlink() or dest.resolve() == src:
        raise ValueError("Destination must be a new isolated file; no overwrite")
    if dest.parent.resolve() == src.parent:
        raise ValueError("Destination must be in a different directory from source")
    if not dest.parent.is_dir():
        raise ValueError("Destination parent must exist; will not create directories")
    if inventory(src)["missing_required"]:
        raise ValueError("Source schema incomplete; migration copy refused")
    stage = None
    published = False
    try:
        fd, raw = tempfile.mkstemp(prefix=".sterling-compat-", suffix=".sqlite",
                                  dir=str(dest.parent))
        stage = Path(raw)
        os.fchmod(fd, 0o600)
        os.close(fd)
        with _read_only(src) as original, sqlite3.connect(stage, timeout=30) as clone:
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
    args = parser.parse_args()
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
