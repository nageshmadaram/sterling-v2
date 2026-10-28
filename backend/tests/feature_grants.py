"""Explicit persisted feature grants for synthetic broker test identities.

Call this from a fixture after selecting its disposable database. Production
admission still reads the real policy and denies unlicensed/missing identities.
No authorization function is patched, and no real database is accepted here.
"""
from pathlib import Path
import os
import tempfile
import time


def grant_kite(*user_ids: str) -> None:
    from app.services import db
    from app.services.auth import account_policy, user_store

    root = Path(tempfile.gettempdir()).resolve()
    path = Path(db._DB_PATH).resolve()
    assert os.environ.get('STERLING_UNDER_TEST') == '1'
    assert path.is_relative_to(root), 'Synthetic feature grants require a temporary database'
    assert db.is_available()
    user_store._init_table()
    now = int(time.time() * 1000)
    with db._conn() as conn:
        admin = conn.execute('SELECT user_id FROM platform_admin WHERE slot=1').fetchone()
        admin_id = admin['user_id'] if admin else 'default'
        # These users cannot sign in: no password hash or token is issued.
        if not admin:
            conn.execute("INSERT INTO users(id,username,role,created_at_ms,updated_at_ms) VALUES(?,?,'admin',?,?)",
                         (admin_id, 'synthetic-policy-admin', now, now))
        for uid in user_ids:
            assert uid and not uid.startswith('sim:'), 'Grant the real owner, never a virtual tenant'
            if conn.execute("SELECT 1 FROM users WHERE id=?", (uid,)).fetchone():
                continue
            conn.execute("INSERT OR IGNORE INTO users(id,username,role,created_at_ms,updated_at_ms) VALUES(?,?,'user',?,?)",
                         (uid, 'synthetic-policy-' + uid, now, now))
    for uid in user_ids:
        policy = account_policy.read(uid)
        if not policy.features['kite']:
            account_policy.set_features(admin_id, uid, {**policy.features, 'kite': True}, policy.revision)


def grant_broker_configuration(*user_ids: str) -> None:
    """Delegate synthetic own-scope CRUD for router tenancy tests."""
    from app.services import db
    from app.services.auth import account_policy, configuration_policy

    grant_kite(*user_ids)
    with db._conn() as conn:
        admin_id = conn.execute('SELECT user_id FROM platform_admin WHERE slot=1').fetchone()['user_id']
    for uid in user_ids:
        if account_policy.read(uid).administrator:
            continue
        configuration_policy.replace_section(admin_id, uid, 'brokers', visible=True,
            grants=[{'action': action, 'resource_id': 'own', 'expires_at_ms': None}
                    for action in ('create', 'update')],
            expected_revision=account_policy.read(uid).revision)
