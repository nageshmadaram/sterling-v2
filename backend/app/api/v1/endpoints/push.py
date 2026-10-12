import hashlib
import json
import re
import time
from typing import Literal
from fastapi import APIRouter,Depends,HTTPException
from pydantic import BaseModel,Field
from app.core.auth import get_current_user,UserContext
from app.core.security import encrypt
from app.services import db
from app.services.notifications import web_push as push
router=APIRouter(prefix='/push',tags=['push'])

def uid(user):return user.owner_uid or user.user_id

def require_db():
    if not db.is_available():raise HTTPException(503,'Push database unavailable')

@router.get('')
def status(user:UserContext=Depends(get_current_user)):
    require_db();public,_=push.vapid()
    return {'public_key':public,'config':push.config(uid(user)),'devices':push.devices(uid(user))}

class PushConfig(BaseModel):
    enabled:bool=False
    signals:bool=True
    pcr:bool=True
    pcr_indices:list[Literal['NIFTY','BANKNIFTY','FINNIFTY','SENSEX','MIDCPNIFTY']]=Field(default_factory=lambda:['NIFTY','SENSEX'],max_length=5)
    alerts:bool=True
    quiet_enabled:bool=False
    quiet_start:str='22:00'
    quiet_end:str='08:00'
    private_preview:bool=True
    max_per_hour:int=Field(default=12,ge=1,le=100)

@router.put('/config')
def configure(body:PushConfig,user:UserContext=Depends(get_current_user)):
    require_db()
    if not all(re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d',v) for v in [body.quiet_start,body.quiet_end]):raise HTTPException(422,'Invalid quiet hours')
    with db._conn() as c:
        push.init(c);c.execute('INSERT INTO push_config VALUES(?,?) ON CONFLICT(uid) DO UPDATE SET data=excluded.data',(uid(user),json.dumps(body.model_dump())))
    return body

class Subscription(BaseModel):
    subscription:dict
    label:str=Field(default='This browser',max_length=60)

@router.post('/subscribe')
def subscribe(body:Subscription,user:UserContext=Depends(get_current_user)):
    require_db()
    try:s=push.validate_subscription(body.subscription)
    except Exception:raise HTTPException(422,'Invalid browser push subscription')
    identifier=hashlib.sha256(s['endpoint'].encode()).hexdigest()
    with db._conn() as c:
        push.init(c)
        count=c.execute('SELECT count(*) FROM push_devices WHERE uid=?',(uid(user),)).fetchone()[0]
        if count>=16 and not c.execute('SELECT 1 FROM push_devices WHERE id=?',(identifier,)).fetchone():raise HTTPException(409,'Device limit reached. Remove an old device first.')
        prior=c.execute('SELECT uid FROM push_devices WHERE id=?',(identifier,)).fetchone()
        if prior and prior['uid']!=uid(user):c.execute('DELETE FROM push_deliveries WHERE device_id=?',(identifier,))
        c.execute('INSERT INTO push_devices(id,uid,label,subscription_enc,enabled,created_ms) VALUES(?,?,?,?,1,?) ON CONFLICT(id) DO UPDATE SET uid=excluded.uid,label=excluded.label,subscription_enc=excluded.subscription_enc,enabled=1',(identifier,uid(user),body.label,encrypt(json.dumps(s)),int(time.time()*1000)))
    return {'id':identifier}

@router.delete('/devices/{device_id}')
def remove(device_id:str,user:UserContext=Depends(get_current_user)):
    require_db()
    with db._conn() as c:
        push.init(c);c.execute('DELETE FROM push_devices WHERE id=? AND uid=?',(device_id,uid(user)))
    return {'removed':True}

@router.post('/test')
async def test_push(user:UserContext=Depends(get_current_user)):
    require_db();count=await push.send(uid(user),'test:'+str(time.time_ns()),'Sterling test notification','Your device can receive Sterling updates.','alerts',force=True)
    return {'delivered':count}
