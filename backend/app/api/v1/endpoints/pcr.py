"""Intraday + weekly Put-Call Ratio — public market data, no session."""
from __future__ import annotations

import asyncio
import re
import time
from datetime import datetime
from zoneinfo import ZoneInfo
from app.services.kite_engine.market_hours import session_phase, is_trading_day, calendar_covers
from typing import Any

import httpx
from fastapi import APIRouter, Depends

from app.core.auth import UserContext, get_current_user

router = APIRouter(prefix="/pcr", tags=["pcr"])

PAGES = {
    "NIFTY": "https://www.niftytrader.in/nifty-put-call-ratio",
    "BANKNIFTY": "https://www.niftytrader.in/banknifty-put-call-ratio",
    "FINNIFTY": "https://www.niftytrader.in/finnifty-put-call-ratio",
    "SENSEX": "https://www.niftytrader.in/sensex-put-call-ratio",
    "MIDCPNIFTY": "https://www.niftytrader.in/midcpnifty-put-call-ratio",
}

SLOT_HHMM = [
    f"{h:02d}:{m:02d}"
    for h, m in (
        (9, 15), (9, 30), (9, 45),
        (10, 0), (10, 15), (10, 30), (10, 45),
        (11, 0), (11, 15), (11, 30), (11, 45),
        (12, 0), (12, 15), (12, 30), (12, 45),
        (13, 0), (13, 15), (13, 30), (13, 45),
        (14, 0), (14, 15), (14, 30), (14, 45),
        (15, 0), (15, 15), (15, 30),
    )
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/json",
    "Referer": "https://www.niftytrader.in/",
}

