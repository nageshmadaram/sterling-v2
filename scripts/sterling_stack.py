#!/usr/bin/env python3
"""Start, supervise and publish the Sterling web stack without changing trading controls."""
from __future__ import annotations
import argparse
import fcntl
import hashlib
import json
import os
import platform
import queue
import re
import shutil
import socket
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT/'.runtime/stack'
PUBLIC_RE = re.compile(r'https://[a-z0-9-]+\.trycloudflare\.com\b')
NGROK_RE = re.compile(r'https://[a-z0-9.-]+\.ngrok(?:-free)?\.(?:app|dev|io)\b',re.I)
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def fetch(url, timeout=8):
    request=urllib.request.Request(url,headers={'User-Agent':'Sterling-Launcher/1','ngrok-skip-browser-warning':'1'})
    with OPENER.open(request,timeout=timeout) as response:
        return response.status,response.read(2_000_000).decode('utf-8',errors='replace')


def public_ready(origin):
    """Publish only a reachable app + desk whose private data still requires authentication."""
    try:
        _,index=fetch(origin+'/')
        _,desk=fetch(origin+'/tma')
        if 'id="root"' not in index or 'Telegram Mini App' not in desk:return False
        try:fetch(origin+'/api/v1/tma/data')
        except urllib.error.HTTPError as error:return error.code==401
        return False
    except Exception:return False


def tunnel_unavailable(origin, port):
    """An application failure is not a reason to replace a healthy hostname."""
    try:
        _,local=fetch(f'http://127.0.0.1:{port}/__sterling_web_health')
        if not json.loads(local).get('ready'):return False
    except Exception:return False
    try:
        _,remote=fetch(origin+'/__sterling_web_health')
        return json.loads(remote).get('service') != 'sterling-web'
    except Exception:return True


def alive(pid):
    try:
        pid=int(pid)
        if pid <= 1:return False
        cmd=Path(f'/proc/{pid}/cmdline')
        if cmd.exists() and 'sterling_stack.py' not in cmd.read_bytes().decode():return False
        os.kill(pid,0);return True
    except (ProcessLookupError,ValueError,TypeError):return False
    except PermissionError:return True


def read_state():
    try:return json.loads((STATE/'status.json').read_text())
    except (OSError,ValueError):return {}


def save_state(data):
    temp=STATE/'status.tmp';temp.write_text(json.dumps(data,indent=2));os.chmod(temp,0o600);temp.replace(STATE/'status.json')


def clean_env():
    return {k:v for k,v in os.environ.items() if not k.startswith('BASH_FUNC_')}


def safe_hostname(value, provider):
    """Validate externally configured HTTPS domains without allowing injection."""
    if not value:
        return ''
    parsed=urlsplit(value)
    if (parsed.scheme!='https' or not parsed.hostname or parsed.path not in {'','/'}
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.port is not None):
        raise ValueError(f'{provider} hostname must be a bare HTTPS origin')
    if parsed.hostname in {'localhost','127.0.0.1','::1'}:
        raise ValueError(f'{provider} hostname must be public')
    return f'https://{parsed.netloc}'.rstrip('/')


def ngrok_url_from_log(line):
    """Accept only a tunnel URL in a trusted ngrok started-tunnel log event."""
    try:
        data=json.loads(line)
        if not isinstance(data,dict):
            return ''
        detail=' '.join(str(data.get(key,'')) for key in ('msg','message','event')).lower()
        if 'started tunnel' not in detail and 'session started' not in detail:
            return ''
        candidate=data.get('url') or data.get('public_url') or ''
        if not isinstance(candidate,str):
            return ''
        origin=safe_hostname(candidate,'ngrok')
        return origin if NGROK_RE.fullmatch(origin) else ''
    except (ValueError,TypeError):
        return ''


