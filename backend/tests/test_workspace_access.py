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


def test_access_inbox_deduplicates_approves_and_preserves_other_grants(workspace):
    from app.services.auth import access_requests as inbox
    account_policy.set_features('default','member',{'kite':False,'radio':False,'tv':True},0)
    request=inbox.request_access('member','radio')
    assert inbox.request_access('member','radio')['id']==request['id']
    assert len(inbox.list_requests('member'))==1
    with pytest.raises(PermissionError): inbox.list_requests('member',administrator=True)
    assert inbox.list_requests('default',administrator=True)[0]['username']=='member'
    inbox.decide('default',request['id'],'approved')
    assert account_policy.read('member').features=={'kite':False,'radio':True,'tv':True}
    assert account_policy.read('member').revision==2
    with pytest.raises(account_policy.PolicyConflict): inbox.decide('default',request['id'],'rejected')
    request=inbox.request_access('member','kite')
    inbox.decide('default',request['id'],'rejected')
    assert not account_policy.read('member').features['kite']
    assert inbox.request_access('member','kite')['status']=='rejected'


def test_access_approval_rolls_back_if_audit_fails(workspace):
    from app.services.auth import access_requests as inbox
    request=inbox.request_access('member','radio')
    with db._conn() as c:
        c.execute("CREATE TRIGGER reject_access_audit BEFORE INSERT ON auth_audit_events BEGIN SELECT RAISE(ABORT,'synthetic'); END")
    with pytest.raises(account_policy.PolicyUnavailable): inbox.decide('default',request['id'],'approved')
    assert not account_policy.read('member').features['radio']
    assert inbox.list_requests('member')[0]['status']=='pending'


def test_access_routes_are_owner_scoped_and_admin_reviewed(workspace):
    from app.api.v1.endpoints import settings_capabilities
    admin, member=workspace
    app=FastAPI();app.include_router(settings_capabilities.router,prefix='/api/v1')
    app.dependency_overrides[get_current_user]=lambda:member
    client=TestClient(app)
    request=client.post('/api/v1/settings/access-requests',json={'feature':'radio'}).json()
    assert client.get('/api/v1/admin/access-requests').status_code==403
    assert client.patch('/api/v1/admin/access-requests/'+request['id'],json={'decision':'approved'}).status_code==403
    app.dependency_overrides[get_current_user]=lambda:admin
    assert client.patch('/api/v1/admin/access-requests/'+request['id'],json={'decision':'approved'}).status_code==200
    assert account_policy.read('member').features['radio']


def test_broker_assignment_clears_session_is_durable_and_preserves_history(workspace,monkeypatch):
    from app.services.exchanges.kite import accounts
    from app.services.exchanges.kite.models import KiteAccountCreate
    monkeypatch.setattr(accounts,'_accounts',{})
    broker=accounts.add('default',KiteAccountCreate(label='Profile',api_key='synthetic',api_secret='synthetic-secret'))
    before=broker.updated_at_ms
    db.set_config_strict(f'kite_login_credentials:default:{broker.id}','fernet:synthetic-ciphertext')
    with db._conn() as c: c.execute("UPDATE kite_accounts SET access_token_enc='saved-session' WHERE id=?",(broker.id,))
    result=accounts.reassign(broker.id,actor_id='default',source_user_id='default',target_user_id='member',expected_updated_at_ms=before)
    assert result.user_id=='member' and not result.is_active and not result.access_token_enc
    assert accounts.get('default',broker.id) is None
    assert db.get_config_strict(f'kite_login_credentials:member:{broker.id}','')=='fernet:synthetic-ciphertext'
    assert not db.get_config_strict(f'kite_login_credentials:default:{broker.id}','')
    with db._conn() as c:
        assert c.execute('SELECT user_id FROM kite_accounts WHERE id=?',(broker.id,)).fetchone()[0]=='member'
        audit=c.execute("SELECT before_json,after_json FROM auth_audit_events WHERE action='broker.assigned'").fetchone()
        assert 'default' in audit['before_json'] and 'member' in audit['after_json']
    with pytest.raises(account_policy.PolicyConflict): accounts.reassign(broker.id,actor_id='default',source_user_id='default',target_user_id='member',expected_updated_at_ms=before)


