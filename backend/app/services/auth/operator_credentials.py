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
    if target and target.role != 'admin' and chosen_role == 'admin' and not password:
        raise ValueError('Set a new strong password when granting administrator access.')
    chosen_active = active if active is not None else (target.is_active if target else True)
    if target and (target.id == actor_id or target.id == 'default') and (not chosen_active or chosen_role != 'admin'):
        raise ValueError('Your own login and the primary Sterling administrator must remain active administrators.')
    if target and not chosen_active:
        from app.services.exchanges.kite import accounts
        if accounts.list_accounts(target.id):
            raise ValueError('Remove this user’s broker accounts through Connect before deleting or disabling its login.')
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
            if c.execute('SELECT 1 FROM users WHERE username=? AND id<>?', (username,candidate.id)).fetchone():
                raise ValueError('That username is already in use.')
            if action == 'update':
                changed=c.execute('UPDATE users SET username=?, password_hash=?, token_version=?, updated_at_ms=?, role=?, is_active=? WHERE id=? AND token_version=?',
                                  (candidate.username,candidate.password_hash,candidate.token_version,candidate.updated_at_ms,candidate.role,int(candidate.is_active),target.id,target.token_version)).rowcount
                if changed != 1:raise RuntimeError('Operator changed meanwhile; reopen the form.')
            else:
                c.execute('INSERT INTO users (id,username,password_hash,role,is_active,token_version,created_at_ms,updated_at_ms) VALUES (?,?,?,?,?,?,?,?)',
                          (candidate.id,candidate.username,candidate.password_hash,candidate.role,int(candidate.is_active),candidate.token_version,candidate.created_at_ms,candidate.updated_at_ms))
            c.commit()
    except sqlite3.IntegrityError:
        raise ValueError('That username is already in use.') from None
    user_store._users[candidate.id]=candidate
    return candidate
