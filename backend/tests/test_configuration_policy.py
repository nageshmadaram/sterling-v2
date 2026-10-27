"""Delegation and legacy API denial: disposable DBs, synthetic providers only."""
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Response
from fastapi.testclient import TestClient
from fastapi import FastAPI

from app.core.auth import UserContext, get_current_user
from app.services import db
from app.services.auth import account_policy, configuration_policy as policy, user_store
from app.api.v1.endpoints import settings_capabilities, kite, kite_telegram, kite_login_credentials, truedata
from app.services.exchanges.kite.models import KiteAccountCreateRequest, KiteAccountUpdate


@pytest.fixture
def identities(tmp_path, monkeypatch):
    monkeypatch.setattr(db, '_DB_PATH', str(tmp_path / 'configuration.db'))
    assert db.init()
    user_store._init_table()
    monkeypatch.setattr(user_store, '_users', {})
    user_store.add('primary', 'Synthetic-password-123', role='admin', user_id='default')
    user_store.add('member', 'Synthetic-password-123', user_id='member')
    user_store.add('other', 'Synthetic-password-123', user_id='other')
    return UserContext(user_id='default', role='admin'), UserContext(user_id='member')


def grant(section='brokers', action='update', resource='own', **kwargs):
    revision = policy.read('member')['revision']
    return policy.replace_section('default', 'member', section, visible=True,
        grants=[{'action': action, 'resource_id': resource, 'expires_at_ms': None}], expected_revision=revision, **kwargs)


def test_default_hidden_and_independent_sections(identities):
    assert all(not s['visible'] for s in policy.read('member')['sections'].values())
    grant()
    policy.require('member', 'brokers', 'update', 'assigned')
    for section in ('telegram', 'integrations'):
        with pytest.raises(PermissionError):
            policy.require('member', section, 'update')
    with pytest.raises(PermissionError):
        policy.require('member', 'brokers', 'create')
    with pytest.raises(PermissionError):
        policy.require('other', 'brokers', 'update')
    assert not account_policy.read('member').features['kite']


def test_scopes_expiry_revocation_and_no_reveal(identities):
    grant(resource='one')
    policy.require('member', 'brokers', 'update', 'one')
    with pytest.raises(PermissionError):
        policy.require('member', 'brokers', 'update', 'two')
    with pytest.raises(PermissionError):
        policy.require('member', 'brokers', 'reveal', 'one')
    with db._conn() as c:
        c.execute("UPDATE user_configuration_grants SET expires_at_ms=1 WHERE user_id='member'")
    with pytest.raises(PermissionError):
        policy.require('member', 'brokers', 'update', 'one')
    grant()
    policy.replace_section('default', 'member', 'brokers', visible=False, grants=[], expected_revision=2)
    with pytest.raises(PermissionError):
        policy.require('member', 'brokers', 'update', 'one')
    # Even administrators cannot use the old raw reveal route without challenge infrastructure.
    with pytest.raises(PermissionError, match='MFA'):
        policy.require('default', 'brokers', 'reveal')


def test_only_current_designated_admin_can_delegate(identities):
    with pytest.raises(PermissionError):
        policy.replace_section('member', 'member', 'brokers', visible=True, grants=[], expected_revision=0)
    with pytest.raises(ValueError):
        policy.replace_section('default', 'default', 'brokers', visible=False, grants=[], expected_revision=0)
    grant()
    with pytest.raises(account_policy.PolicyConflict):
        policy.replace_section('default', 'member', 'brokers', visible=False, grants=[], expected_revision=0)
    with db._conn() as c:
        c.execute("UPDATE users SET is_active=0 WHERE id='member'")
    with pytest.raises(PermissionError):
        policy.require('member', 'brokers', 'update')


@pytest.mark.parametrize('action,resource', [('reveal','own'),('update','*'),('update','global'),('create','one')])
def test_ineligible_scope_rejected(identities, action, resource):
    with pytest.raises(ValueError):
        grant(action=action, resource=resource)
    assert policy.read('member')['revision'] == 0


def test_audit_failure_rolls_back_and_features_share_revision(identities):
    account_policy.set_features('default', 'member', {'kite': True, 'tv': False, 'radio': False}, 0)
    grant()
    assert account_policy.read('member').revision == 2
    with db._conn() as c:
        c.execute("CREATE TRIGGER reject_configuration_audit BEFORE INSERT ON auth_audit_events BEGIN SELECT RAISE(ABORT,'synthetic'); END")
    with pytest.raises(account_policy.PolicyUnavailable):
        policy.replace_section('default', 'member', 'brokers', visible=False, grants=[], expected_revision=2)
    assert policy.read('member')['revision'] == 2
    policy.require('member', 'brokers', 'update')
    with db._conn() as c:
        assert c.execute('SELECT COUNT(*) FROM auth_event_outbox').fetchone()[0] == 2
        events = json.dumps([dict(r) for r in c.execute('SELECT * FROM auth_audit_events')])
    assert 'Synthetic-password' not in events


@pytest.mark.asyncio
async def test_legacy_broker_writes_rejected_before_provider_lookup(identities, monkeypatch):
    admin, member = identities
    def forbidden(*args, **kwargs):
        raise AssertionError('Unauthorized request touched broker store')
    monkeypatch.setattr(kite.kite_accounts, 'get', forbidden)
    monkeypatch.setattr(kite.kite_accounts, 'add', forbidden)
    for operation in (
        lambda: kite.add_account(KiteAccountCreateRequest(label='A', api_key='synthetic', api_secret='synthetic'), member),
        lambda: kite.update_account('one', KiteAccountUpdate(label='Changed'), member),
        lambda: kite.delete_account('one', False, member),
        lambda: kite.reveal_account_credentials('one', Response(), member),
        lambda: kite.reveal_account_credentials('one', Response(), admin),
        lambda: kite_login_credentials.save_credentials('one', kite_login_credentials.LoginCredentials(username='AA001', password='synthetic'), Response(), member),
        lambda: kite_login_credentials.remove_credentials('one', member),
    ):
        with pytest.raises(HTTPException) as denied:
            await operation()
        assert denied.value.status_code == 403


