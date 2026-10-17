#!/usr/bin/env python3
"""Start, supervise and publish the Sterling web stack without changing trading controls."""
from __future__ import annotations
import argparse
import fcntl
import hashlib
import io
import tarfile
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
from concurrent.futures import ThreadPoolExecutor
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


def _gateway_listener_pids(port):
    """Return listener PIDs from ss. Unknown ownership is NOT permission to kill."""
    if not shutil.which('ss'):
        return []
    try:
        result=subprocess.run(['ss','-H','-ltnp',f'sport = :{int(port)}'],
                              capture_output=True,text=True,timeout=4,check=True)
    except (OSError,subprocess.SubprocessError,ValueError):
        return []
    return sorted({int(value) for value in re.findall(r'\bpid=(\d+),',result.stdout)})


def _is_own_orphan_gateway(pid):
    """Only reclaim an old Node gateway running this SAME checkout as this UID."""
    if not sys.platform.startswith('linux') or pid<=1 or pid==os.getpid():
        return False
    try:
        proc=Path(f'/proc/{pid}')
        if proc.stat().st_uid!=os.getuid():
            return False
        argv=[piece.decode('utf-8','replace') for piece in
              (proc/'cmdline').read_bytes().split(b'\x00') if piece]
        expected=str(ROOT/'scripts/serve_frontend.mjs')
        return (expected in argv and (proc/'cwd').resolve()==ROOT.resolve()
                and any(Path(argv[0]).name==name for name in ('node','nodejs')))
    except (OSError,ValueError,IndexError):
        return False


def _verify_gateway_child(child, port):
    """Do not publish a healthy stale gateway when our new Node child failed.

    Require the real port listener to belong to the exact spawned process;
    a 200 response from an unrelated/leftover process is not readiness.
    """
    deadline=time.monotonic()+8
    while time.monotonic()<deadline:
        if child.poll() is not None:
            raise RuntimeError(
                f'New Sterling gateway exited without binding {port}; '
                'read .runtime/stack/web.log for EADDRINUSE.'
            )
        owners=_gateway_listener_pids(port)
        if owners and owners!=[child.pid]:
            raise RuntimeError(
                f'Port {port} was taken by an unexpected listener during gateway startup.'
            )
        if owners==[child.pid] and _is_own_orphan_gateway(child.pid):
            try:
                _,payload=fetch(f'http://127.0.0.1:{port}/__sterling_web_health',2)
                if json.loads(payload).get('service')=='sterling-web' and json.loads(payload).get('ready'):
                    return
            except (OSError,ValueError,urllib.error.URLError):
                pass
        time.sleep(.15)
    raise RuntimeError(
        f'Gateway process {child.pid} never proved ownership and health '
        f'on port {port}; do not publish stale web content.'
    )


def _reclaim_gateway(port):
    """Shut down ONLY an identifiable orphan web process, never the broker."""
    pids=_gateway_listener_pids(port)
    if len(pids)!=1 or not _is_own_orphan_gateway(pids[0]):
        raise RuntimeError(
            f'Web port {port} is occupied by an unverified process; '
            'inspect ss -ltnp before starting Sterling. Broker backend was not stopped.'
        )
    pid=pids[0]
    os.kill(pid,signal.SIGTERM)
    for _ in range(50):
        with socket.socket() as probe:
            if probe.connect_ex(('127.0.0.1',int(port)))!=0:
                return pid
        time.sleep(.2)
    raise RuntimeError(
        f'Old Sterling web PID {pid} did not release port {port}; '
        'do not force-kill or stop the broker backend.'
    )


def alive(pid):
    """Only regard our exact, same-UID checkout's supervisor as running."""
    try:
        pid=int(pid)
        if pid<=1 or not sys.platform.startswith('linux'):return False
        proc=Path(f'/proc/{pid}')
        if proc.stat().st_uid!=os.getuid():return False
        if (proc/'stat').read_text().rsplit(') ',1)[-1].startswith('Z'):return False
        argv=[part.decode('utf-8','replace') for part in
              (proc/'cmdline').read_bytes().split(b'\x00') if part]
        if str(ROOT/'scripts/sterling_stack.py') not in argv:return False
        if (proc/'cwd').resolve()!=ROOT.resolve():return False
        os.kill(pid,0)
        return True
    except (OSError,ValueError,TypeError):return False


