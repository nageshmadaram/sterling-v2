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
async def test_broker_account_owner_cannot_be_disabled(admin):
    created=await api.add_user(api.UserEdit(username='brokeruser',password='Synthetic-member-password-123'),admin)
    accounts.add(created['id'],KiteAccountCreate(label='Broker',api_key='synthetic',api_secret='synthetic-secret'))
    with pytest.raises(HTTPException) as denied:await api.delete_user(created['id'],admin)
    assert 'broker accounts' in denied.value.detail
    assert user_store.get_by_id(created['id']).is_active
