"""Durable, tenant-scoped stream catalogue and low-rate availability audits.

A failed browser attempt queues verification of every known source. Uncertain
network failures never quarantine a station. One global worker, bounded reads,
public-IP-only DNS resolution, and monthly due dates keep audits off playback.
"""
from __future__ import annotations
import asyncio
import hashlib
import ipaddress
import json
import socket
import time
from urllib.parse import urljoin, urlsplit
import aiohttp
from app.services import db

MONTH = 30 * 86400
MAX_CATALOGUE_BYTES = 64 * 1024 * 1024
MAX_TOTAL_BYTES = 512 * 1024 * 1024
_worker: asyncio.Task | None = None
_wake: asyncio.Event | None = None
_idle = False


def schema(c):
    c.execute('''CREATE TABLE IF NOT EXISTS media_stream_health (
      owner TEXT NOT NULL,feature TEXT NOT NULL,station_key TEXT NOT NULL,
      name TEXT NOT NULL,sources TEXT NOT NULL,signature TEXT NOT NULL,
      status TEXT NOT NULL DEFAULT 'available',reason TEXT NOT NULL DEFAULT 'Not checked',
      checked_at INTEGER NOT NULL DEFAULT 0,next_check INTEGER NOT NULL DEFAULT 0,
      updated_at INTEGER NOT NULL,PRIMARY KEY(owner,feature,station_key))''')
    c.execute('CREATE INDEX IF NOT EXISTS ix_media_health_due ON media_stream_health(next_check)')
    c.execute('CREATE TABLE IF NOT EXISTS media_health_catalogues(owner TEXT,feature TEXT,digest TEXT,byte_count INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(owner,feature))')
    columns={row['name'] for row in c.execute('PRAGMA table_info(media_stream_health)')}
    for name,definition in [('listed','INTEGER NOT NULL DEFAULT 1'),('seen_digest',"TEXT NOT NULL DEFAULT ''")]:
        if name not in columns: c.execute(f'ALTER TABLE media_stream_health ADD COLUMN {name} {definition}')
    if 'byte_count' not in {row['name'] for row in c.execute('PRAGMA table_info(media_health_catalogues)')}:
        c.execute('ALTER TABLE media_health_catalogues ADD COLUMN byte_count INTEGER NOT NULL DEFAULT 0')
        c.execute('UPDATE media_health_catalogues SET byte_count=COALESCE((SELECT SUM(length(sources)+length(name)+length(station_key)) FROM media_stream_health WHERE owner=media_health_catalogues.owner AND feature=media_health_catalogues.feature),0)')


def sync(owner, feature, stations, digest=None, complete=False):
    now = int(time.time())
    with db._conn() as c:
        schema(c)
        c.execute('BEGIN IMMEDIATE')
        c.execute('INSERT OR IGNORE INTO media_health_catalogues(owner,feature,digest) VALUES(?,?,NULL)',(owner,feature))
        used=c.execute('SELECT byte_count FROM media_health_catalogues WHERE owner=? AND feature=?',(owner,feature)).fetchone()[0]
        total=c.execute('SELECT COALESCE(SUM(byte_count),0) FROM media_health_catalogues').fetchone()[0]
        count = c.execute('SELECT COUNT(*) FROM media_stream_health WHERE owner=? AND feature=?', (owner, feature)).fetchone()[0]
        for station in stations:
            urls = list(dict.fromkeys(station['sources']))
            if not urls: continue
            signature = hashlib.sha256(json.dumps(sorted(urls)).encode()).hexdigest()
            prior = c.execute('SELECT signature,name,sources FROM media_stream_health WHERE owner=? AND feature=? AND station_key=?', (owner,feature,station['key'])).fetchone()
            if prior is None and count >= 100000: raise ValueError('Catalogue limit reached.')
            source_json=json.dumps(urls)
            size=lambda name,sources:len(name.encode())+len(sources.encode())+len(station['key'].encode())
            delta=size(station['name'],source_json)-(size(prior['name'],prior['sources']) if prior else 0)
            if used+delta>MAX_CATALOGUE_BYTES or total+delta>MAX_TOTAL_BYTES: raise ValueError('Catalogue storage limit reached.')
            used+=delta;total+=delta
            c.execute('''INSERT INTO media_stream_health(owner,feature,station_key,name,sources,signature,updated_at,listed,seen_digest)
                VALUES(?,?,?,?,?,?,?,1,?) ON CONFLICT(owner,feature,station_key) DO UPDATE SET
                name=excluded.name,sources=excluded.sources,signature=excluded.signature,listed=1,
                seen_digest=CASE WHEN excluded.seen_digest!='' THEN excluded.seen_digest ELSE media_stream_health.seen_digest END,
                next_check=CASE WHEN media_stream_health.signature!=excluded.signature THEN 0 ELSE media_stream_health.next_check END,
                updated_at=excluded.updated_at''', (owner,feature,station['key'],station['name'],source_json,signature,now,digest or ''))
            if prior is None: count += 1
        c.execute('UPDATE media_health_catalogues SET byte_count=? WHERE owner=? AND feature=?',(used,owner,feature))
        if complete and digest:
            c.execute('UPDATE media_health_catalogues SET digest=? WHERE owner=? AND feature=?',(digest,owner,feature))
            c.execute('UPDATE media_stream_health SET listed=0 WHERE owner=? AND feature=? AND seen_digest!=?',(owner,feature,digest))
    return {'saved':len(stations)}


