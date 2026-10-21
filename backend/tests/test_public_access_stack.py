"""Remote publication, URL changes and real production proxy contracts."""
import importlib.util,json,socket,subprocess,threading,time,urllib.error,urllib.request
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('stack_test',ROOT/'scripts/sterling_stack.py')
stack=importlib.util.module_from_spec(spec);spec.loader.exec_module(stack)

@pytest.fixture(autouse=True)
def _restore_environ():
 # stack.main() copies STERLING_* keys from the real backend/.env into
 # os.environ; without this, live settings leak into later test files.
 import os
 saved=dict(os.environ)
 yield
 os.environ.clear();os.environ.update(saved)

def test_supervisor_error_diagnostics_keep_errno_but_never_secret_text():
 error=OSError(28,'sensitive credential-bearing detail')
 assert stack.error_code(error)=='OSError(errno=28)'
 assert 'sensitive' not in stack.error_code(error)
 assert stack.error_code(RuntimeError('private url'))=='RuntimeError'

def test_launch_failure_reason_goes_to_private_log_with_urls_redacted(tmp_path):
 log=tmp_path/'manager.log'
 stack.record_launch_failure(RuntimeError('Web port 5180 is occupied; see https://x.trycloudflare.com/?t=secret'),log)
 text=log.read_text()
 assert 'Web port 5180 is occupied' in text
 assert 'secret' not in text and 'trycloudflare' not in text
 assert oct(log.stat().st_mode)[-3:]=='600'

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
  if url.endswith('/health'):return 200,'{"status":"ok"}'
  return 200,'id="root" Telegram Mini App'
 monkeypatch.setattr(stack,'fetch',fetch)
 assert stack.public_ready('https://synthetic.example.test')
 monkeypatch.setattr(stack,'fetch',lambda url:(_ for _ in ()).throw(TimeoutError()))
 assert not stack.public_ready('https://synthetic.example.test')

def test_public_readiness_checks_are_concurrent_and_fail_closed(monkeypatch):
 barrier=threading.Barrier(4,timeout=2)
 def fetch(url):
  barrier.wait()
  if url.endswith('/api/v1/tma/data'):
   raise urllib.error.HTTPError(url,401,'Unauthorized',{},None)
  if url.endswith('/health'):return 200,'{"status":"ok"}'
  return 200,('Telegram Mini App' if url.endswith('/tma') else 'id="root"')
 monkeypatch.setattr(stack,'fetch',fetch)
 assert stack.public_ready('https://parallel.example.test')


def test_public_readiness_rejects_healthy_html_when_backend_is_down(monkeypatch):
 def fetch(url):
  if url.endswith('/data'):raise urllib.error.HTTPError(url,401,'',{},None)
  if url.endswith('/health'):raise urllib.error.HTTPError(url,503,'Service Unavailable',{},None)
  return 200,'id="root" Telegram Mini App'
 monkeypatch.setattr(stack,'fetch',fetch)
 assert not stack.public_ready('https://synthetic.example.test')


def test_public_readiness_rejects_plain_http_without_probe(monkeypatch):
 def reject(url):
  raise AssertionError('No HTTP probe should be made for a public origin')
 monkeypatch.setattr(stack,'fetch',reject)
 assert not stack.public_ready('http://parallel.example.test')


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
 class VacantPort:
  def __enter__(self):return self
  def __exit__(self,*args):return None
  def connect_ex(self,address):return 1
 monkeypatch.setattr(stack.socket,'socket',lambda:VacantPort())
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


def test_new_gateway_must_own_listening_socket_and_respond_to_health(monkeypatch):
 from types import SimpleNamespace
 child=SimpleNamespace(pid=789,poll=lambda:None)
 monkeypatch.setattr(stack,'_gateway_listener_pids',lambda port:[789])
 monkeypatch.setattr(stack,'_is_own_orphan_gateway',lambda pid:pid==789)
 monkeypatch.setattr(stack,'fetch',lambda url,timeout=2:
  (200,'{"service":"sterling-web","ready":true}'))
 stack._verify_gateway_child(child,5180)


def test_old_gateway_serving_200_does_not_allow_ours_to_claim_readiness(monkeypatch):
 from types import SimpleNamespace
 child=SimpleNamespace(pid=789,poll=lambda:None)
 monkeypatch.setattr(stack,'_gateway_listener_pids',lambda port:[555])
 monkeypatch.setattr(stack,'_is_own_orphan_gateway',lambda pid:False)
 monkeypatch.setattr(stack,'fetch',lambda url,timeout=2:
  (200,'{"service":"sterling-web","ready":true}'))
 with pytest.raises(RuntimeError,match='unexpected listener'):
  stack._verify_gateway_child(child,5180)


def test_exited_gateway_does_not_count_as_started(monkeypatch):
 from types import SimpleNamespace
 child=SimpleNamespace(pid=789,poll=lambda:1)
 with pytest.raises(RuntimeError,match='exited without binding'):
  stack._verify_gateway_child(child,5180)