def test_broker_assignment_audit_failure_keeps_cache_and_database_owner(workspace,monkeypatch):
    from app.services.exchanges.kite import accounts
    from app.services.exchanges.kite.models import KiteAccountCreate
    monkeypatch.setattr(accounts,'_accounts',{})
    broker=accounts.add('default',KiteAccountCreate(label='Profile',api_key='synthetic',api_secret='synthetic-secret'))
    with db._conn() as c: c.execute("CREATE TRIGGER reject_assignment_audit BEFORE INSERT ON auth_audit_events BEGIN SELECT RAISE(ABORT,'synthetic'); END")
    with pytest.raises(RuntimeError): accounts.reassign(broker.id,actor_id='default',source_user_id='default',target_user_id='member',expected_updated_at_ms=broker.updated_at_ms)
    assert accounts.get('default',broker.id).user_id=='default'
    with db._conn() as c: assert c.execute('SELECT user_id FROM kite_accounts WHERE id=?',(broker.id,)).fetchone()[0]=='default'


def test_assignment_endpoint_refuses_open_exposure_before_changing_owner(workspace,monkeypatch):
    from fastapi import HTTPException
    from app.api.v1.endpoints import kite
    from app.services.exchanges.kite import accounts
    from app.services.exchanges.kite.models import KiteAccountCreate
    monkeypatch.setattr(accounts,'_accounts',{})
    broker=accounts.add('default',KiteAccountCreate(label='Profile',api_key='synthetic',api_secret='synthetic-secret'))
    async def blocked(*args,**kwargs): raise HTTPException(423,'Broker still holds positions')
    monkeypatch.setattr(kite,'_require_account_detachable',blocked)
    app=FastAPI();app.include_router(admin_connections.router,prefix='/api/v1')
    app.dependency_overrides[get_current_user]=lambda:workspace[0]
    client=TestClient(app)
    response=client.post(f'/api/v1/admin/brokers/{broker.id}/assignment',json={'user_id':'member','expected_user_id':'default','expected_updated_at_ms':broker.updated_at_ms})
    assert response.status_code==423
    assert accounts.get('default',broker.id).user_id=='default'


def test_directory_is_admin_only_and_bulk_grants_are_atomic(workspace,monkeypatch):
    admin,member=workspace
    monkeypatch.setattr(admin_connections.accounts,'live_accounts',lambda:[])
    from app.services.notifications import kite_telegram_store
    monkeypatch.setattr(kite_telegram_store,'list_targets',lambda uid:[])
    app=FastAPI();app.include_router(admin_connections.router,prefix='/api/v1')
    app.dependency_overrides[get_current_user]=lambda:member
    client=TestClient(app)
    assert client.get('/api/v1/admin/directory').status_code==403
    app.dependency_overrides[get_current_user]=lambda:admin
    result=client.get('/api/v1/admin/directory')
    assert result.status_code==200,result.text
    person=next(u for u in result.json()['users'] if u['id']=='member')
    assert person['activity']==[] and not person['online']
    body={'users':[{'user_id':'member','expected_revision':person['revision']}],'features':{'radio':True}}
    assert client.patch('/api/v1/admin/directory/access',json=body).status_code==200
    assert account_policy.read('member').features['radio']
    assert client.patch('/api/v1/admin/directory/access',json=body).status_code==409
    body['users'][0]['expected_revision']+=1
    body['features']['radio']='false'
    assert client.patch('/api/v1/admin/directory/access',json=body).status_code==422
    assert account_policy.read('member').features['radio']