def cloudflared():
    configured=os.environ.get('STERLING_CLOUDFLARED_BIN')
    if configured:
        binary=Path(configured).resolve()
        if not binary.is_file():raise RuntimeError('Configured cloudflared binary missing')
        return str(binary)
    installed=shutil.which('cloudflared')
    if installed:return installed
    binary=ROOT/'.tools/cloudflared'
    if binary.exists():return str(binary)
    arch={'x86_64':'amd64','aarch64':'arm64'}.get(platform.machine())
    if sys.platform!='linux' or not arch:raise RuntimeError('Install cloudflared for this platform first')
    # Use the official release and its published SHA-256 digest, never an unsigned temporary binary.
    _,raw=fetch('https://api.github.com/repos/cloudflare/cloudflared/releases/latest',20)
    assets=json.loads(raw)['assets']
    asset=next(a for a in assets if a['name']==f'cloudflared-linux-{arch}')
    digest=asset.get('digest','')
    if not digest.startswith('sha256:'):raise RuntimeError('Official release has no SHA-256 digest')
    binary.parent.mkdir(exist_ok=True);tmp=binary.with_suffix('.download')
    hasher=hashlib.sha256()
    try:
        with OPENER.open(asset['browser_download_url'],timeout=60) as response, tmp.open('wb') as target:
            while chunk:=response.read(1024*1024):target.write(chunk);hasher.update(chunk)
        if hasher.hexdigest()!=digest.split(':',1)[1]:raise RuntimeError('Cloudflared checksum mismatch')
        os.chmod(tmp,0o700);tmp.replace(binary)
    finally:tmp.unlink(missing_ok=True)
    return str(binary)


def ensure_runtime(skip_build):
    python=ROOT/'backend/.venv/bin/python'
    if not python.exists():
        subprocess.run([sys.executable,'-m','venv',str(ROOT/'backend/.venv')],check=True)
        subprocess.run([str(python),'-m','pip','install','-r',str(ROOT/'backend/requirements.txt')],check=True,stdout=subprocess.DEVNULL)
    if not shutil.which('node') or not shutil.which('npm'):raise RuntimeError('Node.js and npm are required')
    if not (ROOT/'frontend/node_modules').exists():
        subprocess.run(['npm','ci'],cwd=ROOT/'frontend',check=True,stdout=subprocess.DEVNULL)
    if not skip_build or not (ROOT/'frontend/dist/index.html').exists():
        with (STATE/'build.log').open('w') as log:
            subprocess.run(['npm','run','build'],cwd=ROOT/'frontend',env={**clean_env(),'VITE_API_BASE_URL':''},check=True,stdout=log,stderr=log)
    return python


