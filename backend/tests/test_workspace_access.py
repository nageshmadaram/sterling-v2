"""Real HTTP/migration admission tests with disposable data and no broker I/O."""
import hashlib
import sqlite3
import time
from pathlib import Path

import pytest
from fastapi import FastAPI, Depends
from fastapi.testclient import TestClient
from app.core.auth import get_current_user, UserContext
from app.core.feature_access import enforce_feature_access, route_policy
from app.api.v1.endpoints import media, admin_connections
from app.services import db
from app.services.auth import user_store, account_policy


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(db, '_DB_PATH', str(tmp_path / 'workspace.db'))
    assert db.init()
    user_store._init_table()
    monkeypatch.setattr(user_store, '_users', {})
    monkeypatch.setattr(user_store, '_loaded', True)
    user_store.add('primary', 'Synthetic-password-123', role='admin', user_id='default')
    user_store.add('member', 'Synthetic-password-123', user_id='member')
    return UserContext(user_id='default', role='admin'), UserContext(user_id='member')


def app_for_media(user):
    app = FastAPI()
    app.include_router(media.router, prefix='/api/v1')
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def test_media_documents_are_private_finite_and_bound_to_features(workspace):
    _, member = workspace
    client = app_for_media(member)
    assert client.get('/api/v1/media/tv/document').status_code == 401
    assert client.post('/api/v1/media/tv/access').status_code == 403
    account_policy.set_features('default', 'member', {'kite': False, 'tv': True, 'radio': False}, 0)
    issued = client.post('/api/v1/media/tv/access')
    assert issued.status_code == 200
    assert 'HttpOnly' in issued.headers['set-cookie'] and 'SameSite=lax' in issued.headers['set-cookie']
    credential = client.cookies.get('sterling_media_tv')
    with db._conn() as c:
        saved = c.execute('SELECT * FROM media_document_sessions').fetchone()
    assert saved['credential_hash'] == hashlib.sha256(credential.encode()).hexdigest()
    assert saved['expires_at'] - saved['created_at'] == media.TTL_SECONDS
    assert credential not in str(dict(saved))
    document = client.get('/api/v1/media/tv/document')
    assert document.status_code == 200 and 'Sterling TV' in document.text
    csp = document.headers['content-security-policy']
    assert 'sandbox allow-scripts' in csp and 'allow-same-origin' not in csp
    assert document.headers['cache-control'] == 'no-store, private'
    assert client.head('/api/v1/media/tv/document').status_code == 200
    assert client.get('/api/v1/media/radio/document').status_code == 401
    assert client.post('/api/v1/media/radio/access').status_code == 403
    account_policy.set_features('default', 'member', {'kite': False, 'tv': False, 'radio': False}, 1)
    assert client.get('/api/v1/media/tv/document').status_code == 403


def test_media_revokes_on_logout_suspension_and_expiry(workspace):
    _, member = workspace
    account_policy.set_features('default', 'member', dict.fromkeys(account_policy.FEATURES, True), 0)
    client = app_for_media(member)
    assert client.post('/api/v1/media/radio/access').status_code == 200
    user_store.bump_token_version('member')
    assert client.get('/api/v1/media/radio/document').status_code == 403
    client.post('/api/v1/media/radio/access')
    with db._conn() as c:
        c.execute('UPDATE media_document_sessions SET expires_at=0')
    assert client.get('/api/v1/media/radio/document').status_code == 403
    with db._conn() as c:
        c.execute("UPDATE users SET is_active=0 WHERE id='member'")
    assert client.post('/api/v1/media/radio/access').status_code == 403


def test_cookie_does_not_authenticate_trading_in_production(workspace, monkeypatch):
    from app.core import tokens
    from app.core.auth import get_current_user
    monkeypatch.setenv('ENVIRONMENT', 'production')
    app = FastAPI()
    app.include_router(media.router, prefix='/api/v1')
    @app.get('/api/v1/private')
    async def private(user=Depends(get_current_user)):
        return {'id': user.user_id}
    with TestClient(app) as client:
        response = client.get('/api/v1/private', cookies={'__Secure-sterling_media_tv': 'synthetic'})
        assert response.status_code == 401


