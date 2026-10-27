"""Independent opt-in Web Push worker. Never places orders or sends Telegram."""
import asyncio
import base64
import hashlib
import json
import os
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from urllib.parse import urlsplit, urlencode
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives import serialization
from app.core.async_work import run_blocking
from app.core.security import encrypt, decrypt
from app.services import db

DEFAULTS={'enabled':False,'signals':True,'pcr':True,'pcr_indices':['NIFTY','SENSEX'],'alerts':True,'quiet_start':'22:00','quiet_end':'08:00','quiet_enabled':False,'private_preview':False,'max_per_hour':12}

def init(c):
    c.execute('CREATE TABLE IF NOT EXISTS push_vapid(id INTEGER PRIMARY KEY,key_enc TEXT NOT NULL)')
    c.execute("CREATE TABLE IF NOT EXISTS push_config(uid TEXT PRIMARY KEY,data TEXT NOT NULL)")
    c.execute("CREATE TABLE IF NOT EXISTS push_devices(id TEXT PRIMARY KEY,uid TEXT NOT NULL,label TEXT NOT NULL,subscription_enc TEXT NOT NULL,enabled INTEGER NOT NULL,created_ms INTEGER NOT NULL,last_delivery_ms INTEGER NOT NULL DEFAULT 0,last_error TEXT NOT NULL DEFAULT '')")
    c.execute('CREATE TABLE IF NOT EXISTS push_deliveries(device_id TEXT NOT NULL,event_key TEXT NOT NULL,delivered_ms INTEGER NOT NULL,PRIMARY KEY(device_id,event_key))')

def vapid():
    if not db.is_available():raise RuntimeError('Push database unavailable')
    with db._conn() as c:
        init(c);c.execute('BEGIN IMMEDIATE');row=c.execute('SELECT key_enc FROM push_vapid WHERE id=1').fetchone()
        if row:key=serialization.load_pem_private_key(decrypt(row[0]).encode(),password=None)
        else:
            key=ec.generate_private_key(ec.SECP256R1());pem=key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()).decode();c.execute('INSERT INTO push_vapid VALUES(1,?)',(encrypt(pem),))
    pub=key.public_key().public_bytes(serialization.Encoding.X962,serialization.PublicFormat.UncompressedPoint)
    pem=key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()).decode()
    return base64.urlsafe_b64encode(pub).decode().rstrip('='),pem

def config(uid):
    with db._conn() as c:
        init(c);r=c.execute('SELECT data FROM push_config WHERE uid=?',(uid,)).fetchone()
    return {**DEFAULTS,**(json.loads(r[0]) if r else {})}

def devices(uid):
    with db._conn() as c:
        init(c);return [dict(r) for r in c.execute('SELECT id,label,enabled,created_ms,last_delivery_ms,last_error FROM push_devices WHERE uid=?',(uid,)).fetchall()]

def validate_subscription(s):
    endpoint=str(s.get('endpoint') or '');u=urlsplit(endpoint)
    allowed=('fcm.googleapis.com','updates.push.services.mozilla.com','web.push.apple.com')
    host=u.hostname or ''
    if u.scheme!='https' or u.username or u.password or u.port not in (None,443) or not any(host==h or host.endswith('.'+h) for h in allowed):raise ValueError('Unsupported push service endpoint')
    keys=s.get('keys') or {}
    decode=lambda value:base64.urlsafe_b64decode(str(value)+'='*((4-len(str(value))%4)%4))
    pub=decode(keys.get('p256dh',''));auth=decode(keys.get('auth',''))
    if len(auth)!=16:raise ValueError('Invalid push authentication key')
    ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(),pub)
    return {'endpoint':endpoint,'keys':{'p256dh':keys['p256dh'],'auth':keys['auth']}}

def quiet(cfg,now=None):
    if not cfg['quiet_enabled']:return False
    clock=(now or datetime.now(ZoneInfo('Asia/Kolkata'))).strftime('%H:%M')
    start,end=cfg['quiet_start'],cfg['quiet_end']
    return start<=clock<end if start<end else clock>=start or clock<end