def test_up_is_idempotent_with_healthy_current_manager(monkeypatch,tmp_path,capsys):
 monkeypatch.setattr(stack,'STATE',tmp_path)
 existing={'manager_pid':80808,'manager_protocol':4,'mode':'both',
  'dev_url':'http://127.0.0.1:5174','web_port':5180,'phase':'ready',
  'local_url':'http://127.0.0.1:5180',
  'public_ready':{'cloudflare':True,'ngrok':True},
  'urls':{
    'cloudflare':'https://cloudflare.example.test',
    'ngrok':'https://ngrok.example.test'}}
 monkeypatch.setattr(stack,'read_state',lambda:existing)
 monkeypatch.setattr(stack,'alive',lambda pid:True)
 monkeypatch.setattr(stack,'_managed_web_owns_port',lambda state:True)
 monkeypatch.setattr(stack,'_backend_listener_present',lambda:True)
 def no_restart(*args,**kwargs):
  raise AssertionError('Repeated ./sterling.sh up must reuse the manager')
 monkeypatch.setattr(stack,'_stop_old_managed_presentation',no_restart)
 monkeypatch.setattr(stack.subprocess,'Popen',no_restart)
 monkeypatch.setattr(stack.sys,'argv',['sterling_stack.py','up'])
 stack.main()
 out=capsys.readouterr().out
 assert 'MANAGED' in out
 assert 'https://cloudflare.example.test' in out
 assert 'https://ngrok.example.test' in out


def test_up_recovers_missing_adopted_backend_via_single_manager_restart(monkeypatch,tmp_path):
 from types import SimpleNamespace
 monkeypatch.setattr(stack,'STATE',tmp_path)
 old={'manager_pid':909,'manager_protocol':4,'mode':'both','phase':'reconnecting',
      'web_port':5180,'dev_url':'http://127.0.0.1:5174','children':{'web':810}}
 monkeypatch.setattr(stack,'read_state',lambda:old)
 running={'value':True}
 monkeypatch.setattr(stack,'alive',lambda pid:running['value'])
 monkeypatch.setattr(stack,'_managed_web_owns_port',lambda state:True)
 monkeypatch.setattr(stack,'_backend_listener_present',lambda:False)
 def stop_manager(pid):
  assert pid==909
  running['value']=False
 monkeypatch.setattr(stack,'_stop_old_managed_presentation',stop_manager)
 monkeypatch.setattr(stack,'wait_for_up_status',lambda pid,**kwargs:{})
 launched=[]
 def spawn(args,**kwargs):
  launched.append(args)
  return SimpleNamespace(pid=98989)
 monkeypatch.setattr(stack.subprocess,'Popen',spawn)
 monkeypatch.setattr(stack.sys,'argv',['sterling_stack.py','up'])
 stack.main()
 assert len(launched)==1
 assert str(stack.ROOT/'scripts/sterling_stack.py') in launched[0]
 assert 'run' in launched[0] and '--all' in launched[0]


def test_up_preserves_bound_backend_during_reconnect(monkeypatch,tmp_path):
 monkeypatch.setattr(stack,'STATE',tmp_path)
 old={'manager_pid':909,'manager_protocol':4,'mode':'both','phase':'reconnecting',
      'web_port':5180,'dev_url':'http://127.0.0.1:5174'}
 monkeypatch.setattr(stack,'read_state',lambda:old)
 monkeypatch.setattr(stack,'alive',lambda pid:True)
 monkeypatch.setattr(stack,'_managed_web_owns_port',lambda state:True)
 monkeypatch.setattr(stack,'_backend_listener_present',lambda:True)
 monkeypatch.setattr(stack,'wait_for_up_status',lambda pid,**kwargs:old)
 monkeypatch.setattr(stack,'_stop_old_managed_presentation',
                     lambda pid:(_ for _ in ()).throw(AssertionError('Must not stop')))
 monkeypatch.setattr(stack.subprocess,'Popen',
                     lambda *a,**kw:(_ for _ in ()).throw(AssertionError('Must not spawn')))
 monkeypatch.setattr(stack.sys,'argv',['sterling_stack.py','up'])
 stack.main()


def test_status_reports_backend_health_separately_from_gateway(monkeypatch):
 monkeypatch.setattr(stack,'_managed_web_owns_port',lambda state:True)
 monkeypatch.setattr(stack,'_backend_listener_present',lambda:False)
 monkeypatch.setattr(stack,'_backend_http_ready',lambda:False)
 out=stack.live_presentation_status({'phase':'reconnecting',
       'public_ready':{'cloudflare':False}},True)
 assert out['gateway_running'] is True
 assert out['backend_listening'] is False
 assert out['backend_ready'] is False