def test_global_http_policy_default_denies_new_private_routes(workspace):
    # Use the real app auth path (not dependency overrides) to check the process boundary.
    from app.core import tokens
    _, member = workspace
    app = FastAPI(dependencies=[Depends(enforce_feature_access)])
    @app.get('/api/v1/new-private-route')
    async def data():
        return {'private': True}
    token = tokens.mint_access('member', 'member', 'user', 1)
    with TestClient(app) as client:
        headers = {'Authorization': 'Bearer ' + token}
        assert client.get('/api/v1/new-private-route', headers=headers).status_code == 403
        account_policy.set_features('default', 'member', {'kite': True, 'tv': False, 'radio': False}, 0)
        assert client.get('/api/v1/new-private-route', headers=headers).status_code == 200
        account_policy.set_features('default', 'member', dict.fromkeys(account_policy.FEATURES, False), 1)
        assert client.get('/api/v1/new-private-route', headers=headers).status_code == 403
    assert route_policy('/api/v1/kite/callback', 'POST') == 'kite'
    assert route_policy('/api/v1/kite/callback', 'GET') == 'provider'
    assert route_policy('/api/v1/kite/postback', 'POST') == 'provider'
    assert route_policy('/api/v1/auth/users', 'GET') == 'identity'


def test_administrator_can_manage_brokers_without_kite(workspace, monkeypatch):
    admin, member = workspace
    app = FastAPI()
    app.include_router(admin_connections.router, prefix='/api/v1')
    app.dependency_overrides[get_current_user] = lambda: member
    client = TestClient(app)
    assert client.get('/api/v1/admin/brokers').status_code == 403
    app.dependency_overrides[get_current_user] = lambda: admin
    monkeypatch.setattr(admin_connections.accounts, 'live_accounts', lambda: [])
    assert not account_policy.read('default').features['kite']
    assert client.get('/api/v1/admin/brokers').json() == {'brokers': []}


def test_entry_feature_gate_does_not_replace_exposure_classification(workspace):
    assert account_policy.entry_refusal('member')
    account_policy.set_features('default', 'member', {'kite': True, 'tv': False, 'radio': False}, 0)
    assert account_policy.entry_refusal('member') is None
    assert account_policy.entry_refusal('sim:member') is None
    account_policy.set_features('default', 'member', dict.fromkeys(account_policy.FEATURES, False), 1)
    assert account_policy.entry_refusal('member')


@pytest.mark.asyncio
async def test_canonical_new_entry_rejects_without_feature_before_broker_io(workspace):
    from app.services.execution_service import CanonicalExecutionService, ExecutionRequest
    request = ExecutionRequest(uid='member', account_id='synthetic', strategy_id='supertrend', generation_id='g',
        signal_id='s', symbol='SYNTHETIC', exchange='NFO', side='BUY', quantity=1, tag='synthetic')
    result = await CanonicalExecutionService().submit_order(request)
    assert result.status == 'REJECTED_FEATURE_ACCESS'


def test_main_application_preserves_player_nonce_csp(workspace):
    from main import create_app
    from app.core import tokens
    account_policy.set_features('default', 'member', {'kite': False, 'tv': True, 'radio': False}, 0)
    token = tokens.mint_access('member', 'member', 'user', 1)
    client = TestClient(create_app(), raise_server_exceptions=True)
    issued = client.post('/api/v1/media/tv/access', headers={'Authorization': 'Bearer ' + token})
    assert issued.status_code == 200
    response = client.get('/api/v1/media/tv/document')
    assert response.status_code == 200
    csp = response.headers['content-security-policy']
    assert "script-src 'self' 'nonce-" in csp
    assert 'sandbox allow-scripts' in csp
    assert response.headers.get('x-frame-options') != 'DENY'
    assert 'nonce=""' not in response.text