def notification_url(category, *, strategy="", signal_id="", symbol="", at_ms=0):
    """Only known, read-only workspace routes; never accept arbitrary URLs."""
    if category not in {"signals", "pcr", "alerts"}:
        return "/"
    params = {"view": "live", "notification": category}
    if category == "signals":
        permitted = {"kite_supertrend": "supertrend", "supertrend": "supertrend",
                     "snapback": "snapback", "gamma_move": "gamma_move",
                     "ema_trend": "ema_trend"}
        engine = permitted.get(str(strategy))
        if engine:
            params["strategy"] = engine
        if signal_id and len(str(signal_id)) <= 180:
            params["signal"] = str(signal_id)
        if symbol and len(str(symbol)) <= 80:
            params["symbol"] = str(symbol)
        if str(at_ms).isdigit() and 0 < int(at_ms) < 10**14:
            params["at"] = str(at_ms)
    return "/?" + urlencode(params)


async def send(uid,event_key,title,body,category,*,force=False,
               strategy="",signal_id="",symbol="",at_ms=0):
    return await run_blocking(_send,uid,event_key,title,body,category,
                              force=force,strategy=strategy,signal_id=signal_id,symbol=symbol,at_ms=at_ms)

def _send(uid,event_key,title,body,category,*,force=False,
          strategy="",signal_id="",symbol="",at_ms=0):
    from pywebpush import webpush,WebPushException
    from py_vapid import Vapid
    cfg=config(uid)
    if not force and (not cfg['enabled'] or not cfg.get(category) or quiet(cfg)):return 0
    pub,pem=vapid();count=0
    with db._conn() as c:
        init(c);rows=c.execute('SELECT * FROM push_devices WHERE uid=? AND enabled=1',(uid,)).fetchall()
    for row in rows:
        with db._conn() as c:
            if c.execute('SELECT 1 FROM push_deliveries WHERE device_id=? AND event_key=?',(row['id'],event_key)).fetchone():continue
            recent=c.execute('SELECT COUNT(*) FROM push_deliveries WHERE device_id=? AND delivered_ms>?',(row['id'],int(time.time()*1000)-3600000)).fetchone()[0]
            if recent>=(20 if force else cfg['max_per_hour']):continue
        # The operator requested useful message content, rather than the
        # previous generic lock-screen placeholder. Do not include tokens,
        # account credentials or actionable broker URLs in this payload.
        payload={'title':str(title)[:120], 'body':str(body)[:620],
                 'tag':str(event_key)[:160],
                 'url':notification_url(category,strategy=strategy,
                                        signal_id=signal_id,symbol=symbol,at_ms=at_ms)}
        try:
            webpush(subscription_info=json.loads(decrypt(row['subscription_enc'])),data=json.dumps(payload),vapid_private_key=Vapid.from_pem(pem.encode()),vapid_claims={'sub':os.environ.get('STERLING_PUSH_SUBJECT','https://sterling.local')},ttl=300,timeout=8)
            with db._conn() as c:
                # An accepted send must not recreate a removed device's dedupe
                # or record the old owner's event against a transferred device.
                c.execute('INSERT OR IGNORE INTO push_deliveries SELECT ?,?,? WHERE EXISTS (SELECT 1 FROM push_devices WHERE id=? AND uid=?)',(row['id'],event_key,int(time.time()*1000),row['id'],uid))
                c.execute("UPDATE push_devices SET last_delivery_ms=?,last_error='' WHERE id=? AND uid=? AND subscription_enc=?",(int(time.time()*1000),row['id'],uid,row['subscription_enc']))
            count+=1
        except WebPushException as exc:
            status=getattr(getattr(exc,'response',None),'status_code',0)
            # A browser may refresh/transfer its registration while HTTP is in
            # flight. An old failure must not disable or taint that new state.
            with db._conn() as c:c.execute('UPDATE push_devices SET enabled=CASE WHEN ? THEN 0 ELSE enabled END,last_error=? WHERE id=? AND uid=? AND subscription_enc=?',(status in (404,410),f'Push delivery failed ({status or "network"})',row['id'],uid,row['subscription_enc']))
        except Exception:
            with db._conn() as c:c.execute("UPDATE push_devices SET last_error='Push delivery unavailable' WHERE id=? AND uid=? AND subscription_enc=?",(row['id'],uid,row['subscription_enc']))
    return count