def test_up_replaces_old_ui_manager_but_never_calls_backend_shutdown(monkeypatch,tmp_path):
 monkeypatch.setattr(stack,'STATE',tmp_path)
 monkeypatch.setattr(stack,'read_state',lambda:{
   'manager_pid':6661,'mode':'ngrok','phase':'ready'})
 active={'value':True}
 monkeypatch.setattr(stack,'alive',lambda pid:active['value'])
 stopped=[]
 def stop(pid):
  stopped.append(pid)
  active['value']=False
 monkeypatch.setattr(stack,'_stop_old_managed_presentation',stop)
 started=[]
 class Process:
  pid=98989
 monkeypatch.setattr(stack,'wait_for_up_status',lambda pid,**kwargs:{})
 def popen(command,**kwargs):
  started.append(command)
  return Process()
 monkeypatch.setattr(stack.subprocess,'Popen',popen)
 monkeypatch.setattr(stack.sys,'argv',['sterling_stack.py','up'])
 stack.main()
 assert stopped==[6661]
 assert len(started)==1
 assert str(stack.ROOT/'scripts/sterling_stack.py') in started[0]
 assert 'run' in started[0]
 assert '--all' in started[0]
 assert '--skip-build' not in started[0]


def test_up_does_not_kill_unverifiable_old_manager(monkeypatch):
 import signal
 monkeypatch.setattr(stack,'alive',lambda pid:False)
 delivered=[]
 monkeypatch.setattr(stack.os,'kill',lambda *args:delivered.append(args))
 stack._stop_old_managed_presentation(555)
 assert delivered==[]


def test_healthy_manager_must_own_listener_not_just_responsive_port(monkeypatch):
 state={'web_port':5180,'children':{'web':789}}
 monkeypatch.setattr(stack,'_gateway_listener_pids',lambda port:[555])
 monkeypatch.setattr(stack,'_is_own_orphan_gateway',lambda pid:True)
 assert not stack._managed_web_owns_port(state)
 monkeypatch.setattr(stack,'_gateway_listener_pids',lambda port:[789])
 assert stack._managed_web_owns_port(state)


def test_start_web_reclaims_old_matching_listener_before_spawning(monkeypatch,tmp_path):
 from types import SimpleNamespace
 monkeypatch.setattr(stack,'STATE',tmp_path)
 args=SimpleNamespace(provider='both',hostname='',port=5180,dev=True,dev_port=5174)
 manager=stack.Stack(args)
 order=[]
 class Probe:
  def __enter__(self):return self
  def __exit__(self,*args):return False
  def connect_ex(self,loc):return 0  # an old listener occupies 5180
 monkeypatch.setattr(stack.socket,'socket',lambda:Probe())
 monkeypatch.setattr(stack,'_reclaim_gateway',
                     lambda port:(order.append('reclaim'),456)[1])
 class Child:
  pid=789
  def poll(self):return None
 monkeypatch.setattr(stack.subprocess,'Popen',
                     lambda *a,**kw:(order.append('spawn'),Child())[1])
 monkeypatch.setattr(stack,'_verify_gateway_child',
                     lambda child,port:order.append('verify'))
 manager.start_web()
 assert order==['reclaim','spawn','verify']
 assert manager.status['gateway_reclaimed_pid']==456
 assert manager.status['web_ready_pid']==789
 assert manager.children['web'].pid==789


def test_start_web_refuses_unknown_listener_without_launching_second(monkeypatch,tmp_path):
 from types import SimpleNamespace
 monkeypatch.setattr(stack,'STATE',tmp_path)
 args=SimpleNamespace(provider='both',hostname='',port=5180,dev=False,dev_port=5174)
 manager=stack.Stack(args)
 class Probe:
  def __enter__(self):return self
  def __exit__(self,*args):return False
  def connect_ex(self,loc):return 0
 monkeypatch.setattr(stack.socket,'socket',lambda:Probe())
 def block(port):
  raise RuntimeError('unverified process')
 monkeypatch.setattr(stack,'_reclaim_gateway',block)
 def no_spawn(*a,**kw):
  raise AssertionError('must never start second web process')
 monkeypatch.setattr(stack.subprocess,'Popen',no_spawn)
 with pytest.raises(RuntimeError,match='unverified process'):
  manager.start_web()


def test_up_reuses_manager_while_initializing_no_duplicate_start(monkeypatch,tmp_path):
 monkeypatch.setenv('STERLING_UP_WAIT_SECONDS','0')
 monkeypatch.setattr(stack,'STATE',tmp_path)
 monkeypatch.setattr(stack,'read_state',lambda:{
  'manager_pid':80808,'manager_protocol':4,'mode':'both',
  'dev_url':'http://127.0.0.1:5174','web_port':5180,
  'phase':'starting','local_url':'http://127.0.0.1:5180'})
 monkeypatch.setattr(stack,'alive',lambda pid:True)
 monkeypatch.setattr(stack,'_managed_web_owns_port',lambda state:False)
 def no_restart(*a,**kwargs):
  raise AssertionError('initializing supervisor must be reused')
 monkeypatch.setattr(stack,'_stop_old_managed_presentation',no_restart)
 monkeypatch.setattr(stack.subprocess,'Popen',no_restart)
 monkeypatch.setattr(stack.sys,'argv',['sterling_stack.py','up'])
 stack.main()