class Stack:
    def __init__(self,args):
        self.args=args;self.children={};self.logs=[];self.urls=queue.Queue();self.stopping=False;self.rotate=False
        provider=getattr(args,'provider','cloudflare')
        self.status={'manager_pid':os.getpid(),'phase':'starting','app_url':'','mini_app_url':'',
                     'mode':'both' if provider=='both' else 'ngrok' if provider=='ngrok' else ('named' if args.hostname else 'quick'),
                     'web_port':args.port,'local_url':f'http://127.0.0.1:{args.port}',
                     'dev_url':f'http://127.0.0.1:{args.dev_port}' if getattr(args,'dev',False) else '',
                     'cloudflare_url':'','ngrok_url':'','urls':{},'public_ready':{}}
        self.backend_owned=False;self.last_publish=0;self.failures=0;self.published=False
        self.last_public_signature=None;self.last_ngrok_retry=0;self.last_dev_retry=0
        self.ngrok_urls=queue.Queue();self.ngrok_binary=None

    def spawn(self,name,argv,**kwargs):
        log=(STATE/f'{name}.log').open('a');os.chmod(STATE/f'{name}.log',0o600);self.logs.append(log)
        proc=subprocess.Popen(argv,cwd=ROOT,env=clean_env(),stdout=log,stderr=log,start_new_session=True,**kwargs)
        self.children[name]=proc;return proc

    def stop_child(self,name):
        proc=self.children.pop(name,None)
        if proc is None or proc.poll() is not None:return
        proc.terminate()
        try:proc.wait(15)
        except subprocess.TimeoutExpired:
            if name=='backend':return # Never force-kill broker protection during launcher shutdown.
            proc.kill();proc.wait(3)

    def launch_tunnel(self):
        self.stop_child('cloudflare');self.failures=0;self.published=False;self.status['phase']='connecting'
        if not self.args.hostname:self.status.update(app_url='',mini_app_url='')
        command=[self.binary,'tunnel','--no-autoupdate']
        env=clean_env()
        if getattr(self.args,'provider','cloudflare') == 'ngrok':
            command=[self.binary,'http',f'http://127.0.0.1:{self.args.port}',
                     '--url',self.args.hostname,'--inspect=false','--log','stdout','--log-format','json']
        elif self.args.hostname:
            # Named tunnels keep the same hostname. Token is carried only in process environment.
            from_value=env.get('STERLING_CLOUDFLARE_TUNNEL_TOKEN','')
            if from_value:env['TUNNEL_TOKEN']=from_value
            command+=['run']
        else:command+=['--url',f'http://127.0.0.1:{self.args.port}']
        proc=subprocess.Popen(command,cwd=ROOT,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,start_new_session=True)
        self.children['cloudflare']=proc
        def collect():
            with (STATE/'cloudflare.log').open('a') as log:
                os.chmod(STATE/'cloudflare.log',0o600)
                for line in proc.stdout:
                    # Store diagnostic events, never raw command/config/token output.
                    found=PUBLIC_RE.search(line)
                    if found:self.urls.put((proc.pid,found.group(0)));log.write(found.group(0)+'\n');log.flush()
                    elif 'Registered tunnel connection' in line:log.write('Tunnel connection registered\n');log.flush()
                    elif 'ERR' in line:log.write('Tunnel transport error\n');log.flush()
        threading.Thread(target=collect,daemon=True).start()
        if self.args.hostname:
            self.status['app_url']=self.args.hostname.rstrip('/')
            key='ngrok_url' if self.args.provider=='ngrok' else 'cloudflare_url'
            self.status[key]=self.status['app_url']
        elif self.args.provider=='ngrok':
            self.status['ngrok_url']=''
        else:
            self.status['cloudflare_url']=''
        self.status['tunnel_started_at']=time.time()

    def launch_ngrok(self):
        """Independent optional tunnel to the SAME local gateway (never another API)."""
        self.stop_child('ngrok')
        # Never show a prior generated tunnel hostname while its process
        # has been stopped for restart or failed to authenticate.
        self.status['ngrok_url']=''
        self.status.setdefault('public_ready',{})['ngrok']=False
        if not self.ngrok_binary:
            self.status['ngrok_error']='NGROK_BINARY_NOT_INSTALLED'
            return
        hostname=getattr(self.args,'ngrok_hostname','')
        command=[self.ngrok_binary,'http',f'http://127.0.0.1:{self.args.port}']
        if hostname:command+=['--url',hostname]
        command+=['--inspect=false','--log','stdout','--log-format','json']
        process=subprocess.Popen(command,cwd=ROOT,env=clean_env(),stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT,text=True,start_new_session=True)
        self.children['ngrok']=process
        if hostname:self.status['ngrok_url']=hostname
        self.status['ngrok_error']=''
        self.status['ngrok_started_at']=time.time()
        def collect():
            with (STATE/'ngrok.log').open('a') as log:
                os.chmod(STATE/'ngrok.log',0o600)
                for line in process.stdout:
                    origin=ngrok_url_from_log(line)
                    if origin:
                        self.ngrok_urls.put((process.pid,origin))
                        log.write('Tunnel URL discovered\\n')
                        log.flush()
                    elif 'ERR' in line or '"lvl":"eror"' in line:
                        # ngrok logs may include bearer tokens / account data.
                        log.write('Tunnel transport error\\n')
                        log.flush()
        threading.Thread(target=collect,daemon=True).start()

    def start_dev(self):
        """Optional Vite hot-reload UI, bound to loopback, same single backend."""
        self.stop_child('dev')
        env={**clean_env(),'PORT':str(self.args.dev_port),
             'API_TARGET':'http://127.0.0.1:8000'}
        log=(STATE/'dev.log').open('a');os.chmod(STATE/'dev.log',0o600)
        self.logs.append(log)
        self.children['dev']=subprocess.Popen([
            'npm','run','dev','--','--host','127.0.0.1',
            '--port',str(self.args.dev_port),'--strictPort',
        ],cwd=ROOT/'frontend',env=env,stdout=log,stderr=log,start_new_session=True)

    def publish(self):
        origin=self.status['app_url']
        secondary=self.status.get('ngrok_url','') if self.args.provider=='both' and self.status['public_ready'].get('ngrok') else ''
        mode='both' if self.args.provider=='both' else self.status['mode']
        result=subprocess.run([str(self.python),str(ROOT/'scripts/publish_public_access.py'),origin,mode,secondary],
                              cwd=ROOT/'backend',env=clean_env(),capture_output=True,text=True,timeout=40)
        try:data=json.loads(result.stdout.strip().splitlines()[-1])
        except (ValueError,IndexError):data={}
        if result.returncode or not data.get('published'):return False
        self.status.update(phase='ready',mini_app_url=origin+'/tma',callback_url=origin+'/api/v1/kite/callback',
                           last_verified_at=time.time(),telegram_menu_updated=data.get('telegram_menu_updated',False))
        self.last_public_signature=(origin,secondary)
        self.published=True;self.last_publish=time.monotonic()
        print(f"App (local): {self.status['local_url']}\nCloudflare/primary: {origin}"
              + (f"\nngrok: {secondary}" if secondary else "")
              + f"\nTelegram desk: {origin}/tma",flush=True)
        return True

    def run(self):
        self.python=ensure_runtime(self.args.skip_build)
        self.binary=(shutil.which('ngrok') if self.args.provider=='ngrok'
                     else cloudflared())
        if not self.binary:
            raise RuntimeError('Primary tunnel binary is unavailable')
        if self.args.provider=='both':
            self.ngrok_binary=shutil.which('ngrok')
            if not self.ngrok_binary:
                self.status['ngrok_error']='NGROK_BINARY_NOT_INSTALLED'
        # Preserve any already-running backend; never start two trading engines.
        try:fetch('http://127.0.0.1:8000/docs',2)
        except Exception:
            with socket.socket() as probe:
                if probe.connect_ex(('127.0.0.1',8000)) != 0:self.start_backend()
        deadline=time.monotonic()+120
        while time.monotonic()<deadline:
            try:fetch('http://127.0.0.1:8000/docs',2);break
            except Exception:time.sleep(1)
        else:raise RuntimeError('Backend did not become ready')
        self.start_web()
        if getattr(self.args,'dev',False):self.start_dev()
        self.launch_tunnel()
        if self.args.provider=='both' and self.ngrok_binary:
            self.launch_ngrok()
        next_probe=0;retry_at=0
        while not self.stopping:
            if self.backend_owned and self.children['backend'].poll() is not None:self.start_backend()
            if self.children['web'].poll() is not None:self.start_web()
            now=time.monotonic()
            if getattr(self.args,'dev',False) and self.children.get('dev') and self.children['dev'].poll() is not None and now>=self.last_dev_retry:
                self.status['dev_error']='VITE_PROCESS_EXITED_CHECK_DEV_LOG'
                self.last_dev_retry=now+45
                self.start_dev()
            if self.rotate or self.children['cloudflare'].poll() is not None:
                self.rotate=False
                if now>=retry_at:self.launch_tunnel();retry_at=now+15
            if self.args.provider=='both' and self.ngrok_binary:
                child=self.children.get('ngrok')
                if (not child or child.poll() is not None) and now>=self.last_ngrok_retry:
                    self.last_ngrok_retry=now+60
                    self.status['ngrok_error']='NGROK_RESTARTING'
                    self.launch_ngrok()
            while not self.urls.empty():
                pid,url=self.urls.get()
                if pid==self.children['cloudflare'].pid:
                    self.status['app_url']=url
                    key='ngrok_url' if self.args.provider=='ngrok' else 'cloudflare_url'
                    self.status[key]=url
            while not self.ngrok_urls.empty():
                pid,url=self.ngrok_urls.get()
                child=self.children.get('ngrok')
                if child and pid==child.pid:self.status['ngrok_url']=url
            if now>=next_probe:
                next_probe=now+15
                primary=self.status.get('app_url','')
                primary_ready=bool(primary and public_ready(primary))
                secondary=self.status.get('ngrok_url','') if self.args.provider=='both' else ''
                ngrok_child=self.children.get('ngrok')
                ngrok_ready=bool(secondary and ngrok_child and ngrok_child.poll() is None and public_ready(secondary))
                primary_provider='ngrok' if self.args.provider=='ngrok' else 'cloudflare'
                self.status['public_ready']={primary_provider:primary_ready}
                if self.args.provider=='both':self.status['public_ready']['ngrok']=ngrok_ready
                self.status['urls']={
                    **({primary_provider:primary} if primary_ready else {}),
                    **({'ngrok':secondary} if ngrok_ready else {}),
                }
                if primary_ready:
                    self.failures=0
                    self.status['last_verified_at']=time.time()
                    signature=(primary,secondary if ngrok_ready else '')
                    if (signature!=self.last_public_signature or
                            (not self.status.get('telegram_menu_updated') and now-self.last_publish>60)):
                        self.publish()
                else:
                    self.status['phase']='reconnecting'
                    # Do not rotate on backend failure. Only replace an
                    # unavailable public transport after repeated probes.
                    self.failures=(self.failures+1
                        if primary and tunnel_unavailable(primary,self.args.port) else 0)
                    if self.failures>=4 and time.time()-self.status.get('tunnel_started_at',time.time())>90:
                        self.launch_tunnel()
            if not self.status['app_url'] and time.time()-self.status['tunnel_started_at']>120:
                self.launch_tunnel()
            if (self.args.rotate_hours and
                    time.time()-self.status['tunnel_started_at']>=self.args.rotate_hours*3600):
                self.rotate=True
            self.status['children']={name:proc.pid for name,proc in self.children.items()}
            save_state(self.status)
            time.sleep(1)
        self.stop_child('ngrok')
        self.stop_child('dev')
        self.stop_child('cloudflare')
        self.stop_child('web')
        self.status.update(phase='stopped')
        save_state(self.status)


    def start_backend(self):
        log=(STATE/'backend.log').open('a');self.logs.append(log);os.chmod(STATE/'backend.log',0o600)
        self.children['backend']=subprocess.Popen([str(self.python),'-m','uvicorn','main:app','--host','127.0.0.1','--port','8000','--workers','1','--no-access-log'],cwd=ROOT/'backend',env=clean_env(),stdout=log,stderr=log,start_new_session=True)
        self.backend_owned=True

    def start_web(self):
        self.stop_child('web')
        env={**clean_env(),'STERLING_WEB_PORT':str(self.args.port)}
        log=(STATE/'web.log').open('a');self.logs.append(log)
        self.children['web']=subprocess.Popen(['node',str(ROOT/'scripts/serve_frontend.mjs')],cwd=ROOT,env=env,stdout=log,stderr=log,start_new_session=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['start','run','status','urls','rotate','stop'],nargs='?',default='start')
    parser.add_argument('--all',action='store_true',
        help='One-command local app + Cloudflare + ngrok + loopback Vite dev')
    parser.add_argument('--port',type=int,default=5180);parser.add_argument('--skip-build',action='store_true')
    parser.add_argument('--provider',choices=['cloudflare','ngrok','both'],default=None)
    parser.add_argument('--hostname',default='')
    parser.add_argument('--ngrok-hostname',default='')
    parser.add_argument('--dev',action='store_true')
    parser.add_argument('--dev-port',type=int,default=5174)
    parser.add_argument('--rotate-hours',type=float,default=0)
    args=parser.parse_args()
    if not 1024 <= args.port <= 65535 or args.rotate_hours < 0:parser.error('Use a port from 1024–65535 and a non-negative rotation interval')
    STATE.mkdir(parents=True,exist_ok=True);os.chmod(STATE,0o700)
    if args.hostname:
        parsed=urlsplit(args.hostname)
        if parsed.scheme!='https' or not parsed.hostname or parsed.path not in {'','/'} or parsed.query or parsed.fragment or parsed.username:parser.error('--hostname must be an HTTPS origin')
    for line in (ROOT/'backend/.env').read_text().splitlines() if (ROOT/'backend/.env').exists() else []:
        key,sep,value=line.strip().partition('=')
        if sep and key.startswith('STERLING_') and key not in os.environ:
            os.environ[key]=value.strip().strip(chr(34)+chr(39))
    args.provider='both' if args.all else args.provider or os.environ.get('STERLING_TUNNEL_PROVIDER','cloudflare')
    if args.provider not in {'cloudflare','ngrok','both'}:parser.error('Unsupported tunnel provider')
    if args.provider=='both':
        # Legacy STERLING_PUBLIC_HOSTNAME might be an ngrok origin from an
        # older single-tunnel installation. Do not silently use it for CF.
        args.hostname=args.hostname or os.environ.get('STERLING_CLOUDFLARE_HOSTNAME','')
    else:
        args.hostname=args.hostname or os.environ.get('STERLING_PUBLIC_HOSTNAME','')
    args.ngrok_hostname=args.ngrok_hostname or os.environ.get('STERLING_NGROK_HOSTNAME','')
    args.dev=args.all or args.dev or os.environ.get('STERLING_DEV_FRONTEND','').lower() in {'1','true','yes','on'}
    try:
        args.hostname=safe_hostname(args.hostname,'Primary')
        args.ngrok_hostname=safe_hostname(args.ngrok_hostname,'ngrok')
    except ValueError as error:
        parser.error(str(error))
    if args.provider=='ngrok' and not args.hostname:parser.error('ngrok requires a fixed --hostname')
    if args.dev and (args.dev_port in {5173,8000,args.port} or not 1024<=args.dev_port<=65535):
        parser.error('Development port conflicts with gateway, backend or legacy Kite callback port')
    state=read_state();running=alive(state.get('manager_pid')) and state.get('phase')!='stopped'
    if args.command=='status':
        print(json.dumps({**state,'manager_running':running},indent=2))
        return
    if args.command=='urls':
        print(json.dumps({
            'manager_running':running,
            'local':state.get('local_url','http://127.0.0.1:5180'),
            'dev':state.get('dev_url',''),
            'cloudflare':(state.get('cloudflare_url','')
                          if running and state.get('public_ready',{}).get('cloudflare') else ''),
            'ngrok':(state.get('ngrok_url','')
                     if running and state.get('public_ready',{}).get('ngrok') else ''),
            'verified':state.get('public_ready',{}) if running else {},
            'primary_telegram_desk':state.get('mini_app_url',''),
            'kite_callback_guidance':state.get('callback_url',''),
            'ngrok_error':state.get('ngrok_error',''),
        },indent=2))
        return
    if args.command in {'stop','rotate'}:
        if not running:raise RuntimeError('Sterling stack manager is not running')
        os.kill(state['manager_pid'],signal.SIGTERM if args.command=='stop' else signal.SIGUSR1);print('Request accepted');return
    inherited=os.environ.get('STERLING_STACK_LOCK_FD')
    lock=os.fdopen(int(inherited),'a') if inherited else (STATE/'manager.lock').open('a')
    if not inherited:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:print('Sterling is already running. Use ./sterling.sh status');return
    if args.command=='start':
        forwarded=[arg for arg in sys.argv[1:] if arg!='start']
        log=(STATE/'manager.log').open('a');os.chmod(STATE/'manager.log',0o600)
        # Child inherits the singleton lock; no second launcher can race startup.
        subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'run',*forwarded],cwd=ROOT,env={**clean_env(),'STERLING_STACK_LOCK_FD':str(lock.fileno())},stdout=log,stderr=log,start_new_session=True,pass_fds=(lock.fileno(),))
        print('Starting Sterling. Run ./sterling.sh status for the live URL.');return
    stack=Stack(args)
    signal.signal(signal.SIGTERM,lambda *_:setattr(stack,'stopping',True));signal.signal(signal.SIGINT,lambda *_:setattr(stack,'stopping',True));signal.signal(signal.SIGUSR1,lambda *_:setattr(stack,'rotate',True))
    save_state(stack.status)
    try:stack.run()
    finally:
        # Keep broker backend alive for exit, protection and reconciliation.
        # Stop only the locally-managed presentation/tunnel processes.
        for child in ('ngrok','dev','cloudflare','web'):
            stack.stop_child(child)
        stack.status.update(phase='stopped')
        save_state(stack.status)


if __name__=='__main__':
    try:main()
    except Exception as error:print(f'Sterling launch failed: {type(error).__name__}. See .runtime/stack logs.',file=sys.stderr);sys.exit(1)
