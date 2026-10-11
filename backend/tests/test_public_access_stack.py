"""Remote publication, URL changes and real production proxy contracts."""
import importlib.util,json,socket,subprocess,threading,time,urllib.error,urllib.request
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('stack_test',ROOT/'scripts/sterling_stack.py')
stack=importlib.util.module_from_spec(spec);spec.loader.exec_module(stack)

def test_destinations_change_atomically():
 from app.services import db,public_access
 db.init();public_access.publish('https://old.example.test')
 current=public_access.publish('https://new.example.test/')
 assert current['app_url']=='https://new.example.test'
 assert current['callback_url'].endswith('/api/v1/kite/callback')
 assert public_access.status()==current
 assert db.get_config_strict('sterling_tma_url')==current['mini_app_url']
 with db._conn() as connection:
  connection.execute("DELETE FROM system_config WHERE key IN ('sterling_public_access','sterling_tma_url')");connection.commit()

@pytest.mark.parametrize('origin',['http://remote.test','https://remote.test/tma','https://user:pass@remote.test','https://remote.test?token=x','https://remote.test#x'])
def test_unsafe_origin(origin):
 from app.services.public_access import validate_origin
 with pytest.raises(ValueError):validate_origin(origin)

def test_anonymous_private_api_prevents_publication(monkeypatch):
 monkeypatch.setattr(stack,'fetch',lambda url:(200,'id="root" Telegram Mini App'))
 assert not stack.public_ready('https://synthetic.example.test')

def test_publication_requires_live_app_desk_and_auth(monkeypatch):
 def fetch(url):
  if url.endswith('/data'):raise urllib.error.HTTPError(url,401,'',{},None)
  return 200,'id="root" Telegram Mini App'
 monkeypatch.setattr(stack,'fetch',fetch)
 assert stack.public_ready('https://synthetic.example.test')
 monkeypatch.setattr(stack,'fetch',lambda url:(_ for _ in ()).throw(TimeoutError()))
 assert not stack.public_ready('https://synthetic.example.test')

async def test_menu_url_change_preserves_poll_offset(monkeypatch):
 from app.services.notifications import telegram_bot as bot,telegram_kite as desk
 monkeypatch.setattr(bot._tg,'TELEGRAM_TOKEN','synthetic');monkeypatch.setattr(bot._tg,'TELEGRAM_CHAT_ID','1')
 url={'value':'https://first.example.test/tma'};monkeypatch.setattr(desk,'_get_tma_url',lambda:url['value'])
 calls=[];reads=0
 async def api(method,payload,timeout=35):
  nonlocal reads
  calls.append((method,payload))
  if method=='getUpdates':
   reads+=1
   if reads==1:return {'ok':True,'result':[{'update_id':17}]}
   if reads==2:
    url['value']='https://second.example.test/tma'
    return {'ok':True,'result':[]}
   import asyncio
   raise asyncio.CancelledError()
  return {'ok':True,'result':True}
 monkeypatch.setattr(bot,'_api',api)
 import asyncio
 with pytest.raises(asyncio.CancelledError):await bot.poll_loop()
 menus=[p['menu_button']['web_app']['url'] for m,p in calls if m=='setChatMenuButton']
 assert menus==['https://first.example.test/tma','https://second.example.test/tma']
 updates=[p for m,p in calls if m=='getUpdates']
 assert updates[0]['offset']==-1 and updates[1]['offset']==18 and updates[2]['offset']==18

async def test_menu_is_repaired_periodically_without_resetting_poll_offset(monkeypatch):
 from app.services.notifications import telegram_bot as bot,telegram_kite as desk
 monkeypatch.setattr(bot._tg,'TELEGRAM_TOKEN','synthetic');monkeypatch.setattr(bot._tg,'TELEGRAM_CHAT_ID','1')
 monkeypatch.setattr(desk,'_get_tma_url',lambda:'https://current.example.test/tma')
 now={'value':100.0};monkeypatch.setattr(bot.time,'monotonic',lambda:now['value'])
 calls=[];reads=0
 async def api(method,payload,timeout=35):
  nonlocal reads
  calls.append((method,payload))
  if method=='getUpdates':
   reads+=1
   if reads==1:return {'ok':True,'result':[{'update_id':17}]}
   if reads==2:
    now['value']+=301
    return {'ok':True,'result':[]}
   import asyncio
   raise asyncio.CancelledError()
  return {'ok':True,'result':True}
 monkeypatch.setattr(bot,'_api',api)
 import asyncio
 with pytest.raises(asyncio.CancelledError):await bot.poll_loop()
 menus=[p['menu_button']['web_app']['url'] for m,p in calls if m=='setChatMenuButton']
 assert menus==['https://current.example.test/tma']*2
 updates=[p for m,p in calls if m=='getUpdates']
 assert updates[0]['offset']==-1 and updates[1]['offset']==18 and updates[2]['offset']==18