def listing(owner, feature):
    with db._conn() as c:
        schema(c)
        totals = c.execute('SELECT COUNT(*) total,SUM(status=\'review\') review,SUM(next_check<=?) due FROM media_stream_health WHERE owner=? AND feature=? AND listed=1', (int(time.time()),owner,feature)).fetchone()
        rows = c.execute("SELECT station_key AS key,name,status,reason,checked_at FROM media_stream_health WHERE owner=? AND feature=? AND status='review' AND listed=1 ORDER BY name", (owner,feature)).fetchall()
        stamp=c.execute('SELECT digest FROM media_health_catalogues WHERE owner=? AND feature=?',(owner,feature)).fetchone()
        return {'digest':stamp['digest'] if stamp else None,'total':totals['total'],'due':totals['due'] or 0,'review': [dict(row) for row in rows], 'interval_days':30}


def update(owner, feature, key, action):
    now = int(time.time())
    with db._conn() as c:
        schema(c)
        where='owner=? AND feature=?';args=[owner,feature]
        if key is not None: where+=' AND station_key=?';args.append(key)
        if action in ('review','restore','playing'):
            if key is None: raise ValueError('Select a station.')
            status='review' if action=='review' else 'available'
            reason='Moved manually' if action!='playing' else 'Playback confirmed on this device'
            c.execute(f'UPDATE media_stream_health SET status=?,reason=?,next_check=? WHERE {where}',[status,reason,now+MONTH,*args])
        else:
            # Failure only schedules an audit; it cannot quarantine untried backups.
            c.execute(f'UPDATE media_stream_health SET next_check=? WHERE {where}',[-now,*args])
    return listing(owner,feature)


class PublicResolver(aiohttp.abc.AbstractResolver):
    """Validate the actual IPs handed to the connector, including redirects."""
    async def resolve(self, host, port=0, family=socket.AF_INET):
        records=await asyncio.get_running_loop().getaddrinfo(host,port,type=socket.SOCK_STREAM,family=family)
        if not records or any(not ipaddress.ip_address(row[4][0]).is_global for row in records):
            raise ValueError('Only public stream addresses may be checked.')
        return [{'hostname':host,'host':row[4][0],'port':port,'family':row[0],'proto':row[2],'flags':socket.AI_NUMERICHOST} for row in records]
    async def close(self): pass


def public_url(url):
    u=urlsplit(url)
    if u.scheme not in ('http','https') or not u.hostname or u.username or u.password: raise ValueError('Invalid stream URL')
    try: address=ipaddress.ip_address(u.hostname)
    except ValueError: address=None
    if address is not None and not address.is_global: raise ValueError('Private stream address')
    return url


