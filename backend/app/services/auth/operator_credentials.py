"""Atomic operator credential edits: durable first, then revoke app sessions."""
import re
from dataclasses import replace
from app.services import db
from app.services.auth import user_store
from app.core.passwords import hash_password


def manage(actor_id: str, action: str, username: str, password: str, *, target_id: str | None = None, role: str | None = None, active: bool | None = None):
    actor = user_store.get_by_id(actor_id)
    if not actor or not actor.is_active or actor.role != 'admin':
        raise PermissionError('Administrator access required')
    username = username.strip()
    if not re.fullmatch(r'[A-Za-z0-9_.@-]{3,64}', username):
        raise ValueError('Username must contain 3–64 letters, numbers, dots, @, underscores or dashes.')
    if action not in ('update', 'add'):
        raise ValueError('Unknown operator action')
    if (action == 'add' or password) and (len(password) < 12 or user_store._is_weak_admin_password(password)):
        raise ValueError('Use a password of at least 12 characters that is not common.')
    if len(password) > 256:
        raise ValueError('Password must be at most 256 characters.')
    if not db._available:
        raise RuntimeError('Credential storage unavailable; nothing changed.')
    target = user_store.get_by_id(target_id or actor_id) if action == 'update' else None
    if action == 'update' and target is None:
        raise ValueError('User not found.')
    chosen_role = role or (target.role if target else 'user')
    if chosen_role not in ('admin', 'user'):
        raise ValueError('Role must be administrator or user.')
    if chosen_role == 'admin' and (target is None or target.role != 'admin'):
        raise ValueError('Only one designated administrator is allowed.')
    chosen_active = active if active is not None else (target.is_active if target else True)
    if target and (target.id == actor_id or target.id == 'default') and (not chosen_active or chosen_role != 'admin'):
        raise ValueError('Your own login and the primary Sterling administrator must remain active administrators.')
    if action == 'update':
        candidate = replace(target, username=username, password_hash=hash_password(password) if password else target.password_hash,
                            token_version=target.token_version+1, updated_at_ms=user_store._now_ms(), role=chosen_role, is_active=chosen_active)
    else:
        import uuid
        candidate = user_store._User(id='USR-'+uuid.uuid4().hex[:10].upper(), username=username,
                                     password_hash=hash_password(password), role=chosen_role, is_active=chosen_active)
    import sqlite3
    try:
        with db._conn() as c:
            c.execute('BEGIN IMMEDIATE')
            actor_row = c.execute("SELECT 1 FROM users u JOIN platform_admin a ON a.user_id=u.id WHERE u.id=? AND u.role='admin' AND u.is_active=1", (actor_id,)).fetchone()
            if not actor_row:
                raise PermissionError('Designated administrator access required')
            if c.execute('SELECT 1 FROM users WHERE username=? AND id<>?', (username,candidate.id)).fetchone():
                raise ValueError('That username is already in use.')
            if action == 'update':
                changed=c.execute('UPDATE users SET username=?, password_hash=?, token_version=?, updated_at_ms=?, role=?, is_active=? WHERE id=? AND token_version=?',
                                  (candidate.username,candidate.password_hash,candidate.token_version,candidate.updated_at_ms,candidate.role,int(candidate.is_active),target.id,target.token_version)).rowcount
                if changed != 1:raise RuntimeError('Operator changed meanwhile; reopen the form.')
            else:
                c.execute('INSERT INTO users (id,username,password_hash,role,is_active,token_version,created_at_ms,updated_at_ms) VALUES (?,?,?,?,?,?,?,?)',
                          (candidate.id,candidate.username,candidate.password_hash,candidate.role,int(candidate.is_active),candidate.token_version,candidate.created_at_ms,candidate.updated_at_ms))
            policy_revision = None
            if action == 'update':
                if target.is_active and not candidate.is_active:
                    # Suspend interactive/new-entry authority without detaching the
                    # broker or terminating its persisted protection workers.
                    c.execute('UPDATE user_feature_grants SET enabled=0,actor_id=?,updated_at_ms=? WHERE user_id=?',
                              (actor_id, candidate.updated_at_ms, candidate.id))
                    c.execute('UPDATE user_configuration_sections SET visible=0 WHERE user_id=?', (candidate.id,))
                    c.execute('DELETE FROM user_configuration_grants WHERE user_id=?', (candidate.id,))
                row = c.execute('SELECT revision FROM account_policy_revisions WHERE user_id=?', (candidate.id,)).fetchone()
                policy_revision = (int(row['revision']) if row else 0) + 1
                c.execute('INSERT INTO account_policy_revisions VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET revision=excluded.revision',
                          (candidate.id, policy_revision))
            from app.services.auth.account_policy import audit
            event_id = audit(c, actor_id, candidate.id, 'user.' + action,
                  {'username': target.username, 'role': target.role, 'active': target.is_active} if target else {},
                  {'username': candidate.username, 'role': candidate.role, 'active': candidate.is_active},
                  candidate.updated_at_ms)
            if policy_revision is not None:
                c.execute('INSERT INTO auth_event_outbox(event_id,user_id,revision) VALUES(?,?,?)', (event_id,candidate.id,policy_revision))
            c.commit()
    except sqlite3.IntegrityError:
        raise ValueError('Identity conflicts with an existing username or administrator invariant.') from None
    except sqlite3.Error as exc:
        raise RuntimeError('Credential storage failed; nothing changed.') from exc
    user_store._users[candidate.id]=candidate
    return candidate