_CACHE: dict[str, Any] = {"at": 0.0, "payload": None}
_CACHE_S = 8.0
# A scrape that finds nothing live used to be uncached, so every poll (and every
# concurrent caller) re-fetched five third-party pages. Cache the miss briefly and
# let one caller scrape while the rest wait for its answer.
_MISS_CACHE_S = 20.0
_history_retry_at = 0.0
_scrape_lock: tuple[Any, asyncio.Lock] | None = None  # (event loop, lock)
_NEXT = re.compile(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>')


def _num(v: Any) -> float | None:
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    if n != n:  # NaN
        return None
    return n


def _hhmm(time_s: str) -> str:
    if "T" in time_s and len(time_s) >= 16:
        return time_s[11:16]
    return time_s[:5]


def _mins(hhmm: str) -> int:
    try:
        return int(hhmm[:2]) * 60 + int(hhmm[3:5])
    except (TypeError, ValueError):
        return -1


def _marks(ticks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Last print in each 15-minute bucket.

    NiftyTrader now emits ~1-minute rows (09:10, 09:11, …) instead of only the
    15-minute clock. Matching the clock exactly dropped every row on those
    days, the session looked empty, and the desk froze on yesterday's close.
    """
    ordered = sorted(ticks, key=lambda t: str(t.get("time") or ""))
    out: list[dict[str, Any]] = []
    for i, hhmm in enumerate(SLOT_HHMM):
        end = _mins(hhmm)
        if ordered and end > max(_mins(_hhmm(str(t.get("time") or ""))) for t in ordered):
            continue
        start = 9 * 60 + 10 if i == 0 else _mins(SLOT_HHMM[i - 1]) + 1
        hit = None
        for t in ordered:
            m = _mins(_hhmm(str(t.get("time") or "")))
            if start <= m <= end:
                hit = t
        if not hit:
            continue
        out.append({
            "hhmm": hhmm,
            "pcr": hit["pcr"],
            "volumePcr": hit["volumePcr"],
            "changeOiPcr": hit["changeOiPcr"],
            "indexClose": hit["indexClose"],
        })
    return out


def _latest(ticks: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not ticks:
        return None
    last = max(ticks, key=lambda t: str(t.get("time") or ""))
    return {
        "hhmm": _hhmm(str(last["time"])),
        "pcr": last["pcr"],
        "volumePcr": last["volumePcr"],
        "changeOiPcr": last["changeOiPcr"],
        "indexClose": last["indexClose"],
    }


def _spot(page: dict[str, Any], latest: dict[str, Any] | None) -> dict[str, Any]:
    spot = page.get("initialSpot") or {}
    ltp = _num(spot.get("last_trade_price"))
    if ltp is None:
        ltp = _num(spot.get("ltp"))
    if ltp is None and latest is not None:
        ltp = _num(latest.get("indexClose"))
    chg = _num(spot.get("change_per"))
    if chg is None:
        close = _num(spot.get("close"))
        if ltp is not None and close not in (None, 0):
            chg = round((ltp - close) / close * 100, 2)
    ts = spot.get("timestamp") or spot.get("created_at")
    return {
        "ltp": ltp,
        "changePer": chg,
        "maxPain": _num(spot.get("max_pain")),
        "vix": _num(spot.get("vix_value")),
        "timestamp": ts,
    }


async def _scrape(client: httpx.AsyncClient, symbol: str, url: str) -> dict[str, Any] | None:
    import json
    try:
        res = await client.get(url, headers=HEADERS)
        res.raise_for_status()
    except Exception:
        return None
    m = _NEXT.search(res.text)
    if not m:
        return None
    try:
        page = json.loads(m.group(1)).get("props", {}).get("pageProps", {})
    except Exception:
        return None
    raw = page.get("initialPcrData") or []
    ticks = []
    for row in raw:
        t = str(row.get("time") or "")
        if not t:
            continue
        pcr = _num(row.get("pcr"))
        if pcr is None:
            continue
        ticks.append({
            "time": t,
            "pcr": pcr,
            "volumePcr": _num(row.get("volume_pcr")) or 0.0,
            "changeOiPcr": _num(row.get("change_oi_pcr")) or 0.0,
            "indexClose": _num(row.get("index_close")) or 0.0,
            "expiry": str(row.get("expiry_date") or "")[:10],
        })
    dates = sorted({str(t['time'])[:10] for t in ticks if len(str(t['time'])) >= 10 and str(t['time'])[4:5] == '-'})
    session_date = dates[-1] if dates else ''
    if session_date:
        ticks = [t for t in ticks if str(t['time']).startswith(session_date)]
    latest = _latest(ticks)
    marks = _marks(ticks)
    if not ticks and page.get("initialPcr") is None:
        return None
    live = _num(page.get("initialPcr"))
    if live is None and latest is not None:
        live = latest["pcr"]
    expiry = str(page.get("initialExpiry") or (ticks[0].get("expiry") if ticks else "") or "")[:10]
    return {
        "id": symbol,
        "sessionDate": session_date,
        "expiry": expiry,
        "livePcr": live,
        "spot": _spot(page, latest),
        "marks": marks,
        "latest": latest,
    }


def _fresh_cached(now: float) -> dict[str, Any] | None:
    cached = _CACHE.get("payload")
    if not cached:
        return None
    ttl = _CACHE_S if cached.get("source") == "live" else _MISS_CACHE_S
    return cached if now - float(_CACHE["at"]) < ttl else None


_SNAPSHOT_SERIES: dict[str, Any] = {
    "NIFTY": {
        "expiry": "2026-09-01",
        "livePcr": 0.5928,
        "spot": {"ltp": 24090.85, "changePer": -0.48, "maxPain": 24200, "vix": 11.07, "timestamp": "2026-08-27T15:48:03"},
        "latest": {"hhmm": "15:48", "pcr": 0.5928, "volumePcr": 1.0192, "changeOiPcr": 0.2849, "indexClose": 24090.85},
        "marks": [
            {"hhmm": "09:15", "pcr": 0.7332, "volumePcr": 0.7687, "changeOiPcr": 0.0, "indexClose": 24283.15},
            {"hhmm": "15:30", "pcr": 0.5757, "volumePcr": 1.0342, "changeOiPcr": 0.1902, "indexClose": 24090.85},
            {"hhmm": "15:48", "pcr": 0.5928, "volumePcr": 1.0192, "changeOiPcr": 0.2849, "indexClose": 24090.85},
        ],
    },
    "BANKNIFTY": {
        "expiry": "2026-09-29",
        "livePcr": 1.0961,
        "spot": {"ltp": 57509.95, "changePer": -0.47, "maxPain": 57800, "vix": 11.07, "timestamp": "2026-08-27T15:48:03"},
        "latest": {"hhmm": "15:48", "pcr": 1.0961, "volumePcr": 1.0468, "changeOiPcr": 0.551, "indexClose": 57509.95},
        "marks": [
            {"hhmm": "09:15", "pcr": 1.1449, "volumePcr": 0.5296, "changeOiPcr": 0.0, "indexClose": 57997.2},
            {"hhmm": "15:30", "pcr": 1.1015, "volumePcr": 1.0589, "changeOiPcr": 0.6102, "indexClose": 57509.95},
            {"hhmm": "15:48", "pcr": 1.0961, "volumePcr": 1.0468, "changeOiPcr": 0.551, "indexClose": 57509.95},
        ],
    },
    "FINNIFTY": {
        "expiry": "2026-09-29",
        "livePcr": 0.5579,
        "spot": {"ltp": 26280.65, "changePer": -0.4, "maxPain": 26500, "vix": 11.07, "timestamp": "2026-08-27T15:48:03"},
        "latest": {"hhmm": "15:48", "pcr": 0.5579, "volumePcr": 0.5979, "changeOiPcr": 1.341, "indexClose": 26280.65},
        "marks": [
            {"hhmm": "09:15", "pcr": 0.5152, "volumePcr": 0.3333, "changeOiPcr": 0.0, "indexClose": 26480.6},
            {"hhmm": "15:30", "pcr": 0.5708, "volumePcr": 0.656, "changeOiPcr": 0.9839, "indexClose": 26389.2},
            {"hhmm": "15:48", "pcr": 0.5579, "volumePcr": 0.5979, "changeOiPcr": 1.341, "indexClose": 26280.65},
        ],
    },
    "SENSEX": {
        "expiry": "2026-09-29",
        "livePcr": 0.8123,
        "spot": {"ltp": 78500.0, "changePer": -0.3, "maxPain": 78500, "vix": 11.07, "timestamp": "2026-08-27T15:48:03"},
        "latest": {"hhmm": "15:48", "pcr": 0.8123, "volumePcr": 0.9, "changeOiPcr": 0.5, "indexClose": 78500.0},
        "marks": [
            {"hhmm": "09:15", "pcr": 0.85, "volumePcr": 0.8, "changeOiPcr": 0.0, "indexClose": 78600.0},
            {"hhmm": "15:48", "pcr": 0.8123, "volumePcr": 0.9, "changeOiPcr": 0.5, "indexClose": 78500.0},
        ],
    },
    "MIDCPNIFTY": {
        "expiry": "2026-09-29",
        "livePcr": 0.9412,
        "spot": {"ltp": 12500.0, "changePer": -0.2, "maxPain": 12500, "vix": 11.07, "timestamp": "2026-08-27T15:48:03"},
        "latest": {"hhmm": "15:48", "pcr": 0.9412, "volumePcr": 1.0, "changeOiPcr": 0.7, "indexClose": 12500.0},
        "marks": [
            {"hhmm": "09:15", "pcr": 0.98, "volumePcr": 0.9, "changeOiPcr": 0.0, "indexClose": 12550.0},
            {"hhmm": "15:48", "pcr": 0.9412, "volumePcr": 1.0, "changeOiPcr": 0.7, "indexClose": 12500.0},
        ],
    },
}


def merge_session_history(previous: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    """Keep observed prints across partial provider responses, never across dates."""
    if str(previous.get("asOf", ""))[:10] != str(current.get("asOf", ""))[:10]:
        return current
    merged = {**current, "series": dict(previous.get("series") or {})}
    for symbol, row in (current.get("series") or {}).items():
        old = merged["series"].get(symbol) or {}
        marks = {m["hhmm"]: m for m in old.get("marks", []) if m.get("hhmm")}
        marks.update({m["hhmm"]: m for m in row.get("marks", []) if m.get("hhmm")})
        latest = max((r for r in (old.get("latest"), row.get("latest")) if r), key=lambda r: r.get("hhmm", ""), default=None)
        merged["series"][symbol] = {**old, **row, "marks": [marks[k] for k in sorted(marks)], "latest": latest}
    return merged


@router.get("/session")
async def pcr_session(_user: UserContext = Depends(get_current_user)) -> dict[str, Any]:
    global _scrape_lock, _history_retry_at
    now = datetime.now(ZoneInfo('Asia/Kolkata'))
    phase = session_phase(now, exchange='NFO')
    if phase != 'continuous':
        cached = _CACHE.get('payload') or {}
        if not cached:
            try:
                import json
                from app.services import db
                cached = json.loads(db.get_config('pcr.last_session.v1', '{}'))
                if not isinstance(cached, dict): cached = {}
            except Exception:
                cached = {}
        complete = str(cached.get('asOf', ''))[:10] == now.date().isoformat() and len(cached.get('series') or {}) == len(PAGES) and all(any(m.get('hhmm') == '15:30' for m in row.get('marks', [])) for row in cached.get('series', {}).values())
        # Recover historical prints after close; no holiday/live scanner is enabled.
        if phase == 'closed' and now.hour >= 16 and calendar_covers(now.date()) and is_trading_day(now.date()) and not complete and time.time() - _history_retry_at > 300:
            _history_retry_at = time.time()
            recovered = await _scrape_all()
            dates = [r.get('sessionDate') for r in recovered.get('series', {}).values() if r.get('sessionDate')]
            if dates and max(dates) == now.date().isoformat():
                recovered['asOf'] = max(dates) + 'T' + max((r.get('latest') or {}).get('hhmm', '00:00') for r in recovered['series'].values()) + ':00+05:30'
                cached = merge_session_history(cached, recovered)
                _CACHE['payload'] = cached
                try:
                    import json
                    from app.services import db
                    db.set_config('pcr.last_session.v1', json.dumps(cached))
                except Exception:
                    pass
        series = cached.get('series') or {}
        return {
            **cached,
            'asOf': cached.get('asOf') or now.isoformat(),
            'source': 'snapshot',
            'series': series,
            'sessionPhase': phase,
            'marketOpen': False,
        }
    hit = _fresh_cached(time.time())
    if hit is not None:
        return hit
    loop = asyncio.get_running_loop()
    if _scrape_lock is None or _scrape_lock[0] is not loop:
        _scrape_lock = (loop, asyncio.Lock())
    async with _scrape_lock[1]:
        hit = _fresh_cached(time.time())  # another caller may have just scraped
        if hit is not None:
            return hit
        previous = _CACHE.get('payload') or {}
        if not previous:
            try:
                import json
                from app.services import db
                previous = json.loads(db.get_config('pcr.last_session.v1', '{}'))
            except Exception:
                previous = {}
        result = await _scrape_all()
        session_dates = [row.get('sessionDate') for row in result.get('series', {}).values() if row.get('sessionDate')]
        current = bool(session_dates) and max(session_dates) == now.date().isoformat()
        result.update(marketOpen=True, sessionPhase=phase)
        if not current: result['source']='snapshot'
        if session_dates: result['asOf']=max(session_dates)+'T'+max((row.get('latest') or {}).get('hhmm','00:00') for row in result['series'].values())+':00+05:30'
        result = merge_session_history(previous, result)
        _CACHE['payload'] = result
        if result.get('series'):
            try:
                import json
                from app.services import db
                db.set_config('pcr.last_session.v1', json.dumps(result))
            except Exception:
                pass  # In-memory/browser history remains available if DB storage fails.
        return result


async def _scrape_all() -> dict[str, Any]:
    now = time.time()
    series: dict[str, Any] = {}
    async with httpx.AsyncClient(timeout=12.0) as client:
        results = await asyncio.gather(
            *[_scrape(client, sym, url) for sym, url in PAGES.items()],
            return_exceptions=True,
        )
    live = 0
    for (sym, _), result in zip(PAGES.items(), results):
        if isinstance(result, dict) and (result.get("marks") or result.get("latest") or result.get("livePcr") is not None):
            series[sym] = result
            live += 1
    payload = {
        "asOf": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": "live" if live else "snapshot",
        "series": series,
    }
    _CACHE["at"] = now
    _CACHE["payload"] = payload
    return payload

_STOCK_CACHE: dict[tuple[str,str],tuple[float,dict]] = {}
@router.get('/instrument')
async def stock_pcr(symbol: str, user: UserContext = Depends(get_current_user)):
    from .kite import option_chain
    key=(user.user_id,symbol.upper())
    now=datetime.now(ZoneInfo('Asia/Kolkata'));opened=session_phase(now,exchange='NFO')=='continuous'
    cached=_STOCK_CACHE.get(key)
    if not opened:
        return {**(cached[1] if cached else {'ratio':None,'coverage':'Nearest-expiry options within 20% of spot','reason':'Market closed; no fresh stock PCR snapshot'}),'marketOpen':False}
    if cached and time.time()-cached[0]<60:return cached[1]
    chain=await option_chain(symbol,user)
    expiries=chain.get('expiries') or []
    nearest=expiries[0]['date'] if expiries else ''
    rows=chain.get('chain',{}).get(nearest,[])
    calls=sum(max(0,float((r.get('call') or {}).get('oi') or 0)) for r in rows)
    puts=sum(max(0,float((r.get('put') or {}).get('oi') or 0)) for r in rows)
    result={'ratio':round(puts/calls,3) if calls>0 else None,'expiry':nearest,'coverage':f'Nearest expiry; {len(rows)} strikes within 20% of spot; not whole-market PCR','marketOpen':True,'observedAt':now.isoformat()}
    _STOCK_CACHE[key]=(time.time(),result)
    return result