def test_activity_uses_trusted_peer_and_revocation(workspace):
    from starlette.requests import Request
    from app.services.auth import login_activity
    def request(peer):
        return Request({'type':'http','client':(peer,123),'headers':[(b'user-agent',b'Mozilla/5.0 (iPhone) Safari/605.1'),(b'x-sterling-client-ip',b'203.0.113.22'),(b'x-sterling-client-country',b'IN')]})
    assert login_activity.metadata(request('198.51.100.1'))[4]=='198.51.100.1'
    req=request('127.0.0.1')
    login_activity.record('member',1,req,login=True,expires_ms=int(time.time()*1000)+60000)
    with db._conn() as c:
        row=login_activity.for_user(c,'member')[0]
        assert row['online'] and row['device']=='Mobile' and row['country']=='IN'
        assert row['last_login_ms'] and row['ip']=='203.0.113.22'
        c.execute("UPDATE users SET token_version=2 WHERE id='member'")
    with db._conn() as c: assert not login_activity.for_user(c,'member')[0]['online']


def test_admin_broker_login_is_masked_owner_bound_and_audited(workspace,monkeypatch):
    from app.services.exchanges.kite import accounts
    from app.services.exchanges.kite.models import KiteAccountCreate
    admin,member=workspace
    monkeypatch.setattr(accounts,'_accounts',{})
    broker=accounts.add('member',KiteAccountCreate(label='Profile',api_key='synthetic',api_secret='synthetic-secret'))
    monkeypatch.setattr(accounts,'_accounts',{broker.id:broker})
    app=FastAPI();app.include_router(admin_connections.router,prefix='/api/v1')
    app.dependency_overrides[get_current_user]=lambda:member
    client=TestClient(app)
    path=f'/api/v1/admin/brokers/{broker.id}/login-credentials'
    body={'username':'AA001','password':'Synthetic-broker-password','expected_user_id':'member','expected_updated_at_ms':broker.updated_at_ms}
    assert client.put(path,json=body).status_code==403
    app.dependency_overrides[get_current_user]=lambda:admin
    assert client.put(path,json={**body,'expected_user_id':'default'}).status_code==409
    response=client.put(path,json=body)
    assert response.status_code==200,response.text
    assert response.json()['password_saved'] and 'Synthetic-broker-password' not in response.text
    with db._conn() as c:
        raw=c.execute('SELECT value FROM system_config WHERE key=?',(f'kite_login_credentials:member:{broker.id}',)).fetchone()[0]
        assert raw.startswith('fernet:') and 'Synthetic-broker-password' not in raw
        audit=c.execute("SELECT after_json FROM auth_audit_events WHERE action='broker.login_credentials_updated'").fetchone()[0]
        assert 'Synthetic-broker-password' not in audit


def test_next_upgrade_feature_and_configuration_revisions_are_joint_and_fail_closed(workspace):
    """Disposable DB: independent configuration permission must never imply a Kite grant."""
    from app.services.auth import configuration_policy
    _, member = workspace
    assert account_policy.read('member').revision == 0
    first = configuration_policy.replace_section(
        'default', 'member', 'brokers', visible=True,
        grants=[{'action': 'update', 'resource_id': 'own', 'expires_at_ms': None}],
        expected_revision=0,
    )
    assert first['revision'] == account_policy.read('member').revision == 1
    configuration_policy.require('member', 'brokers', 'update')
    with pytest.raises(PermissionError):
        account_policy.require_feature('member', 'kite')
    assert route_policy('/api/v1/kite/private', 'GET') == 'kite'
    assert route_policy('/api/v1/admin/directory', 'GET') == 'administration'
    account_policy.set_features('default', 'member',
        {'kite': False, 'tv': True, 'radio': False}, 1)
    assert configuration_policy.read('member')['revision'] == account_policy.read('member').revision == 2
    with pytest.raises(account_policy.PolicyConflict):
        configuration_policy.replace_section('default', 'member', 'brokers',
            visible=False, grants=[], expected_revision=1)
    configuration_policy.replace_section('default', 'member', 'brokers',
        visible=False, grants=[], expected_revision=2)
    with pytest.raises(PermissionError):
        configuration_policy.require('member', 'brokers', 'update')
    with db._conn() as conn:
        assert conn.execute('SELECT COUNT(*) FROM auth_event_outbox').fetchone()[0] == 3
        assert conn.execute('SELECT COUNT(*) FROM auth_audit_events').fetchone()[0] == 3


