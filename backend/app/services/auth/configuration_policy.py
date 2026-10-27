"""Configuration delegation is independent from feature use and broker ownership.

Legacy resources are exclusively tenant scoped. Shared/global credentials remain
administrator-only until the central registry can establish their exact scope.
Saved-secret reveal is unavailable until MFA-bound reveal challenges exist.
"""
from __future__ import annotations

import sqlite3
from typing import Literal

from app.services import db
from app.services.auth import account_policy

Section = Literal['brokers', 'telegram', 'integrations']
Action = Literal['create', 'update', 'test', 'recipients', 'reveal']
SECTIONS = ('brokers', 'telegram', 'integrations')
ACTIONS = ('create', 'update', 'test', 'recipients', 'reveal')
DELEGABLE_ACTIONS = {
    'brokers': ('create', 'update'),
    'telegram': ('recipients', 'test'),
    'integrations': ('create', 'update', 'test'),
}


def migrate(c: sqlite3.Connection) -> None:
    c.execute('''CREATE TABLE IF NOT EXISTS user_configuration_sections (
        user_id TEXT NOT NULL REFERENCES users(id), section TEXT NOT NULL
            CHECK(section IN ('brokers','telegram','integrations')),
        visible INTEGER NOT NULL CHECK(visible IN (0,1)), PRIMARY KEY(user_id,section)
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS user_configuration_grants (
        user_id TEXT NOT NULL REFERENCES users(id), section TEXT NOT NULL
            CHECK(section IN ('brokers','telegram','integrations')),
        action TEXT NOT NULL CHECK(action IN ('create','update','test','recipients','reveal')),
        resource_id TEXT NOT NULL CHECK(length(resource_id) BETWEEN 1 AND 128),
        expires_at_ms INTEGER, actor_id TEXT NOT NULL, updated_at_ms INTEGER NOT NULL,
        PRIMARY KEY(user_id,section,action,resource_id)
    )''')
    c.execute("INSERT OR IGNORE INTO account_schema_versions VALUES(2,'scoped configuration delegation')")


def _read(c, user_id: str, now: int) -> dict:
    row = c.execute('''SELECT u.is_active, p.revision,
        EXISTS(SELECT 1 FROM platform_admin a WHERE a.user_id=u.id AND u.role='admin') AS administrator
        FROM users u LEFT JOIN account_policy_revisions p ON p.user_id=u.id WHERE u.id=?''', (user_id,)).fetchone()
    active = bool(row and row['is_active'] == 1)
    administrator = bool(active and row['administrator'])
    visible = {r['section']: bool(r['visible']) for r in c.execute(
        'SELECT section,visible FROM user_configuration_sections WHERE user_id=?', (user_id,))}
    grants = c.execute('''SELECT section,action,resource_id,expires_at_ms FROM user_configuration_grants
        WHERE user_id=? AND (expires_at_ms IS NULL OR (typeof(expires_at_ms)='integer' AND expires_at_ms>?))
        ORDER BY section,action,resource_id''', (user_id, now)).fetchall()
    return {'user_id': user_id, 'revision': int(row['revision'] or 0) if row else 0,
            'administrator': administrator, 'active': active,
            'sections': {section: {
                'visible': active and (administrator or visible.get(section, False)),
                # Reveal remains unavailable even if an imported grant says yes.
                'grants': ([{'action': action, 'resource_id': 'own', 'expires_at_ms': None}
                            for action in ACTIONS if action != 'reveal'] if administrator else
                           [{'action': r['action'], 'resource_id': r['resource_id'], 'expires_at_ms': r['expires_at_ms']}
                            for r in grants if r['section'] == section and r['action'] in DELEGABLE_ACTIONS[section]
                            and active and visible.get(section, False)])
            } for section in SECTIONS}}


def read(user_id: str) -> dict:
    from app.services.auth.user_store import _now_ms
    account_policy._available()
    try:
        with db._conn() as c:
            c.execute('BEGIN')
            return _read(c, user_id, _now_ms())
    except sqlite3.Error as exc:
        raise account_policy.PolicyUnavailable('Configuration policy unavailable; access denied.') from exc


