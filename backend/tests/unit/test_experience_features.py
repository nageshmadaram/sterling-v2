import base64,json
from datetime import datetime
from zoneinfo import ZoneInfo
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization
from app.services import db
from app.core.auth import get_current_user,UserContext
from app.api.v1.endpoints import profile,push,pcr,feed_test
from app.services.auth import user_store
from app.services.notifications import web_push
from app.core.passwords import hash_password,verify_password

@pytest.fixture
def client(tmp_path,monkeypatch):
 monkeypatch.setattr(db,'_DB_PATH',str(tmp_path/'test.db'));monkeypatch.setattr(db,'_available',True)
 u=user_store._User(id='alice',username='alice',password_hash=hash_password('Current secure passphrase!42'))
 monkeypatch.setattr(user_store,'_users',{'alice':u});monkeypatch.setattr(user_store,'_loaded',True)
 with db._conn() as conn:db._create_tables(conn)
 user_store._init_table();user_store._persist(u)
 monkeypatch.setattr(profile,'check_login',lambda r:None)
 monkeypatch.setattr(profile.tokens,'mint_access',lambda *a:'test-access');monkeypatch.setattr(profile.tokens,'mint_refresh',lambda *a:'test-refresh')
 app=FastAPI()
 for router in [profile.router,push.router,pcr.router,feed_test.router]:app.include_router(router)
 app.dependency_overrides[get_current_user]=lambda:UserContext('alice')
 return TestClient(app),app

def test_profile_identity_and_security(client):
 c,app=client
 assert c.get('/profile').json()['username']=='alice'
 assert c.patch('/profile',json={'display_name':'My name','photo':''}).json()['display_name']=='My name'
 assert c.patch('/profile',json={'display_name':'X','photo':'data:image/svg+xml;base64,AAAA'}).status_code==422
 assert c.post('/profile/security',json={'current_password':'wrong','username':'renamed'}).status_code==400
 r=c.post('/profile/security',json={'current_password':'Current secure passphrase!42','username':'renamed','new_password':'New secure passphrase!42'})
 assert r.status_code==200
 assert c.get('/profile').json()['username']=='renamed'
 assert user_store.get_by_id('alice').token_version==2
 with db._conn() as conn:
  row=conn.execute('SELECT username,password_hash,token_version FROM users WHERE id=?',('alice',)).fetchone()
  assert row['username']=='renamed' and row['token_version']==2 and verify_password('New secure passphrase!42',row['password_hash'])
 app.dependency_overrides[get_current_user]=lambda:UserContext('bob')
 assert c.get('/profile').status_code==401

def test_profile_photo_upload_replace_remove_persists(client):
 c,app=client
 photo1='data:image/jpeg;base64,'+base64.b64encode(b'\xff\xd8\xfffirst').decode()
 photo2='data:image/jpeg;base64,'+base64.b64encode(b'\xff\xd8\xffsecond').decode()
 first=c.patch('/profile',json={'display_name':'Alice','photo':photo1})
 assert first.status_code==200 and first.json()['photo']==photo1
 assert c.get('/profile').json()['photo']==photo1
 replacement=c.patch('/profile',json={'display_name':'Alice','photo':photo2})
 assert replacement.status_code==200 and c.get('/profile').json()['photo']==photo2
 removed=c.patch('/profile',json={'display_name':'Alice','photo':''})
 assert removed.status_code==200 and removed.json()['photo']==''
 assert c.get('/profile').json()['photo']==''
 app.dependency_overrides[get_current_user]=lambda:UserContext('bob')
 assert c.patch('/profile',json={'display_name':'Other','photo':photo1}).status_code==401
 app.dependency_overrides[get_current_user]=lambda:UserContext('alice')
 assert c.get('/profile').json()['photo']==''

def subscription():
 key=ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(serialization.Encoding.X962,serialization.PublicFormat.UncompressedPoint)
 enc=lambda v:base64.urlsafe_b64encode(v).decode().rstrip('=')
 return {'endpoint':'https://fcm.googleapis.com/test/device','keys':{'p256dh':enc(key),'auth':enc(b'1234567890abcdef')}}