def test_next_upgrade_assignment_audit_rollback_preserves_credential_ownership(workspace, monkeypatch):
    """Synthetic account/DB only: a failed assignment audit must not transfer a vault entry."""
    from app.services.exchanges.kite import accounts
    from app.services.exchanges.kite.models import KiteAccountCreate
    monkeypatch.setattr(accounts, '_accounts', {})
    account = accounts.add('default', KiteAccountCreate(label='Synthetic', api_key='test-key', api_secret='test-secret'))
    old_key = f'kite_login_credentials:default:{account.id}'
    target_key = f'kite_login_credentials:member:{account.id}'
    db.set_config_strict(old_key, 'fernet:synthetic-ciphertext')
    with db._conn() as conn:
        conn.execute("CREATE TRIGGER deny_next_upgrade_assignment_audit BEFORE INSERT ON auth_audit_events BEGIN SELECT RAISE(ABORT,'synthetic'); END")
    with pytest.raises(RuntimeError):
        accounts.reassign(account.id, actor_id='default', source_user_id='default',
            target_user_id='member', expected_updated_at_ms=account.updated_at_ms)
    assert accounts.get('default', account.id).user_id == 'default'
    assert accounts.get('member', account.id) is None
    assert db.get_config_strict(old_key, '') == 'fernet:synthetic-ciphertext'
    assert db.get_config_strict(target_key, '') == ''
    with db._conn() as conn:
        assert conn.execute('SELECT user_id FROM kite_accounts WHERE id=?',(account.id,)).fetchone()[0] == 'default'


def test_next_upgrade_admin_credential_write_checks_revision_and_audit_atomically(workspace, monkeypatch):
    """Synthetic-admin HTTP contract: stale owner and failed audit cannot save a password."""
    from app.services.exchanges.kite import accounts
    from app.services.exchanges.kite.models import KiteAccountCreate
    admin, member = workspace
    monkeypatch.setattr(accounts, '_accounts', {})
    account = accounts.add('member', KiteAccountCreate(label='Synthetic', api_key='test-key', api_secret='test-secret'))
    monkeypatch.setattr(accounts, '_accounts', {account.id: account})
    app = FastAPI()
    app.include_router(admin_connections.router, prefix='/api/v1')
    app.dependency_overrides[get_current_user] = lambda: member
    client = TestClient(app, raise_server_exceptions=False)
    path = f'/api/v1/admin/brokers/{account.id}/login-credentials'
    body = {'username': 'AA001', 'password': 'test-only-credential',
            'expected_user_id': 'member', 'expected_updated_at_ms': account.updated_at_ms}
    assert client.put(path, json=body).status_code == 403
    app.dependency_overrides[get_current_user] = lambda: admin
    assert client.put(path, json={**body,'expected_user_id':'default'}).status_code == 409
    assert client.put(path, json={**body,'expected_updated_at_ms':account.updated_at_ms + 1}).status_code == 409
    key = f'kite_login_credentials:member:{account.id}'
    assert db.get_config_strict(key, '') == ''
    with db._conn() as conn:
        conn.execute("CREATE TRIGGER deny_next_upgrade_credential_audit BEFORE INSERT ON auth_audit_events BEGIN SELECT RAISE(ABORT,'synthetic'); END")
    error = client.put(path, json=body)
    assert error.status_code >= 400
    assert 'test-only-credential' not in error.text
    assert db.get_config_strict(key, '') == ''
    with db._conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM auth_audit_events WHERE action='broker.login_credentials_updated'").fetchone()[0] == 0