@pytest.mark.asyncio
async def test_delegated_broker_replacement_remains_owner_scoped(identities, monkeypatch):
    _, member = identities
    grant(resource='one')
    monkeypatch.setattr(kite.kite_accounts, 'get', lambda uid, aid: None)
    with pytest.raises(HTTPException) as not_owned:
        await kite.update_account('one', KiteAccountUpdate(label='Changed'), member)
    assert not_owned.value.status_code == 404
    with pytest.raises(HTTPException) as wrong_scope:
        await kite.update_account('two', KiteAccountUpdate(label='Changed'), member)
    assert wrong_scope.value.status_code == 403


@pytest.mark.asyncio
async def test_telegram_scopes_do_not_allow_shared_token_or_test(identities, monkeypatch):
    _, member = identities
    grant(section='telegram', action='recipients', resource='target')
    calls = []
    monkeypatch.setattr(kite_telegram.store, 'update', lambda *a, **kw: calls.append(kw) or None)
    with pytest.raises(HTTPException) as missing:
        await kite_telegram.update_target('target', kite_telegram.TargetPatch(chat_id='123'), member)
    assert missing.value.status_code == 404
    assert len(calls) == 1
    with pytest.raises(HTTPException) as token:
        await kite_telegram.update_target('target', kite_telegram.TargetPatch(bot_token='synthetic'), member)
    assert token.value.status_code == 403 and len(calls) == 1
    with pytest.raises(HTTPException) as send:
        await kite_telegram.test_target('target', member)
    assert send.value.status_code == 403
    with pytest.raises(HTTPException):
        await kite_telegram.create_target(kite_telegram.TargetIn(label='A', bot_token='synthetic', chat_id='123'), member)


@pytest.mark.asyncio
async def test_provider_configuration_default_denied(identities):
    _, member = identities
    with pytest.raises(HTTPException) as denied:
        await truedata.list_credentials(member)
    assert denied.value.status_code == 403


def test_http_contract_and_simulation_cannot_inherit_authority(identities):
    admin, member = identities
    app = FastAPI()
    app.include_router(settings_capabilities.router, prefix='/api/v1')
    app.dependency_overrides[get_current_user] = lambda: member
    client = TestClient(app)
    response = client.get('/api/v1/settings/capabilities')
    assert response.status_code == 200
    assert response.headers['cache-control'] == 'no-store, private'
    assert not response.json()['sections']['brokers']['visible']
    body = {'section': 'brokers', 'visible': True, 'grants': [], 'expected_revision': 0}
    assert client.put('/api/v1/auth/users/member/configuration-access', json=body).status_code == 403
    app.dependency_overrides[get_current_user] = lambda: admin
    assert client.put('/api/v1/auth/users/member/configuration-access', json=body).status_code == 200
    assert client.put('/api/v1/auth/users/member/configuration-access', json=body).status_code == 409
    assert client.put('/api/v1/auth/users/member/configuration-access', json={**body, 'role': 'admin'}).status_code == 422
    app.dependency_overrides[get_current_user] = lambda: UserContext(user_id='SIM', role='admin', view='simulation', owner_uid='default')
    assert client.get('/api/v1/settings/capabilities').status_code == 403


def test_unavailable_policy_does_not_fall_back_to_owner_or_role(identities, monkeypatch):
    monkeypatch.setattr(db, '_available', False)
    with pytest.raises(account_policy.PolicyUnavailable):
        policy.require('default', 'brokers', 'update')


@pytest.mark.asyncio
async def test_revocation_during_broker_safety_probe_prevents_the_write(identities, monkeypatch):
    _, member = identities
    grant(resource='one')
    account = SimpleNamespace(id='one', is_paper=False)
    monkeypatch.setattr(kite.kite_accounts, 'get', lambda *a: account)
    async def probe(*args, **kwargs):
        policy.replace_section('default', 'member', 'brokers', visible=False, grants=[], expected_revision=1)
    monkeypatch.setattr(kite, '_require_account_detachable', probe)
    writes = []
    monkeypatch.setattr(kite.kite_accounts, 'update', lambda *a: writes.append(a))
    with pytest.raises(HTTPException) as denied:
        await kite.update_account('one', KiteAccountUpdate(api_key='synthetic-replacement'), member)
    assert denied.value.status_code == 403
    assert not writes


@pytest.mark.asyncio
async def test_all_provider_credential_writes_are_default_denied(identities, monkeypatch):
    _, member = identities
    from app.services.providers.truedata.models import TrueDataCredentialCreate, TrueDataCredentialUpdate
    writes = []
    monkeypatch.setattr(truedata.truedata_service, 'add', lambda *a: writes.append(a))
    for operation in (
        lambda: truedata.add_credentials(TrueDataCredentialCreate(label='A', username='synthetic', password='synthetic'), member),
        lambda: truedata.update_credentials('one', TrueDataCredentialUpdate(label='A'), member),
        lambda: truedata.delete_credentials('one', member),
        lambda: truedata.update_settings(truedata.TrueDataSettingsModel(data_source='yahoo'), member),
        lambda: truedata.run_diagnostics(None, member),
    ):
        with pytest.raises(HTTPException) as denied:
            await operation()
        assert denied.value.status_code == 403
    assert not writes