async def probe_source(session, url, depth=0):
    """Validate playlists and a referenced media response; never download a stream."""
    if depth>5: return None
    try:
        public_url(url)
        async with session.get(url,allow_redirects=False,headers={'Range':'bytes=0-65535'}) as response:
            if response.status in (301,302,303,307,308):
                location=response.headers.get('Location')
                response.release()
                return await probe_source(session,urljoin(url,location),depth+1) if location else False
            if response.status in (401,403,408,429,500,502,503,504): return None
            if response.status not in (200,206): return False
            body=await response.content.read(65536)
            mime=response.headers.get('Content-Type','').lower()
            if 'mpegurl' in mime or body.startswith((b'#EXTM3U',b'[playlist]')):
                while len(body)<65536 and not response.content.at_eof():
                    chunk=await response.content.read(65536-len(body))
                    if not chunk: break
                    body+=chunk
            response.release()
            if not body: return False
            text=body.decode('utf-8',errors='replace').strip()
            if text.startswith('#EXTM3U'):
                entries=[line.strip() for line in text.splitlines() if line.strip() and not line.startswith('#')]
                return await probe_source(session,urljoin(url,entries[0]),depth+1) if entries else False
            if text.startswith('[playlist]'):
                entries=[line.split('=',1)[1].strip() for line in text.splitlines() if line.lower().startswith('file') and '=' in line]
                return await probe_source(session,urljoin(url,entries[0]),depth+1) if entries else False
            if 'text/html' in mime or text.lower().startswith(('<!doctype html','<html')): return False
            if any(x in mime for x in ('audio/','video/','application/ogg')) or body[:3]==b'ID3' or body[:4]==b'OggS' or (len(body)>188 and body[0]==0x47): return True
            return None  # Unknown format is inconclusive, never a deletion signal.
    except (ValueError, aiohttp.ClientError, asyncio.TimeoutError, OSError): return None


async def audit(row, session):
    results=[]
    try:
        async with asyncio.timeout(120):
            for url in json.loads(row['sources']):
                result=await probe_source(session,url);results.append(result)
                if result is True: break
    except asyncio.TimeoutError: results.append(None)
    now=int(time.time())
    healthy=any(x is True for x in results)
    failed=bool(results) and all(x is False for x in results)
    status='available' if healthy else 'review' if failed else row['status']
    reason='A source responded with media' if healthy else 'Every known source failed verification' if failed else 'Check inconclusive; retry scheduled'
    due=now+(MONTH if healthy or failed else 86400)
    with db._conn() as c:
        schema(c)
        # A changed source set or a manual action during I/O invalidates this result.
        c.execute('''UPDATE media_stream_health SET status=?,reason=?,checked_at=?,next_check=?
            WHERE owner=? AND feature=? AND station_key=? AND signature=? AND next_check=?''',
            (status,reason,now,due,row['owner'],row['feature'],row['station_key'],row['signature'],row['next_check']))


async def run_worker():
    global _idle
    connector=aiohttp.TCPConnector(resolver=PublicResolver(),limit=1,ttl_dns_cache=0)
    async with aiohttp.ClientSession(connector=connector,timeout=aiohttp.ClientTimeout(total=8),trust_env=False,headers={'User-Agent':'Sterling-Stream-Health/1.0'}) as session:
        while True:
            try:
                with db._conn() as c:
                    schema(c)
                    c.execute('BEGIN IMMEDIATE')
                    row=c.execute('SELECT h.* FROM media_stream_health h JOIN users u ON u.id=h.owner AND u.is_active=1 JOIN user_feature_grants g ON g.user_id=h.owner AND g.feature=h.feature AND g.enabled=1 WHERE h.listed=1 AND h.next_check<=? ORDER BY h.next_check,h.checked_at LIMIT 1',(int(time.time()),)).fetchone()
                    if row:
                        row=dict(row);lease=int(time.time())+180
                        c.execute('UPDATE media_stream_health SET next_check=? WHERE owner=? AND feature=? AND station_key=?',(lease,row['owner'],row['feature'],row['station_key']))
                        row['next_check']=lease
                if row: await audit(dict(row),session)
                if row: await asyncio.sleep(5)
                else:
                    _wake.clear();_idle=True
                    try: await asyncio.wait_for(_wake.wait(),timeout=60)
                    except asyncio.TimeoutError: pass
                    finally: _idle=False
            except asyncio.CancelledError: raise
            except Exception:
                await asyncio.sleep(60)  # DB/network outage must not spin or affect playback.


def ensure_worker():
    global _worker,_wake
    if _worker is None or _worker.done():
        _wake=asyncio.Event();_worker=asyncio.create_task(run_worker(),name='media-stream-health')
    elif _idle: _wake.set()


async def stop_worker():
    global _worker
    if _worker:
        _worker.cancel()
        try: await _worker
        except asyncio.CancelledError: pass
        _worker=None