def test_committed_frontend_extraction_ignores_modified_and_untracked_source(tmp_path,monkeypatch):
 import os
 repo=tmp_path/'repo'
 source=repo/'frontend'
 source.mkdir(parents=True)
 (source/'package.json').write_text('{"name":"sterling-fixture","version":"1.0.0"}')
 (source/'package-lock.json').write_text('{"lockfileVersion":3}')
 (source/'index.html').write_text('COMMITTED RELEASE')
 subprocess.run(['git','init','-q',str(repo)],check=True)
 subprocess.run(['git','-C',str(repo),'add','frontend'],check=True)
 subprocess.run(['git','-C',str(repo),'-c','user.name=Test',
                 '-c','user.email=test@example.test','commit','-qm','release'],check=True)
 sha=subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD'],text=True).strip()
 (source/'index.html').write_text('DIRTY WORKING EDIT')
 (source/'untracked-secret.txt').write_text('UNCOMMITTED CONTENT')
 state=tmp_path/'release-state'
 state.mkdir()
 monkeypatch.setattr(stack,'ROOT',repo)
 monkeypatch.setattr(stack,'STATE',state)
 publisher=stack.CommittedFrontend('HEAD')
 assert publisher.resolve_commit()==sha
 publisher._extract(sha)
 assert (publisher.frontend/'index.html').read_text()=='COMMITTED RELEASE'
 assert not (publisher.frontend/'untracked-secret.txt').exists()
 assert (source/'index.html').read_text()=='DIRTY WORKING EDIT'
 assert (source/'untracked-secret.txt').is_file()


def test_committed_publish_is_atomic_and_failed_build_keeps_last_success(tmp_path,monkeypatch):
 from types import SimpleNamespace
 state=tmp_path/'release-state'
 state.mkdir()
 monkeypatch.setattr(stack,'STATE',state)
 publisher=stack.CommittedFrontend()
 current=['a'*40]
 monkeypatch.setattr(publisher,'resolve_commit',lambda:current[0])
 def committed_extract(sha):
  publisher.frontend.mkdir(parents=True,exist_ok=True)
  (publisher.frontend/'package-lock.json').write_text('{"lockfileVersion":3}')
  # Deliberately leave stale contents: promotion must clear them before build.
  (publisher.frontend/'dist').mkdir(exist_ok=True)
  (publisher.frontend/'dist/leftover.js').write_text('STALE')
 monkeypatch.setattr(publisher,'_extract',committed_extract)
 monkeypatch.setattr(publisher,'_dependencies',lambda:None)
 fail=[False]
 def simulated_build(command,**kwargs):
  assert command==['npm','run','build']
  assert not (publisher.frontend/'dist/leftover.js').exists()
  if fail[0]:
   raise subprocess.CalledProcessError(1,command)
  (publisher.frontend/'dist').mkdir(exist_ok=True)
  (publisher.frontend/'dist/index.html').write_text(f'BUILD {current[0]}')
  (publisher.frontend/'dist/assets').mkdir(exist_ok=True)
  (publisher.frontend/'dist/assets/old.js').write_text('OLD HASHED ASSET')
  return SimpleNamespace(returncode=0)
 monkeypatch.setattr(stack.subprocess,'run',simulated_build)
 sha,changed=publisher.publish()
 assert changed and sha=='a'*40
 assert publisher.current()==sha
 assert (publisher.pointer/'index.html').read_text()==f'BUILD {sha}'
 current[0]='b'*40
 fail[0]=True
 with pytest.raises(subprocess.CalledProcessError):
  publisher.publish()
 assert publisher.current()=='a'*40
 assert (publisher.pointer/'index.html').read_text()=='BUILD '+('a'*40)
 fail[0]=False
 sha,changed=publisher.publish()
 assert changed and sha=='b'*40
 assert publisher.current()=='b'*40
 assert (publisher.pointer/'assets/old.js').read_text()=='OLD HASHED ASSET'
 assert publisher.publish()==('b'*40,False)