@pytest.fixture
def callback_port():
 with socket.socket() as sock:
  sock.bind(('127.0.0.1',0))
  return sock.getsockname()[1]

@pytest.fixture
def gateway(tmp_path, callback_port):
 class Stub(BaseHTTPRequestHandler):
  def do_GET(self):
   if self.path=='/api/v1/test':
    self.send_response(200);self.end_headers();self.wfile.write(json.dumps({'host':self.headers['Host'],'identity':self.headers.get('X-User-Id')}).encode())
   elif self.path=='/tma':self.send_response(503);self.end_headers()
   else:self.send_response(404);self.end_headers()
  def log_message(self,*args):pass
 backend=ThreadingHTTPServer(('127.0.0.1',0),Stub)
 threading.Thread(target=backend.serve_forever,daemon=True).start()
 with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
 import os
 shell=tmp_path/'shell.json'
 shell.write_text(json.dumps({'html':'<html>Telegram Mini App</html>','headers':{'content-type':'text/html'}}))
 process=subprocess.Popen(['node',str(ROOT/'scripts/serve_frontend.mjs')],env={**os.environ,'STERLING_TMA_SHELL_CACHE':str(shell),'STERLING_WEB_PORT':str(port),'STERLING_CALLBACK_PORT':str(callback_port),'STERLING_API_TARGET':f'http://127.0.0.1:{backend.server_port}'},stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
 origin=f'http://127.0.0.1:{port}'
 try:
  for _ in range(50):
   try:urllib.request.urlopen(origin+'/__sterling_web_health',timeout=.2);break
   except Exception:time.sleep(.05)
  yield origin
 finally:process.terminate();process.wait(5);backend.shutdown();backend.server_close()

def test_gateway_auth_and_identity(gateway):
 with pytest.raises(urllib.error.HTTPError) as denied:urllib.request.urlopen(gateway+'/api/v1/test')
 assert denied.value.code==401
 req=urllib.request.Request(gateway+'/api/v1/test',headers={'Host':'remote.example.test','Authorization':'Bearer synthetic','X-User-Id':'other-user'})
 with urllib.request.urlopen(req) as response:data=json.load(response)
 assert data=={'host':'remote.example.test','identity':None}

def test_legacy_callback_preserves_signed_query_and_moves_to_app_origin(gateway, callback_port):
 class NoRedirect(urllib.request.HTTPRedirectHandler):
  def redirect_request(self, req, fp, code, msg, headers, newurl):return None
 opener=urllib.request.build_opener(NoRedirect())
 query='?request_token=synthetic&state=signed%2Bstate&status=success'
 with pytest.raises(urllib.error.HTTPError) as redirect:
  opener.open(f'http://127.0.0.1:{callback_port}/api/v1/kite/callback'+query)
 assert redirect.value.code==302
 assert redirect.value.headers['Location']==gateway+'/api/v1/kite/callback'+query
 assert redirect.value.headers['Cache-Control']=='no-store'
 assert redirect.value.headers['Referrer-Policy']=='no-referrer'

def test_legacy_callback_port_does_not_expose_the_app_or_private_api(gateway, callback_port):
 for path in ['/', '/api/v1/auth/users', '/api/v1/kite/accounts', '/api/v1/kite/callback/extra']:
  with pytest.raises(urllib.error.HTTPError) as response:
   urllib.request.urlopen(f'http://127.0.0.1:{callback_port}'+path)
  assert response.value.code==404

def test_missing_asset_and_source_never_become_spa(gateway):
 for path in ['/assets/missing.js','/.env','/docs','/scripts/sterling_stack.py']:
  with pytest.raises(urllib.error.HTTPError) as missing:urllib.request.urlopen(gateway+path)
  assert missing.value.code==404


def test_backend_outage_keeps_public_desk_shell_and_protects_private_data(gateway):
 with urllib.request.urlopen(gateway+'/tma') as response:
  assert response.status==200
  assert 'Telegram Mini App' in response.read().decode()
 with pytest.raises(urllib.error.HTTPError) as denied:urllib.request.urlopen(gateway+'/api/v1/tma/data')
 assert denied.value.code==401


def test_backend_failure_does_not_require_new_tunnel(monkeypatch):
 def healthy(url):
  return 200,json.dumps({'service':'sterling-web','ready':True})
 monkeypatch.setattr(stack,'fetch',healthy)
 assert stack.tunnel_unavailable('https://public.example',5180) is False
 def unavailable(url):
  if url.startswith('https:'):raise OSError('tunnel unavailable')
  return healthy(url)
 monkeypatch.setattr(stack,'fetch',unavailable)
 assert stack.tunnel_unavailable('https://public.example',5180) is True
 monkeypatch.setattr(stack,'fetch',lambda url: (_ for _ in ()).throw(OSError('local gateway down')))
 assert stack.tunnel_unavailable('https://public.example',5180) is False
