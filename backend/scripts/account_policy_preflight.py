"""Read-only account migration preflight; prints no credentials or hashes.

Run with an explicit database path. This script never imports runtime settings,
boots the app, writes schemas, or connects to a broker.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3


def inspect(path: Path) -> dict:
    with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True) as connection:
        connection.row_factory = sqlite3.Row
        tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'users' not in tables:
            return {'ready': True, 'state': 'fresh_installation', 'users': 0,
                    'notes': ['Explicit administrator bootstrap credentials required.']}
        users = connection.execute('SELECT id, username, role, is_active FROM users ORDER BY id').fetchall()
        admins = [u for u in users if u['role'] == 'admin']
        blockers = []
        if len(admins) != 1 or not admins[0]['is_active']:
            blockers.append('Exactly one active administrator is required; recover the existing primary identity before rollout.')
        if 'platform_admin' in tables:
            designation = connection.execute('SELECT user_id FROM platform_admin WHERE slot=1').fetchone()
            if designation and (len(admins) != 1 or admins[0]['id'] != designation['user_id']):
                blockers.append('Stored administrator designation differs from administrator identity.')
        canonical = {}
        for user in users:
            canonical.setdefault(user['username'].strip().casefold(), []).append(user['id'])
        collisions = [ids for ids in canonical.values() if len(ids) > 1]
        return {'ready': not blockers, 'state': 'existing_installation', 'users': len(users),
                'administrator_ids': [u['id'] for u in admins], 'blockers': blockers,
                'canonical_username_collisions': collisions,
                'notes': ['Existing feature grants are not inferred; review explicit assignments before feature enforcement.',
                          'This preflight does not establish broker exposure, MFA, encryption or deployment readiness.']}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database', type=Path)
    args = parser.parse_args()
    try:
        report = inspect(args.database)
    except sqlite3.Error:
        report = {'ready': False, 'blockers': ['Database could not be read. No changes made.']}
    print(json.dumps(report, indent=2))
    return 0 if report['ready'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