def _managed_web_owns_port(state):
    """A healthy manager is reusable only when its Node child owns 5180."""
    try:
        port=int(state.get('web_port',5180))
        pid=int((state.get('children') or {}).get('web') or 0)
        return pid>1 and _gateway_listener_pids(port)==[pid] and _is_own_orphan_gateway(pid)
    except (TypeError,ValueError):
        return False


def _stop_old_managed_presentation(pid):
    """Gracefully replace only our old manager; never terminate its broker."""
    if not alive(pid):
        return
    os.kill(pid,signal.SIGTERM)
    # The previous launcher may wait for web/tunnel close independently.
    # Never escalate to SIGKILL if it has not drained yet.
    deadline=time.monotonic()+75
    while alive(pid) and time.monotonic()<deadline:
        time.sleep(.2)
    if alive(pid):
        raise RuntimeError(
            'Existing Sterling manager did not stop; refusing a second '
            'stack. Trading backend remains unchanged.'
        )


def _terminal_color_enabled():
    """ANSI color is for a real terminal, never logs, pipes, or NO_COLOR users."""
    return bool(sys.stdout.isatty() and 'NO_COLOR' not in os.environ
                and os.environ.get('TERM', '') != 'dumb')


def _terminal_style(value, color, enabled):
    palette={'brand':'\x1b[1;96m','green':'\x1b[1;92m',
             'yellow':'\x1b[1;93m','magenta':'\x1b[1;95m',
             'muted':'\x1b[2m','bold':'\x1b[1m'}
    return f'{palette[color]}{value}\x1b[0m' if enabled else value


def _clean_terminal_value(value):
    return re.sub(r'[\x00-\x1f\x7f-\x9f]', '', str(value or ''))


def print_up_dashboard(state, *, reused, gateway_ready=False):
    """One prominent overview; report verified tunnel URLs, never guessed links."""
    color=_terminal_color_enabled()
    fmt=lambda text,tone: _terminal_style(text,tone,color)
    phase=state.get('phase','starting')
    ready=bool(gateway_ready)  # Local process ownership is independent of tunnel state.
    # Do not print cached URLs unless the running manager confirmed them.
    healthy=state.get('public_ready') or {}
    urls=state.get('urls') or {}
    def public_origin(provider):
        if not gateway_ready or not healthy.get(provider):
            return None
        origin=urls.get(provider,'')
        try:
            parsed=urlsplit(origin)
            if (parsed.scheme=='https' and parsed.hostname and
                    parsed.username is None and parsed.password is None
                    and not parsed.query and not parsed.fragment):
                return _clean_terminal_value(origin)
        except (ValueError,TypeError):
            pass
        return None

    def line(name, url, state_label, tone, icon='●'):
        # Keep the actual URL as readable, copyable text (no hidden hyperlinks).
        label=fmt(f'  {name:<14}', 'bold')
        marker=fmt(f'{icon} {state_label:<14}',tone)
        target=fmt(_clean_terminal_value(url),'brand' if url.startswith('http') else 'muted')
        print(f'{label} {marker} {target}')

    print()
    print(fmt('  ╔══════════════════════════════════════════════════════════════════╗','brand'))
    print(fmt('  ║  ✦ S T E R L I N G     ·     ONE-COMMAND WORKSPACE             ║','brand'))
    print(fmt('  ╚══════════════════════════════════════════════════════════════════╝','brand'))
    if reused:
        message=('● MANAGED — both tunnels verified' if ready and
                 healthy.get('cloudflare') and healthy.get('ngrok') else
                 '◉ MANAGED — connecting/verifying public tunnels')
        print('  '+fmt(message,'green' if 'verified' in message else 'yellow'))
    else:
        print('  '+fmt('◉ STARTING — one backend, both public tunnels and dev','yellow'))
    print()
    local=_clean_terminal_value(state.get('local_url') or 'http://127.0.0.1:5180')
    development=_clean_terminal_value(state.get('dev_url') or 'http://127.0.0.1:5174')
    line('LOCAL APP',local,'READY' if ready else 'STARTING',
         'green' if ready else 'yellow')
    for name,provider in [('CLOUDFLARE','cloudflare'),('NGROK','ngrok')]:
        found=public_origin(provider)
        discovered=_clean_terminal_value(state.get(f'{provider}_url',''))
        if found:
            line(name,found,'VERIFIED','green')
        elif discovered.startswith('https://'):
            line(name,discovered+' (unverified)','VERIFYING','yellow','◌')
        else:
            line(name,'Tunnel URL not yet discovered','CONNECTING','yellow','◌')
    # Vite is launched by the supervisor, but do not claim HTTP readiness from
    # the existence of a remembered child PID alone.
    line('DEV · HOT RELOAD',development,'LOCAL WORKTREE','magenta')
    print()
    print('  '+fmt('PUBLIC  ','green')+
          'committed frontend · same release on local, Cloudflare and ngrok')
    print('  '+fmt('DEV     ','magenta')+
          'uncommitted edits appear only on the development URL')
    release=_clean_terminal_value(state.get('release_commit',''))
    if release:
        print('  '+fmt('RELEASE ','brand')+
              f'{_clean_terminal_value(state.get("release_ref","main"))} · {release[:12]}')
    if state.get('release_stale'):
        print('  '+fmt('! Previous committed release retained; latest build failed.','yellow'))
    if state.get('ngrok_error'):
        print('  '+fmt(f'! ngrok: {_clean_terminal_value(state["ngrok_error"])}','yellow'))
    if not gateway_ready and reused:
        print('  '+fmt('! Local gateway ownership not verified; inspect ./sterling.sh status','yellow'))
    print('  '+fmt('DETAILS ','muted')+'./sterling.sh urls   ·   ./sterling.sh status')
    print()


