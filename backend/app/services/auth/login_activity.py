"""Successful logins and recent authenticated browser activity, never tokens."""
import hashlib
import ipaddress
import time
from app.services import db

_seen = {}

def migrate(c):
    c.execute('''CREATE TABLE IF NOT EXISTS user_login_activity (
        user_id TEXT NOT NULL, fingerprint TEXT NOT NULL, token_version INTEGER NOT NULL,
        browser TEXT NOT NULL, os TEXT NOT NULL, device TEXT NOT NULL, ip TEXT NOT NULL,
        country TEXT NOT NULL, user_agent TEXT NOT NULL, first_seen_ms INTEGER NOT NULL,
        last_seen_ms INTEGER NOT NULL, last_login_ms INTEGER, access_expires_ms INTEGER NOT NULL,
        PRIMARY KEY(user_id,fingerprint,token_version))''')


def metadata(request):
    peer = request.client.host if request.client else ''
    ip = peer
    country = ''
    if peer in ('127.0.0.1','::1'):
        forwarded = request.headers.get('x-sterling-client-ip','')
        try: ip = str(ipaddress.ip_address(forwarded))
        except ValueError: pass
        proposed = request.headers.get('x-sterling-client-country','')
        if len(proposed)==2 and proposed.isalpha() and proposed not in ('XX','T1'): country=proposed.upper()
    ua = request.headers.get('user-agent','')[:512]
    browser = next((label for marker,label in [('Edg/','Edge'),('OPR/','Opera'),('Firefox/','Firefox'),('FxiOS/','Firefox'),('Chrome/','Chrome'),('CriOS/','Chrome'),('Safari/','Safari')] if marker in ua),'Unknown')
    os_name = next((label for marker,label in [('Android','Android'),('iPhone','iOS'),('iPad','iPadOS'),('Windows','Windows'),('Mac OS','macOS'),('Linux','Linux')] if marker in ua),'Unknown')
    device='Mobile' if any(word in ua for word in ('Mobile','Android','iPhone','iPad')) or request.headers.get('sec-ch-ua-mobile')=='?1' else 'Desktop'
    fingerprint=hashlib.sha256((ua+'\n'+ip).encode()).hexdigest()
    return fingerprint,browser,os_name,device,ip,country,ua


def record(user_id, version, request, *, login=False, expires_ms=0):
    now=int(time.time()*1000)
    meta=metadata(request)
    key=(user_id,version,meta[0])
    if not login and now-_seen.get(key,0)<60_000: return
    try:
        with db._conn() as c:
            c.execute('''INSERT INTO user_login_activity VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(user_id,fingerprint,token_version) DO UPDATE SET
                last_seen_ms=excluded.last_seen_ms,access_expires_ms=excluded.access_expires_ms,
                last_login_ms=COALESCE(excluded.last_login_ms,user_login_activity.last_login_ms)''',
                (user_id,meta[0],version,*meta[1:],now,now,now if login else None,expires_ms))
        if len(_seen)>4096: _seen.clear()
        _seen[key]=now
    except Exception:
        # Activity capture must never break an authenticated trading/exit request.
        pass


def for_user(c,user_id):
    now=int(time.time()*1000)
    rows=c.execute('''SELECT a.browser,a.os,a.device,a.ip,a.country,a.first_seen_ms,a.last_seen_ms,a.last_login_ms,
        (u.is_active=1 AND u.token_version=a.token_version AND a.access_expires_ms>? AND a.last_seen_ms>?) AS online
        FROM user_login_activity a JOIN users u ON u.id=a.user_id WHERE a.user_id=? ORDER BY a.last_seen_ms DESC LIMIT 20''', (now,now-120_000,user_id)).fetchall()
    return [dict(row) for row in rows]