def test_next_upgrade_stale_broker_assignment_fails_before_broker_probe(workspace, monkeypatch):
    """No external provider I/O: stale CAS must abort prior to detachment checks."""
    from app.services.exchanges.kite import accounts
    from app.services.exchanges.kite.models import KiteAccountCreate
    from app.api.v1.endpoints import kite
    monkeypatch.setattr(accounts, '_accounts', {})
    account = accounts.add('default', KiteAccountCreate(label='Synthetic', api_key='test-key', api_secret='test-secret'))
    async def forbidden_probe(*args, **kwargs):
        raise AssertionError('stale request reached broker exposure probe')
    monkeypatch.setattr(kite, '_require_account_detachable', forbidden_probe)
    app = FastAPI()
    app.include_router(admin_connections.router, prefix='/api/v1')
    app.dependency_overrides[get_current_user] = lambda: workspace[0]
    response = TestClient(app).post(f'/api/v1/admin/brokers/{account.id}/assignment', json={
        'user_id':'member', 'expected_user_id':'default', 'expected_updated_at_ms':account.updated_at_ms+1})
    assert response.status_code == 409
    assert accounts.get('default', account.id).user_id == 'default'


def test_next_upgrade_admin_credential_requires_real_bearer_and_current_session_version(workspace, monkeypatch):
    """A production-mode synthetic HTTP request cannot use a missing/member/revoked admin JWT."""
    from app.core import tokens
    from app.services.auth import user_store
    from app.services.exchanges.kite import accounts
    from app.services.exchanges.kite.models import KiteAccountCreate
    monkeypatch.setenv('ENVIRONMENT', 'production')
    monkeypatch.setattr(accounts, '_accounts', {})
    account = accounts.add('member', KiteAccountCreate(
        label='Synthetic', api_key='test-key', api_secret='test-secret'))
    monkeypatch.setattr(accounts, '_accounts', {account.id: account})
    app = FastAPI()
    app.include_router(admin_connections.router, prefix='/api/v1')
    client = TestClient(app, raise_server_exceptions=False)
    path = f'/api/v1/admin/brokers/{account.id}/login-credentials'
    body = {'username': 'AA001', 'password': 'test-only-credential',
            'expected_user_id': 'member', 'expected_updated_at_ms': account.updated_at_ms}
    admin_user = user_store.get_by_id('default')
    member_user = user_store.get_by_id('member')
    admin_token = tokens.mint_access('default', admin_user.username, 'admin', admin_user.token_version)
    member_token = tokens.mint_access('member', member_user.username, 'user', member_user.token_version)
    assert client.put(path, json=body).status_code == 401
    assert client.put(path, json=body, headers={'Authorization': f'Bearer {member_token}'}).status_code == 403
    user_store.bump_token_version('default')
    assert client.put(path, json=body, headers={'Authorization': f'Bearer {admin_token}'}).status_code == 401
    assert db.get_config_strict(f'kite_login_credentials:member:{account.id}', '') == ''


