#!/usr/bin/env python3
"""Read-only source -> isolated SQLite backup, with integrity and row-count parity.

Never restores over the source or changes broker/execution state. A successful
check is SQLite snapshot evidence, not proof of PostgreSQL or host recovery.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sqlite3
from urllib.parse import quote


def _tables(conn: sqlite3.Connection) -> dict[str, int]:
    names = [
        row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    ]
    return {
        name: conn.execute('SELECT COUNT(*) FROM "' + name.replace('"', '""') + '"').fetchone()[0]
        for name in names
    }


def verify_snapshot(source: Path, destination: Path) -> dict:
    source = source.resolve(strict=True)
    if not source.is_file():
        raise ValueError("Source is not a regular database file")
    destination = destination.absolute()
    if destination == source or destination.exists() or destination.is_symlink():
        raise ValueError("Destination must be a new isolated file; never overwrite a database")
    if destination.parent.resolve() == source.parent.resolve():
        raise ValueError("Keep verification copies outside the source database directory")
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    try:
        uri = "file:" + quote(str(source), safe="/") + "?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=10) as origin:
            origin.execute("PRAGMA query_only=ON")
            with sqlite3.connect(destination, timeout=10) as restored:
                origin.backup(restored)
                assert restored.execute("PRAGMA quick_check").fetchone()[0] == "ok", (
                    "Restored copy failed integrity check"
                )
                source_tables = _tables(origin)
                restored_tables = _tables(restored)
                if source_tables != restored_tables:
                    raise RuntimeError("Restored schema/table row counts differ from source")
                if origin.execute("PRAGMA user_version").fetchone()[0] != restored.execute(
                    "PRAGMA user_version"
                ).fetchone()[0]:
                    raise RuntimeError("Restored schema version differs from source")
        return {"result": "PASS", "mode": "isolated_sqlite_snapshot",
                "tables_checked": len(source_tables),
                "broker_connection": "NOT_ATTEMPTED",
                "production_recovery": "NOT_CERTIFIED"}
    except BaseException:
        destination.unlink(missing_ok=True)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--destination", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(verify_snapshot(args.source, args.destination), sort_keys=True))


if __name__ == "__main__":
    main()
