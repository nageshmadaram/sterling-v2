"""Durable, default-deny account policy. No trading-worker authority lives here.

The additive migration preserves legacy identities. Ambiguous administrator data
requires explicit recovery; it is never silently reassigned or demoted.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from typing import Literal

from app.services import db

Feature = Literal['kite', 'tv', 'radio']
FEATURES: tuple[Feature, ...] = ('kite', 'tv', 'radio')


class PolicyUnavailable(RuntimeError):
    pass


class PolicyConflict(ValueError):
    pass


def migrate(c: sqlite3.Connection) -> None:
    """Run inside the caller's schema transaction, before loading identities."""
    admins = c.execute("SELECT id, is_active FROM users WHERE role='admin'").fetchall()
    if len(admins) > 1 or (admins and not admins[0]['is_active']):
        raise PolicyUnavailable('Administrator migration requires one active administrator; no identities changed.')
    c.execute('''CREATE TABLE IF NOT EXISTS platform_admin (
        slot INTEGER PRIMARY KEY CHECK(slot=1), user_id TEXT NOT NULL UNIQUE REFERENCES users(id) ON DELETE RESTRICT
    )''')
    if admins:
        c.execute('INSERT OR IGNORE INTO platform_admin(slot,user_id) VALUES(1,?)', (admins[0]['id'],))
    designated = c.execute('SELECT user_id FROM platform_admin WHERE slot=1').fetchone()
    if designated and (not admins or admins[0]['id'] != designated['user_id']):
        raise PolicyUnavailable('Designated administrator does not match active identity; recovery required.')
    c.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_users_single_admin ON users(role) WHERE role='admin'")
    # Triggers protect raw SQL, scripts and legacy INSERT OR REPLACE writers too.
    statements = (
        '''CREATE TRIGGER IF NOT EXISTS platform_admin_validate_insert BEFORE INSERT ON platform_admin
           WHEN NOT EXISTS(SELECT 1 FROM users WHERE id=NEW.user_id AND role='admin' AND is_active=1)
           BEGIN SELECT RAISE(ABORT,'designation requires an active administrator'); END''',
        '''CREATE TRIGGER IF NOT EXISTS users_preserve_admin_replace BEFORE INSERT ON users
           WHEN NEW.id=(SELECT user_id FROM platform_admin WHERE slot=1)
           AND (NEW.role<>'admin' OR NEW.is_active<>1)
           BEGIN SELECT RAISE(ABORT,'designated administrator must remain active'); END''',
        '''CREATE TRIGGER IF NOT EXISTS users_designate_admin AFTER INSERT ON users
           WHEN NEW.role='admin' BEGIN
             INSERT INTO platform_admin(slot,user_id) VALUES(1,NEW.id);
           END''',
        '''CREATE TRIGGER IF NOT EXISTS users_preserve_admin_update BEFORE UPDATE ON users
           WHEN OLD.id=(SELECT user_id FROM platform_admin WHERE slot=1)
           AND (NEW.id<>OLD.id OR NEW.role<>'admin' OR NEW.is_active<>1)
           BEGIN SELECT RAISE(ABORT,'designated administrator must remain active'); END''',
        '''CREATE TRIGGER IF NOT EXISTS users_preserve_admin_delete BEFORE DELETE ON users
           WHEN OLD.id=(SELECT user_id FROM platform_admin WHERE slot=1)
           BEGIN SELECT RAISE(ABORT,'designated administrator cannot be deleted'); END''',
        '''CREATE TRIGGER IF NOT EXISTS platform_admin_preserve_update BEFORE UPDATE ON platform_admin
           BEGIN SELECT RAISE(ABORT,'administrator identity cannot be transferred'); END''',
        '''CREATE TRIGGER IF NOT EXISTS platform_admin_preserve_delete BEFORE DELETE ON platform_admin
           BEGIN SELECT RAISE(ABORT,'administrator designation cannot be deleted'); END''',
        '''CREATE TRIGGER IF NOT EXISTS users_prevent_admin_promotion BEFORE UPDATE OF role ON users
           WHEN NEW.role='admin' AND OLD.role<>'admin'
           BEGIN SELECT RAISE(ABORT,'additional administrator is forbidden'); END''',
        '''CREATE TRIGGER IF NOT EXISTS users_admin_active BEFORE INSERT ON users
           WHEN NEW.role='admin' AND NEW.is_active<>1
           BEGIN SELECT RAISE(ABORT,'administrator must be active'); END''',
    )
    for statement in statements:
        c.execute(statement)
    c.execute('''CREATE TABLE IF NOT EXISTS account_policy_revisions (
        user_id TEXT PRIMARY KEY, revision INTEGER NOT NULL CHECK(revision>=1)
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS user_feature_grants (
        user_id TEXT NOT NULL, feature TEXT NOT NULL CHECK(feature IN ('kite','tv','radio')),
        enabled INTEGER NOT NULL CHECK(enabled IN (0,1)), actor_id TEXT NOT NULL,
        updated_at_ms INTEGER NOT NULL, PRIMARY KEY(user_id,feature)
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS auth_audit_events (
        id TEXT PRIMARY KEY, actor_id TEXT NOT NULL, target_id TEXT NOT NULL,
        action TEXT NOT NULL, before_json TEXT NOT NULL, after_json TEXT NOT NULL,
        created_at_ms INTEGER NOT NULL
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS auth_event_outbox (
        event_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, revision INTEGER NOT NULL,
        delivered_at_ms INTEGER
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS account_schema_versions (
        version INTEGER PRIMARY KEY, description TEXT NOT NULL
    )''')
    c.execute("INSERT OR IGNORE INTO account_schema_versions VALUES(1,'singleton administrator and feature policy')")
    from app.services.auth.configuration_policy import migrate as migrate_configuration
    migrate_configuration(c)
    from app.services.auth.media_document_store import migrate as migrate_media
    migrate_media(c)
    from app.services.auth.access_requests import migrate as migrate_requests
    migrate_requests(c)
    from app.services.auth.login_activity import migrate as migrate_activity
    migrate_activity(c)