def wait_for_up_status(manager_pid, *, seconds=12):
    """Bounded readiness wait, never turning an unverified HTTPS URL green."""
    deadline=time.monotonic()+max(0,min(float(seconds),20))
    latest=read_state()
    while time.monotonic()<deadline:
        state=read_state()
        if state.get('manager_pid')!=manager_pid:
            time.sleep(.2)
            continue
        latest=state
        if state.get('phase')=='stopped' or not alive(manager_pid):
            break
        if (_managed_web_owns_port(state)
                and state.get('public_ready',{}).get('cloudflare')
                and (state.get('public_ready',{}).get('ngrok')
                     or state.get('ngrok_error')=='NGROK_BINARY_NOT_INSTALLED')):
            break
        time.sleep(.2)
    return latest


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
    # Never compile the public app from ROOT/frontend: local edits and
    # untracked source are strictly for Vite dev on 5174.
    # CommittedFrontend publishes the isolated build from Git archive.
    return python



class CommittedFrontend:
    """Publish ONLY Git-owned frontend files, never a dirty working tree.

    The 5180 gateway dynamically reads the 'published' atomic symlink. This
    class does not modify Git refs, local edits or the long-lived Kite backend.
    """
    def __init__(self, ref='main'):
        if not re.fullmatch(
            r'(?:HEAD|main|origin/main|refs/(?:heads|remotes/origin)/[A-Za-z0-9._/-]+)',
            ref,
        ) or any(part in {'', '.', '..'} for part in ref.split('/')):
            raise ValueError('Invalid published Git ref')
        self.ref=ref
        self.stage=STATE/'committed-source'
        self.frontend=self.stage/'frontend'
        self.pointer=STATE/'published'
        self.releases=STATE/'releases'
        self.manifest=STATE/'published.json'
        self.lock_stamp=STATE/'committed-npm-lock.sha256'

    def resolve_commit(self):
        rev=subprocess.run(['git','-C',str(ROOT),'rev-parse','--verify',
                            f'{self.ref}^{{commit}}'],
                           capture_output=True,text=True,timeout=12,check=True)
        result=rev.stdout.strip()
        if not re.fullmatch(r'[a-f0-9]{40,64}',result):
            raise RuntimeError('Git did not return a commit SHA')
        return result

    def current(self):
        try:
            item=json.loads(self.manifest.read_text())
            path=self.pointer.resolve(strict=True)
            if (path==self.releases/item['sha'] and
                    (path/'index.html').is_file()):
                return item['sha']
        except (OSError,ValueError,KeyError,TypeError):
            pass
        return ''

    def _extract(self, sha):
        """Use git archive, not checkout: uncommitted files cannot enter build."""
        self.frontend.mkdir(parents=True,exist_ok=True)
        # Preserve installed npm dependencies only; every other build input
        # is restored from the same immutable Git commit.
        for child in self.frontend.iterdir():
            if child.name=='node_modules':
                continue
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()
        proc=subprocess.Popen(['git','-C',str(ROOT),'archive','--format=tar',
                               sha,'frontend'],
                              stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        try:
            with tarfile.open(fileobj=proc.stdout,mode='r|') as archive:
                for member in archive:
                    name=Path(member.name)
                    if (name.is_absolute() or '..' in name.parts or
                            (not member.isfile() and not member.isdir())):
                        raise RuntimeError('Unsafe frontend archive member')
                    target=(self.stage/name).resolve()
                    if not target.is_relative_to(self.stage.resolve()):
                        raise RuntimeError('Git archive path escaped release staging')
                    archive.extract(member,path=self.stage,filter='data')
        except Exception:
            proc.kill()
            proc.wait()
            raise
        if proc.wait(timeout=30):
            raise RuntimeError('Could not extract committed frontend')
        if not (self.frontend/'package-lock.json').is_file():
            raise RuntimeError('Committed frontend has no package-lock.json')

    def _dependencies(self):
        digest=hashlib.sha256((self.frontend/'package-lock.json').read_bytes()).hexdigest()
        try:cached=self.lock_stamp.read_text().strip()
        except OSError:cached=''
        if cached==digest and (self.frontend/'node_modules/.bin/vite').exists():
            return
        with (STATE/'release-build.log').open('a') as log:
            subprocess.run(['npm','ci','--no-audit','--no-fund'],
                           cwd=self.frontend,stdout=log,stderr=log,
                           timeout=600,check=True)
        self.lock_stamp.write_text(digest)

    def publish(self):
        sha=self.resolve_commit()
        if self.current()==sha:return sha,False
        existing=self.releases/sha
        if not (existing/'index.html').is_file():
            # Remove only a leftover unpublished staging directory from an
            # interrupted run. Published release directories are immutable.
            self._extract(sha)
            self._dependencies()
            # Never carry a tracked or leftover dist into a new committed
            # build; Vite intentionally has emptyOutDir=false for other flows.
            old_dist=self.frontend/'dist'
            if old_dist.exists():shutil.rmtree(old_dist)
            with (STATE/'release-build.log').open('a') as log:
                subprocess.run(['npm','run','build'],
                               cwd=self.frontend,
                               env={**clean_env(),'VITE_API_BASE_URL':''},
                               stdout=log,stderr=log,
                               timeout=360,check=True)
            built=self.frontend/'dist'
            if not (built/'index.html').is_file():
                raise RuntimeError('Committed build did not produce index.html')
            self.releases.mkdir(parents=True,exist_ok=True)
            staged=self.releases/(sha+'.tmp')
            if staged.exists():shutil.rmtree(staged)
            shutil.copytree(built,staged)
            # Keep already referenced content-hashed assets for open browser
            # sessions across a release, avoiding broken tabs after promotion.
            previous=self.pointer.resolve()
            old_assets=previous/'assets'
            new_assets=staged/'assets'
            if old_assets.is_dir():
                new_assets.mkdir(exist_ok=True)
                for item in old_assets.iterdir():
                    if item.is_file() and not (new_assets/item.name).exists():
                        shutil.copy2(item,new_assets/item.name)
            staged.rename(existing)
        tmp=STATE/'published.next'
        tmp.unlink(missing_ok=True)
        tmp.symlink_to(existing,target_is_directory=True)
        tmp.replace(self.pointer)
        manifest={'sha':sha,'ref':self.ref,'updated_ms':int(time.time()*1000)}
        pending=STATE/'published.json.tmp'
        pending.write_text(json.dumps(manifest))
        os.chmod(pending,0o600)
        pending.replace(self.manifest)
        # Retain the last two releases plus the new current release. Old
        # hashed files are already carried into new current for open tabs.
        previous=self.pointer.resolve()
        old=sorted((x for x in self.releases.iterdir()
                    if x.is_dir() and not x.name.endswith('.tmp')
                    and x!=previous),key=lambda x:x.stat().st_mtime,reverse=True)
        for path in old[2:]:shutil.rmtree(path)
        return sha,True


class Stack:
    def __init__(self,args):
        self.args=args;self.children={};self.logs=[];self.urls=queue.Queue();self.stopping=False;self.rotate=False
        provider=getattr(args,'provider','cloudflare')
        self.status={'manager_pid':os.getpid(),'manager_protocol':3,'phase':'starting','app_url':'','mini_app_url':'',
                     'mode':'both' if provider=='both' else 'ngrok' if provider=='ngrok' else ('named' if args.hostname else 'quick'),
                     'web_port':args.port,'local_url':f'http://127.0.0.1:{args.port}',
                     'dev_url':f'http://127.0.0.1:{args.dev_port}' if getattr(args,'dev',False) else '',
                     'cloudflare_url':'','ngrok_url':'','urls':{},'public_ready':{}}
        self.backend_owned=False;self.last_publish=0;self.failures=0;self.published=False
        self.last_public_signature=None;self.last_ngrok_retry=0;self.last_dev_retry=0
        self.ngrok_urls=queue.Queue();self.ngrok_binary=None
        self.publisher=CommittedFrontend(os.environ.get('STERLING_PUBLISH_REF','main'))
        self.last_release_check=0;self.last_fetch=0
        self.status['release_ref']=self.publisher.ref
        self.status['release_commit']=self.publisher.current()
        self.status['release_error']=''
        self.release_results=queue.Queue()
        self.release_running=False

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
                        log.write('Tunnel URL discovered\n')
                        log.flush()
                    elif 'ERR' in line or '"lvl":"eror"' in line:
                        # ngrok logs may include bearer tokens / account data.
                        log.write('Tunnel transport error\n')
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

    def refresh_committed_release(self, fetch_remote: bool):
        """Background build: do not block protection and tunnel checks."""
        try:
            if fetch_remote:
                subprocess.run(['git','-C',str(ROOT),'fetch','origin','main'],
                               timeout=45,check=True,
                               stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            revision,promoted=self.publisher.publish()
            self.release_results.put(('ready',revision,promoted))
        except Exception as error:
            self.release_results.put(('failed',type(error).__name__,False))

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
        # Fail closed before opening public URLs if no committed build exists.
        try:
            revision,promoted=self.publisher.publish()
            self.status['release_commit']=revision
        except Exception as error:
            self.status['release_error']=type(error).__name__
            previous=self.publisher.current()
            if not previous:
                raise RuntimeError('No valid committed frontend build. Check release-build.log.') from error
            self.status['release_commit']=previous
            self.status['release_stale']=True
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
            while not self.release_results.empty():
                outcome,revision,promoted=self.release_results.get()
                self.release_running=False
                if outcome=='ready':
                    self.status['release_commit']=revision
                    self.status['release_error']=''
                    self.status['release_stale']=False
                    if promoted:
                        print(f'Committed frontend updated: {revision[:12]} on all public URLs',flush=True)
                else:
                    self.status['release_error']=revision
                    self.status['release_stale']=True
                    self.last_release_check=max(self.last_release_check,now+60)
                    print(f'Committed build failed ({revision}); retaining last good frontend.',flush=True)
            if now>=self.last_release_check and not self.release_running:
                self.last_release_check=now+15
                should_fetch=(os.environ.get('STERLING_PUBLISH_FETCH')=='1'
                              and self.publisher.ref=='origin/main'
                              and now-self.last_fetch>=60)
                if should_fetch:self.last_fetch=now
                self.release_running=True
                threading.Thread(target=self.refresh_committed_release,
                                 args=(should_fetch,),daemon=True).start()
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
        # A crashed prior manager can leave behind the standalone Node
        # gateway. Reclaim it only when ss+proc prove exact same script,
        # checkout and user; never kill an unidentified port owner.
        with socket.socket() as probe:
            if probe.connect_ex(('127.0.0.1',self.args.port))==0:
                old=_reclaim_gateway(self.args.port)
                self.status['gateway_reclaimed_pid']=old
        env={**clean_env(),'STERLING_WEB_PORT':str(self.args.port),
             'STERLING_STATIC_ROOT':str(self.publisher.pointer)}
        log=(STATE/'web.log').open('a');self.logs.append(log)
        child=subprocess.Popen(['node',str(ROOT/'scripts/serve_frontend.mjs')],
                               cwd=ROOT,env=env,stdout=log,stderr=log,start_new_session=True)
        self.children['web']=child
        try:
            _verify_gateway_child(child,self.args.port)
            self.status['web_ready_pid']=child.pid
            self.status.pop('web_error',None)
        except Exception as exc:
            self.status['web_error']=type(exc).__name__
            self.stop_child('web')
            raise


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['up','start','run','status','urls','rotate','stop'],nargs='?',default='start')
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
    if args.command=='up':
        args.all=True
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
            'release_ref':state.get('release_ref',''),
            'release_commit':state.get('release_commit',''),
            'release_stale':state.get('release_stale',False),
            'release_error':state.get('release_error',''),
        },indent=2))
        return
    if args.command in {'stop','rotate'}:
        if not running:raise RuntimeError('Sterling stack manager is not running')
        os.kill(state['manager_pid'],signal.SIGTERM if args.command=='stop' else signal.SIGUSR1);print('Request accepted');return
    if args.command=='up' and running:
        # Re-running the one-command launcher is idempotent. A verified
        # current gateway is already supervised; do not churn its tunnels.
        current=(state.get('manager_protocol',0)>=3 and
                 state.get('mode')=='both' and
                 state.get('dev_url')==f'http://127.0.0.1:{args.dev_port}' and
                 state.get('web_port')==args.port)
        if current and state.get('phase')!='stopped':
            if state.get('phase')!='ready' or _managed_web_owns_port(state):
                state=wait_for_up_status(state['manager_pid'],
                    seconds=os.environ.get('STERLING_UP_WAIT_SECONDS','12'))
                print_up_dashboard(state,reused=True,
                                   gateway_ready=_managed_web_owns_port(state))
                return
        # An old-version or unhealthy manager is stopped *gracefully*.
        # The manager's shutdown leaves the broker process alone. There
        # can never be a second concurrent 8000 trading engine.
        print('Replacing old/unhealthy Sterling presentation manager; keeping broker backend.')
        _stop_old_managed_presentation(state['manager_pid'])
    inherited=os.environ.get('STERLING_STACK_LOCK_FD')
    lock=os.fdopen(int(inherited),'a') if inherited else (STATE/'manager.lock').open('a')
    if not inherited:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:print('Sterling is already running. Use ./sterling.sh status');return
    if args.command in {'start','up'}:
        forwarded=[arg for arg in sys.argv[1:] if arg not in {'start','up'}]
        if args.command=='up':
            forwarded.append('--all')
        log=(STATE/'manager.log').open('a');os.chmod(STATE/'manager.log',0o600)
        # Child inherits the singleton lock; no second launcher can race startup.
        manager=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'run',*forwarded],cwd=ROOT,env={**clean_env(),'STERLING_STACK_LOCK_FD':str(lock.fileno())},stdout=log,stderr=log,start_new_session=True,pass_fds=(lock.fileno(),))
        # The manager continues asynchronously; wait briefly for *verified*
        # URLs so the first dashboard is useful instead of stale startup data.
        updated=(wait_for_up_status(manager.pid,
                  seconds=os.environ.get('STERLING_UP_WAIT_SECONDS','12'))
                 if args.command=='up' else {})
        if updated.get('manager_pid')!=manager.pid:
            updated={'phase':'starting','local_url':f'http://127.0.0.1:{args.port}',
                     'dev_url':f'http://127.0.0.1:{args.dev_port}',
                     'public_ready':{},'urls':{}}
        print_up_dashboard(updated,reused=False,
                           gateway_ready=_managed_web_owns_port(updated))
        return
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
