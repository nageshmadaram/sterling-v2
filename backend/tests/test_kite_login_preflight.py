from types import SimpleNamespace
from unittest.mock import AsyncMock
import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.services.exchanges.kite import login_preflight as preflight, session
from app.services.notifications import telegram_kite as desk
from app.api.v1.endpoints import kite as endpoint
from app.core.auth import UserContext, get_current_user

@pytest.mark.parametrize('kind,rejected', [('invalid', True), ('valid', False), ('network', False)])
async def test_preflight_only_sends_public_login_get(monkeypatch, kind, rejected):
    calls = []
    def handler(request):
        calls.append(request)
        if kind == 'network': raise httpx.ConnectError('synthetic offline', request=request)
        if kind == 'invalid': return httpx.Response(400, json={'status':'error','message':'Invalid `api_key`.','error_type':'InputException'})
        return httpx.Response(200, text='<html>Kite login</html>')
    original = httpx.AsyncClient
    monkeypatch.setattr(preflight.httpx, 'AsyncClient', lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))
    if rejected:
        with pytest.raises(ValueError, match='Update the API key'): await preflight.check_login_key('synthetic')
    else: await preflight.check_login_key('synthetic')
    assert len(calls) == 1 and calls[0].method == 'GET'
    assert calls[0].url.host == 'kite.zerodha.com'
    assert not calls[0].content and not calls[0].headers.get('Authorization')


def test_login_url_encodes_key_without_query_injection():
    from urllib.parse import urlparse, parse_qs
    query = parse_qs(urlparse(session.login_url(' key&extra=value ', state='state')).query)
    assert query['api_key'] == ['key&extra=value'] and 'extra' not in query


async def test_telegram_invalid_key_shows_repair_without_broken_link(monkeypatch):
    monkeypatch.setattr(desk, '_active_account', lambda: SimpleNamespace(api_key='synthetic', api_secret='secret'))
    monkeypatch.setattr(preflight, 'check_login_key', AsyncMock(side_effect=ValueError(preflight.INVALID_KEY_MESSAGE)))
    text, keyboard = await desk.checked_broker_login_card()
    assert 'credentials need updating' in text.lower() and 'Connect' in text and 'url' not in str(keyboard)


def test_app_invalid_key_returns_actionable_error(monkeypatch):
    monkeypatch.setattr(endpoint, '_require_active', lambda user: SimpleNamespace(api_key='synthetic', id='synthetic-account'))
    monkeypatch.setattr(preflight, 'check_login_key', AsyncMock(side_effect=ValueError(preflight.INVALID_KEY_MESSAGE)))
    app = FastAPI(); app.include_router(endpoint.router, prefix='/api/v1')
    app.dependency_overrides[get_current_user] = lambda: UserContext(user_id='default', username='operator', role='admin')
    with TestClient(app) as client: response = client.get('/api/v1/kite/login-url')
    assert response.status_code == 400 and 'Update the API key' in response.json()['detail']
