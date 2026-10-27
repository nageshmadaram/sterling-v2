"""Account policy contract tests use disposable SQLite only."""
import itertools
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import HTTPException, Response
from pydantic import ValidationError

from app.api.v1.endpoints import sterling_users as api
from app.core.auth import UserContext
from app.services import db
from app.services.auth import account_policy as policy, operator_credentials, user_store


@pytest.fixture
def identities(tmp_path, monkeypatch):
    monkeypatch.setattr(db, '_DB_PATH', str(tmp_path / 'policy.db'))
    assert db.init()
    user_store._init_table()
    monkeypatch.setattr(user_store, '_users', {})
    admin = user_store.add('primary', 'Synthetic-admin-password-123', role='admin', user_id='default')
    member = user_store.add('member', 'Synthetic-member-password-123', user_id='member')
    return admin, member


def test_default_deny_and_all_eight_combinations(identities):
    assert policy.read('member').features == dict.fromkeys(policy.FEATURES, False)
    assert not policy.read('missing').active
    assert policy.read('default').administrator
    assert not any(policy.read('default').features.values())  # admin does not imply feature access
    for revision, values in enumerate(itertools.product((False, True), repeat=3)):
        grants = dict(zip(policy.FEATURES, values))
        saved = policy.set_features('default', 'member', grants, revision)
        assert saved.revision == revision + 1
        assert saved.features == grants
        for feature, enabled in grants.items():
            if enabled:
                policy.require_feature('member', feature)
            else:
                with pytest.raises(PermissionError):
                    policy.require_feature('member', feature)
    with db._conn() as c:
        assert c.execute('SELECT COUNT(*) FROM auth_audit_events').fetchone()[0] == 8
        assert c.execute('SELECT COUNT(*) FROM auth_event_outbox').fetchone()[0] == 8


def test_denied_mutations_and_conflicts_do_not_commit(identities):
    grants = dict.fromkeys(policy.FEATURES, True)
    with pytest.raises(PermissionError):
        policy.set_features('member', 'member', grants, 0)
    policy.set_features('default', 'member', grants, 0)
    with pytest.raises(policy.PolicyConflict):
        policy.set_features('default', 'member', dict.fromkeys(policy.FEATURES, False), 0)
    assert all(policy.read('member').features.values())
    with pytest.raises(ValueError):
        policy.set_features('default', 'member', {'kite': 1, 'tv': False, 'radio': False}, 1)
    with db._conn() as c:
        c.execute("UPDATE users SET is_active=0 WHERE id='member'")
    assert not any(policy.read('member').features.values())


def test_audit_failure_rolls_back_grants_and_revision(identities):
    with db._conn() as c:
        c.execute("CREATE TRIGGER reject_audit BEFORE INSERT ON auth_audit_events BEGIN SELECT RAISE(ABORT,'synthetic failure'); END")
    with pytest.raises(policy.PolicyUnavailable):
        policy.set_features('default', 'member', dict.fromkeys(policy.FEATURES, True), 0)
    assert policy.read('member').revision == 0
    assert not any(policy.read('member').features.values())
    with db._conn() as c:
        assert c.execute('SELECT COUNT(*) FROM auth_event_outbox').fetchone()[0] == 0
    with pytest.raises(ValueError):
        operator_credentials.manage('default', 'add', 'another', 'Synthetic-password-123')
    assert user_store.get_by_username('another') is None
    with db._conn() as c:
        assert not c.execute("SELECT 1 FROM users WHERE username='another'").fetchone()


