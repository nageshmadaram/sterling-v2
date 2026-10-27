import pytest
from fastapi import HTTPException, Response
from app.api.v1.endpoints.kite import reveal_account_credentials
from app.core.auth import UserContext
from app.services.exchanges.kite import accounts
from app.services.exchanges.kite.models import KiteAccountCreate


@pytest.mark.asyncio
async def test_legacy_reveal_is_closed_until_mfa_bound_challenges_exist(tmp_path, monkeypatch):
    from app.services import db
    from app.services.auth import user_store
    monkeypatch.setattr(db, '_DB_PATH', str(tmp_path / 'reveal.db'))
    assert db.init()
    user_store._init_table()
    monkeypatch.setattr(user_store, '_users', {})
    user_store.add('primary', 'Synthetic-password-123', role='admin', user_id='default')
    user_store.add('owner', 'Synthetic-password-123', user_id='owner')
    a = accounts.add('owner', KiteAccountCreate(label='A', api_key='synthetic-key', api_secret='synthetic-secret'))
    response = Response()
    with pytest.raises(HTTPException) as owner:
        await reveal_account_credentials(a.id, response, UserContext(user_id='owner'))
    assert owner.value.status_code == 403
    with pytest.raises(HTTPException) as admin:
        await reveal_account_credentials(a.id, Response(), UserContext(user_id='default', role='admin'))
    assert admin.value.status_code == 403
    assert 'synthetic-secret' not in accounts.to_response(a).model_dump_json()
    with pytest.raises(HTTPException) as error:
        await reveal_account_credentials(a.id, Response(), UserContext(user_id='other'))
    assert error.value.status_code == 403