def test_push_validation_isolation_and_durable_keys(client):
 c,app=client
 assert c.get('/push').json()['config']['enabled'] is False
 assert c.get('/push').json()['public_key']==c.get('/push').json()['public_key']
 s=subscription();identifier=c.post('/push/subscribe',json={'subscription':s,'label':'Phone'}).json()['id']
 with db._conn() as conn:assert s['endpoint'] not in conn.execute('SELECT subscription_enc FROM push_devices').fetchone()[0]
 invalid={**s,'endpoint':'http://127.0.0.1:8000/private'}
 assert c.post('/push/subscribe',json={'subscription':invalid}).status_code==422
 app.dependency_overrides[get_current_user]=lambda:UserContext('bob')
 assert c.get('/push').json()['devices']==[]
 c.delete('/push/devices/'+identifier)
 app.dependency_overrides[get_current_user]=lambda:UserContext('alice')
 assert len(c.get('/push').json()['devices'])==1

@pytest.mark.asyncio
async def test_push_dedupe_quiet_and_opt_in(client,monkeypatch):
 c,_=client;c.post('/push/subscribe',json={'subscription':subscription()})
 import pywebpush
 sent=[];monkeypatch.setattr(pywebpush,'webpush',lambda **kw:sent.append(kw))
 assert await web_push.send('alice','one','T','B','signals')==0
 cfg={**web_push.DEFAULTS,'enabled':True};c.put('/push/config',json=cfg)
 assert await web_push.send('alice','one','T','B','signals')==1
 assert await web_push.send('alice','one','T','B','signals')==0
 payload=json.loads(sent[0]['data'])
 assert payload['title']=='T' and payload['body']=='B'
 assert payload['url']=='/kite?view=live&notification=signals'
 assert web_push.quiet({**cfg,'quiet_enabled':True},datetime(2026,10,12,23,0,tzinfo=ZoneInfo('Asia/Kolkata')))

@pytest.mark.asyncio
@pytest.mark.parametrize('stamp',['2026-10-11T11:00:00','2026-10-20T11:00:00','2026-10-12T18:00:00'])
async def test_pcr_never_scrapes_closed_market(stamp,monkeypatch):
 now=datetime.fromisoformat(stamp).replace(tzinfo=ZoneInfo('Asia/Kolkata'))
 monkeypatch.setattr(pcr,'datetime',SimpleNamespace(now=lambda tz:now))
 monkeypatch.setitem(pcr._CACHE,'payload',None)
 # A recent history attempt prevents a separate after-close recovery fetch.
 monkeypatch.setattr(pcr, '_history_retry_at', __import__('time').time())
 scrape=AsyncMock(return_value={});monkeypatch.setattr(pcr,'_scrape_all',scrape)
 result=await pcr.pcr_session(None)
 assert result['marketOpen'] is False and result['source']=='snapshot';scrape.assert_not_awaited()

@pytest.mark.asyncio
async def test_provider_probe_keeps_identity_and_freshness(client,monkeypatch):
 from app.api.v1.endpoints import kite
 now=datetime(2026,10,12,10,0,tzinfo=ZoneInfo('Asia/Kolkata'))
 monkeypatch.setattr(feed_test,'datetime',SimpleNamespace(now=lambda tz:now,fromisoformat=datetime.fromisoformat,fromtimestamp=datetime.fromtimestamp))
 quote=AsyncMock(return_value={'NSE:NIFTY 50':{'last_price':100,'timestamp':now.isoformat()}});monkeypatch.setattr(kite,'quote',quote)
 result=await feed_test.probe(feed_test.Probe(provider='kite',symbol='NSE:NIFTY 50'),UserContext('alice'))
 assert result['realtime_verified'] and result['symbol']=='NSE:NIFTY 50'
 monkeypatch.setattr(feed_test.time,'time',lambda:now.timestamp())
 assert feed_test.confirm(feed_test.Confirmation(id=result['id']),UserContext('alice'))['confirmed']

def test_password_change_keeps_legacy_username(client):
 c,_=client;u=user_store.get_by_id('alice');u.username='existing@example.com';user_store._persist(u)
 result=c.post('/profile/security',json={'current_password':'Current secure passphrase!42','username':u.username,'new_password':'New secure passphrase!42'})
 assert result.status_code==200 and result.json()['profile']['username']=='existing@example.com'