def _enabled_uids():
    with db._conn() as c:
        init(c)
        return [r[0] for r in c.execute('SELECT DISTINCT uid FROM push_devices WHERE enabled=1').fetchall()]

def _recent_alerts(uid):
    with db._conn() as c:
        exists=c.execute("SELECT 1 FROM sqlite_master WHERE name='telegram_lifecycle_outbox'").fetchone()
        return c.execute('SELECT event_id,title,detail FROM telegram_lifecycle_outbox WHERE uid=? AND created_at_ms>? ORDER BY created_at_ms DESC LIMIT 20',(uid,int(time.time()*1000)-5*60000)).fetchall() if exists else []

def _prune_deliveries():
    with db._conn() as c:
        c.execute('DELETE FROM push_deliveries WHERE delivered_ms<?',(int(time.time()*1000)-7*86400000,))

async def tick():
    from app.services.kite_engine.market_hours import is_market_open
    from app.services.notifications import telegram_kite as signals
    from app.api.v1.endpoints.pcr import pcr_session
    from app.core.auth import UserContext
    uids=await run_blocking(_enabled_uids)
    from app.services.auth import user_store
    for uid in uids:
        account=await run_blocking(user_store.get_by_id,uid)
        if not account or not account.is_active:continue
        cfg=await run_blocking(config,uid)
        if not cfg['enabled'] or quiet(cfg):continue
        if cfg['signals'] and is_market_open():
            for collect in (signals._supertrend_alerts,signals._gamma_move_alerts,signals._snapback_alerts):
                for event in await run_blocking(collect,uid):
                    if signals._is_recent(event.timestamp_ms,max_age_ms=5*60000):
                        summary = " · ".join(part for part in
                                           (event.direction, event.option_symbol, event.detail)
                                           if part)
                        await send(uid,event.dedupe_key,
                                   event.strategy_label+' · '+event.title,
                                   summary or event.direction,'signals',
                                   strategy=event.strategy,signal_id=event.signal_id,
                                   symbol=event.title,at_ms=event.timestamp_ms)
        if cfg['pcr'] and is_market_open():
            payload=await pcr_session(UserContext(uid))
            if payload.get('source')=='live':
                for symbol,row in payload.get('series',{}).items():
                    if symbol not in cfg.get('pcr_indices',['NIFTY','SENSEX']):continue
                    marks=row.get('marks') or [];last=marks[-1] if marks else {};clock=last.get('hhmm');ratio=last.get('pcr')
                    if clock:
                        printed=datetime.fromisoformat(payload['asOf'][:10]+'T'+clock).replace(tzinfo=ZoneInfo('Asia/Kolkata'))
                        if not 0<=time.time()-printed.timestamp()<=20*60:continue
                    if clock and ratio is not None:await send(uid,f'pcr:{payload["asOf"][:10]}:{symbol}:{clock}',f'{symbol} PCR · {clock}',f'PCR {ratio:.2f}. Open the desk for context.','pcr')
        if cfg['alerts']:
            events=await run_blocking(_recent_alerts,uid)
            for event in events:await send(uid,'alert:'+event['event_id'],event['title'],event['detail'],'alerts')
    await run_blocking(_prune_deliveries)

async def run():
    while True:
        try:
            if db.is_available():await tick()
        except asyncio.CancelledError:raise
        except Exception:pass  # delivery failure must never disrupt trading
        await asyncio.sleep(30)
