import os,sys,tempfile,json,socket,secrets
from pathlib import Path
for key in list(os.environ):
 if key.startswith(('STERLING_','KITE_','TELEGRAM_','TRUEDATA_')):os.environ.pop(key,None)
folder=Path(tempfile.mkdtemp(prefix='sterling-admin-browser-'))
os.environ.update(ENVIRONMENT='development',STERLING_UNDER_TEST='1',STERLING_DB_PATH=str(folder/'identity.db'),STERLING_SECRET_KEY='browser-test-encryption-'+'x'*48,STERLING_JWT_SECRET='browser-test-jwt-'+'y'*48,STERLING_AUDIT_LOG=str(folder/'audit.jsonl'))
sys.path.insert(0,str(Path(__file__).resolve().parents[3]/'backend'))
from app.core import runtime_env
from tests.hermetic_env import isolate_dotenv
isolate_dotenv(runtime_env)
from main import create_app
from app.services import db
from app.services.auth import user_store
from app.core import tokens
assert db.init();user_store._init_table();user_store._users={};user_store._loaded=True
admin=user_store.add('browser-admin','Synthetic-browser-admin-passphrase-123',role='admin',user_id='default')
token=tokens._mint(admin.id,admin.username,admin.role,admin.token_version,'access',3600)
nonce=secrets.token_hex(24)
p=Path(os.environ.get('ADMIN_ACCEPTANCE_SESSION','/tmp/sterling-admin-browser-session.json'));p.write_text(json.dumps({'token':token,'db':str(folder/'identity.db'),'fixture_nonce':nonce}));p.chmod(0o600)
# No service lifespan or worker starts; deny all non-loopback network attempts.
original=socket.socket.connect
network_attempts=[]
def connect(sock,address):
 if isinstance(address,tuple) and address[0] not in ('127.0.0.1','::1','localhost'):
  network_attempts.append(str(address[0]));raise RuntimeError('External network is disabled in acceptance fixture')
 return original(sock,address)
socket.socket.connect=connect
from fastapi.testclient import TestClient
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
app=create_app()
class Handler(BaseHTTPRequestHandler):
 def serve(self):
  if self.path=='/api/v1/__acceptance_fixture__':
   payload=json.dumps({'fixture_nonce':nonce,'disposable':True}).encode()
   self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload);return
  body=self.rfile.read(int(self.headers.get('Content-Length',0)))
  with_client=TestClient(app)
  r=with_client.request(self.command,self.path,content=body,headers={k:v for k,v in self.headers.items() if k.lower() not in ('host','connection','content-length')})
  self.send_response(r.status_code)
  for k,v in r.headers.multi_items():
   if k.lower() not in ('content-length','transfer-encoding','connection','content-encoding'):self.send_header(k,v)
  self.send_header('Content-Length',str(len(r.content)));self.end_headers()
  if self.command!='HEAD':self.wfile.write(r.content)
 do_GET=do_POST=do_PUT=do_PATCH=do_DELETE=do_HEAD=do_OPTIONS=serve
 def log_message(self,*args):pass
print('Disposable authenticated admin API ready; external network denied.',flush=True)
ThreadingHTTPServer(('127.0.0.1',int(os.environ.get('ADMIN_ACCEPTANCE_API_PORT','8205'))),Handler).serve_forever()
