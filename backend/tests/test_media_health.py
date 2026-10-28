import asyncio
import json
import socket
import pytest
from app.services import db, media_health as health

@pytest.fixture
def store(tmp_path,monkeypatch):
    monkeypatch.setattr(db,'_DB_PATH',str(tmp_path/'health.db'))
    assert db.init()
    return health

def item(key='station',sources=None):
    return {'key':key,'name':'Test broadcaster','sources':sources or ['https://example.com/main','https://example.com/backup']}

def row():
    with db._conn() as c:return dict(c.execute('SELECT * FROM media_stream_health LIMIT 1').fetchone())

@pytest.mark.asyncio
async def test_all_sources_fail_then_recovery_persists(store,monkeypatch):
    store.sync('a','radio',[item()]);visited=[]
    async def failed(session,url,depth=0):visited.append(url);return False
    monkeypatch.setattr(store,'probe_source',failed)
    await store.audit(row(),None)
    assert visited==item()['sources']
    assert store.listing('a','radio')['review'][0]['key']=='station'
    assert store.listing('b','radio')['review']==[]
    store.update('a','radio','station','refresh')
    async def recovered(session,url,depth=0):return url.endswith('/backup')
    monkeypatch.setattr(store,'probe_source',recovered)
    await store.audit(row(),None)
    assert store.listing('a','radio')['review']==[]
    assert row()['checked_at']>0 and row()['next_check']-row()['checked_at']==store.MONTH

@pytest.mark.asyncio
async def test_timeout_is_inconclusive_and_does_not_quarantine(store,monkeypatch):
    store.sync('a','tv',[item()])
    async def uncertain(*args):return None
    monkeypatch.setattr(store,'probe_source',uncertain)
    await store.audit(row(),None)
    assert store.listing('a','tv')['review']==[]
    assert row()['reason']=='Check inconclusive; retry scheduled'

def test_manual_moves_are_scoped_and_playback_restores(store):
    for owner,feature in [('a','tv'),('b','tv'),('a','radio')]:store.sync(owner,feature,[item()])
    store.update('a','tv','station','review')
    assert len(store.listing('a','tv')['review'])==1
    assert store.listing('b','tv')['review']==[]
    assert store.listing('a','radio')['review']==[]
    store.update('a','tv','station','playing')
    assert store.listing('a','tv')['review']==[]

def test_failure_only_queues_all_source_check(store):
    store.sync('a','tv',[item()]);store.update('a','tv','station','failed')
    assert row()['next_check']<0 and row()['status']=='available'

@pytest.mark.asyncio
async def test_changed_sources_and_manual_moves_win_over_stale_audit(store,monkeypatch):
    store.sync('a','tv',[item()]);original=row()
    async def changed(*args):
        store.sync('a','tv',[item(sources=['https://example.com/new'])])
        return False
    monkeypatch.setattr(store,'probe_source',changed)
    await store.audit(original,None)
    assert json.loads(row()['sources'])==['https://example.com/new']
    assert row()['status']=='available' and row()['checked_at']==0

@pytest.mark.parametrize('address',['file:///etc/passwd','http://127.0.0.1/a','https://[::1]/','http://169.254.169.254/latest','https://user:secret@example.com/','http://10.0.0.1/'])
def test_rejects_non_public_or_credentialed_urls(address):
    with pytest.raises(ValueError):health.public_url(address)

@pytest.mark.asyncio
async def test_dns_resolution_rejects_mixed_public_private_addresses(monkeypatch):
    async def lookup(*args,**kwargs):return [(socket.AF_INET,socket.SOCK_STREAM,6,'',('1.1.1.1',443)),(socket.AF_INET,socket.SOCK_STREAM,6,'',('127.0.0.1',443))]
    monkeypatch.setattr(asyncio.get_running_loop(),'getaddrinfo',lookup)
    with pytest.raises(ValueError):await health.PublicResolver().resolve('example.com',443)

def test_digest_only_committed_when_upload_complete_and_old_status_is_retained(store):
    store.sync('a','radio',[item()]);assert store.listing('a','radio')['digest'] is None
    store.update('a','radio','station','review')
    store.sync('a','radio',[item()],digest='v1',complete=True)
    assert store.listing('a','radio')['digest']=='v1'
    assert len(store.listing('a','radio')['review'])==1

class Body:
    def __init__(self,data):self.data=data
    async def read(self,limit):value=self.data[:limit];self.data=self.data[limit:];return value
    def at_eof(self):return not self.data

class Response:
    def __init__(self,status,mime,data=b'',location=None):
        self.status=status;self.headers={'Content-Type':mime};self.content=Body(data);self.released=False
        if location:self.headers['Location']=location
    async def __aenter__(self):return self
    async def __aexit__(self,*args):self.release()
    def release(self):self.released=True

class Session:
    def __init__(self,responses):self.responses=responses;self.calls=[]
    def get(self,url,**kwargs):self.calls.append(url);return self.responses[url]

@pytest.mark.asyncio
async def test_playlist_checks_media_segment_and_releases_before_following():
    manifest=Response(200,'application/vnd.apple.mpegurl',b'#EXTM3U\n#EXTINF:4\npart.ts\n')
    segment=Response(206,'video/mp2t',b'G'+b'X'*200)
    session=Session({'https://example.com/a.m3u8':manifest,'https://example.com/part.ts':segment})
    assert await health.probe_source(session,'https://example.com/a.m3u8') is True
    assert session.calls==['https://example.com/a.m3u8','https://example.com/part.ts'] and manifest.released

@pytest.mark.asyncio
async def test_redirect_cannot_reach_private_service():
    session=Session({'https://example.com/start':Response(302,'',location='http://169.254.169.254/latest')})
    assert await health.probe_source(session,'https://example.com/start') is None
    assert session.calls==['https://example.com/start']

@pytest.mark.asyncio
@pytest.mark.parametrize('status,mime,body,expected',[(404,'text/html',b'gone',False),(200,'text/html',b'<html>Not a stream</html>',False),(403,'text/html',b'blocked',None),(429,'text/plain',b'rate limit',None),(200,'audio/mpeg',b'ID3test',True)])
async def test_probe_distinguishes_media_absence_and_inconclusive_errors(status,mime,body,expected):
    session=Session({'https://example.com/stream':Response(status,mime,body)})
    assert await health.probe_source(session,'https://example.com/stream') is expected
