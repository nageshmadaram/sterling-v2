import pytest
from fastapi import HTTPException, Response
from app.core.auth import UserContext
from app.core.passwords import verify_password
from app.services import db
from app.services.auth import operator_credentials as credentials,user_store
from app.api.v1.endpoints import telegram_operator as endpoint

@pytest.fixture
def admin(tmp_path,monkeypatch):
    monkeypatch.setattr(db,'_DB_PATH',str(tmp_path/'users.db'))
    db.init();user_store._init_table()
    monkeypatch.setattr(user_store,'_users',{})
    return user_store.add('original','Synthetic-original-password-123',role='admin',user_id='owner')


def test_rename_password_atomic_preserves_id_and_revokes_tokens(admin):
    version=admin.token_version
    result=credentials.manage(admin.id,'update','renamed','Synthetic-new-password-123')
    assert result.id==admin.id and result.token_version==version+1
    assert user_store.get_by_username('original') is None
    assert verify_password('Synthetic-new-password-123',result.password_hash)
    with db._conn() as c:
        row=c.execute('SELECT * FROM users WHERE id=?',(admin.id,)).fetchone()
        assert row['username']=='renamed' and row['token_version']==version+1
        assert row['password_hash']!='Synthetic-new-password-123'


def test_add_user_is_separate_and_does_not_change_admin(admin):
    result=credentials.manage(admin.id,'add','another','Synthetic-user-password-123')
    assert result.role=='user' and result.id!=admin.id
    assert user_store.get_by_id(admin.id).username=='original'
    with pytest.raises(ValueError):credentials.manage(admin.id,'update','another','')
    assert user_store.get_by_id(admin.id).username=='original'


def test_failed_storage_never_changes_runtime_credentials(admin,monkeypatch):
    monkeypatch.setattr(db,'_available',False)
    with pytest.raises(RuntimeError):credentials.manage(admin.id,'update','renamed','Strong-new-password-123')
    assert user_store.get_by_id(admin.id) is admin


@pytest.mark.asyncio
async def test_forms_bound_to_admin_one_shot_and_validate_password(admin):
    user=UserContext(user_id=admin.id,username=admin.username,role='admin')
    with pytest.raises(HTTPException) as denied:
        await endpoint.operator_data(Response(),UserContext(user_id='other',role='user'))
    assert denied.value.status_code==403
    response=Response();form=await endpoint.operator_data(response,user)
    assert response.headers['Cache-Control']=='no-store, private'
    body=endpoint.CredentialEdit(action='update',username='newname',password='',challenge=form['challenge'])
    result=await endpoint.operator_manage(body,user)
    assert result['ok'] and user_store.get_by_id(admin.id).username=='newname'
    with pytest.raises(HTTPException) as replay:await endpoint.operator_manage(body,user)
    assert replay.value.status_code==409


def test_password_validation_and_no_plaintext_logging(admin):
    with pytest.raises(ValueError):credentials.manage(admin.id,'add','user','short')
    assert len(user_store.all_users())==1
    assert 'sendData' not in endpoint.FORM
    assert 'innerHTML' not in endpoint.FORM


@pytest.mark.asyncio
async def test_telegram_operator_menu_tracks_current_origin(monkeypatch):
    from unittest.mock import AsyncMock
    from app.services.notifications import telegram_kite as desk
    monkeypatch.setattr(desk,'_get_tma_url',lambda:'https://current.example/tma')
    monkeypatch.setattr('app.services.public_access.status',lambda:{'app_url':'https://current.example'})
    send=AsyncMock();monkeypatch.setattr(desk,'_send',send)
    await desk.operator_login_card('123')
    rows=send.call_args.args[2]['inline_keyboard']
    assert rows[0][0] == {'text':'Sign in to Sterling','url':'https://current.example/?sterling_login=1&broker_login=1'}
    assert rows[1][0] == {'text':'Manage Sterling users','web_app':{'url':'https://current.example/tma/operator'}}
    assert 'username and password' in send.call_args.args[0]
    assert 'Change your Sterling username/password' not in send.call_args.args[0]