def require(user_id: str, section: Section, action: Action | None = None,
            resource_id: str = 'own', *, administrator_only: bool = False) -> dict:
    if section not in SECTIONS or (action is not None and action not in ACTIONS):
        raise ValueError('Unknown configuration capability.')
    policy = read(user_id)
    if not policy['active'] or not policy['sections'][section]['visible']:
        raise PermissionError('This configuration section is not enabled.')
    if action == 'reveal':
        raise PermissionError('Saved-secret reveal requires an MFA-bound challenge; unavailable in this release.')
    if administrator_only and not policy['administrator']:
        raise PermissionError('This operation requires the designated administrator.')
    if action is None or policy['administrator']:
        return policy
    if action not in DELEGABLE_ACTIONS[section]:
        raise PermissionError('This action cannot be delegated for this section.')
    # 'own' is a scope, not a wildcard for other users. Endpoint ownership checks
    # still bind every resource before invoking the provider store.
    if not any(g['action'] == action and g['resource_id'] in ('own', resource_id)
               for g in policy['sections'][section]['grants']):
        raise PermissionError('This configuration action is not enabled for this resource.')
    return policy


def replace_section(actor_id: str, user_id: str, section: Section, *, visible: bool,
                    grants: list[dict], expected_revision: int) -> dict:
    from app.services.auth.user_store import _now_ms
    if section not in SECTIONS or type(visible) is not bool:
        raise ValueError('Invalid configuration section.')
    if len(grants) > 100:
        raise ValueError('At most 100 scoped grants are allowed per section.')
    now = _now_ms()
    seen = set()
    for grant in grants:
        if set(grant) != {'action', 'resource_id', 'expires_at_ms'}:
            raise ValueError('Invalid grant fields.')
        action, resource, expiry = grant['action'], grant['resource_id'], grant['expires_at_ms']
        if action not in ACTIONS or not isinstance(resource, str) or not 1 <= len(resource) <= 128 or resource in ('*', 'global', 'shared'):
            raise ValueError('Grant must name an eligible action and an own-scope resource.')
        if action == 'create' and resource != 'own':
            raise ValueError('Creation can only be delegated in the user’s own scope.')
        if action == 'reveal':
            raise ValueError('Reveal delegation is unavailable until MFA-bound challenges are implemented.')
        if action not in DELEGABLE_ACTIONS[section]:
            raise ValueError('This action is not delegable for this section in the current release.')
        if expiry is not None and (type(expiry) is not int or not now < expiry <= 2**63 - 1):
            raise ValueError('Grant expiry must be a future timestamp.')
        if (action, resource) in seen:
            raise ValueError('Duplicate configuration grant.')
        seen.add((action, resource))
    account_policy._available()
    try:
        with db._conn() as c:
            c.execute('BEGIN IMMEDIATE')
            actor = _read(c, actor_id, now)
            if not actor['administrator']:
                raise PermissionError('Designated administrator access required.')
            target = c.execute('SELECT 1 FROM users WHERE id=?', (user_id,)).fetchone()
            if not target:
                raise ValueError('User not found.')
            before = _read(c, user_id, now)
            if before['administrator']:
                raise ValueError('Administrator configuration authority cannot be delegated or hidden.')
            if before['revision'] != expected_revision:
                raise account_policy.PolicyConflict('Account policy changed; reload before saving.')
            c.execute('''INSERT INTO user_configuration_sections VALUES(?,?,?)
                ON CONFLICT(user_id,section) DO UPDATE SET visible=excluded.visible''', (user_id, section, int(visible)))
            c.execute('DELETE FROM user_configuration_grants WHERE user_id=? AND section=?', (user_id, section))
            for grant in grants:
                c.execute('INSERT INTO user_configuration_grants VALUES(?,?,?,?,?,?,?)',
                          (user_id, section, grant['action'], grant['resource_id'], grant['expires_at_ms'], actor_id, now))
            revision = expected_revision + 1
            c.execute('''INSERT INTO account_policy_revisions VALUES(?,?)
                ON CONFLICT(user_id) DO UPDATE SET revision=excluded.revision''', (user_id, revision))
            event = account_policy.audit(c, actor_id, user_id, 'configuration.updated',
                {'section': section, **before['sections'][section]},
                {'section': section, 'visible': visible, 'grants': grants}, now)
            c.execute('INSERT INTO auth_event_outbox(event_id,user_id,revision) VALUES(?,?,?)', (event, user_id, revision))
            result = _read(c, user_id, now)
        return result
    except sqlite3.Error as exc:
        raise account_policy.PolicyUnavailable('Configuration policy could not be saved; nothing changed.') from exc