def test_gateway_serves_new_committed_release_without_restart(tmp_path):
 import os
 with socket.socket() as sock:
  sock.bind(('127.0.0.1',0))
  port=sock.getsockname()[1]
 first=tmp_path/'releases/a'
 second=tmp_path/'releases/b'
 first.mkdir(parents=True)
 second.mkdir(parents=True)
 (first/'index.html').write_text('<html><div id="root">COMMIT A</div></html>')
 (second/'index.html').write_text('<html><div id="root">COMMIT B</div></html>')
 pointer=tmp_path/'published'
 pointer.symlink_to(first,target_is_directory=True)
 manifest=tmp_path/'published.json'
 manifest.write_text(json.dumps({'sha':'a'*40,'ref':'HEAD'}))
 process=subprocess.Popen(['node',str(ROOT/'scripts/serve_frontend.mjs')],
   env={**os.environ,'STERLING_WEB_PORT':str(port),
        'STERLING_CALLBACK_PORT':'0','STERLING_STATIC_ROOT':str(pointer),
        'STERLING_RELEASE_MANIFEST':str(manifest)},
   stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
 origin=f'http://127.0.0.1:{port}'
 try:
  for _ in range(80):
   try:
    with urllib.request.urlopen(origin+'/',timeout=.25) as response:
     assert response.status==200
     assert 'COMMIT A' in response.read().decode()
     assert response.headers['Cache-Control']=='no-store'
    break
   except (ConnectionError,urllib.error.URLError):
    time.sleep(.05)
  else:pytest.fail('Committed gateway failed to start')
  temp=tmp_path/'published.next'
  temp.symlink_to(second,target_is_directory=True)
  temp.replace(pointer)
  manifest.write_text(json.dumps({'sha':'b'*40,'ref':'HEAD'}))
  with urllib.request.urlopen(origin+'/',timeout=2) as response:
   assert 'COMMIT B' in response.read().decode()
  with urllib.request.urlopen(origin+'/__sterling_web_health',timeout=2) as response:
   health=json.load(response)
   assert health['ready'] is True
   assert health['release_commit']=='b'*40
  assert process.poll() is None
 finally:
  process.terminate()
  process.wait(8)



def test_release_update_worker_reports_committed_version_without_restarting_services(monkeypatch,tmp_path):
 from types import SimpleNamespace
 monkeypatch.setattr(stack,'STATE',tmp_path)
 manager=stack.Stack(SimpleNamespace(provider='both',hostname='',
                                     port=5180,dev=True,dev_port=5174))
 monkeypatch.setattr(manager.publisher,'publish',lambda:('c'*40,True))
 monkeypatch.setattr(stack.subprocess,'run',
                     lambda *args,**kwargs:(_ for _ in ()).throw(
                         AssertionError('No remote fetch requested')))
 manager.refresh_committed_release(False)
 assert manager.release_results.get_nowait()==('ready','c'*40,True)
 assert manager.backend_owned is False
 assert manager.children=={}


def test_release_update_worker_fails_closed_and_retains_prior_release(monkeypatch,tmp_path):
 from types import SimpleNamespace
 monkeypatch.setattr(stack,'STATE',tmp_path)
 manager=stack.Stack(SimpleNamespace(provider='both',hostname='',
                                     port=5180,dev=True,dev_port=5174))
 def fail_compile():
  raise subprocess.CalledProcessError(2,['npm','run','build'])
 monkeypatch.setattr(manager.publisher,'publish',fail_compile)
 manager.refresh_committed_release(False)
 assert manager.release_results.get_nowait()==('failed','CalledProcessError',False)


def test_public_source_defaults_to_local_main_and_rejects_invalid_ref(monkeypatch,tmp_path):
 monkeypatch.setattr(stack,'STATE',tmp_path)
 publisher=stack.CommittedFrontend()
 assert publisher.ref=='main'
 assert publisher.frontend==tmp_path/'committed-source/frontend'
 for ref in ('--output=/tmp/leak', 'refs/heads/../foo', 'HEAD;rm -rf /'):
  with pytest.raises(ValueError,match='Invalid published Git ref'):
   stack.CommittedFrontend(ref)




def test_stopped_manager_never_reports_cached_tunnels_as_verified(monkeypatch,tmp_path,capsys):
 monkeypatch.setattr(stack,'STATE',tmp_path)
 old={
  'manager_pid':3386654, 'phase':'stopped','local_url':'http://127.0.0.1:5180',
  'dev_url':'http://127.0.0.1:5174','mode':'both',
  'public_ready':{'cloudflare':True,'ngrok':True},
  'urls':{'cloudflare':'https://cf.example.test','ngrok':'https://ngrok.example.test'},
  'cloudflare_url':'https://cf.example.test','ngrok_url':'https://ngrok.example.test',
  'mini_app_url':'https://cf.example.test/tma',
  'callback_url':'https://cf.example.test/api/v1/kite/callback',
 }
 monkeypatch.setattr(stack,'read_state',lambda:old)
 monkeypatch.setattr(stack,'alive',lambda pid:False)
 monkeypatch.setattr(stack,'_managed_web_owns_port',lambda state:False)
 monkeypatch.setattr(stack.sys,'argv',['sterling_stack.py','status'])
 stack.main()
 state=json.loads(capsys.readouterr().out)
 assert state['manager_running'] is False
 assert state['gateway_running'] is False
 assert state['public_ready']=={} and state['urls']=={}
 assert state['mini_app_url']=='' and state['callback_url']==''
 monkeypatch.setattr(stack.sys,'argv',['sterling_stack.py','urls'])
 stack.main()
 urls=json.loads(capsys.readouterr().out)
 assert urls['cloudflare']=='' and urls['ngrok']==''
 assert urls['primary_telegram_desk']==''
 assert urls['kite_callback_guidance']==''


def test_live_manager_with_dead_gateway_does_not_claim_public_readiness():
 cached={'phase':'ready','mode':'both','public_ready':{'cloudflare':True,'ngrok':True},
         'urls':{'cloudflare':'https://cf.example.test'}}
 from unittest.mock import patch
 with patch.object(stack,'_managed_web_owns_port',return_value=False):
  reported=stack.live_presentation_status(cached,True)
 assert reported['manager_running'] is True
 assert reported['gateway_running'] is False
 assert reported['public_ready']=={} and reported['urls']=={}


def test_verified_tunnels_preserved_when_gateway_owned_and_supervised():
 cached={'phase':'ready','mode':'both','public_ready':{'cloudflare':True,'ngrok':False},
         'urls':{'cloudflare':'https://cf.example.test','ngrok':'https://old.ngrok.test'},
         'mini_app_url':'https://cf.example.test/tma',
         'callback_url':'https://cf.example.test/api/v1/kite/callback'}
 from unittest.mock import patch
 with patch.object(stack,'_managed_web_owns_port',return_value=True), \
      patch.object(stack,'_backend_listener_present',return_value=True), \
      patch.object(stack,'_backend_http_ready',return_value=True):
  reported=stack.live_presentation_status(cached,True)
 assert reported['public_ready']=={'cloudflare':True,'ngrok':False}
 assert reported['urls']=={'cloudflare':'https://cf.example.test'}
 assert reported['mini_app_url'].endswith('/tma')
 assert reported['callback_url'].endswith('/api/v1/kite/callback')


def test_last_known_public_urls_are_hidden_when_backend_dies(monkeypatch):
 """Gateway and tunnels can be alive after an adopted API has vanished."""
 cached={'phase':'ready','mode':'both',
         'public_ready':{'cloudflare':True,'ngrok':True},
         'urls':{'cloudflare':'https://old-cf.example.test',
                 'ngrok':'https://old-ngrok.example.test'},
         'mini_app_url':'https://old-cf.example.test/tma',
         'callback_url':'https://old-cf.example.test/api/v1/kite/callback'}
 monkeypatch.setattr(stack,'_managed_web_owns_port',lambda _:True)
 monkeypatch.setattr(stack,'_backend_listener_present',lambda:False)
 def never_fetch_health():
  raise AssertionError('A vacant backend port must short-circuit HTTP probing')
 monkeypatch.setattr(stack,'_backend_http_ready',never_fetch_health)
 visible=stack.live_presentation_status(cached,True)
 assert visible['manager_running'] is True
 assert visible['gateway_running'] is True
 assert visible['backend_listening'] is False
 assert visible['backend_ready'] is False
 assert visible['public_ready']=={} and visible['urls']=={}
 assert visible['mini_app_url']=='' and visible['callback_url']==''

 monkeypatch.setattr(stack,'_backend_listener_present',lambda:True)
 monkeypatch.setattr(stack,'_backend_http_ready',lambda:False)
 visible=stack.live_presentation_status(cached,True)
 assert visible['backend_listening'] is True
 assert visible['backend_ready'] is False
 assert visible['public_ready']=={} and visible['urls']=={}


def test_up_dashboard_is_prominent_and_prints_only_verified_urls(monkeypatch,capsys):
 from types import SimpleNamespace
 monkeypatch.setattr(stack,'_terminal_color_enabled',lambda:False)
 state={
  'phase':'ready','release_ref':'main','release_commit':'a'*40,
  'local_url':'http://127.0.0.1:5180',
  'dev_url':'http://127.0.0.1:5174',
  'public_ready':{'cloudflare':True,'ngrok':False},
  'urls':{'cloudflare':'https://cf.example.test',
          'ngrok':'https://OLD-UNVERIFIED.ngrok-free.app'},
 }
 stack.print_up_dashboard(state,reused=True,gateway_ready=True)
 out=capsys.readouterr().out
 assert 'S T E R L I N G' in out
 assert 'MANAGED' in out
 assert 'LOCAL APP' in out and 'http://127.0.0.1:5180' in out
 assert 'CLOUDFLARE' in out and 'https://cf.example.test' in out
 assert 'NGROK' in out and 'CONNECTING' in out
 assert 'OLD-UNVERIFIED' not in out
 assert 'http://127.0.0.1:5174' in out
 assert 'main · aaaaaaaaaaaa' in out
 assert '\x1b[' not in out
 assert 'uncommitted edits' in out


def test_up_dashboard_contains_ansi_colors_only_for_terminal(monkeypatch,capsys):
 monkeypatch.setattr(stack,'_terminal_color_enabled',lambda:True)
 state={'phase':'ready','public_ready':{'cloudflare':True,'ngrok':True},
        'urls':{'cloudflare':'https://cf.example.test',
                'ngrok':'https://ngrok.example.test'}}
 stack.print_up_dashboard(state,reused=True,gateway_ready=True)
 out=capsys.readouterr().out
 assert '\x1b[1;96m' in out  # bright cyan URLs / branding
 assert '\x1b[1;92m' in out  # verified green status
 assert '\x1b[1;95m' in out  # dev purple
 assert 'https://cf.example.test' in out
 assert 'https://ngrok.example.test' in out


def test_up_dashboard_starting_never_displays_stale_public_urls(monkeypatch,capsys):
 monkeypatch.setattr(stack,'_terminal_color_enabled',lambda:False)
 state={'phase':'starting',
        'public_ready':{'cloudflare':True,'ngrok':True},
        'urls':{'cloudflare':'https://stale-cf.example.test',
                'ngrok':'https://stale-ngrok.example.test'}}
 stack.print_up_dashboard(state,reused=False,gateway_ready=False)
 output=capsys.readouterr().out
 assert 'STARTING' in output
 assert output.count('CONNECTING')==2
 assert 'stale-cf' not in output and 'stale-ngrok' not in output
 assert 'http://127.0.0.1:5180' in output
 assert 'http://127.0.0.1:5174' in output


def test_wait_for_up_status_returns_genuinely_verified_urls(monkeypatch):
 state={'manager_pid':999,'phase':'ready',
   'public_ready':{'cloudflare':True,'ngrok':True},
   'children':{'web':321},'urls':{'cloudflare':'https://cf.example.test',
                                   'ngrok':'https://tunnel.ngrok-free.dev'}}
 monkeypatch.setattr(stack,'read_state',lambda:state)
 monkeypatch.setattr(stack,'alive',lambda pid:True)
 monkeypatch.setattr(stack,'_managed_web_owns_port',lambda value:True)
 assert stack.wait_for_up_status(999,seconds=2)==state


def test_ngrok_stays_visible_even_when_cloudflare_is_reconnecting(monkeypatch,capsys):
 monkeypatch.setattr(stack,'_terminal_color_enabled',lambda:False)
 state={'phase':'reconnecting','public_ready':{'cloudflare':False,'ngrok':True},
        'urls':{'ngrok':'https://verified.ngrok-free.dev'},
        'ngrok_url':'https://verified.ngrok-free.dev',
        'cloudflare_url':'https://unverified.trycloudflare.com'}
 stack.print_up_dashboard(state,reused=True,gateway_ready=True)
 out=capsys.readouterr().out
 assert 'LOCAL APP' in out and 'READY' in out
 assert 'NGROK' in out and 'VERIFIED' in out
 assert 'https://verified.ngrok-free.dev' in out
 assert 'https://unverified.trycloudflare.com (unverified)' in out


def test_no_color_environment_disables_ansi_even_for_real_tty(monkeypatch):
 from types import SimpleNamespace
 monkeypatch.setattr(stack.sys,'stdout',SimpleNamespace(isatty=lambda:True))
 monkeypatch.setenv('TERM','xterm-256color')
 monkeypatch.setenv('NO_COLOR','1')
 assert stack._terminal_color_enabled() is False
 monkeypatch.delenv('NO_COLOR')
 assert stack._terminal_color_enabled() is True
 monkeypatch.setenv('TERM','dumb')
 assert stack._terminal_color_enabled() is False


def test_external_backend_service_prevents_second_worker(monkeypatch):
 supervisor=object.__new__(stack.Stack)
 supervisor.status={}
 supervisor.children={}
 supervisor.backend_owned=False
 monkeypatch.setattr(stack,'externally_supervised_backend',lambda:True)
 monkeypatch.setattr(stack.subprocess,'Popen',lambda *a,**k:pytest.fail('duplicate worker'))
 supervisor.start_backend()
 assert supervisor.status['backend_error']=='EXTERNAL_SUPERVISOR_RECOVERY_PENDING'
 assert not supervisor.backend_owned
 assert not supervisor.children

@pytest.mark.parametrize('state,expected',[('loaded',True),('not-found',False)])
def test_external_backend_service_detection(monkeypatch,state,expected):
 from types import SimpleNamespace
 monkeypatch.setattr(stack.subprocess,'run',lambda *a,**k:SimpleNamespace(returncode=0,stdout=state))
 assert stack.externally_supervised_backend() is expected


def test_foreground_service_waits_for_existing_presentation_owner(tmp_path):
 import fcntl
 owner=(tmp_path/'manager.lock').open('a')
 standby=(tmp_path/'manager.lock').open('a')
 fcntl.flock(owner,fcntl.LOCK_EX|fcntl.LOCK_NB)
 assert not stack.acquire_manager_lock(standby,foreground=False)
 acquired=threading.Event()
 def wait_for_owner():
  assert stack.acquire_manager_lock(standby,foreground=True)
  acquired.set()
 thread=threading.Thread(target=wait_for_owner)
 thread.start()
 try:
  assert not acquired.wait(.1)
  fcntl.flock(owner,fcntl.LOCK_UN)
  assert acquired.wait(3)
 finally:
  owner.close()
  thread.join(3)
  standby.close()
 assert not thread.is_alive()


def test_temporary_public_failures_preserve_the_quick_tunnel_login_origin():
 assert stack.should_rotate_failed_tunnel('',100,1,10000) is False
 assert stack.should_rotate_failed_tunnel('https://named.example',4,1,10000) is True
 assert stack.should_rotate_failed_tunnel('https://named.example',3,1,10000) is False
 assert stack.should_rotate_failed_tunnel('https://named.example',4,100,120) is False


def test_cloudflare_can_use_tcp_transport_without_exposing_credentials(monkeypatch,tmp_path):
 from types import SimpleNamespace
 monkeypatch.setattr(stack,'STATE',tmp_path)
 monkeypatch.setenv('STERLING_CLOUDFLARE_PROTOCOL','http2')
 commands=[]
 class Process:
  pid=412
  stdout=[]
  def poll(self):return 0
 monkeypatch.setattr(stack.subprocess,'Popen',lambda command,**kw:commands.append(command) or Process())
 args=SimpleNamespace(provider='cloudflare',hostname='',port=5180,dev=False,dev_port=5174)
 supervisor=stack.Stack(args);supervisor.binary='/usr/bin/cloudflared'
 supervisor.launch_tunnel()
 assert commands[0][:5]==['/usr/bin/cloudflared','tunnel','--no-autoupdate','--protocol','http2']
 monkeypatch.setenv('STERLING_CLOUDFLARE_PROTOCOL','invalid')
 with pytest.raises(RuntimeError):supervisor.launch_tunnel()
 assert len(commands)==1


@pytest.mark.parametrize('script,expected', [
 ('scripts/serve_frontend.mjs',True),
 ('ABSOLUTE',True),
 ('../other/scripts/serve_frontend.mjs',False),
 ('-e',False),
])
def test_gateway_ownership_resolves_relative_script_in_same_checkout(monkeypatch,tmp_path,script,expected):
 repo=tmp_path/'repo';repo.mkdir()
 proc=tmp_path/'process';proc.mkdir()
 (proc/'cwd').symlink_to(repo,target_is_directory=True)
 value=str(repo/'scripts/serve_frontend.mjs') if script=='ABSOLUTE' else script
 (proc/'cmdline').write_bytes(('node\0'+value+'\0').encode())
 original_path=Path
 monkeypatch.setattr(stack,'ROOT',repo)
 monkeypatch.setattr(stack,'Path',lambda value:proc if str(value)=='/proc/987654' else original_path(value))
 assert stack._is_own_orphan_gateway(987654) is expected


def test_startup_reports_discovered_url_before_verification(monkeypatch):
 initial={'manager_pid':999,'phase':'starting','mode':'both'}
 discovered={**initial,'cloudflare_url':'https://new.trycloudflare.com'}
 ready={**discovered,'phase':'ready','ngrok_url':'https://new.ngrok-free.app',
        'public_ready':{'cloudflare':True,'ngrok':True}}
 states=iter([initial,initial,discovered,ready])
 monkeypatch.setattr(stack,'read_state',lambda:next(states))
 monkeypatch.setattr(stack,'alive',lambda pid:True)
 monkeypatch.setattr(stack,'_managed_web_owns_port',lambda state:True)
 monkeypatch.setattr(stack.time,'sleep',lambda seconds:None)
 updates=[]
 assert stack.wait_for_up_status(999,seconds=2,notify=updates.append)==ready
 assert updates==[initial,discovered,ready]
 assert not updates[1].get('public_ready')


@pytest.mark.parametrize('mode,provider',[('ngrok','ngrok'),('quick','cloudflare'),('named','cloudflare')])
def test_single_provider_startup_does_not_wait_for_unused_tunnel(monkeypatch,mode,provider):
 state={'manager_pid':999,'phase':'ready','mode':mode,'public_ready':{provider:True}}
 monkeypatch.setattr(stack,'read_state',lambda:state)
 monkeypatch.setattr(stack,'alive',lambda pid:True)
 monkeypatch.setattr(stack,'_managed_web_owns_port',lambda state:True)
 monkeypatch.setattr(stack.time,'sleep',lambda _:pytest.fail('unused provider delayed startup'))
 assert stack.wait_for_up_status(999,seconds=2)==state


def test_ngrok_quota_error_is_reported_without_echoing_provider_page(monkeypatch,capsys):
 import io
 def exhausted(*args,**kwargs):
  raise urllib.error.HTTPError('https://quota.ngrok-free.dev',403,'Forbidden',{},
                               io.BytesIO(b'private provider text ERR_NGROK_725'))
 monkeypatch.setattr(stack,'fetch',exhausted)
 assert stack.probe_public_endpoint('https://quota.ngrok-free.dev')==(False,'ERR_NGROK_725')
 state={'mode':'both','public_errors':{'ngrok':'ERR_NGROK_725'},
        'ngrok_url':'https://quota.ngrok-free.dev'}
 stack.print_up_dashboard(state,reused=True,gateway_ready=True)
 out=capsys.readouterr().out
 assert 'Monthly bandwidth exhausted (ERR_NGROK_725)' in out
 assert 'UNAVAILABLE' in out and 'private provider text' not in out
