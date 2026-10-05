#!/usr/bin/env python3
"""Set (or reset) the Sterling operator password. Prompts; never echoes or logs it.

Why this exists: the bootstrap admin is only seeded into an EMPTY users table, so
changing STERLING_ADMIN_PASSWORD later does nothing, and a development password that
was generated once is not recoverable (log lines are redacted). Once a LIVE broker
account is stored the anonymous development identity is refused, so the operator needs
a known password to sign in.

    cd backend && .venv/bin/python scripts/reset_admin_password.py [--username admin]

Honours STERLING_DB_PATH / backend/.env exactly like the server. Bumps the account's
token version, which signs out every existing session.
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

MIN_LENGTH = 12


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--username", default=os.environ.get("STERLING_ADMIN_USERNAME", "admin"))
    args = parser.parse_args()

    from app.services import db
    from app.services.auth import user_store

    # Deliberately not db.init(): this must only touch the users table, never
    # create/alter other tables in a live database.
    db._available = True
    user_store.bootstrap()
    user = user_store.get_by_username(args.username)
    if user is None:
        print(f"No user named {args.username!r}. Existing users: "
              f"{[u.username for u in user_store.all_users()]}", file=sys.stderr)
        return 2

    password = getpass.getpass(f"New password for {user.username!r} (min {MIN_LENGTH} chars): ")
    if len(password) < MIN_LENGTH:
        print(f"Refusing: password shorter than {MIN_LENGTH} characters.", file=sys.stderr)
        return 2
    if password != getpass.getpass("Repeat password: "):
        print("Refusing: passwords do not match.", file=sys.stderr)
        return 2

    user_store.set_password(user.id, password)
    user_store.bump_token_version(user.id)
    print(f"Password updated for {user.username!r}; all existing sessions were signed out.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