@pytest.mark.parametrize('statement', [
    "INSERT INTO users(id,username,role,created_at_ms,updated_at_ms) VALUES('second','second','admin',0,0)",
    "UPDATE users SET role='admin' WHERE id='member'",
    "UPDATE users SET role='user' WHERE id='default'",
    "UPDATE users SET is_active=0 WHERE id='default'",
    "DELETE FROM users WHERE id='default'",
    "DELETE FROM platform_admin",
    "UPDATE platform_admin SET user_id='member'",
    "INSERT OR REPLACE INTO users(id,username,role,created_at_ms,updated_at_ms) VALUES('default','primary','user',0,0)",
])
def test_singleton_admin_even_for_direct_sql(identities, statement):
    with pytest.raises(sqlite3.IntegrityError):
        with db._conn() as c:
            c.execute(statement)
    assert policy.read('default').administrator


def test_service_prevents_second_admin(identities):
    with pytest.raises(ValueError, match='one designated'):
        user_store.add('second', 'Synthetic-password-123', role='admin')
    with pytest.raises(ValueError, match='one designated'):
        operator_credentials.manage('default', 'add', 'second', 'Synthetic-password-123', role='admin')
    with pytest.raises(ValueError, match='one designated'):
        operator_credentials.manage('default', 'update', 'member', 'Synthetic-password-123', target_id='member', role='admin')


def test_competing_revisions_have_one_winner(identities):
    def update(enabled):
        try:
            policy.set_features('default', 'member', dict.fromkeys(policy.FEATURES, enabled), 0)
            return 'saved'
        except policy.PolicyConflict:
            return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(update, (True, False))) == ['conflict', 'saved']
    assert policy.read('member').revision == 1


def test_persistence_and_unavailable_store(identities, monkeypatch):
    policy.set_features('default', 'member', dict.fromkeys(policy.FEATURES, True), 0)
    user_store._init_table()  # idempotent restart migration
    assert all(policy.read('member').features.values())
    monkeypatch.setattr(db, '_available', False)
    with pytest.raises(policy.PolicyUnavailable):
        policy.require_feature('member', 'kite')
    version = user_store.get_by_id('member').token_version
    with pytest.raises(RuntimeError):
        user_store.bump_token_version('member')
    assert user_store.get_by_id('member').token_version == version


def test_legacy_ambiguous_admin_migration_is_atomic(tmp_path, monkeypatch):
    monkeypatch.setattr(db, '_DB_PATH', str(tmp_path / 'legacy.db'))
    assert db.init()
    with db._conn() as c:
        c.execute('CREATE TABLE users(id TEXT PRIMARY KEY,username TEXT UNIQUE,password_hash TEXT,role TEXT,is_active INTEGER,token_version INTEGER,created_at_ms INTEGER,updated_at_ms INTEGER)')
        c.executemany("INSERT INTO users VALUES(?,?,'','admin',1,1,0,0)", [('default','primary'),('second','second')])
    with pytest.raises(RuntimeError, match='migration failed'):
        user_store._init_table()
    with db._conn() as c:
        assert c.execute("SELECT COUNT(*) FROM users WHERE role='admin'").fetchone()[0] == 2
        assert not c.execute("SELECT name FROM sqlite_master WHERE name='platform_admin'").fetchone()


@pytest.mark.asyncio
async def test_api_conflict_and_strict_contract(identities):
    actor = UserContext(user_id='default', role='admin')
    body = api.FeatureEdit(expected_revision=0, features=api.FeatureGrants(kite=False, tv=True, radio=False))
    result = await api.update_features('member', body, Response(), actor)
    assert result['features']['tv']
    with pytest.raises(HTTPException) as conflict:
        await api.update_features('member', body, Response(), actor)
    assert conflict.value.status_code == 409
    with pytest.raises(ValidationError):
        api.FeatureGrants(kite='true', tv=False, radio=False)
    with pytest.raises(ValidationError):
        api.UserEdit(username='member', administrator=True)


def test_preflight_is_read_only_and_redacted(identities):
    from pathlib import Path
    from scripts.account_policy_preflight import inspect
    path = Path(db._DB_PATH)
    before = path.read_bytes()
    report = inspect(path)
    assert report['ready']
    assert report['administrator_ids'] == ['default']
    assert 'password' not in str(report)
    assert path.read_bytes() == before
