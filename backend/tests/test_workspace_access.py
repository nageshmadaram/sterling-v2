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


def test_broker_activate_and_connect_require_delegated_configuration_not_just_kite(workspace, monkeypatch):
    """An operator's Kite read grant is not permission to switch or probe an account."""
    from app.api.v1.endpoints import kite
    from app.services.auth import configuration_policy
    _, member = workspace
    account_policy.set_features(
        'default', 'member', {'kite': True, 'tv': False, 'radio': False}, 0,
    )
    app = FastAPI()
    app.include_router(kite.router, prefix='/api/v1')
    app.dependency_overrides[get_current_user] = lambda: member
    client = TestClient(app)

    def never_reach_broker(*_args, **_kwargs):
        pytest.fail('Unprivileged request reached broker account store')

    monkeypatch.setattr(kite.kite_accounts, 'get', never_reach_broker)
    for endpoint in ('/api/v1/kite/accounts/synthetic/activate',
                     '/api/v1/kite/accounts/synthetic/test'):
        response = client.post(endpoint)
        assert response.status_code == 403, response.text

    # The same grants permit a checked-through capability, which then reaches
    # resource lookup. A missing account remains 404 and never creates one.
    monkeypatch.setattr(kite.kite_accounts, 'get', lambda *_args, **_kwargs: None)
    configuration_policy.replace_section(
        'default', 'member', 'brokers', visible=True,
        grants=[{'action': 'update', 'resource_id': 'own', 'expires_at_ms': None}],
        expected_revision=1,
    )
    for endpoint in ('/api/v1/kite/accounts/synthetic/activate',
                     '/api/v1/kite/accounts/synthetic/test'):
        response = client.post(endpoint)
        assert response.status_code == 404, response.text


def test_role_kite_mutation_matrix_and_simulation_isolation(workspace, monkeypatch):
    """Owner's virtual paper writes remain available; live writes never leak."""
    from app.core import tokens, auth
    monkeypatch.setattr(auth, "_owner_uid", lambda: "default")

    user_store.add("operator", "Synthetic-password-123", role="operator", user_id="operator")
    user_store.add("viewer", "Synthetic-password-123", role="viewer", user_id="viewer")
    for uid in ("default", "member", "operator", "viewer"):
        account_policy.set_features(
            "default", uid, {"kite": True, "tv": False, "radio": False},
            account_policy.read(uid).revision,
        )

    app = FastAPI(dependencies=[Depends(enforce_feature_access)])

    @app.post("/api/v1/kite/orders")
    async def synthetic_write():
        return {"reached": True}

    @app.post("/api/v1/kite/trading/kill-switch")
    async def forbidden_global_halt():
        return {"should_not_run": True}

    @app.get("/api/v1/kite/orders")
    async def synthetic_read():
        return {"reached": True}

    with TestClient(app) as client:
        for uid, role in (
            ("default", "admin"), ("member", "user"),
            ("operator", "operator"), ("viewer", "viewer"),
        ):
            headers = {"Authorization": "Bearer " + tokens.mint_access(uid, uid, role, 1)}
            assert client.get("/api/v1/kite/orders", headers=headers).status_code == 200
            written = client.post("/api/v1/kite/orders", headers=headers)
            assert written.status_code == (403 if role == "viewer" else 200), written.text
            if role != "viewer":
                # No virtual simulation identity may mutate a live account,
                # even if its owner has an explicit Kite write grant.
                sim_headers = {**headers, "X-Sterling-View": "simulation"}
                # Only the designated admin may enter the Simulation tenant.
                # Its virtual book remains writable, but external/global routes
                # are prohibited by auth.simulation_denied, not a blanket block.
                expect_sim = 200 if uid == "default" else 403
                assert client.post("/api/v1/kite/orders", headers=sim_headers).status_code == expect_sim
                if uid == "default":
                    assert client.post("/api/v1/kite/trading/kill-switch",
                                       headers=sim_headers).status_code == 403


def test_broker_activation_and_session_test_are_configuration_mutations(workspace):
    for path in (
        "/api/v1/kite/accounts/tenant/activate",
        "/api/v1/kite/accounts/tenant/test",
        "/api/v1/kite/accounts/tenant/credentials/reveal",
    ):
        assert route_policy(path, "POST") == "configuration"
    assert route_policy("/api/v1/kite/orders", "POST") == "kite"
    assert route_policy("/api/v1/kite/orders", "GET") == "kite"