def test_central_broker_creation_is_masked_audited_and_rolls_back_on_audit_failure(workspace, monkeypatch):
    admin, _ = workspace
    monkeypatch.setattr(admin_connections.accounts, '_accounts', {})
    app = FastAPI()
    app.include_router(admin_connections.router, prefix='/api/v1')
    app.dependency_overrides[get_current_user] = lambda: admin
    client = TestClient(app)
    body = {'user_id': 'member', 'label': 'Assigned broker', 'api_key': 'synthetic-key', 'api_secret': 'synthetic-secret'}
    response = client.post('/api/v1/admin/brokers', json=body)
    assert response.status_code == 200
    assert response.json()['user_id'] == 'member'
    assert 'synthetic-secret' not in response.text
    assert 'synthetic-key' not in response.text
    assert not account_policy.read('member').features['kite']
    with db._conn() as c:
        assert c.execute("SELECT COUNT(*) FROM auth_audit_events WHERE action='broker.created'").fetchone()[0] == 1
        c.execute("CREATE TRIGGER reject_broker_audit BEFORE INSERT ON auth_audit_events BEGIN SELECT RAISE(ABORT,'synthetic'); END")
    failed = client.post('/api/v1/admin/brokers', json={**body, 'label': 'Must roll back'})
    assert failed.status_code == 503
    with db._conn() as c:
        assert c.execute('SELECT COUNT(*) FROM kite_accounts').fetchone()[0] == 1
    assert len(admin_connections.accounts.live_accounts()) == 1


@pytest.mark.asyncio
async def test_interactive_login_revocation_before_exchange_completion_does_not_save(workspace, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from app.services.exchanges.kite import login_recovery
    account_policy.set_features('default', 'member', {'kite': True, 'tv': False, 'radio': False}, 0)
    async def exchange(_token):
        account_policy.set_features('default', 'member', dict.fromkeys(account_policy.FEATURES, False), 1)
        return {'access_token': 'synthetic', 'user_id': 'SYNTHETIC'}
    broker = SimpleNamespace(generate_session=exchange, close=AsyncMock())
    monkeypatch.setattr(login_recovery.accounts, 'build_client', lambda _: broker)
    saves = []
    monkeypatch.setattr(login_recovery.accounts, 'save_session', lambda *a, **kw: saves.append(kw))
    with pytest.raises(login_recovery.LoginAccessError):
        await login_recovery.complete_login('member', SimpleNamespace(id='one', api_key='synthetic', api_secret='synthetic'), 'synthetic')
    assert saves == []


def test_suspension_clears_access_and_restore_does_not_resurrect_grants(workspace):
    from app.services.auth import operator_credentials, configuration_policy
    account_policy.set_features('default', 'member', dict.fromkeys(account_policy.FEATURES, True), 0)
    configuration_policy.replace_section('default', 'member', 'brokers', visible=True,
        grants=[{'action': 'update', 'resource_id': 'own', 'expires_at_ms': None}], expected_revision=1)
    suspended = operator_credentials.manage('default', 'update', 'member', '', target_id='member', active=False)
    assert not suspended.is_active
    assert account_policy.entry_refusal('member')
    restored = operator_credentials.manage('default', 'update', 'member', '', target_id='member', active=True)
    assert restored.is_active
    assert not any(account_policy.read('member').features.values())
    assert not configuration_policy.read('member')['sections']['brokers']['visible']
    assert restored.token_version == 3


def test_supervisor_liveness_survives_default_deny_without_credentials():
    from app.api.v1.endpoints.snapback_ops import health_live
    app = FastAPI(dependencies=[Depends(enforce_feature_access)])
    app.add_api_route('/api/v1/health/live', health_live, methods=['GET'])
    with TestClient(app) as client:
        assert client.get('/api/v1/health/live').json()['alive'] is True
    assert route_policy('/api/v1/health', 'GET') == 'public'
    assert route_policy('/api/v1/health/ready', 'GET') == 'public'
    assert route_policy('/api/v1/health/deep', 'GET') == 'kite'
    assert route_policy('/api/v1/health/unknown', 'GET') == 'kite'
    assert route_policy('/api/v1/health/live', 'POST') == 'kite'