def test_increment3_real_admin_bearer_credential_write_is_redacted_and_owner_scoped(workspace, monkeypatch):
    """Positive actual auth dependency; synthetic encrypted write in disposable SQLite only."""
    from app.core import tokens
    from app.services.auth import user_store
    from app.services.exchanges.kite import accounts
    from app.services.exchanges.kite.models import KiteAccountCreate
    monkeypatch.setenv('ENVIRONMENT', 'production')
    monkeypatch.setattr(accounts, '_accounts', {})
    account = accounts.add('member', KiteAccountCreate(label='Synthetic', api_key='test-key', api_secret='test-secret'))
    monkeypatch.setattr(accounts, '_accounts', {account.id: account})
    app = FastAPI()
    app.include_router(admin_connections.router, prefix='/api/v1')
    with TestClient(app) as client:
        admin = user_store.get_by_id('default')
        access = tokens.mint_access(admin.id, admin.username, 'admin', admin.token_version)
        headers = {'Authorization': 'Bearer ' + access}
        path = f'/api/v1/admin/brokers/{account.id}/login-credentials'
        body = {'username': 'AA001', 'password': 'synthetic-disposable-password',
                'expected_user_id': 'member', 'expected_updated_at_ms': account.updated_at_ms}
        read = client.get(path, headers=headers)
        assert read.status_code == 200 and read.headers['cache-control'] == 'no-store, private'
        assert read.json()['password_saved'] is False
        response = client.put(path, json=body, headers=headers)
        assert response.status_code == 200, response.text
        assert response.json()['username'] == 'AA001'
        assert 'synthetic-disposable-password' not in response.text
        assert 'synthetic-disposable-password' not in str(response.headers)
        with db._conn() as c:
            key = f'kite_login_credentials:member:{account.id}'
            stored = c.execute('SELECT value FROM system_config WHERE key=?',(key,)).fetchone()[0]
            assert stored.startswith('fernet:') and 'synthetic-disposable-password' not in stored
            assert c.execute("SELECT COUNT(*) FROM auth_audit_events WHERE action='broker.login_credentials_updated'").fetchone()[0] == 1
            audit_text = '\\n'.join(str(dict(row)) for row in c.execute(
                "SELECT * FROM auth_audit_events WHERE action='broker.login_credentials_updated'"))
            assert 'synthetic-disposable-password' not in audit_text
        member = user_store.get_by_id('member')
        member_access = tokens.mint_access(member.id, member.username, 'user', member.token_version)
        assert client.put(path, json=body, headers={'Authorization': 'Bearer ' + member_access}).status_code == 403
        refresh = tokens.mint_refresh(admin.id, admin.username, 'admin', admin.token_version)
        assert client.put(path, json=body, headers={'Authorization': 'Bearer ' + refresh}).status_code == 401


def test_increment3_real_auth_refresh_logout_revoke_and_expiry(workspace, monkeypatch):
    """Production-mode test transport only: no production host/network or external secrets."""
    import jwt
    import time
    from app.core import security, tokens
    from app.api.v1.endpoints import auth
    from app.services.auth import user_store
    monkeypatch.setenv('ENVIRONMENT', 'production')
    app = FastAPI()
    app.include_router(auth.router, prefix='/api/v1')
    with TestClient(app) as client:
        admin = user_store.get_by_id('default')
        access = tokens.mint_access(admin.id, admin.username, 'admin', admin.token_version)
        refresh = tokens.mint_refresh(admin.id, admin.username, 'admin', admin.token_version)
        headers = {'Authorization': 'Bearer ' + access}
        success = client.get('/api/v1/auth/me', headers=headers)
        assert success.status_code == 200 and success.json()['administrator'] is True
        assert success.headers['cache-control'] == 'no-store, private'
        assert client.get('/api/v1/auth/me', headers={'Authorization': 'Bearer ' + refresh}).status_code == 401
        assert client.post('/api/v1/auth/refresh', json={'refresh_token': access}).status_code == 401
        claims = tokens.decode(access, expected_typ='access')
        claims['exp'] = int(time.time()) - 60
        expired = jwt.encode(claims, security.get_jwt_key(), algorithm='HS256')
        assert client.get('/api/v1/auth/me', headers={'Authorization': 'Bearer ' + expired}).status_code == 401
        renewed = client.post('/api/v1/auth/refresh', json={'refresh_token': refresh})
        assert renewed.status_code == 200
        rotated_access = renewed.json()['access_token']
        rotated_refresh = renewed.json()['refresh_token']
        assert tokens.decode(rotated_access, expected_typ='access')['sub'] == 'default'
        assert client.post('/api/v1/auth/logout', headers=headers).status_code == 200
        assert client.get('/api/v1/auth/me', headers=headers).status_code == 401
        assert client.get('/api/v1/auth/me', headers={'Authorization':'Bearer ' + rotated_access}).status_code == 401
        assert client.post('/api/v1/auth/refresh', json={'refresh_token':refresh}).status_code == 401
        assert client.post('/api/v1/auth/refresh', json={'refresh_token':rotated_refresh}).status_code == 401


