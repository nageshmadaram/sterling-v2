"""Private controller form; passwords never pass through Telegram chat messages."""
import secrets
import time
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from app.core.auth import UserContext
from app.services.auth import user_store, operator_credentials
from app.services.notifications.telegram_webapp_auth import tma_user

router=APIRouter()
_challenges: dict[str,tuple[str,float]]={}


def require_admin(user):
    if user.role!='admin':raise HTTPException(403,'Administrator access required')


@router.get('/tma/operator/data')
async def operator_data(response: Response, user: UserContext=Depends(tma_user)):
    require_admin(user)
    response.headers["Cache-Control"]="no-store, private"
    now=time.monotonic()
    for token,(_,until) in list(_challenges.items()):
        if until<now:_challenges.pop(token,None)
    # Keep at most one outstanding form per operator.
    for token,(owner,_) in list(_challenges.items()):
        if owner==user.user_id:_challenges.pop(token,None)
    token=secrets.token_urlsafe(32);_challenges[token]=(user.user_id,now+300)
    return {'username':user.username,'challenge':token}


class CredentialEdit(BaseModel):
    action: str=Field(max_length=12)
    username: str=Field(max_length=64)
    password: str=Field(default='',max_length=256)
    challenge: str=Field(max_length=100)


@router.post('/tma/operator/manage')
async def operator_manage(body: CredentialEdit,user: UserContext=Depends(tma_user)):
    require_admin(user)
    challenge=_challenges.get(body.challenge)
    if not challenge or challenge[0]!=user.user_id or challenge[1]<time.monotonic():
        raise HTTPException(409,'Form expired or already used. Reopen Sterling Login.')
    _challenges.pop(body.challenge,None)
    try:updated=operator_credentials.manage(user.user_id,body.action,body.username,body.password)
    except ValueError as exc:raise HTTPException(422,str(exc)) from None
    except PermissionError:raise HTTPException(403,'Administrator access required') from None
    except RuntimeError as exc:raise HTTPException(503,str(exc)) from None
    return {'ok':True,'username':updated.username,'message':'Sterling login updated. Sign in to the app again; your workspace and broker session are preserved.' if body.action=='update' else 'User added with a separate workspace. It has no broker account until configured.'}


@router.get('/tma/operator',response_class=HTMLResponse)
async def operator_form(request:Request):
    nonce=getattr(request.state,'csp_nonce','')
    html=FORM.replace('__NONCE__',nonce)
    return HTMLResponse(html,headers={'Cache-Control':'no-store','Referrer-Policy':'no-referrer'})


FORM=r'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Sterling · Sterling Login</title>
<script nonce="__NONCE__" src="https://telegram.org/js/telegram-web-app.js"></script>
<style nonce="__NONCE__">body{font:15px system-ui;background:#101722;color:#eef2f7;margin:0;padding:22px}main{max-width:460px;margin:auto}label{display:block;margin:18px 0 5px}input,select,button{box-sizing:border-box;width:100%;padding:12px;border:1px solid #536174;border-radius:8px;background:#1c2838;color:inherit;font:inherit}button{margin-top:18px;background:#de622f;cursor:pointer}button:disabled{opacity:.5}p{line-height:1.5;color:#c1ccd9}.field{display:flex;gap:8px}.field button{width:auto;margin:0;background:#1c2838}#status{white-space:pre-line}</style></head>
<body><main><h1>Sterling Login</h1><p>Manage the Sterling app login. This does not change Zerodha credentials.</p><form id="form">
<label for="action">Action</label><select id="action"><option value="update">Change my Sterling login</option><option value="add">Add a user (separate workspace)</option></select>
<label for="username">Username</label><input id="username" required minlength="3" maxlength="64" autocomplete="username">
<label for="password">New password</label><div class="field"><input id="password" type="password" maxlength="256" autocomplete="new-password"><button id="eye" type="button" aria-label="Show password">Show</button></div>
<label for="repeat">Repeat new password</label><input id="repeat" type="password" maxlength="256" autocomplete="new-password">
<p id="note">Leave password blank to keep it. Username/password changes sign out existing app sessions. Your broker session remains connected.</p>
<button id="save" disabled>Review and save</button></form><p id="status" role="status">Checking Telegram authorization…</p></main>
<script nonce="__NONCE__">
const tg=window.Telegram?.WebApp;tg?.ready();tg?.expand();let challenge='';let current='';let busy=false;
const el=id=>document.getElementById(id);const headers={'Content-Type':'application/json','X-Telegram-Init-Data':tg?.initData||''};
async function load(){const r=await fetch('/tma/operator/data',{headers,cache:'no-store'});const d=await r.json();if(!r.ok)throw Error(d.detail||'Authorization failed');challenge=d.challenge;current=d.username;if(el('action').value==='update')el('username').value=current;el('save').disabled=false;}
if(!tg?.initData){el('status').textContent='Open Sterling Login from your configured Sterling Telegram controller chat.';}else{load().then(()=>el('status').textContent='Ready. Passwords are saved directly to Sterling, not sent into chat.').catch(e=>el('status').textContent=e.message);}
el('eye').onclick=()=>{const visible=el('password').type==='password';el('password').type=visible?'text':'password';el('eye').textContent=visible?'Hide':'Show';el('eye').setAttribute('aria-label',visible?'Hide password':'Show password');};
window.addEventListener('blur',()=>{el('password').type='password';el('eye').textContent='Show';el('eye').setAttribute('aria-label','Show password');});
el('action').onchange=()=>{const add=el('action').value==='add';el('username').value=add?'':current;el('password').value='';el('repeat').value='';el('password').required=add;el('note').textContent=add?'Adds a regular user with a separate empty workspace; it cannot access your existing broker account. Password must have at least 12 characters.':'Leave password blank to keep it. Changes sign out app sessions; your workspace and broker session stay connected.';};
el('form').onsubmit=async event=>{event.preventDefault();if(busy)return;const password=el('password').value;const action=el('action').value;const username=el('username').value.trim();if(password!==el('repeat').value){el('status').textContent='Passwords do not match.';return;}if((password||action==='add')&&password.length<12){el('status').textContent='Use at least 12 characters.';return;}
const confirmed=await new Promise(resolve=>tg.showConfirm(action==='add'?'Add '+username+' with a separate workspace?':'Update '+username+' and sign out existing app sessions?',resolve));if(!confirmed)return;busy=true;el('save').disabled=true;
try{const r=await fetch('/tma/operator/manage',{method:'POST',headers,body:JSON.stringify({action,username,password,challenge})});const d=await r.json();el('password').value='';el('repeat').value='';if(!r.ok)throw Error(typeof d.detail==='string'?d.detail:'Could not save credentials.');el('status').textContent=d.message;await load();}catch(e){el('status').textContent=e.message;try{await load();}catch{} }finally{busy=false;}};
</script></body></html>'''
