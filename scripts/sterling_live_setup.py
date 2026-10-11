#!/usr/bin/env python3
"""Configure fresh live secrets offline without rotating encrypted credentials.

Run with the backend stopped, from the repository root. Existing encryption keys
are preserved. A weak/missing key with saved credentials requires a separately
verified migration; this utility refuses to make those credentials unreadable.
Never prints generated secrets. The protected .env file holds the admin password.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import secrets
import sqlite3
import tempfile

KEYS = ("STERLING_SECRET_KEY", "STERLING_JWT_SECRET")
WEAK = {"secret", "dev-secret", "change-me", "sterling-dev-insecure-key", "sterling-dev-jwt-secret-key"}


def strong(value: str) -> bool:
    return len(value) >= 32 and value not in WEAK


def values(text: str) -> dict[str, str]:
    parsed = {}
    for line in text.splitlines():
        key, separator, value = line.strip().removeprefix("export ").partition("=")
        if separator and not key.startswith("#"):
            parsed[key.strip()] = value.strip().strip("\"'")
    return parsed


def saved_credentials(path: Path) -> bool:
    if not path.exists():
        return False
    # Read-only mode must not create a database or alter broker/account state.
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as conn:
        tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        for table in tables:
            quoted = '"' + table.replace('"', '""') + '"'
            columns = [row[1] for row in conn.execute(f"PRAGMA table_info({quoted})")]
            secret_columns = [column for column in columns if column.endswith("_enc")]
            if table == "exchange_configs":
                secret_columns += [column for column in ("api_key", "api_secret") if column in columns]
            for column in secret_columns:
                name = '"' + column.replace('"', '""') + '"'
                if conn.execute(f"SELECT 1 FROM {quoted} WHERE COALESCE({name}, '') <> '' LIMIT 1").fetchone():
                    return True
            if table == "system_config" and {"key", "value"} <= set(columns):
                for (value,) in conn.execute("SELECT value FROM system_config WHERE key LIKE 'kite_tg_targets:%'"):
                    try:
                        targets = json.loads(value)
                    except (ValueError, TypeError):
                        return True  # Unknown credential inventory cannot justify rotation.
                    if not isinstance(targets, list) or any(
                        not isinstance(target, dict) or target.get("bot_token_enc")
                        for target in targets
                    ):
                        return True
                if conn.execute("SELECT 1 FROM system_config WHERE value<>'' AND "
                                "(lower(key) LIKE '%secret%' OR lower(key) LIKE '%token%' "
                                "OR lower(key) LIKE '%password%') LIMIT 1").fetchone():
                    return True
    return False


def configure(path: Path, database: Path, *, check: bool = False) -> bool:
    if path.is_symlink():
        raise ValueError("Refusing a symlinked environment file")
    original = path.read_text() if path.exists() else ""
    current = values(original)
    effective = {key: os.environ.get(key) or current.get(key, "") for key in KEYS}
    ready = all(strong(effective[key]) for key in KEYS) and effective[KEYS[0]] != effective[KEYS[1]]
    if check:
        return ready
    if ready:
        return True
    # Key rotation requires re-encrypting every saved credential, not replacing
    # the key and relying on a restart to discover the resulting decryption loss.
    if not strong(effective[KEYS[0]]) and saved_credentials(database):
        raise ValueError("Saved credentials exist. Preserve the encryption key; verified credential migration is required.")
    updates = {}
    for key in KEYS:
        if not strong(effective[key]):
            if os.environ.get(key):
                raise ValueError(f"{key} is overridden by the process environment; fix that override first.")
            updates[key] = secrets.token_urlsafe(48)
    if effective[KEYS[0]] == effective[KEYS[1]] and not updates:
        if os.environ.get(KEYS[1]):
            raise ValueError("JWT key override must be distinct from the encryption key.")
        updates[KEYS[1]] = secrets.token_urlsafe(48)
    lines = []
    for line in original.splitlines():
        key = line.strip().removeprefix("export ").partition("=")[0].strip()
        if key not in updates:
            lines.append(line)
    lines.extend(f"{key}={value}" for key, value in updates.items())
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".sterling-keys-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as output:
            os.fchmod(output.fileno(), 0o600)
            output.write("\n".join(lines) + "\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return True


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=root / "backend" / ".env")
    parser.add_argument("--database", type=Path, help="Database whose encrypted credentials must be preserved")
    parser.add_argument("--check", action="store_true", help="Check key strength without writing")
    args = parser.parse_args()
    try:
        configured = values(args.env_file.read_text()) if args.env_file.exists() else {}
        raw_database = os.environ.get("STERLING_DB_PATH") or configured.get("STERLING_DB_PATH")
        database = args.database or (Path(raw_database) if raw_database else args.env_file.parent / "sterling_paper.db")
        if raw_database and not args.database and not database.is_absolute():
            database = args.env_file.parent / database
        ready = configure(args.env_file, database, check=args.check)
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(f"Setup refused: {exc}")
        return 1
    print("Secure distinct keys configured. Restart backend; other live checks still apply." if ready
          else "Secure distinct keys are missing. Run this command without --check.")
    return 0 if ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
