"""Sterling identity profile, separate from the connected broker identity."""
import base64
import binascii
import re
import sqlite3
import time
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from app.core.auth import UserContext, get_current_user
from app.core.passwords import verify_password, hash_password
from app.core.rate_limit import check_login
from app.core import tokens
from app.services import db
from app.services.auth import user_store
router=APIRouter(prefix='/profile',tags=['profile'])

def table(c):
    c.execute("CREATE TABLE IF NOT EXISTS user_profiles(user_id TEXT PRIMARY KEY,display_name TEXT NOT NULL DEFAULT '',photo TEXT NOT NULL DEFAULT '',updated_ms INTEGER NOT NULL)")

def account(ctx):
    uid=ctx.owner_uid or ctx.user_id
    u=user_store.get_by_id(uid)
    if not u: raise HTTPException(401,'Sterling account unavailable')
    if not db.is_available(): raise HTTPException(503,'Account database unavailable')
    return u

def profile(u):
    with db._conn() as c:
        table(c);r=c.execute('SELECT display_name,photo FROM user_profiles WHERE user_id=?',(u.id,)).fetchone()
    return {'username':u.username,'display_name':r['display_name'] if r else '', 'photo':r['photo'] if r else '', 'role':u.role,'user_id':u.id}

@router.get('')
def get_profile(ctx:UserContext=Depends(get_current_user)):
    return profile(account(ctx))

class ProfileEdit(BaseModel):
    display_name:str=Field(max_length=80)
    photo:str=Field(default='',max_length=280000)

@router.patch('')
def edit_profile(body:ProfileEdit,ctx:UserContext=Depends(get_current_user)):
    u=account(ctx)
    if body.photo:
        match=re.fullmatch(r'data:image/(png|jpeg|webp);base64,([A-Za-z0-9+/=]+)',body.photo)
        if not match: raise HTTPException(422,'Use a PNG, JPEG or WebP image')
        try: raw=base64.b64decode(match[2],validate=True)
        except (ValueError,binascii.Error): raise HTTPException(422,'Invalid image')
        if len(raw)>200000 or not (raw.startswith(b'\x89PNG\r\n\x1a\n') or raw.startswith(b'\xff\xd8\xff') or (raw.startswith(b'RIFF') and raw[8:12]==b'WEBP')): raise HTTPException(422,'Invalid or oversized image')
    with db._conn() as c:
        table(c);c.execute('INSERT INTO user_profiles VALUES(?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET display_name=excluded.display_name,photo=excluded.photo,updated_ms=excluded.updated_ms',(u.id,body.display_name.strip(),body.photo,int(time.time()*1000)))
    return profile(u)

class SecurityEdit(BaseModel):
    current_password:str=Field(min_length=1,max_length=256)
    username:str|None=None
    new_password:str|None=Field(default=None,min_length=12,max_length=256)

@router.post('/security')
def edit_security(body:SecurityEdit,request:Request,ctx:UserContext=Depends(get_current_user)):
    check_login(request);u=account(ctx)
    if not verify_password(body.current_password,u.password_hash):raise HTTPException(400,'Current password is incorrect')
    name=body.username.strip() if body.username is not None else u.username
    if body.username is not None and name!=u.username and not re.fullmatch(r'[A-Za-z0-9_.-]{3,48}',name):raise HTTPException(422,'Username must be 3–48 letters, digits, dots, dashes or underscores')
    if body.new_password and user_store._is_weak_admin_password(body.new_password):raise HTTPException(422,'Choose a stronger password of at least 12 characters')
    password=hash_password(body.new_password) if body.new_password else u.password_hash
    version=u.token_version+1
    try:
        with db._conn() as c:
            c.execute('BEGIN IMMEDIATE')
            c.execute('UPDATE users SET username=?,password_hash=?,token_version=?,updated_at_ms=? WHERE id=? AND token_version=?',(name,password,version,int(time.time()*1000),u.id,u.token_version))
            if c.execute('SELECT changes()').fetchone()[0]!=1:raise HTTPException(409,'Account changed; sign in again before retrying')
    except sqlite3.IntegrityError:raise HTTPException(409,'Username is already in use')
    u.username=name;u.password_hash=password;u.token_version=version
    return {'profile':profile(u),'access_token':tokens.mint_access(u.id,u.username,u.role,version),'refresh_token':tokens.mint_refresh(u.id,u.username,u.role,version),'token_type':'bearer'}

@router.post('/revoke-sessions')
def revoke(ctx:UserContext=Depends(get_current_user)):
    u=account(ctx)
    with db._conn() as c:
        c.execute('UPDATE users SET token_version=token_version+1 WHERE id=?',(u.id,))
        exists=c.execute("SELECT 1 FROM sqlite_master WHERE name='push_devices'").fetchone()
        if exists:c.execute('UPDATE push_devices SET enabled=0 WHERE uid=?',(u.id,))
    u.token_version+=1
    return {'revoked':True}
