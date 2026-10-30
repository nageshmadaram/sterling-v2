#!/usr/bin/env python3
"""Verify a read-only SQLite snapshot before publishing a private isolated copy.

Never restores over the source or changes broker/execution state. A successful
check is SQLite snapshot evidence, not PostgreSQL or production recovery proof.
Content digests are compared in process only and NEVER returned or logged.
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


def _tables(conn: sqlite3.Connection) -> dict[str, int]:
    names = [
        row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    ]
    return {
        name: conn.execute('SELECT COUNT(*) FROM "' + name.replace('"', '""') + '"').fetchone()[0]
        for name in sorted(names)
    }


def _schema(conn: sqlite3.Connection) -> list[tuple]:
    """Compare the complete schema, not only table names and user_version."""
    return conn.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master "
        "ORDER BY type,name"
    ).fetchall()


def _table_fingerprints(conn: sqlite3.Connection, tables: dict[str, int]) -> dict[str, str]:
    """Compare ordered *values*, including BLOBs, without exporting their hashes.

    Works for WITHOUT ROWID tables; the order is by all declared columns.
    Identical duplicate rows yield the same digest regardless of tie ordering.
    """
    fingerprints = {}
    for name in sorted(tables):
        identifier = '"' + name.replace('"', '""') + '"'
        columns = [row[1] for row in conn.execute(f"PRAGMA table_info({identifier})")]
        if not columns:
            raise ValueError(f"Cannot fingerprint table: {name}")
        ordering = ", ".join('"' + c.replace('"', '""') + '"' for c in columns)
        digest = hashlib.sha256()
        cursor = conn.execute(f"SELECT * FROM {identifier} ORDER BY {ordering}")
        while True:
            batch = cursor.fetchmany(256)
            if not batch:
                break
            for row in batch:
                data = repr(tuple(row)).encode("utf-8", errors="backslashreplace")
                digest.update(len(data).to_bytes(8, "big"))
                digest.update(data)
        fingerprints[name] = digest.hexdigest()
    return fingerprints


def _integrity(conn: sqlite3.Connection) -> None:
    if conn.execute("PRAGMA quick_check").fetchone() != ("ok",):
        raise RuntimeError("SQLite integrity verification failed")
    if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise RuntimeError("SQLite foreign key verification failed")


def verify_snapshot(source: Path, destination: Path) -> dict:
    supplied = Path(source)
    if supplied.is_symlink():
        raise ValueError("Source must be a regular non-symlink SQLite file")
    source = supplied.resolve(strict=True)
    if not source.is_file() or not stat.S_ISREG(source.stat().st_mode):
        raise ValueError("Source is not a regular database file")
    destination = Path(destination).absolute()
    if destination == source or destination.exists() or destination.is_symlink():
        raise ValueError("Destination must be a new isolated file; never overwrite a database")
    if destination.parent.resolve() == source.parent.resolve():
        raise ValueError("Keep verification copies outside the source database directory")
    destination.parent.mkdir(parents=True, exist_ok=True)

    stage = None
    published = False
    try:
        fd, stage_name = tempfile.mkstemp(
            prefix=".sterling-recovery-", suffix=".sqlite", dir=str(destination.parent)
        )
        stage = Path(stage_name)
        os.fchmod(fd, 0o600)
        os.close(fd)

        uri = "file:" + quote(str(source), safe="/") + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=10)) as origin:
            origin.execute("PRAGMA query_only=ON")
            _integrity(origin)
            with closing(sqlite3.connect(stage, timeout=10)) as restored:
                origin.backup(restored)
                # A WAL-mode source may produce a WAL-mode copy. The published
                # artifact must reopen alone without a companion WAL/SHM file.
                mode = restored.execute("PRAGMA journal_mode=DELETE").fetchone()[0]
                if str(mode).lower() != "delete":
                    raise RuntimeError("Snapshot could not be made standalone")
                _integrity(restored)
                source_tables = _tables(origin)
                restored_tables = _tables(restored)
                if source_tables != restored_tables:
                    raise RuntimeError("Restored schema/table row counts differ from source")
                if _schema(origin) != _schema(restored):
                    raise RuntimeError("Restored schema definitions differ from source")
                if origin.execute("PRAGMA user_version").fetchone()[0] != restored.execute(
                    "PRAGMA user_version"
                ).fetchone()[0]:
                    raise RuntimeError("Restored schema version differs from source")
                if origin.execute("PRAGMA application_id").fetchone()[0] != restored.execute(
                    "PRAGMA application_id"
                ).fetchone()[0]:
                    raise RuntimeError("Restored application ID differs from source")
                # Do not treat matching counts as proof of identical execution,
                # broker-session or risk state.
                if _table_fingerprints(origin, source_tables) != _table_fingerprints(
                    restored, restored_tables
                ):
                    raise RuntimeError("Restored operational table content differs from source")

        with stage.open("rb") as handle:
            os.fsync(handle.fileno())
        # Publish only after complete verification; hard-link is atomic and
        # never replaces a destination another process created meanwhile.
        os.link(stage, destination)
        published = True
        directory_fd = os.open(
            destination.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        )
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        return {
            "result": "PASS",
            "mode": "isolated_sqlite_snapshot",
            "tables_checked": len(source_tables),
            "content_verified": True,
            "schema_verified": True,
            "source_modified": False,
            "broker_connection": "NOT_ATTEMPTED",
            "production_recovery": "NOT_CERTIFIED",
        }
    except BaseException:
        if published:
            destination.unlink(missing_ok=True)
        raise
    finally:
        if stage is not None:
            stage.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--destination", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(verify_snapshot(args.source, args.destination), sort_keys=True))


if __name__ == "__main__":
    main()
