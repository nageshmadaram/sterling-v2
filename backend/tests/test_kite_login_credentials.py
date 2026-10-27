from types import SimpleNamespace
import json
import pytest
from fastapi import HTTPException, Response
from cryptography.fernet import Fernet
from app.api.v1.endpoints import kite_login_credentials as vault

@pytest.fixture
def store(monkeypatch, tmp_path):
    from app.services import db
    from app.services.auth import user_store, configuration_policy
    monkeypatch.setattr(db, '_DB_PATH', str(tmp_path / 'vault-policy.db'))
    assert db.init()
    user_store._init_table()
    monkeypatch.setattr(user_store, '_users', {})
    user_store.add('primary', 'Synthetic-password-123', role='admin', user_id='default')
    user_store.add('owner', 'Synthetic-password-123', user_id='owner')
    configuration_policy.replace_section('default', 'owner', 'brokers', visible=True,
        grants=[{'action': 'update', 'resource_id': 'account', 'expires_at_ms': None}], expected_revision=0)
    values = {}
    cipher = Fernet(Fernet.generate_key())
    monkeypatch.setattr(vault, 'encrypt', lambda value: 'fernet:' + cipher.encrypt(value.encode()).decode())
    monkeypatch.setattr(vault, 'decrypt', lambda value: cipher.decrypt(value[7:].encode()).decode())
    monkeypatch.setattr(vault.db, 'get_config_strict', lambda key, default='': values.get(key, default))
    monkeypatch.setattr(vault.db, 'set_config_strict', lambda key, value: values.__setitem__(key, value))
    monkeypatch.setattr(vault.accounts, 'get', lambda uid, aid: SimpleNamespace(kite_user_id='AA001', id=aid) if (uid, aid) == ('owner', 'account') else None)
    monkeypatch.setattr(vault.accounts, 'is_sim_account', lambda acct: False)
    return values

async def test_save_encrypts_and_response_never_contains_password(store):
    user = SimpleNamespace(user_id='owner')
    result = await vault.save_credentials('account', vault.LoginCredentials(username='aa001', password='broker-password'), Response(), user)
    assert result == {'username':'AA001', 'password_saved':True, 'automatic_login_available':True}
    assert 'broker-password' not in json.dumps(store)
    assert 'broker-password' not in json.dumps(await vault.get_credentials('account', Response(), user))
    await vault.save_credentials('account', vault.LoginCredentials(username='AA001'), Response(), user)
    assert vault.read(user, 'account')['password'] == 'broker-password'
    await vault.save_credentials('account', vault.LoginCredentials(username='AA001', password='new-password'), Response(), user)
    assert vault.read(user, 'account')['password'] == 'new-password'

async def test_cross_owner_and_wrong_broker_identity_are_rejected(store):
    with pytest.raises(HTTPException) as denied:
        await vault.get_credentials('account', Response(), SimpleNamespace(user_id='other'))
    assert denied.value.status_code == 404
    with pytest.raises(HTTPException) as mismatch:
        await vault.save_credentials('account', vault.LoginCredentials(username='BB002', password='anything'), Response(), SimpleNamespace(user_id='owner'))
    assert mismatch.value.status_code == 409
    assert store == {}

async def test_no_base64_fallback_for_password_storage(store, monkeypatch):
    monkeypatch.setattr(vault, 'encrypt', lambda value: 'b64:encoded')
    with pytest.raises(HTTPException) as rejected:
        await vault.save_credentials('account', vault.LoginCredentials(username='AA001', password='password'), Response(), SimpleNamespace(user_id='owner'))
    assert rejected.value.status_code == 503
    assert store == {}
