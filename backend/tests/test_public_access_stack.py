"""Remote publication, URL changes and real production proxy contracts."""
import importlib.util,json,os,socket,subprocess,threading,time,urllib.error,urllib.request
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


def test_existing_hidden_file_and_nested_symlink_cannot_escape_public_assets(tmp_path):
 """Integration check of the real Node gateway, without broker dependencies."""
 with socket.socket() as sock:
  sock.bind(('127.0.0.1',0))
  port=sock.getsockname()[1]
 public=tmp_path/'dist'
 (public/'assets').mkdir(parents=True)
 (public/'index.html').write_text('<html>PUBLIC STERLING</html>')
 (public/'assets/app.js').write_text('PUBLIC SCRIPT')
 (public/'.env').write_text('SHOULD NEVER BE SERVED')
 outside=tmp_path/'private-key.txt'
 outside.write_text('OUTSIDE RELEASE')
 (public/'assets/escaped.txt').symlink_to(outside)
 (public/'assets/untyped').symlink_to(outside)
 (public/'jump').symlink_to(tmp_path,target_is_directory=True)
 process=subprocess.Popen(['node',str(ROOT/'scripts/serve_frontend.mjs')],
   env={**os.environ,'STERLING_WEB_PORT':str(port),'STERLING_CALLBACK_PORT':'0',
        'STERLING_STATIC_ROOT':str(public)},
   stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
 origin=f'http://127.0.0.1:{port}'
 try:
  for _ in range(60):
   try:
    with urllib.request.urlopen(origin+'/__sterling_web_health',timeout=.25) as response:
     if json.load(response)['ready']:break
   except (OSError,urllib.error.URLError):
    pass
   time.sleep(.05)
  else:pytest.fail('Gateway failed to serve valid static fixture')
  for path in ['/.env','/%2eenv','/assets/escaped.txt','/assets/untyped',
               '/jump/private-key.txt','/private/.token','/assets/missing.js']:
   with pytest.raises(urllib.error.HTTPError) as denied:
    urllib.request.urlopen(origin+path,timeout=2)
   assert denied.value.code==404,path
  with urllib.request.urlopen(origin+'/assets/app.js',timeout=2) as response:
   assert response.read().decode()=='PUBLIC SCRIPT'
  with urllib.request.urlopen(origin+'/signals',timeout=2) as response:
   assert 'PUBLIC STERLING' in response.read().decode()
  assert process.poll() is None
 finally:
  process.terminate()
  process.wait(5)


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


def test_cloudflare_named_tunnel_keeps_configured_hostname(monkeypatch, tmp_path):
 from types import SimpleNamespace
 monkeypatch.setattr(stack,'STATE',tmp_path)
 instance=stack.Stack(SimpleNamespace(provider='cloudflare',
     hostname='https://named.example.test',port=5180,dev=False,dev_port=5174))
 instance.binary='/usr/bin/cloudflared'
 monkeypatch.setattr(instance,'stop_child',lambda name:None)
 calls=[]
 class Process:
  pid=123
  stdout=[]
 def spawn(command,**kwargs):
  calls.append(command);return Process()
 monkeypatch.setattr(stack.subprocess,'Popen',spawn)
 instance.launch_tunnel();instance.launch_tunnel()
 assert calls[0]==calls[1]
 assert calls[0][:3]==['/usr/bin/cloudflared','tunnel','--no-autoupdate']
 assert calls[0][-1]=='run'
 assert instance.status['app_url']=='https://named.example.test'
 assert instance.status['cloudflare_url']==instance.status['app_url']
 assert instance.status['mode']=='named'
 assert set(instance.children)=={'cloudflare'}


def test_cloudflare_only_publication_is_atomic_and_rejects_legacy_mode():
 from app.services import db,public_access
 db.init()
 current=public_access.publish('https://primary.example.test',mode='quick')
 assert current['urls']=={'cloudflare':'https://primary.example.test'}
 assert current['callback_url']=='https://primary.example.test/api/v1/kite/callback'
 assert public_access.status()['urls']==current['urls']
 assert db.get_config_strict('sterling_tma_url')=='https://primary.example.test/tma'
 with pytest.raises(ValueError):
  public_access.publish('https://other.example.test',mode='both')
 with pytest.raises(ValueError):
  public_access.publish('https://other.example.test',mode='ngrok')
 with pytest.raises(ValueError):
  public_access.publish('https://invalid.ngrok-free.dev',mode='quick')
 assert public_access.status()['app_url']=='https://primary.example.test'
 with db._conn() as connection:
  connection.execute("DELETE FROM system_config WHERE key IN ('sterling_public_access','sterling_tma_url')")
  connection.commit()


def test_cloudflare_menu_has_single_launch_link(monkeypatch):
 from app.services.notifications import telegram_kite as desk
 from app.services import public_access
 monkeypatch.setattr(public_access,'status',lambda:{
    'app_url':'https://cf.example.test',
    'urls':{'cloudflare':'https://cf.example.test'}})
 monkeypatch.setattr(desk,'_get_tma_url',lambda:'https://cf.example.test/tma')
 buttons=desk.kite_menu_kb()['inline_keyboard']
 assert buttons[0]==[{'text':'☁️ Sterling · Cloudflare','url':'https://cf.example.test'}]
 assert sum('web_app' in entry for row in buttons for entry in row)==1


def test_cloudflare_only_launcher_preserves_dev_and_backend(monkeypatch,tmp_path):
 from types import SimpleNamespace
 monkeypatch.setattr(stack,'STATE',tmp_path)
 instance=stack.Stack(SimpleNamespace(provider='cloudflare',hostname='',
     port=5180,dev=True,dev_port=5174))
 class Probe:
  def __enter__(self):return self
  def __exit__(self,*args):return False
  def connect_ex(self,address):return 1
 monkeypatch.setattr(stack.socket,'socket',lambda:Probe())
 created=[]
 class Process:
  pid=321
  def poll(self):return None
 def spawn(command,**kwargs):
  created.append((command,kwargs))
  return Process()
 monkeypatch.setattr(stack.subprocess,'Popen',spawn)
 monkeypatch.setattr(instance,'stop_child',lambda *_:None)
 instance.start_dev()
 assert created[0][0]==['npm','run','dev','--','--host','127.0.0.1',
                          '--port','5174','--strictPort']
 assert created[0][1]['env']['API_TARGET']=='http://127.0.0.1:8000'
 assert instance.status['local_url']=='http://127.0.0.1:5180'
 assert instance.status['dev_url']=='http://127.0.0.1:5174'
 assert instance.children.keys()=={'dev'}
 assert instance.backend_owned is False


def test_cloudflare_only_publisher_calls_public_access_once(monkeypatch):
 from types import SimpleNamespace
 instance=stack.Stack(SimpleNamespace(provider='cloudflare',hostname='',
     port=5180,dev=False,dev_port=5174))
 instance.python='python'
 instance.status.update(app_url='https://cf.example.test',
    public_ready={'cloudflare':True})
 calls=[]
 def publish(cmd,**kwargs):
  calls.append(cmd)
  return SimpleNamespace(returncode=0,stdout='{"published":true,"telegram_menu_updated":true}')
 monkeypatch.setattr(stack.subprocess,'run',publish)
 assert instance.publish()
 assert calls[-1][-2:]==['https://cf.example.test','quick']
 assert instance.status['callback_url']=='https://cf.example.test/api/v1/kite/callback'


def test_secondary_forwarded_headers_are_untrusted(gateway,tmp_path):
 status=tmp_path/'status.json'
 status.write_text(json.dumps({'phase':'ready','children':{},'cloudflare_url':''}))
 req=urllib.request.Request(gateway+'/api/v1/test',headers={
  'Host':'other.example.test','Authorization':'Bearer test','X-Forwarded-Proto':'https'})
 with urllib.request.urlopen(req) as response:
  payload=json.load(response)
 assert payload['proto']=='http'


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
 monkeypatch.setenv('STERLING_PUBLISH_REF','origin/main')
 monkeypatch.setenv('STERLING_PUBLISH_FETCH','1')
 monkeypatch.setattr(stack,'STATE',tmp_path)
 existing={'manager_pid':80808,'manager_protocol':4,'mode':'quick',
  'dev_url':'http://127.0.0.1:5174','web_port':5180,'phase':'ready',
  'local_url':'http://127.0.0.1:5180',
  'release_ref':'origin/main','release_auto_fetch':True,
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
 assert 'ngrok' not in out.lower()


def test_up_recovers_missing_adopted_backend_via_single_manager_restart(monkeypatch,tmp_path):
 from types import SimpleNamespace
 monkeypatch.setattr(stack,'STATE',tmp_path)
 old={'manager_pid':909,'manager_protocol':4,'mode':'quick','phase':'reconnecting',
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
 monkeypatch.setenv('STERLING_PUBLISH_REF','origin/main')
 monkeypatch.setenv('STERLING_PUBLISH_FETCH','1')
 monkeypatch.setattr(stack,'STATE',tmp_path)
 old={'manager_pid':909,'manager_protocol':4,'mode':'quick','phase':'reconnecting',
      'web_port':5180,'dev_url':'http://127.0.0.1:5174',
      'release_ref':'origin/main','release_auto_fetch':True}
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
 args=SimpleNamespace(provider='cloudflare',hostname='',port=5180,dev=True,dev_port=5174)
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
 args=SimpleNamespace(provider='cloudflare',hostname='',port=5180,dev=False,dev_port=5174)
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
 monkeypatch.setenv('STERLING_PUBLISH_REF','origin/main')
 monkeypatch.setenv('STERLING_PUBLISH_FETCH','1')
 monkeypatch.setenv('STERLING_UP_WAIT_SECONDS','0')
 monkeypatch.setattr(stack,'STATE',tmp_path)
 monkeypatch.setattr(stack,'read_state',lambda:{
  'manager_pid':80808,'manager_protocol':4,'mode':'quick',
  'dev_url':'http://127.0.0.1:5174','web_port':5180,
  'phase':'starting','local_url':'http://127.0.0.1:5180',
   'release_ref':'origin/main','release_auto_fetch':True})
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


@pytest.mark.parametrize('allow_links',[True,False])
def test_committed_publish_is_atomic_and_failed_build_keeps_last_success(tmp_path,monkeypatch,allow_links):
 from types import SimpleNamespace
 state=tmp_path/'release-state'
 state.mkdir()
 monkeypatch.setattr(stack,'STATE',state)
 publisher=stack.CommittedFrontend()
 monkeypatch.setattr(publisher,'_require_release_headroom',lambda:None)
 if not allow_links:
  import errno
  def unsupported_link(*args,**kwargs):raise OSError(errno.EOPNOTSUPP,'hard links unavailable')
  monkeypatch.setattr(stack.os,'link',unsupported_link)
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
  assert kwargs['env']['STERLING_BUILD_SHA']==current[0]
  assert not (publisher.frontend/'dist/leftover.js').exists()
  if fail[0]:
   raise subprocess.CalledProcessError(1,command)
  (publisher.frontend/'dist').mkdir(exist_ok=True)
  (publisher.frontend/'dist/index.html').write_text(f'BUILD {current[0]}')
  (publisher.frontend/'dist/assets').mkdir(exist_ok=True)
  (publisher.frontend/'dist/assets'/f'{current[0]}.js').write_text(f'ASSET {current[0]}')
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
 # Simulate an older/crashed publisher leaving an incomplete SHA directory.
 # The retry may replace only this unreferenced, invalid directory.
 incomplete=publisher.releases/('b'*40)
 incomplete.mkdir(parents=True)
 (incomplete/'orphan.txt').write_text('PARTIAL')
 fail[0]=False
 sha,changed=publisher.publish()
 assert changed and sha=='b'*40
 assert not (incomplete/'orphan.txt').exists()
 assert publisher.current()=='b'*40
 carried=publisher.pointer/'assets'/f"{'a'*40}.js"
 original=publisher.releases/('a'*40)/'assets'/f"{'a'*40}.js"
 assert carried.read_text()==original.read_text()=='ASSET '+('a'*40)
 assert (carried.stat().st_ino==original.stat().st_ino) is allow_links
 assert (publisher.pointer/'assets'/f"{'b'*40}.js").read_text()=='ASSET '+('b'*40)
 assert publisher.publish()==('b'*40,False)


def test_gateway_serves_new_committed_release_without_restart(tmp_path):
 import os
 with socket.socket() as sock:
  sock.bind(('127.0.0.1',0))
  port=sock.getsockname()[1]
 first=tmp_path/'releases'/('a'*40)
 second=tmp_path/'releases'/('b'*40)
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
  # Simulate the dangerous boundary: gateway already serves COMMIT B, while
  # published.json still contains COMMIT A (or its update may have failed).
  with urllib.request.urlopen(origin+'/',timeout=2) as response:
   assert 'COMMIT B' in response.read().decode()
  assert json.loads(manifest.read_text())['sha']=='a'*40
  with urllib.request.urlopen(origin+'/__sterling_web_health',timeout=2) as response:
   health=json.load(response)
   assert health['ready'] is True
   assert health['release_commit']=='b'*40
  # Missing/corrupt metadata must also not hide the actual served version.
  manifest.write_text('{invalid')
  with urllib.request.urlopen(origin+'/__sterling_web_health',timeout=2) as response:
   assert json.load(response)['release_commit']=='b'*40
  manifest.write_text(json.dumps({'sha':'b'*40,'ref':'HEAD'}))
  with urllib.request.urlopen(origin+'/__sterling_web_health',timeout=2) as response:
   assert json.load(response)['release_commit']=='b'*40
  # A directory outside the managed releases tree cannot claim readiness,
  # even with a complete index.html and a previously valid published.json.
  outside=tmp_path/'untrusted'/('c'*40)
  outside.mkdir(parents=True)
  (outside/'index.html').write_text('EXTERNAL')
  swap=tmp_path/'published.next'
  swap.symlink_to(outside,target_is_directory=True)
  swap.replace(pointer)
  with urllib.request.urlopen(origin+'/__sterling_web_health',timeout=2) as response:
   health=json.load(response)
   assert health['ready'] is False
   assert health['release_commit'] is None
  with pytest.raises(urllib.error.HTTPError) as blocked:
   urllib.request.urlopen(origin+'/',timeout=2)
  assert blocked.value.code==503
  # Broken symlinks must also fail closed rather than using the old manifest.
  pointer.unlink()
  pointer.symlink_to(tmp_path/'missing',target_is_directory=True)
  with urllib.request.urlopen(origin+'/__sterling_web_health',timeout=2) as response:
   health=json.load(response)
   assert health['ready'] is False
   assert health['release_commit'] is None
  with pytest.raises(urllib.error.HTTPError) as blocked:
   urllib.request.urlopen(origin+'/',timeout=2)
  assert blocked.value.code==503
  pointer.unlink()
  pointer.symlink_to(second,target_is_directory=True)
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
 manager=stack.Stack(SimpleNamespace(provider='cloudflare',hostname='',
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
 manager=stack.Stack(SimpleNamespace(provider='cloudflare',hostname='',
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
  'dev_url':'http://127.0.0.1:5174','mode':'quick',
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
 assert urls['cloudflare']=='' and 'ngrok' not in urls
 assert urls['primary_telegram_desk']==''
 assert urls['kite_callback_guidance']==''


def test_live_manager_with_dead_gateway_does_not_claim_public_readiness():
 cached={'phase':'ready','mode':'quick','public_ready':{'cloudflare':True,'ngrok':True},
         'urls':{'cloudflare':'https://cf.example.test'}}
 from unittest.mock import patch
 with patch.object(stack,'_managed_web_owns_port',return_value=False):
  reported=stack.live_presentation_status(cached,True)
 assert reported['manager_running'] is True
 assert reported['gateway_running'] is False
 assert reported['public_ready']=={} and reported['urls']=={}


def test_verified_tunnels_preserved_when_gateway_owned_and_supervised():
 cached={'phase':'ready','mode':'quick','public_ready':{'cloudflare':True,'ngrok':False},
         'urls':{'cloudflare':'https://cf.example.test','ngrok':'https://old.ngrok.test'},
         'mini_app_url':'https://cf.example.test/tma',
         'callback_url':'https://cf.example.test/api/v1/kite/callback'}
 from unittest.mock import patch
 with patch.object(stack,'_managed_web_owns_port',return_value=True), \
      patch.object(stack,'_backend_listener_present',return_value=True), \
      patch.object(stack,'_backend_http_ready',return_value=True):
  reported=stack.live_presentation_status(cached,True)
 assert reported['public_ready']=={'cloudflare':True}
 assert reported['urls']=={'cloudflare':'https://cf.example.test'}
 assert reported['mini_app_url'].endswith('/tma')
 assert reported['callback_url'].endswith('/api/v1/kite/callback')


def test_last_known_public_urls_are_hidden_when_backend_dies(monkeypatch):
 """Gateway and tunnels can be alive after an adopted API has vanished."""
 cached={'phase':'ready','mode':'quick',
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
 assert 'NGROK' not in out
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
 assert 'ngrok' not in out.lower()


def test_up_dashboard_starting_never_displays_stale_public_urls(monkeypatch,capsys):
 monkeypatch.setattr(stack,'_terminal_color_enabled',lambda:False)
 state={'phase':'starting',
        'public_ready':{'cloudflare':True,'ngrok':True},
        'urls':{'cloudflare':'https://stale-cf.example.test',
                'ngrok':'https://stale-ngrok.example.test'}}
 stack.print_up_dashboard(state,reused=False,gateway_ready=False)
 output=capsys.readouterr().out
 assert 'STARTING' in output
 assert output.count('CONNECTING')==1
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


def test_public_dashboard_never_renders_legacy_secondary(monkeypatch,capsys):
 monkeypatch.setattr(stack,'_terminal_color_enabled',lambda:False)
 state={'phase':'reconnecting','public_ready':{'cloudflare':False,'ngrok':True},
        'urls':{'ngrok':'https://retired.ngrok-free.dev'},
        'cloudflare_url':'https://unverified.trycloudflare.com'}
 stack.print_up_dashboard(state,reused=True,gateway_ready=True)
 out=capsys.readouterr().out
 assert 'NGROK' not in out.upper()
 assert 'retired.ngrok-free.dev' not in out
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
 initial={'manager_pid':999,'phase':'starting','mode':'quick'}
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


@pytest.mark.parametrize('mode,provider',[('quick','cloudflare'),('named','cloudflare')])
def test_single_provider_startup_does_not_wait_for_unused_tunnel(monkeypatch,mode,provider):
 state={'manager_pid':999,'phase':'ready','mode':mode,'public_ready':{provider:True}}
 monkeypatch.setattr(stack,'read_state',lambda:state)
 monkeypatch.setattr(stack,'alive',lambda pid:True)
 monkeypatch.setattr(stack,'_managed_web_owns_port',lambda state:True)
 monkeypatch.setattr(stack.time,'sleep',lambda _:pytest.fail('unused provider delayed startup'))
 assert stack.wait_for_up_status(999,seconds=2)==state


def test_provider_http_error_does_not_echo_private_body(monkeypatch,capsys):
 import io
 def denied(*args,**kwargs):
  raise urllib.error.HTTPError('https://cf.example.test',403,'Forbidden',{},
                               io.BytesIO(b'private provider error'))
 monkeypatch.setattr(stack,'fetch',denied)
 assert stack.probe_public_endpoint('https://cf.example.test')==(False,'APP_HTTP_403')
 state={'mode':'quick','public_errors':{'cloudflare':'APP_HTTP_403'}}
 stack.print_up_dashboard(state,reused=True,gateway_ready=True)
 out=capsys.readouterr().out
 assert 'APP_HTTP_403' in out
 assert 'private provider error' not in out


@pytest.mark.parametrize('verified,expected',[
 ({'cloudflare':True},'ready'),
 ({'cloudflare':False},'reconnecting'),
])
def test_provider_phase_reports_availability(verified,expected):
 assert stack.public_phase(verified)==expected


def test_publication_selects_only_verified_cloudflare():
 state={'app_url':'https://primary.example',
        'public_ready':{'cloudflare':False},
        'urls':{'cloudflare':'https://primary.example'}}
 assert stack.verified_origin(state)==''
 state['public_ready']['cloudflare']=True
 assert stack.verified_origin(state)=='https://primary.example'


@pytest.mark.parametrize('route,expected',[
 ('/','APP_TIMEOUT'),('/tma','TELEGRAM_TIMEOUT'),('/health','HEALTH_TIMEOUT'),
 ('/api/v1/tma/data','PRIVATE_API_TIMEOUT'),
])
def test_public_probe_reports_timeout_route_without_private_details(monkeypatch,route,expected):
 def fetch(url):
  if url.endswith(route) and (route!='/' or url.endswith('.test/')):
   raise urllib.error.URLError(TimeoutError('private token=never-log'))
  if url.endswith('/data'):raise urllib.error.HTTPError(url,401,'',{},None)
  if url.endswith('/health'):return 200,'{"status":"ok"}'
  return 200,'id="root" Telegram Mini App'
 monkeypatch.setattr(stack,'fetch',fetch)
 assert stack.probe_public_endpoint('https://probe.example.test')==(False,expected)

@pytest.mark.parametrize('route,payload,expected',[
 ('/','unexpected login page','APP_SHELL_INVALID'),
 ('/tma','unexpected login page','TELEGRAM_SHELL_INVALID'),
 ('/health','not-json','HEALTH_PAYLOAD_INVALID'),
 ('/health','[]','HEALTH_PAYLOAD_INVALID'),
 ('/health','{"status":"starting"}','HEALTH_NOT_READY'),
 ('/api/v1/tma/data','{"private":"data"}','PRIVATE_API_AUTH_REQUIRED'),
])
def test_public_probe_explains_failed_route_contract(monkeypatch,route,payload,expected):
 def fetch(url):
  if url=='https://probe.example.test'+route:return 200,payload
  if url.endswith('/data'):raise urllib.error.HTTPError(url,401,'',{},None)
  if url.endswith('/health'):return 200,'{"status":"ok"}'
  return 200,'id="root" Telegram Mini App'
 monkeypatch.setattr(stack,'fetch',fetch)
 assert stack.probe_public_endpoint('https://probe.example.test')==(False,expected)

def test_healthy_backend_clears_stale_recovery_warning_in_live_status(monkeypatch):
 cached={'phase':'degraded','backend_error':'EXTERNAL_SUPERVISOR_RECOVERY_PENDING'}
 monkeypatch.setattr(stack,'_managed_web_owns_port',lambda s:True)
 monkeypatch.setattr(stack,'_backend_listener_present',lambda:True)
 monkeypatch.setattr(stack,'_backend_http_ready',lambda:True)
 actual=stack.live_presentation_status(cached,True)
 assert actual['backend_ready'] is True
 assert 'backend_error' not in actual
 assert cached['backend_error']=='EXTERNAL_SUPERVISOR_RECOVERY_PENDING'

@pytest.mark.parametrize('listening,healthy',[(False,False),(True,False)])
def test_backend_recovery_warning_remains_until_health_confirmed(monkeypatch,listening,healthy):
 cached={'phase':'degraded','backend_error':'EXTERNAL_SUPERVISOR_RECOVERY_PENDING'}
 monkeypatch.setattr(stack,'_managed_web_owns_port',lambda s:True)
 monkeypatch.setattr(stack,'_backend_listener_present',lambda:listening)
 monkeypatch.setattr(stack,'_backend_http_ready',lambda:healthy)
 assert stack.live_presentation_status(cached,True)['backend_error']=='EXTERNAL_SUPERVISOR_RECOVERY_PENDING'

@pytest.mark.parametrize('failure,expected',[
 (TimeoutError('private URL token=secret'),'HEALTH_TIMEOUT'),
 (urllib.error.URLError('private URL token=secret'),'HEALTH_URLError'),
 (OSError(28,'private URL token=secret'),'HEALTH_OSError(errno=28)'),
])
def test_public_probe_transport_diagnostics_never_echo_error_text(monkeypatch,failure,expected):
 def fetch(url):
  if url.endswith('/health'):raise failure
  if url.endswith('/data'):raise urllib.error.HTTPError(url,401,'',{},None)
  return 200,'id="root" Telegram Mini App'
 monkeypatch.setattr(stack,'fetch',fetch)
 assert stack.probe_public_endpoint('https://probe.example.test')==(False,expected)

def test_public_probe_names_http_failure_route_without_echoing_page(monkeypatch):
 import io
 def fetch(url):
  if url.endswith('/health'):
   raise urllib.error.HTTPError(url,503,'private message',{},io.BytesIO(b'private URL token=secret'))
  if url.endswith('/data'):raise urllib.error.HTTPError(url,401,'',{},None)
  return 200,'id="root" Telegram Mini App'
 monkeypatch.setattr(stack,'fetch',fetch)
 assert stack.probe_public_endpoint('https://probe.example.test')==(False,'HEALTH_HTTP_503')

def test_public_probe_keeps_unrelated_backend_warning_after_recovery(monkeypatch):
 monkeypatch.setattr(stack,'_managed_web_owns_port',lambda s:True)
 monkeypatch.setattr(stack,'_backend_listener_present',lambda:True)
 monkeypatch.setattr(stack,'_backend_http_ready',lambda:True)
 cached={'phase':'degraded','backend_error':'BACKEND_OWNERSHIP_UNVERIFIED'}
 assert stack.live_presentation_status(cached,True)['backend_error']=='BACKEND_OWNERSHIP_UNVERIFIED'

def test_public_probe_reports_executor_failure_and_stays_fail_closed(monkeypatch):
 def unavailable(*a,**k):raise RuntimeError('private URL token=secret')
 monkeypatch.setattr(stack,'ThreadPoolExecutor',unavailable)
 assert stack.probe_public_endpoint('https://probe.example.test')==(False,'PUBLIC_RuntimeError')

def test_enrolled_remote_publisher_overrides_stale_service_environment(monkeypatch,tmp_path):
 """An old systemd process environment cannot defeat explicit UI enrollment."""
 (tmp_path/'backend').mkdir()
 (tmp_path/'backend'/'.env').write_text(
   'STERLING_PUBLISH_REF=origin/main\nSTERLING_PUBLISH_FETCH=1\n'
 )
 monkeypatch.setattr(stack,'ROOT',tmp_path)
 monkeypatch.setattr(stack,'STATE',tmp_path/'runtime')
 monkeypatch.setenv('STERLING_PUBLISH_REF','refs/heads/dev')
 monkeypatch.setenv('STERLING_PUBLISH_FETCH','0')
 monkeypatch.setattr(stack,'read_state',lambda:{})
 monkeypatch.setattr(stack,'alive',lambda pid:False)
 monkeypatch.setattr(stack,'_backend_listener_present',lambda:False)
 monkeypatch.setattr(stack,'_backend_http_ready',lambda:False)
 monkeypatch.setattr(stack.sys,'argv',['sterling_stack.py','status'])
 stack.main()
 assert os.environ['STERLING_PUBLISH_REF']=='origin/main'
 assert os.environ['STERLING_PUBLISH_FETCH']=='1'

def test_old_manager_identity_mismatch_never_signals_process(monkeypatch,tmp_path):
 """Stale status.json PID must not allow terminating an unrelated process."""
 fake=tmp_path/'unrelated-process'
 fake.mkdir()
 (fake/'cmdline').write_bytes(b'/usr/bin/python\0/srv/unrelated.py\0run\0')
 original_path=Path
 monkeypatch.setattr(stack,'Path',lambda value:fake if str(value)=='/proc/456789' else original_path(value))
 monkeypatch.setattr(stack,'alive',lambda pid:True)
 def fail_kill(*args):
  raise AssertionError('Must not terminate unrelated PID')
 monkeypatch.setattr(stack.os,'kill',fail_kill)
 with pytest.raises(RuntimeError,match='identity mismatch'):
  stack._stop_old_managed_presentation(456789)

def test_up_replaces_stale_dev_publisher_after_main_enrollment(monkeypatch,tmp_path):
 """A healthy old gateway cannot silently defeat operator-enrolled UI updates."""
 from types import SimpleNamespace
 root=tmp_path/'repo'
 (root/'backend').mkdir(parents=True)
 (root/'backend'/'.env').write_text(
   'STERLING_PUBLISH_REF=origin/main\nSTERLING_PUBLISH_FETCH=1\n')
 monkeypatch.setattr(stack,'ROOT',root)
 monkeypatch.setattr(stack,'STATE',tmp_path/'stack-state')
 old={'manager_pid':81818,'manager_protocol':4,'mode':'quick',
      'phase':'ready','web_port':5180,'dev_url':'http://127.0.0.1:5174',
      'release_ref':'refs/heads/dev','release_auto_fetch':False}
 monkeypatch.setattr(stack,'read_state',lambda:old)
 monkeypatch.setattr(stack,'alive',lambda _:True)
 monkeypatch.setattr(stack,'_backend_listener_present',lambda:True)
 monkeypatch.setattr(stack,'_managed_web_owns_port',lambda _:True)
 stopped=[]
 monkeypatch.setattr(stack,'_stop_old_managed_presentation',lambda pid:stopped.append(pid))
 monkeypatch.setattr(stack,'wait_for_up_status',lambda *_args,**_kwargs:{})
 launched=[]
 def spawn(args,**kwargs):
  launched.append(args)
  return SimpleNamespace(pid=19191)
 monkeypatch.setattr(stack.subprocess,'Popen',spawn)
 monkeypatch.setattr(stack.sys,'argv',['sterling_stack.py','up'])
 stack.main()
 assert stopped==[81818]
 assert len(launched)==1 and 'run' in launched[0]
 assert 'origin/main'==os.environ['STERLING_PUBLISH_REF']
 assert os.environ['STERLING_PUBLISH_FETCH']=='1'


def test_up_reuses_healthy_supervisor_with_matching_enrolled_publisher(monkeypatch,tmp_path):
 """A genuinely synchronized manager should not rotate its Cloudflare hostname."""
 root=tmp_path/'repo'
 (root/'backend').mkdir(parents=True)
 (root/'backend'/'.env').write_text(
   'STERLING_PUBLISH_REF=origin/main\nSTERLING_PUBLISH_FETCH=1\n')
 monkeypatch.setattr(stack,'ROOT',root)
 monkeypatch.setattr(stack,'STATE',tmp_path/'stack-state')
 current={'manager_pid':92929,'manager_protocol':4,'mode':'quick',
          'phase':'ready','web_port':5180,'dev_url':'http://127.0.0.1:5174',
          'release_ref':'origin/main','release_auto_fetch':True,
          'public_ready':{'cloudflare':True},
          'urls':{'cloudflare':'https://working.trycloudflare.com'}}
 monkeypatch.setattr(stack,'read_state',lambda:current)
 monkeypatch.setattr(stack,'alive',lambda _:True)
 monkeypatch.setattr(stack,'_backend_listener_present',lambda:True)
 monkeypatch.setattr(stack,'_managed_web_owns_port',lambda _:True)
 monkeypatch.setattr(stack,'wait_for_up_status',lambda *_args,**_kwargs:current)
 def no_restart(*_args,**_kwargs):
  raise AssertionError('A matching supervisor must be reused')
 monkeypatch.setattr(stack,'_stop_old_managed_presentation',no_restart)
 monkeypatch.setattr(stack.subprocess,'Popen',no_restart)
 monkeypatch.setattr(stack.sys,'argv',['sterling_stack.py','up'])
 stack.main()

def test_old_manager_missing_publisher_status_is_not_reused(monkeypatch,tmp_path):
 """Legacy supervisor without release fields is not proof of enrollment."""
 from types import SimpleNamespace
 root=tmp_path/'repo'
 (root/'backend').mkdir(parents=True)
 (root/'backend'/'.env').write_text(
   'STERLING_PUBLISH_REF=origin/main\nSTERLING_PUBLISH_FETCH=1\n')
 monkeypatch.setattr(stack,'ROOT',root)
 monkeypatch.setattr(stack,'STATE',tmp_path/'status')
 old={'manager_pid':77111,'manager_protocol':4,'mode':'quick',
      'phase':'ready','web_port':5180,'dev_url':'http://127.0.0.1:5174'}
 monkeypatch.setattr(stack,'read_state',lambda:old)
 monkeypatch.setattr(stack,'alive',lambda pid:True)
 monkeypatch.setattr(stack,'_backend_listener_present',lambda:True)
 monkeypatch.setattr(stack,'_managed_web_owns_port',lambda state:True)
 stopped=[]
 monkeypatch.setattr(stack,'_stop_old_managed_presentation',lambda pid:stopped.append(pid))
 monkeypatch.setattr(stack,'wait_for_up_status',lambda *args,**kwargs:{})
 started=[]
 def spawn(args,**kwargs):
  started.append(args)
  return SimpleNamespace(pid=88222)
 monkeypatch.setattr(stack.subprocess,'Popen',spawn)
 monkeypatch.setattr(stack.sys,'argv',['sterling_stack.py','up'])
 stack.main()
 assert stopped==[77111]
 assert len(started)==1
 assert stack.publisher_config_loaded(old) is False


def test_status_distinguishes_loaded_publisher_from_activation_needed(monkeypatch):
 monkeypatch.setenv('STERLING_PUBLISH_REF','origin/main')
 monkeypatch.setenv('STERLING_PUBLISH_FETCH','1')
 monkeypatch.setattr(stack,'_managed_web_owns_port',lambda state:True)
 monkeypatch.setattr(stack,'_backend_listener_present',lambda:True)
 monkeypatch.setattr(stack,'_backend_http_ready',lambda:True)
 old={'manager_protocol':4,'release_ref':'refs/heads/dev','release_auto_fetch':False,
      'public_ready':{'cloudflare':True}}
 state=stack.live_presentation_status(old,True)
 assert state['publisher_config_loaded'] is False
 assert state['publisher_activation_required'] is True
 assert stack.live_presentation_status({},False)['publisher_activation_required'] is False
 new={**old,'release_ref':'origin/main','release_auto_fetch':True}
 state=stack.live_presentation_status(new,True)
 assert state['publisher_config_loaded'] is True
 assert state['publisher_activation_required'] is False

def test_frontend_publication_blocks_before_touching_staging_when_disk_is_low(monkeypatch,tmp_path):
 from types import SimpleNamespace
 monkeypatch.setattr(stack,'STATE',tmp_path)
 publisher=stack.CommittedFrontend('main')
 monkeypatch.setattr(publisher,'resolve_commit',lambda:'b'*40)
 monkeypatch.setattr(publisher,'current',lambda:'')
 monkeypatch.setattr(stack.shutil,'disk_usage',lambda path:SimpleNamespace(
     free=stack.MIN_FRONTEND_RELEASE_FREE_BYTES-1))
 def no_stage(*args,**kwargs):
  raise AssertionError('Staging must not be modified on low disk space')
 monkeypatch.setattr(publisher,'_extract',no_stage)
 with pytest.raises(stack.ReleaseStorageBlocked):
  publisher.publish()
 assert not publisher.frontend.exists()
 assert not publisher.pointer.exists()
 assert not publisher.manifest.exists()


def test_frontend_publication_fails_closed_when_disk_probe_is_unavailable(monkeypatch,tmp_path):
 monkeypatch.setattr(stack,'STATE',tmp_path)
 publisher=stack.CommittedFrontend('main')
 monkeypatch.setattr(publisher,'resolve_commit',lambda:'b'*40)
 monkeypatch.setattr(publisher,'current',lambda:'')
 def unavailable(*args):
  raise OSError('disk metrics unavailable')
 monkeypatch.setattr(stack.shutil,'disk_usage',unavailable)
 with pytest.raises(stack.ReleaseStorageBlocked):
  publisher.publish()
 assert not publisher.pointer.exists()


def test_existing_published_release_remains_served_when_disk_is_low(monkeypatch,tmp_path):
 from types import SimpleNamespace
 monkeypatch.setattr(stack,'STATE',tmp_path)
 publisher=stack.CommittedFrontend('main')
 release='b'*40
 monkeypatch.setattr(publisher,'resolve_commit',lambda:release)
 monkeypatch.setattr(publisher,'current',lambda:release)
 monkeypatch.setattr(stack.shutil,'disk_usage',lambda _:SimpleNamespace(free=0))
 assert publisher.publish()==(release,False)


def test_frontend_release_requires_two_gibibytes_free_before_build(monkeypatch,tmp_path):
 from types import SimpleNamespace
 monkeypatch.setattr(stack,'STATE',tmp_path)
 publisher=stack.CommittedFrontend()
 seen=[]
 monkeypatch.setattr(stack.shutil,'disk_usage',lambda path:
     (seen.append(path),SimpleNamespace(free=stack.MIN_FRONTEND_RELEASE_FREE_BYTES))[1])
 publisher._require_release_headroom()
 assert seen==[tmp_path]

def test_frontend_dependency_disk_drop_keeps_prior_published_assets(monkeypatch,tmp_path):
 from types import SimpleNamespace
 monkeypatch.setattr(stack,'STATE',tmp_path)
 publisher=stack.CommittedFrontend('main')
 old='a'*40
 next_sha='b'*40
 previous=publisher.releases/old
 previous.mkdir(parents=True)
 (previous/'index.html').write_text('LAST WORKING RELEASE')
 publisher.pointer.symlink_to(previous,target_is_directory=True)
 publisher.manifest.write_text(json.dumps({'sha':old,'ref':'main'}))
 monkeypatch.setattr(publisher,'resolve_commit',lambda:next_sha)
 free={'value':stack.MIN_FRONTEND_RELEASE_FREE_BYTES + 1024}
 monkeypatch.setattr(stack.shutil,'disk_usage',lambda path:
     SimpleNamespace(free=free['value']))
 def extract(sha):
  publisher.frontend.mkdir(parents=True)
  (publisher.frontend/'package-lock.json').write_text('{}')
 monkeypatch.setattr(publisher,'_extract',extract)
 def install():
  free['value']=stack.MIN_FRONTEND_RELEASE_FREE_BYTES-1
 monkeypatch.setattr(publisher,'_dependencies',install)
 def no_build(*args,**kwargs):
  raise AssertionError('npm build must not run after disk reserve is exhausted')
 monkeypatch.setattr(stack.subprocess,'run',no_build)
 with pytest.raises(stack.ReleaseStorageBlocked):
  publisher.publish()
 assert publisher.current()==old
 assert (publisher.pointer/'index.html').read_text()=='LAST WORKING RELEASE'
 assert not (publisher.releases/next_sha).exists()

def test_release_destination_never_deletes_current_published_directory(tmp_path,monkeypatch):
 monkeypatch.setattr(stack,'STATE',tmp_path)
 publisher=stack.CommittedFrontend()
 sha='a'*40
 destination=publisher.releases/sha
 destination.mkdir(parents=True)
 (destination/'partial.txt').write_text('KEEP')
 publisher.pointer.symlink_to(destination,target_is_directory=True)
 with pytest.raises(RuntimeError,match='active or untrusted'):
  publisher._replace_incomplete_unpublished_release(sha)
 assert (destination/'partial.txt').read_text()=='KEEP'
 assert publisher.pointer.resolve()==destination.resolve()


def test_release_destination_rejects_symlink_and_complete_immutable_release(tmp_path,monkeypatch):
 monkeypatch.setattr(stack,'STATE',tmp_path)
 publisher=stack.CommittedFrontend()
 destination=publisher.releases/('b'*40)
 destination.parent.mkdir(parents=True)
 other=tmp_path/'external'
 other.mkdir()
 (other/'do-not-delete.txt').write_text('SAFE')
 destination.symlink_to(other,target_is_directory=True)
 with pytest.raises(RuntimeError,match='symlink'):
  publisher._replace_incomplete_unpublished_release('b'*40)
 assert (other/'do-not-delete.txt').read_text()=='SAFE'
 destination.unlink()
 destination.mkdir()
 (destination/'index.html').write_text('GOOD RELEASE')
 with pytest.raises(RuntimeError,match='cannot be overwritten'):
  publisher._replace_incomplete_unpublished_release('b'*40)
 assert (destination/'index.html').read_text()=='GOOD RELEASE'

def test_runtime_bootstrap_refuses_low_disk_before_making_virtualenv(monkeypatch,tmp_path):
 from types import SimpleNamespace
 root=tmp_path/'repo'
 root.mkdir()
 state=tmp_path/'stack'
 state.mkdir()
 monkeypatch.setattr(stack,'ROOT',root)
 monkeypatch.setattr(stack,'STATE',state)
 monkeypatch.setattr(stack.shutil,'disk_usage',lambda path:SimpleNamespace(
     free=stack.MIN_FRONTEND_RELEASE_FREE_BYTES-1))
 def forbid(*args,**kwargs):
  raise AssertionError('No venv or pip operations permitted on low disk')
 monkeypatch.setattr(stack.subprocess,'run',forbid)
 with pytest.raises(stack.ReleaseStorageBlocked):
  stack.ensure_runtime(False)
 assert not (root/'backend/.venv').exists()


def test_runtime_bootstrap_refuses_low_disk_before_npm_ci(monkeypatch,tmp_path):
 from types import SimpleNamespace
 root=tmp_path/'repo'
 state=tmp_path/'stack'
 (root/'backend/.venv/bin').mkdir(parents=True)
 (root/'backend/.venv/bin/python').write_text('')
 (root/'frontend').mkdir()
 state.mkdir()
 monkeypatch.setattr(stack,'ROOT',root)
 monkeypatch.setattr(stack,'STATE',state)
 monkeypatch.setattr(stack.shutil,'which',lambda name:'/usr/bin/'+name)
 monkeypatch.setattr(stack.shutil,'disk_usage',lambda path:SimpleNamespace(
     free=stack.MIN_FRONTEND_RELEASE_FREE_BYTES-1))
 def forbid(*args,**kwargs):
  raise AssertionError('npm ci must not run on low disk')
 monkeypatch.setattr(stack.subprocess,'run',forbid)
 with pytest.raises(stack.ReleaseStorageBlocked):
  stack.ensure_runtime(False)
 assert not (root/'frontend/node_modules').exists()


def test_existing_runtime_needs_no_install_before_publisher_headroom_check(monkeypatch,tmp_path):
 from types import SimpleNamespace
 root=tmp_path/'repo'
 (root/'backend/.venv/bin').mkdir(parents=True)
 python=root/'backend/.venv/bin/python'
 python.write_text('')
 (root/'frontend/node_modules').mkdir(parents=True)
 monkeypatch.setattr(stack,'ROOT',root)
 monkeypatch.setattr(stack.shutil,'which',lambda name:'/usr/bin/'+name)
 def forbid(*args,**kwargs):
  raise AssertionError('Ready dependencies should not be reinstalled')
 monkeypatch.setattr(stack.shutil,'disk_usage',forbid)
 monkeypatch.setattr(stack.subprocess,'run',forbid)
 assert stack.ensure_runtime(False)==python


def test_remote_main_fetch_refuses_low_disk_and_retains_release(monkeypatch,tmp_path):
 from types import SimpleNamespace
 monkeypatch.setattr(stack,'STATE',tmp_path)
 args=SimpleNamespace(provider='cloudflare',hostname='',port=5180,dev=False,dev_port=5174)
 manager=stack.Stack(args)
 monkeypatch.setattr(stack.shutil,'disk_usage',lambda path:SimpleNamespace(
     free=stack.MIN_FRONTEND_RELEASE_FREE_BYTES-1))
 def forbid(*args,**kwargs):
  raise AssertionError('Git fetch must not consume final disk reserve')
 monkeypatch.setattr(stack.subprocess,'run',forbid)
 monkeypatch.setattr(manager.publisher,'publish',forbid)
 manager.refresh_committed_release(True)
 assert manager.release_results.get_nowait()==('failed','ReleaseStorageBlocked',False)
 assert manager.backend_owned is False
 assert manager.children=={}


def test_disk_probe_failure_blocks_runtime_bootstrap(monkeypatch,tmp_path):
 root=tmp_path/'repo'
 root.mkdir()
 state=tmp_path/'stack'
 state.mkdir()
 monkeypatch.setattr(stack,'ROOT',root)
 monkeypatch.setattr(stack,'STATE',state)
 def no_disk(*args,**kwargs):
  raise OSError('private storage information')
 monkeypatch.setattr(stack.shutil,'disk_usage',no_disk)
 with pytest.raises(stack.ReleaseStorageBlocked):
  stack.ensure_runtime(False)
 assert not (root/'backend/.venv').exists()


def test_up_retains_old_presentation_manager_when_bootstrap_needs_low_disk(monkeypatch,tmp_path):
 from types import SimpleNamespace
 root=tmp_path/'repo'
 root.mkdir()
 monkeypatch.setattr(stack,'ROOT',root)
 monkeypatch.setattr(stack,'STATE',tmp_path/'status')
 monkeypatch.setenv('STERLING_PUBLISH_REF','origin/main')
 monkeypatch.setenv('STERLING_PUBLISH_FETCH','1')
 old={'manager_pid':554433,'manager_protocol':4,'mode':'quick','phase':'ready',
      'web_port':5180,'dev_url':'http://127.0.0.1:5174',
      'release_ref':'refs/heads/dev','release_auto_fetch':False}
 monkeypatch.setattr(stack,'read_state',lambda:old)
 monkeypatch.setattr(stack,'alive',lambda pid:True)
 monkeypatch.setattr(stack,'_managed_web_owns_port',lambda state:True)
 monkeypatch.setattr(stack,'_backend_listener_present',lambda:True)
 monkeypatch.setattr(stack.shutil,'disk_usage',lambda path:SimpleNamespace(
     free=stack.MIN_FRONTEND_RELEASE_FREE_BYTES-1))
 def forbidden(*args,**kwargs):
  raise AssertionError('The running presentation manager must stay online')
 monkeypatch.setattr(stack,'_stop_old_managed_presentation',forbidden)
 monkeypatch.setattr(stack.subprocess,'Popen',forbidden)
 monkeypatch.setattr(stack.sys,'argv',['sterling_stack.py','up'])
 with pytest.raises(stack.ReleaseStorageBlocked):
  stack.main()


def test_publisher_current_uses_served_release_when_manifest_is_stale(tmp_path,monkeypatch):
 monkeypatch.setattr(stack,'STATE',tmp_path)
 publisher=stack.CommittedFrontend()
 old='a'*40
 new='b'*40
 for sha in (old,new):
  release=publisher.releases/sha
  release.mkdir(parents=True)
  (release/'index.html').write_text('RELEASE '+sha)
 publisher.pointer.symlink_to(publisher.releases/new,target_is_directory=True)
 publisher.manifest.write_text(json.dumps({'sha':old,'ref':'origin/main'}))
 assert publisher.current()==new
 publisher.manifest.write_text('{not json')
 assert publisher.current()==new
 publisher.manifest.unlink()
 assert publisher.current()==new


def test_publisher_current_rejects_untrusted_or_incomplete_symlink_target(tmp_path,monkeypatch):
 monkeypatch.setattr(stack,'STATE',tmp_path)
 publisher=stack.CommittedFrontend()
 outside=tmp_path/'outside'/('a'*40)
 outside.mkdir(parents=True)
 (outside/'index.html').write_text('NOT MANAGED')
 publisher.pointer.symlink_to(outside,target_is_directory=True)
 publisher.manifest.write_text(json.dumps({'sha':'a'*40,'ref':'origin/main'}))
 assert publisher.current()==''
 publisher.pointer.unlink()
 incomplete=publisher.releases/('b'*40)
 incomplete.mkdir(parents=True)
 publisher.pointer.symlink_to(incomplete,target_is_directory=True)
 assert publisher.current()==''
 (incomplete/'index.html').write_text('MANAGED')
 assert publisher.current()=='b'*40
