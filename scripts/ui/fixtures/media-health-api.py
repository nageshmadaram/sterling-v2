"""Disposable real API + persistent async worker. No production or public network."""
import os,sys,tempfile,json,secrets,socket
from pathlib import Path
for key in list(os.environ):
    if key.startswith(('STERLING_','KITE_','TELEGRAM_','TRUEDATA_')):os.environ.pop(key,None)
folder=Path(tempfile.mkdtemp(prefix='sterling-health-browser-'))
os.environ.update(ENVIRONMENT='development',STERLING_UNDER_TEST='1',STERLING_DB_PATH=str(folder/'identity.db'),STERLING_SECRET_KEY='health-test-encryption-'+'x'*48,STERLING_JWT_SECRET='health-test-jwt-'+'y'*48)
sys.path.insert(0,str(Path(__file__).resolve().parents[3]/'backend'))
from app.core import runtime_env,tokens
from tests.hermetic_env import isolate_dotenv
isolate_dotenv(runtime_env)
from main import create_app
from app.services import db,media_health
from app.services.auth import user_store,account_policy
assert db.init();user_store._init_table();user_store._users={};user_store._loaded=True
admin=user_store.add('browser-admin','Synthetic-browser-admin-passphrase-123',role='admin',user_id='default')
member=user_store.add('health-member','Synthetic-browser-member-passphrase-123',user_id='member')
account_policy.set_features(admin.id,member.id,{'kite':True,'tv':True,'radio':True},0)
sessions={}
for name,user in [('admin',admin),('sterling',member)]:sessions[name]={'token':tokens._mint(user.id,user.username,user.role,user.token_version,'access',7200)}
nonce=secrets.token_hex(24);sessions.update(fixture_nonce=nonce,db=str(folder/'identity.db'))
path=Path(os.environ.get('HEALTH_ACCEPTANCE_SESSION','/tmp/sterling-health-browser-session.json'));path.write_text(json.dumps(sessions));path.chmod(0o600)
recovered=False;probes=[]
async def probe(session,url,depth=0):
    if 'health-fixture.example' not in url:return None
    probes.append(url.rsplit('/',1)[-1])
    return recovered or url.endswith('/backup-working')
media_health.probe_source=probe
# Keep real auth, routes, models, policy, SQLite and worker scheduling. No lifespan jobs.
app=create_app()
from fastapi import Request,HTTPException
@app.api_route('/api/v1/__acceptance_health_fixture__',methods=['GET','POST'])
async def fixture(request:Request):
    global recovered
    if request.headers.get('x-fixture-nonce')!=nonce:raise HTTPException(403)
    if request.method=='POST':recovered=True
    return {'disposable':True,'fixture_nonce':nonce,'recovered':recovered,'probes':probes[-100:]}
original=socket.socket.connect

def connect(sock,address):
    if isinstance(address,tuple) and address[0] not in ('127.0.0.1','::1','localhost'):raise RuntimeError('Public network disabled in disposable fixture')
    return original(sock,address)
socket.socket.connect=connect
import uvicorn
uvicorn.run(app,host='127.0.0.1',port=int(os.environ.get('HEALTH_ACCEPTANCE_API_PORT','8210')),lifespan='off',log_level='warning')