def test_increment4_member_cannot_elevate_to_admin_with_jwt_role_or_mfa_headers(workspace, monkeypatch):
    """Synthetic HTTP requests: resolve the role from the DB, never from JWT claims or client step-up flags."""
    from app.core import tokens
    from app.services.auth import user_store
    monkeypatch.setenv('ENVIRONMENT', 'production')
    app = FastAPI()
    app.include_router(admin_connections.router, prefix='/api/v1')
    member = user_store.get_by_id('member')
    claimed_admin = tokens.mint_access(member.id, member.username, 'admin', member.token_version)
    route = '/api/v1/admin/brokers'
    with TestClient(app) as client:
        assert client.get(route).status_code == 401
        response = client.get(route, headers={
            'Authorization': 'Bearer ' + claimed_admin,
            'X-Sterling-MFA-Verified': 'true',
            'X-Sterling-Step-Up': 'passed',
            'X-User-Id': 'default',
        })
        assert response.status_code == 403
        assert client.get(route, headers={'Authorization': 'Bearer malformed'}).status_code == 401
        admin = user_store.get_by_id('default')
        real_admin = tokens.mint_access(admin.id, admin.username, 'admin', admin.token_version)
        valid = client.get(route, headers={'Authorization': 'Bearer ' + real_admin})
        assert valid.status_code == 200
        user_store.bump_token_version('default')
        assert client.get(route, headers={
            'Authorization': 'Bearer ' + real_admin, 'X-Sterling-MFA-Verified': 'true',
        }).status_code == 401


def test_increment4_expired_refresh_and_remembered_token_do_not_prove_device_mfa(workspace, monkeypatch):
    """Synthetic remember/refresh contract: finite tokens, expiry denial, no inferred device attestation."""
    import jwt
    from app.core import security, tokens
    from app.api.v1.endpoints import auth
    from app.services.auth import user_store
    monkeypatch.setenv('ENVIRONMENT', 'production')
    app = FastAPI()
    app.include_router(auth.router, prefix='/api/v1')
    admin = user_store.get_by_id('default')
    access = tokens.mint_access(admin.id, admin.username, admin.role, admin.token_version)
    refresh = tokens.mint_refresh(admin.id, admin.username, admin.role, admin.token_version)
    with TestClient(app) as client:
        remembered = client.post('/api/v1/auth/remember',
                                 headers={'Authorization': 'Bearer ' + access})
        assert remembered.status_code == 200
        claims = tokens.decode(remembered.json()['refresh_token'], expected_typ='refresh')
        assert claims.get('remembered') is True
        assert int(claims['exp']) > int(claims['iat'])
        assert int(claims['exp']) - int(claims['iat']) <= 30 * 24 * 60 * 60
        # A remembered refresh token is not a per-device attestation or MFA-bound session.
        assert not any(key in claims for key in ('amr', 'acr', 'device_id', 'mfa_verified_at', 'step_up_at'))
        expired_claims = tokens.decode(refresh, expected_typ='refresh')
        expired_claims['exp'] = 1
        expired = jwt.encode(expired_claims, security.get_jwt_key(), algorithm='HS256')
        assert client.post('/api/v1/auth/refresh', json={'refresh_token': expired}).status_code == 401
        assert client.post('/api/v1/auth/refresh',
                           json={'refresh_token': remembered.json()['access_token']}).status_code == 401
