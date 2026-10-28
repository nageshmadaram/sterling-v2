import pytest
from fastapi import HTTPException,Response
from app.core.auth import UserContext
from app.services import db
from app.services.auth import user_store
from app.services.exchanges.kite import accounts
from app.services.exchanges.kite.models import KiteAccountCreate
from app.api.v1.endpoints import sterling_users as api

@pytest.fixture
def admin(tmp_path,monkeypatch):
    monkeypatch.setattr(db,'_DB_PATH',str(tmp_path/'users.db'))
    db.init();user_store._init_table()
    monkeypatch.setattr(user_store,'_users',{})
    user_store.add('primary','Synthetic-admin-password-123',role='admin',user_id='default')
    return UserContext(user_id='default',username='primary',role='admin')

@pytest.mark.asyncio
async def test_crud_no_secret_leak_revokes_sessions_and_retains_audit_identity(admin):
    created=await api.add_user(api.UserEdit(username='member',password='Synthetic-member-password-123'),admin)
    assert 'password' not in created and 'password_hash' not in created and created['password_set']
    member=user_store.get_by_id(created['id']);version=member.token_version
    updated=await api.update_user(member.id,api.UserEdit(username='changed',password='Synthetic-changed-password-123'),admin)
    assert updated['username']=='changed' and user_store.get_by_id(member.id).token_version==version+1
    result=await api.delete_user(member.id,admin)
    assert result['ok'] and not user_store.get_by_id(member.id).is_active
    assert user_store.get_by_id(member.id).token_version==version+2
    response=Response();listing=await api.list_users(response,admin)
    assert len(listing['users'])==2 and 'password_hash' not in str(listing)
    assert response.headers['Cache-Control']=='no-store, private'

@pytest.mark.asyncio
async def test_user_cannot_manage_users_and_primary_cannot_be_deleted(admin):
    with pytest.raises(HTTPException) as denied:
        await api.list_users(Response(),UserContext(user_id='member',role='user'))
    assert denied.value.status_code==403
    with pytest.raises(HTTPException):await api.delete_user(admin.user_id,admin)
    with pytest.raises(HTTPException):await api.update_user(admin.user_id,api.UserEdit(username='Sterling',role='user'),admin)
    assert user_store.get_by_id(admin.user_id).is_active

@pytest.mark.asyncio
async def test_broker_account_owner_is_suspended_without_detaching_protection(admin):
    created=await api.add_user(api.UserEdit(username='brokeruser',password='Synthetic-member-password-123'),admin)
    accounts.add(created['id'],KiteAccountCreate(label='Broker',api_key='synthetic',api_secret='synthetic-secret'))
    result = await api.delete_user(created['id'],admin)
    assert result['ok']
    assert not user_store.get_by_id(created['id']).is_active
    assert accounts.list_accounts(created['id'])

@pytest.mark.asyncio
async def test_create_commits_identity_and_initial_access_together(admin):
    from app.services.auth import account_policy, configuration_policy
    body=api.UserCreate(username='configured-member',password='Synthetic-member-password-123',
        features={'kite':True,'radio':True,'tv':False},
        configuration={'brokers':{'visible':True,'grants':[{'action':'create','resource_id':'own','expires_at_ms':None}]},
                       'telegram':{'visible':True,'grants':[{'action':'recipients','resource_id':'own','expires_at_ms':None}]}})
    created=await api.add_user(body,admin)
    policy=account_policy.read(created['id'])
    assert policy.revision==1 and policy.features=={'kite':True,'radio':True,'tv':False}
    settings=configuration_policy.read(created['id'])
    assert settings['sections']['brokers']['visible']
    configuration_policy.require(created['id'],'brokers','create')
    configuration_policy.require(created['id'],'telegram','recipients')
    with pytest.raises(PermissionError):configuration_policy.require(created['id'],'integrations','test')
    with db._conn() as c:
        event=c.execute("SELECT * FROM auth_audit_events WHERE target_id=? AND action='user.add'",(created['id'],)).fetchone()
        assert event and 'Synthetic-member-password' not in str(dict(event))
        assert c.execute('SELECT COUNT(*) FROM auth_event_outbox WHERE user_id=?',(created['id'],)).fetchone()[0]==1

@pytest.mark.asyncio
async def test_invalid_initial_access_leaves_no_user_or_cache_entry(admin):
    body=api.UserCreate(username='invalid-config',password='Synthetic-member-password-123',
        features={'kite':True,'radio':False,'tv':False},
        configuration={'telegram':{'visible':True,'grants':[{'action':'reveal','resource_id':'own','expires_at_ms':None}]}})
    with pytest.raises(HTTPException) as rejected:await api.add_user(body,admin)
    assert rejected.value.status_code==422
    assert user_store.get_by_username('invalid-config') is None
    with db._conn() as c:assert not c.execute("SELECT 1 FROM users WHERE username='invalid-config'").fetchone()

@pytest.mark.asyncio
async def test_failed_initial_grant_rolls_back_identity_and_policy(admin):
    with db._conn() as c:c.execute("CREATE TRIGGER test_grant_failure BEFORE INSERT ON user_feature_grants BEGIN SELECT RAISE(ABORT,'synthetic disk write failure'); END")
    with pytest.raises(HTTPException):
        await api.add_user(api.UserCreate(username='rolled-back',password='Synthetic-member-password-123',features={'kite':True,'radio':True,'tv':True}),admin)
    assert user_store.get_by_username('rolled-back') is None
    with db._conn() as c:
        assert not c.execute("SELECT 1 FROM users WHERE username='rolled-back'").fetchone()
        assert c.execute("SELECT COUNT(*) FROM auth_audit_events WHERE action='user.add'").fetchone()[0]==0


def test_http_create_accepts_initial_access_and_new_user_can_authenticate(admin):
    from fastapi.testclient import TestClient
    from app.core import tokens
    from main import create_app
    stored=user_store.get_by_id(admin.user_id)
    token=tokens._mint(stored.id,stored.username,stored.role,stored.token_version,'access',900)
    client=TestClient(create_app(),headers={'Authorization':'Bearer '+token})
    response=client.post('/api/v1/auth/users',json={'username':'http-member','password':'Synthetic-member-password-123','role':'user','active':True,'features':{'kite':False,'radio':True,'tv':True},'configuration':{'integrations':{'visible':True,'grants':[{'action':'test','resource_id':'own','expires_at_ms':None}]}}})
    assert response.status_code==200,response.text
    assert 'Synthetic-member-password' not in response.text
    user=user_store.verify_credentials('http-member','Synthetic-member-password-123')
    assert user and user.id==response.json()['id']
    from app.services.auth import account_policy,configuration_policy
    assert account_policy.read(user.id).features=={'kite':False,'radio':True,'tv':True}
    configuration_policy.require(user.id,'integrations','test')
