import hashlib, hmac, json, time
from urllib.parse import urlencode
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from app.services.notifications.telegram_webapp_auth import verify_init_data, tma_user
from app.services.notifications import telegram_kite as desk
from app.api.v1.endpoints import tma
from app.core.auth import UserContext


def signed(ident=123, age=0):
    data={'user':json.dumps({'id':ident}), 'auth_date':str(int(time.time())-age)}
    check='\n'.join(f'{k}={v}' for k,v in sorted(data.items()))
    secret=hmac.new(b'WebAppData', b'synthetic-token', hashlib.sha256).digest()
    data['hash']=hmac.new(secret,check.encode(),hashlib.sha256).hexdigest()
    return urlencode(data)


def test_valid_controller_signature():
    verify_init_data(signed(), 'synthetic-token', '123')


@pytest.mark.parametrize('make_raw', [lambda: signed(124), lambda: signed(age=3601), lambda: signed(age=-120), lambda: signed()+'&user=bad', lambda: 'invalid', lambda: signed()+'tamper'])
def test_invalid_expired_and_wrong_controller_rejected(make_raw):
    raw = make_raw()
    with pytest.raises(HTTPException) as e: verify_init_data(raw,'synthetic-token','123')
    assert e.value.status_code==401


def test_private_mini_app_endpoints_require_auth():
    app=FastAPI();app.include_router(tma.router)
    with TestClient(app) as c:
        assert c.get('/api/v1/tma/data').status_code==401
        assert c.post('/api/v1/tma/action',json={'action':'toggle_algo'}).status_code==401
        assert c.get('/tma').status_code==200


def test_unconfigured_menu_never_links_placeholder_domain(monkeypatch):
    monkeypatch.setattr(desk,'_get_tma_url',lambda:'')
    assert 'web_app' not in str(desk.kite_menu_kb())
    assert 'sterling.app' not in str(desk.kite_menu_kb())


@pytest.mark.parametrize('url',['http://example.test/tma','https://user:pass@example.test/tma','javascript:alert(1)','https://example.test/tma#secret'])
def test_invalid_mini_app_urls_rejected(url):
    with pytest.raises(ValueError):desk._valid_tma_url(url)


def test_market_card_does_not_invent_bias(monkeypatch):
    monkeypatch.setattr(desk,'_ready_rows',lambda:[])
    assert 'No fresh' in desk.build_kite_market_text()
    assert 'Bullish' not in desk.build_kite_market_text()


def test_manual_order_uses_authenticated_tenant_and_validates_input(monkeypatch):
    from app.services.kite_engine import service
    submit=AsyncMock(return_value={'status':'ok','order_id':'synthetic'})
    monkeypatch.setattr(service,'place_manual_order',submit)
    app=FastAPI();app.include_router(tma.router)
    app.dependency_overrides[tma_user]=lambda:UserContext(user_id='alice',username='alice',role='user')
    with TestClient(app) as c:
        r=c.post('/api/v1/tma/action',json={'action':'place_order','symbol':'SYNTHETIC','side':'BUY','quantity':25,'exchange':'NFO'})
        assert r.status_code==200 and r.json()['success']
        assert c.post('/api/v1/tma/action',json={'action':'place_order','symbol':'SYNTHETIC','quantity':-1}).status_code==422
    submit.assert_awaited_once_with('alice','SYNTHETIC','BUY',25,'NFO')


def test_position_exit_rechecks_current_broker_holding(monkeypatch):
    from app.services.exchanges.kite import accounts
    from app.services.kite_engine import service
    broker=SimpleNamespace(get_positions_raw=AsyncMock(return_value={'net':[{'tradingsymbol':'SYNTHETIC','exchange':'BFO','quantity':-20}]}))
    monkeypatch.setattr(accounts,'get_active',lambda uid:SimpleNamespace(id='synthetic'))
    monkeypatch.setattr(accounts,'acquire_client',AsyncMock(return_value=broker))
    submit=AsyncMock(return_value={'status':'ok'})
    monkeypatch.setattr(service,'place_manual_order',submit)
    app=FastAPI();app.include_router(tma.router)
    app.dependency_overrides[tma_user]=lambda:UserContext(user_id='alice',username='alice',role='user')
    with TestClient(app) as c:
        assert c.post('/tma/action',json={'action':'square_off','symbol':'SYNTHETIC','exchange':'BFO','quantity':20,'side':'SELL'}).status_code==409
        assert c.post('/tma/action',json={'action':'square_off','symbol':'SYNTHETIC','exchange':'BFO','quantity':20,'side':'BUY'}).json()['success']
    submit.assert_awaited_once_with('alice','SYNTHETIC','BUY',20,'BFO')


def test_mini_app_scripts_have_nonce_and_no_inline_handlers():
    html=tma._generate_tma_html('synthetic-nonce')
    assert '<script nonce="synthetic-nonce"' in html
    assert 'onclick=' not in html and 'onchange=' not in html and 'onkeyup=' not in html
    assert '22,520.45' not in html and '|| 1.25' not in html
    assert 'performSearch' not in html
    assert 'eval(' not in html and 'X-Telegram-Init-Data' in html
    assert '24OCT' not in html
