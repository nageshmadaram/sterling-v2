import pytest
from fastapi import HTTPException, Response
from app.api.v1.endpoints.kite import reveal_account_credentials
from app.core.auth import UserContext
from app.services.exchanges.kite import accounts
from app.services.exchanges.kite.models import KiteAccountCreate


@pytest.mark.asyncio
async def test_reveal_is_explicit_owner_only_and_uncacheable():
    a = accounts.add('owner', KiteAccountCreate(label='A', api_key='synthetic-key', api_secret='synthetic-secret'))
    response = Response()
    result = await reveal_account_credentials(a.id, response, UserContext(user_id='owner'))
    assert result == {'api_key': 'synthetic-key', 'api_secret': 'synthetic-secret'}
    assert response.headers['Cache-Control'] == 'no-store, private'
    assert 'synthetic-secret' not in accounts.to_response(a).model_dump_json()
    with pytest.raises(HTTPException) as error:
        await reveal_account_credentials(a.id, Response(), UserContext(user_id='other'))
    assert error.value.status_code == 404