def _available() -> None:
    if not db.is_available():
        raise PolicyUnavailable('Account policy storage unavailable; access denied.')


@dataclass(frozen=True)
class Policy:
    user_id: str
    revision: int
    active: bool
    administrator: bool
    features: dict[str, bool]


def read(user_id: str) -> Policy:
    _available()
    try:
        with db._conn() as c:
            # One read transaction prevents mixing account and policy revisions.
            c.execute('BEGIN')
            row = c.execute('''SELECT u.is_active, p.revision,
                EXISTS(SELECT 1 FROM platform_admin a WHERE a.user_id=u.id AND u.role='admin') AS administrator
                FROM users u LEFT JOIN account_policy_revisions p ON p.user_id=u.id WHERE u.id=?''', (user_id,)).fetchone()
            grants = c.execute('SELECT feature,enabled FROM user_feature_grants WHERE user_id=?', (user_id,)).fetchall()
        active = bool(row and row['is_active'] == 1)
        stored = {r['feature']: r['enabled'] == 1 for r in grants}
        return Policy(user_id, int(row['revision'] or 0) if row else 0, active,
                      bool(active and row['administrator']),
                      {feature: active and stored.get(feature, False) for feature in FEATURES})
    except sqlite3.Error as exc:
        raise PolicyUnavailable('Account policy could not be loaded; access denied.') from exc


def require_feature(user_id: str, feature: Feature) -> Policy:
    if feature not in FEATURES:
        raise ValueError('Unknown feature.')
    policy = read(user_id)
    if not policy.features[feature]:
        raise PermissionError('Feature access is not enabled.')
    return policy


def audit(c, actor_id: str, target_id: str, action: str, before: dict, after: dict, now: int) -> str:
    event_id = uuid.uuid4().hex
    c.execute('INSERT INTO auth_audit_events VALUES(?,?,?,?,?,?,?)',
              (event_id, actor_id, target_id, action, json.dumps(before, sort_keys=True),
               json.dumps(after, sort_keys=True), now))
    return event_id


def set_features(actor_id: str, target_id: str, features: dict[str, bool], expected_revision: int) -> Policy:
    """Compare-and-swap, audit and invalidation outbox in a single transaction."""
    from app.services.auth import user_store
    if set(features) != set(FEATURES) or any(type(v) is not bool for v in features.values()):
        raise ValueError('Provide explicit booleans for Kite, TV and Radio.')
    _available()
    try:
        with db._conn() as c:
            c.execute('BEGIN IMMEDIATE')
            actor = c.execute('''SELECT 1 FROM users u JOIN platform_admin a ON a.user_id=u.id
                WHERE u.id=? AND u.is_active=1 AND u.role='admin' ''', (actor_id,)).fetchone()
            if not actor:
                raise PermissionError('Designated administrator access required.')
            if not c.execute('SELECT 1 FROM users WHERE id=?', (target_id,)).fetchone():
                raise ValueError('User not found.')
            row = c.execute('SELECT revision FROM account_policy_revisions WHERE user_id=?', (target_id,)).fetchone()
            revision = int(row['revision']) if row else 0
            if revision != expected_revision:
                raise PolicyConflict('Account policy changed; reload before saving.')
            old = {r['feature']: bool(r['enabled']) for r in c.execute(
                'SELECT feature,enabled FROM user_feature_grants WHERE user_id=?', (target_id,))}
            now = user_store._now_ms()
            for feature, enabled in features.items():
                c.execute('''INSERT INTO user_feature_grants VALUES(?,?,?,?,?)
                    ON CONFLICT(user_id,feature) DO UPDATE SET enabled=excluded.enabled,
                    actor_id=excluded.actor_id,updated_at_ms=excluded.updated_at_ms''',
                          (target_id, feature, int(enabled), actor_id, now))
            c.execute('''INSERT INTO account_policy_revisions VALUES(?,?)
                ON CONFLICT(user_id) DO UPDATE SET revision=excluded.revision''', (target_id, revision + 1))
            event_id = audit(c, actor_id, target_id, 'features.updated',
                             {f: old.get(f, False) for f in FEATURES}, features, now)
            c.execute('INSERT INTO auth_event_outbox(event_id,user_id,revision) VALUES(?,?,?)',
                      (event_id, target_id, revision + 1))
    except sqlite3.Error as exc:
        raise PolicyUnavailable('Account policy could not be saved; nothing changed.') from exc
    return read(target_id)


def entry_refusal(user_id: str) -> str | None:
    """New-risk admission only; protective reductions do not call this helper."""
    from app.services.simulation.identity import owner_uid
    try:
        require_feature(owner_uid(user_id), 'kite')
    except (PermissionError, PolicyUnavailable):
        return 'Kite access is disabled or its policy cannot be verified.'
    return None
