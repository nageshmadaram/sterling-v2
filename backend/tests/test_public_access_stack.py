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
    self.send_response(200);self.end_headers();self.wfile.write(json.dumps({'host':self.headers['Host'],'identity':self.headers.get('X-User-Id'),
                'proto':self.headers.get('X-Forwarded-Proto')}).encode())
   elif self.path.startswith('/api/v1/kite/callback?'):
    self.send_response(200);self.end_headers();self.wfile.write(self.path.encode())
   elif self.path=='/tma':self.send_response(503);self.end_headers()
   else:self.send_response(404);self.end_headers()
  def log_message(self,*args):pass
 backend=ThreadingHTTPServer(('127.0.0.1',0),Stub)
 threading.Thread(target=backend.serve_forever,daemon=True).start()
 with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
 import os
 shell=tmp_path/'shell.json'
 shell.write_text(json.dumps({'html':'<html>Telegram Mini App</html>','headers':{'content-type':'text/html'}}))
 process=subprocess.Popen(['node',str(ROOT/'scripts/serve_frontend.mjs')],env={**os.environ,'STERLING_TMA_SHELL_CACHE':str(shell),'STERLING_WEB_PORT':str(port),'STERLING_CALLBACK_PORT':str(callback_port),
     'STERLING_STACK_STATUS_FILE':str(tmp_path/'status.json'),
     'STERLING_API_TARGET':f'http://127.0.0.1:{backend.server_port}'},stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
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
 assert data=={'host':'remote.example.test','identity':None,'proto':'http'}

def test_public_broker_callback_does_not_require_app_bearer_token(gateway):
 query='?request_token=synthetic&state=signed%2Bstate&status=success'
 with urllib.request.urlopen(gateway+'/api/v1/kite/callback'+query) as response:
  assert response.status==200
  assert response.read().decode()=='/api/v1/kite/callback'+query

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


def test_ngrok_launch_preserves_fixed_hostname(monkeypatch):
 from types import SimpleNamespace
 args=SimpleNamespace(provider='ngrok',hostname='https://fixed.ngrok-free.dev',port=5180)
 instance=stack.Stack(args);instance.binary='/usr/local/bin/ngrok'
 monkeypatch.setattr(instance,'stop_child',lambda name:None)
 calls=[]
 class Process:
  pid=123
  stdout=[]
 def spawn(command,**kwargs):
  calls.append(command)
  return Process()
 monkeypatch.setattr(stack.subprocess,'Popen',spawn)
 instance.launch_tunnel();instance.launch_tunnel()
 assert calls[0]==calls[1]
 assert calls[0][1:]==['http','http://127.0.0.1:5180','--url',args.hostname,'--inspect=false','--log','stdout','--log-format','json']
 assert instance.status['app_url']==args.hostname
 assert instance.status['mode']=='ngrok'


def test_dual_publication_is_atomic_and_fails_closed_for_invalid_secondary():
 from app.services import db,public_access
 db.init()
 first=public_access.publish('https://primary.example.test',mode='both',
     alternate_origins={'ngrok':'https://alternate.ngrok-free.app'})
 assert first['app_url']=='https://primary.example.test'
 assert first['urls']=={'cloudflare':'https://primary.example.test','ngrok':'https://alternate.ngrok-free.app'}
 assert first['callback_url']=='https://primary.example.test/api/v1/kite/callback'
 assert public_access.status()['urls']==first['urls']
 assert db.get_config_strict('sterling_tma_url')=='https://primary.example.test/tma'
 with pytest.raises(ValueError):
  public_access.publish('https://changed.example.test',mode='both',
      alternate_origins={'ngrok':'http://localhost:5180'})
 assert public_access.status()['app_url']=='https://primary.example.test'
 # A lost tunnel is removed from the published secondary set on next
 # successful primary verification, without changing primary callback URL.
 single=public_access.publish('https://primary.example.test',mode='both')
 assert single['urls']=={'cloudflare':'https://primary.example.test'}
 with db._conn() as connection:
  connection.execute("DELETE FROM system_config WHERE key IN ('sterling_public_access','sterling_tma_url')")
  connection.commit()


def test_menu_links_both_verified_tunnels_and_one_primary_miniapp(monkeypatch):
 from app.services.notifications import telegram_kite as desk
 from app.services import public_access
 monkeypatch.setattr(public_access,'status',lambda:{
    'app_url':'https://cf.example.test',
    'urls':{'cloudflare':'https://cf.example.test',
            'ngrok':'https://secondary.ngrok-free.app'}})
 monkeypatch.setattr(desk,'_get_tma_url',lambda:'https://cf.example.test/tma')
 buttons=desk.kite_menu_kb()['inline_keyboard']
 assert buttons[0]==[{'text':'☁️ Sterling · Cloudflare','url':'https://cf.example.test'}]
 assert buttons[1]==[{'text':'🌐 Sterling · ngrok','url':'https://secondary.ngrok-free.app'}]
 assert buttons[2][0]['web_app']['url']=='https://cf.example.test/tma'
 assert sum('web_app' in entry for row in buttons for entry in row)==1


def test_secondary_ngrok_never_exposes_unverified_or_local_hostname():
 from types import SimpleNamespace
 assert stack.safe_hostname('https://fixed.ngrok-free.app/','ngrok')=='https://fixed.ngrok-free.app'
 for value in ('http://localhost:5180','https://127.0.0.1',
               'https://example.test/path','https://bad.example.test?token=1',
               'https://bad.example.test:5180'):
  with pytest.raises(ValueError):
   stack.safe_hostname(value,'ngrok')
 good=json.dumps({'lvl':'info','msg':'started tunnel',
                 'url':'https://auto.ngrok-free.app'})
 assert stack.ngrok_url_from_log(good)=='https://auto.ngrok-free.app'
 assert stack.ngrok_url_from_log(json.dumps({'msg':'authentication token error',
      'url':'https://auto.ngrok-free.app'}))==''
 assert stack.ngrok_url_from_log(json.dumps({'msg':'started tunnel',
      'url':'http://localhost:5180'}))==''
 assert stack.ngrok_url_from_log(json.dumps({'msg':'started tunnel',
      'url':'https://attacker.example.test'}))==''


def test_dual_launcher_starts_optional_secondary_and_dev_on_same_backend(monkeypatch,tmp_path):
 from types import SimpleNamespace
 args=SimpleNamespace(provider='both',hostname='',ngrok_hostname='https://secondary.ngrok-free.app',
                      port=5180,dev=True,dev_port=5174)
 instance=stack.Stack(args)
 instance.ngrok_binary='/usr/local/bin/ngrok'
 created=[]
 class Process:
  pid=321
  stdout=[]
  def poll(self):return None
 def spawn(command,**kwargs):
  created.append((command,kwargs))
  return Process()
 monkeypatch.setattr(stack.subprocess,'Popen',spawn)
 monkeypatch.setattr(stack,'STATE',tmp_path)
 monkeypatch.setattr(instance,'stop_child',lambda *_:None)
 instance.launch_ngrok()
 instance.start_dev()
 assert created[0][0]==[
   '/usr/local/bin/ngrok','http','http://127.0.0.1:5180','--url',
   'https://secondary.ngrok-free.app','--inspect=false','--log','stdout',
   '--log-format','json']
 assert created[1][0]==['npm','run','dev','--','--host','127.0.0.1',
                        '--port','5174','--strictPort']
 assert created[1][1]['env']['API_TARGET']=='http://127.0.0.1:8000'
 assert instance.status['local_url']=='http://127.0.0.1:5180'
 assert instance.status['dev_url']=='http://127.0.0.1:5174'
 assert instance.status['ngrok_url']=='https://secondary.ngrok-free.app'


def test_primary_publisher_receives_only_verified_secondary(monkeypatch):
 from types import SimpleNamespace
 args=SimpleNamespace(provider='both',hostname='',port=5180,dev=False)
 instance=stack.Stack(args)
 instance.python='python'
 instance.status.update(app_url='https://cf.example.test',
     ngrok_url='https://secondary.ngrok-free.app',
     public_ready={'cloudflare':True,'ngrok':True})
 calls=[]
 def publish(cmd,**kwargs):
  calls.append(cmd)
  return SimpleNamespace(returncode=0,stdout='{"published":true,"telegram_menu_updated":true}')
 monkeypatch.setattr(stack.subprocess,'run',publish)
 assert instance.publish()
 assert calls[-1][-3:]==['https://cf.example.test','both',
                         'https://secondary.ngrok-free.app']
 assert instance.status['callback_url']=='https://cf.example.test/api/v1/kite/callback'
 instance.status['public_ready']['ngrok']=False
 assert instance.publish()
 assert calls[-1][-1]==''


def test_named_primary_and_single_ngrok_urls_are_reported_without_guessing(monkeypatch,tmp_path):
 from types import SimpleNamespace
 monkeypatch.setattr(stack,'STATE',tmp_path)
 calls=[]
 class Process:
  pid=412
  stdout=[]
  def poll(self):return None
 def fake_popen(command,**kwargs):
  calls.append(command)
  return Process()
 monkeypatch.setattr(stack.subprocess,'Popen',fake_popen)
 for provider,host,key in [
   ('both','https://named-cloudflare.example.test','cloudflare_url'),
   ('ngrok','https://reserved.ngrok-free.app','ngrok_url'),
 ]:
  args=SimpleNamespace(provider=provider,hostname=host,port=5180,
       dev=False,dev_port=5174,ngrok_hostname='')
  supervisor=stack.Stack(args)
  supervisor.binary='/usr/bin/cloudflared' if provider=='both' else '/usr/bin/ngrok'
  supervisor.launch_tunnel()
  assert supervisor.status['app_url']==host
  assert supervisor.status[key]==host
  assert supervisor.status['tunnel_started_at']
 assert calls[0][:3]==['/usr/bin/cloudflared','tunnel','--no-autoupdate']
 assert calls[1][:3]==['/usr/bin/ngrok','http','http://127.0.0.1:5180']


def test_missing_ngrok_does_not_stop_cloudflare_or_backend(monkeypatch,tmp_path):
 from types import SimpleNamespace
 monkeypatch.setattr(stack,'STATE',tmp_path)
 args=SimpleNamespace(provider='both',hostname='',ngrok_hostname='',
      port=5180,dev=True,dev_port=5174)
 supervisor=stack.Stack(args)
 supervisor.ngrok_binary=None
 supervisor.children['backend']=object()
 supervisor.launch_ngrok()
 assert supervisor.status['ngrok_error']=='NGROK_BINARY_NOT_INSTALLED'
 assert supervisor.status['ngrok_url']==''
 assert 'backend' in supervisor.children
 assert supervisor.status['local_url']=='http://127.0.0.1:5180'


def test_unverified_ngrok_republication_preserves_primary_without_secondary(monkeypatch):
 from types import SimpleNamespace
 args=SimpleNamespace(provider='both',hostname='',port=5180,dev=False)
 supervisor=stack.Stack(args)
 supervisor.python='python'
 supervisor.status.update(app_url='https://named.example.test',
     ngrok_url='https://secondary.ngrok-free.app',
     public_ready={'cloudflare':True,'ngrok':False})
 calls=[]
 def fake_publisher(command,**kwargs):
  calls.append(command)
  return SimpleNamespace(returncode=0,
      stdout='{"published":true,"telegram_menu_updated":true}')
 monkeypatch.setattr(stack.subprocess,'run',fake_publisher)
 assert supervisor.publish()
 assert calls[-1][-1]==''
 assert supervisor.status['callback_url']=='https://named.example.test/api/v1/kite/callback'


def test_ngrok_https_is_trusted_only_when_supervisor_owns_matching_tunnel(gateway,tmp_path):
 status=tmp_path/'status.json'
 status.write_text(json.dumps({
   'ngrok_url':'https://verified.ngrok-free.app','children':{'ngrok':456},
   'phase':'ready',
 }))
 def request(host):
  req=urllib.request.Request(gateway+'/api/v1/test',headers={
   'Host':host,'Authorization':'Bearer test','X-Forwarded-Proto':'https'})
  with urllib.request.urlopen(req) as response:return json.load(response)
 good=request('verified.ngrok-free.app')
 assert good['proto']=='https'
 assert good['host']=='verified.ngrok-free.app'
 assert request('untrusted.example.test')['proto']=='http'
 status.write_text(json.dumps({
   'ngrok_url':'https://verified.ngrok-free.app','children':{'ngrok':456},
   'phase':'stopped',
 }))
 assert request('verified.ngrok-free.app')['proto']=='http'


def test_ss_listener_parser_recognizes_only_reported_listening_pid(monkeypatch):
 from types import SimpleNamespace
 monkeypatch.setattr(stack.shutil, 'which', lambda exe: '/usr/bin/ss' if exe == 'ss' else None)
 def run(command, **kwargs):
  assert command == ['ss','-H','-ltnp','sport = :5180']
  return SimpleNamespace(stdout=('LISTEN 0 511 127.0.0.1:5180 0.0.0.0:* '
   'users:(("node",pid=17937,fd=18))\n'))
 monkeypatch.setattr(stack.subprocess,'run',run)
 assert stack._gateway_listener_pids(5180)==[17937]


def test_unverified_existing_gateway_is_not_terminated(monkeypatch):
 import signal
 monkeypatch.setattr(stack,'_gateway_listener_pids',lambda port:[5555])
 monkeypatch.setattr(stack,'_is_own_orphan_gateway',lambda pid:False)
 sent=[]
 monkeypatch.setattr(stack.os,'kill',lambda pid,sig:sent.append((pid,sig)))
 with pytest.raises(RuntimeError,match='unverified process'):
  stack._reclaim_gateway(5180)
 assert sent == []


def test_gateway_reclaimer_only_terminates_verified_same_checkout_web(monkeypatch):
 import signal
 monkeypatch.setattr(stack,'_gateway_listener_pids',lambda port:[6666])
 monkeypatch.setattr(stack,'_is_own_orphan_gateway',lambda pid:pid==6666)
 sent=[]
 closed={'yes':False}
 monkeypatch.setattr(stack.os,'kill',lambda pid,sig:
  (sent.append((pid,sig)),closed.__setitem__('yes',True)))
 class Probe:
  def __enter__(self):return self
  def __exit__(self,*args):return None
  def connect_ex(self,location):return 1 if closed['yes'] else 0
 monkeypatch.setattr(stack.socket,'socket',lambda:Probe())
 assert stack._reclaim_gateway(5180)==6666
 assert sent==[(6666,signal.SIGTERM)]
 assert not any(pid==8000 for pid,_ in sent)


def test_ambiguous_multiple_gateway_owners_must_not_be_killed(monkeypatch):
 monkeypatch.setattr(stack,'_gateway_listener_pids',lambda port:[111,222])
 monkeypatch.setattr(stack,'_is_own_orphan_gateway',lambda pid:True)
 called=[]
 monkeypatch.setattr(stack.os,'kill',lambda *a:called.append(a))
 with pytest.raises(RuntimeError,match='unverified process'):
  stack._reclaim_gateway(5180)
 assert called == []
