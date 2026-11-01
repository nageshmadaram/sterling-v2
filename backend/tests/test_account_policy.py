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



@pytest.mark.asyncio
async def test_administrator_reason_code_is_durable_and_not_an_approval(identities):
    actor = UserContext(user_id='default', role='admin')
    payload = api.FeatureEdit(
        expected_revision=0,
        features=api.FeatureGrants(kite=True, tv=False, radio=False),
        reason_code="ACCESS_REVIEW",
    )
    updated = await api.update_features('member', payload, Response(), actor)
    assert updated["revision"] == 1
    with db._conn() as c:
        audit = c.execute(
            "SELECT actor_id,target_id,action,reason_code,before_json,after_json,created_at_ms "
            "FROM auth_audit_events WHERE action='features.updated'"
        ).fetchone()
        assert audit["actor_id"] == "default" and audit["target_id"] == "member"
        assert audit["reason_code"] == "ACCESS_REVIEW"
        assert audit["created_at_ms"] > 0
        assert '"kite": true' in audit["after_json"]
        assert '"kite": false' in audit["before_json"]
    user_store._init_table()  # repeated migration preserves reason and event
    with db._conn() as c:
        assert c.execute("SELECT reason_code FROM auth_audit_events").fetchone()[0] == "ACCESS_REVIEW"
    with pytest.raises(ValidationError):
        api.FeatureEdit(
            expected_revision=1,
            features=api.FeatureGrants(kite=False, tv=False, radio=False),
            reason_code="user supplied free-form secret",
        )


def test_unsupplied_reason_is_honestly_recorded_as_not_provided(identities):
    policy.set_features("default", "member", dict.fromkeys(policy.FEATURES, True), 0)
    with db._conn() as c:
        assert c.execute("SELECT reason_code FROM auth_audit_events").fetchone()[0] == "NOT_PROVIDED"



def test_historical_audit_schema_upgrade_preserves_existing_events(tmp_path, monkeypatch):
    """The additive reason-code migration must not rewrite prior audit causality."""
    monkeypatch.setattr(db, '_DB_PATH', str(tmp_path / 'legacy-audit.sqlite'))
    assert db.init()
    with db._conn() as c:
        c.execute("""CREATE TABLE auth_audit_events (
            id TEXT PRIMARY KEY, actor_id TEXT NOT NULL, target_id TEXT NOT NULL,
            action TEXT NOT NULL, before_json TEXT NOT NULL, after_json TEXT NOT NULL,
            created_at_ms INTEGER NOT NULL
        )""")
        c.execute(
            "INSERT INTO auth_audit_events VALUES(?,?,?,?,?,?,?)",
            ('legacy-event', 'historical-actor', 'historical-member', 'features.updated',
             '{"kite": false}', '{"kite": true}', 1660000000000),
        )
    user_store._init_table()
    user_store._init_table()  # second migration is required to be a no-op
    with db._conn() as c:
        rows = c.execute(
            'SELECT * FROM auth_audit_events WHERE id=?', ('legacy-event',)
        ).fetchall()
        assert len(rows) == 1
        record = dict(rows[0])
        assert record['actor_id'] == 'historical-actor'
        assert record['target_id'] == 'historical-member'
        assert record['action'] == 'features.updated'
        assert record['before_json'] == '{"kite": false}'
        assert record['after_json'] == '{"kite": true}'
        assert record['created_at_ms'] == 1660000000000
        assert record['reason_code'] == 'NOT_PROVIDED'
