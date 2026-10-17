"""Read-only exact-instrument history adapter with completed, anchored tapes.

No provider fallback is silently substituted. The tape start is immutable so a
rolling history request cannot renumber a held campaign's bar indices.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.engines.ema_trend.config import PROFILES, canonical_mode, stable_id
from app.engines.ema_trend.contracts import Bar, Setup
from app.engines.ema_trend.dataset import pair_manifest
from app.engines.ema_trend.instruments import contract
from app.engines.ema_trend.calendar import historical_regular_session,HISTORY_START,regular_close
from app.services.simulation.quotes import QuoteSource
from . import config_store, repository as repo

IST = ZoneInfo("Asia/Kolkata")
_tasks: dict[str, asyncio.Task] = {}


async def market_client(uid: str):
    from app.services.exchanges.kite import accounts
    account = accounts.get_active(repo.owner(uid))
    if not account or not account.connected:
        raise ValueError("MARKET_DATA_SESSION_UNAVAILABLE")
    return await accounts.acquire_client(account)


async def equity_metadata(uid: str, symbol: str, client=None):
    """Legacy name retained; validates cash and supported derivative contracts."""
    client = client or await market_client(uid)
    source = QuoteSource(lambda: client, owner=uid)
    exchange, name = symbol.split(":", 1)
    meta = await source.instrument(exchange, name)
    # QuoteSource supplies fallbacks for generic display; admission must prove
    # the raw broker lot/tick fields instead of accepting those fallbacks.
    rows=await client._instruments.load(exchange)
    matches=[r for r in rows if str(r.get("tradingsymbol"))==name and str(r.get("exchange"))==exchange]
    if len(matches)!=1:
        raise ValueError("EXACT_BROKER_CONTRACT_METADATA_REQUIRED")
    verified=contract(matches[0],"swing")
    if verified["lot_size"]!=meta.lot_size or Decimal(verified["tick_size"])!=Decimal(str(meta.tick_size)):
        raise ValueError("CONTRADICTORY_BROKER_CONTRACT_METADATA")
    if meta.token<=0:
        raise ValueError("VALID_INSTRUMENT_TOKEN_REQUIRED")
    return meta


def normalize_history(rows: list, minutes: int, *, asof_ms: int, exchange: str) -> list[Bar]:
    result: dict[int, Bar] = {}
    for row in rows:
        if isinstance(row, dict):
            stamp = row.get("date", row.get("time", row.get("timestamp")))
            values = [row[k] for k in ("open", "high", "low", "close", "volume")]
        else:
            stamp, *values = row[:6]
        if isinstance(stamp, (float, int)):
            start = datetime.fromtimestamp(float(stamp) / (1000 if float(stamp) > 1e11 else 1), IST)
        else:
            start = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
            start = start.replace(tzinfo=IST) if start.tzinfo is None else start.astimezone(IST)
        if not historical_regular_session(start.date(), exchange):
            continue
        opening = datetime.combine(start.date(), time(9, 15), IST)
        closing = datetime.combine(start.date(), regular_close(start.date(),exchange), IST)
        if minutes == 1440:
            start, end = opening, closing
        else:
            if start < opening or start >= closing:
                continue
            end = min(start + timedelta(minutes=minutes), closing)
        close_ms = int(end.timestamp() * 1000)
        if close_ms > asof_ms:
            continue
        bar = Bar(open_ms=int(start.timestamp()*1000), close_ms=close_ms, available_ms=close_ms,
                  open=values[0], high=values[1], low=values[2], close=values[3], volume=values[4],
                  provider="kite_historical", adjustment_version="RAW_UNADJUSTED_CORPORATE_ACTIONS_UNVERIFIED")
        previous = result.get(bar.open_ms)
        if previous is not None and previous != bar:
            raise ValueError("CONTRADICTORY_HISTORY_DUPLICATE")
        result[bar.open_ms] = bar
    return sorted(result.values(), key=lambda b: b.open_ms)


async def fetch_bars(client, meta, minutes: int, now: datetime) -> list[Bar]:
    interval = {1: "minute", 3: "3minute", 5: "5minute", 15: "15minute", 60: "60minute", 1440: "day"}[minutes]
    days = 1100 if minutes == 1440 else (180 if minutes == 60 else 30)
    # Historical endpoints limit intraday spans. Requests are sequential and
    # share the existing client's global provider budget.
    span = 55 if minutes < 1440 else 1500
    minimum=HISTORY_START if meta.exchange in {"NSE","NFO"} else datetime(2026,1,1).date()
    earliest=datetime.combine(minimum,time(0,0),IST)
    cursor, raw = max(now - timedelta(days=days),earliest), []
    while cursor < now:
        end = min(cursor + timedelta(days=span), now)
        payload = await client.get_historical(meta.token, interval,
            cursor.strftime("%Y-%m-%d %H:%M:%S"), end.strftime("%Y-%m-%d %H:%M:%S"))
        raw.extend(payload.get("candles", (payload.get("data") or {}).get("candles", [])))
        cursor = end
    return normalize_history(raw, minutes, asof_ms=int(now.timestamp()*1000), exchange=meta.exchange)


def anchored_tape(uid: str, key: str, bars: list[Bar], instrument_meta: dict|None=None) -> list[Bar]:
    try:
        old = repo.get(uid, "tape", key)
    except repo.NotFound:
        old = None
    current = {b.open_ms: b for b in bars}
    if old:
        original_meta=old.get("instrument")
        keys=("exchange","tradingsymbol","instrument_token","lot_size","tick_size","expiry","instrument_type")
        if original_meta and instrument_meta and any(original_meta.get(k)!=instrument_meta.get(k) for k in keys):
            raise ValueError("INSTRUMENT_METADATA_CHANGED_RECONCILIATION_REQUIRED")
        anchor = old["anchor"]
        prior = {int(b["open_ms"]): Bar.model_validate(b) for b in old["bars"]}
        for stamp, bar in current.items():
            if stamp < anchor:
                continue
            if stamp in prior and prior[stamp] != bar:
                raise ValueError("HISTORICAL_BAR_RESTATED_RECONCILIATION_REQUIRED")
            prior[stamp] = bar
        current = prior
    if not current:
        return []
    if len(current) > 20000:
        raise ValueError("TAPE_CAPACITY_REQUIRES_EXPLICIT_REBASE")
    result = sorted(current.values(), key=lambda b: b.open_ms)
    repo.put(uid, "tape", key, {"anchor": result[0].open_ms,
             "instrument":instrument_meta or (old or {}).get("instrument"),
             "bars": [b.model_dump(mode="json") for b in result]}, old["revision"] if old else 0)
    return result


async def scan_symbol(uid: str, symbol: str, mode: str, *, client=None, now=None):
    from app.engines.ema_trend.features import aggregate_completed_bars, build_features
    from app.engines.ema_trend.setups import evaluate_setups
    current = config_store.get(uid)
    profile = PROFILES[canonical_mode(mode)]
    now = now or datetime.now(IST)
    client = client or await market_client(uid)
    meta = await equity_metadata(uid, symbol, client)
    bars = await fetch_bars(client, meta, profile.signal_minutes, now)
    context_base = 1440 if profile.context_minutes == 10080 else profile.context_minutes
    context = await fetch_bars(client, meta, context_base, now)
    bars = anchored_tape(uid, f"{symbol}:{mode}:signal", bars,{**meta.as_meta(),"instrument_token":meta.token})
    context = anchored_tape(uid, f"{symbol}:{mode}:context", context,{**meta.as_meta(),"instrument_token":meta.token})
    if profile.context_minutes == 10080:
        context = aggregate_completed_bars(context, 10080, asof_ms=int(now.timestamp()*1000), exchange=meta.exchange)
    audit=pair_manifest(symbol,profile.signal_minutes,profile.context_minutes,bars,context,asof_ms=int(now.timestamp()*1000))
    dataset_id=stable_id(symbol,mode,audit["content_sha256"])
    try:
        repo.get(uid,"dataset",dataset_id)
    except repo.NotFound:
        repo.put(uid,"dataset",dataset_id,{"symbol":symbol,"mode":mode,"config_revision":current["revision"],
            "manifest":audit},0)
    features = build_features(bars, context)
    setups = evaluate_setups(symbol, mode, features, config_revision=current["revision"], tick=Decimal(str(meta.tick_size)))
    # No result from a superseded scan is actionable.
    if config_store.get(uid)["revision"] != current["revision"]:
        raise repo.Conflict("SCAN_CONFIG_SUPERSEDED")
    for setup in setups[-100:]:
        value = setup.model_dump(mode="json")
        value["metadata"] = {**meta.as_meta(),"instrument_token":meta.token}
        value["contract"]=contract({**meta.as_meta(),"instrument_token":meta.token},mode)
        value["dataset_id"]=dataset_id
        value["dataset_sha256"]=audit["content_sha256"]
        value["activation_eligible"] = setup.close_ms > current["activation_ms"]
        value["scan_asof_ms"] = int(now.timestamp()*1000)
        repo.put(uid, "signal", setup.id, value)
    return {"symbol": symbol, "mode": mode, "bars": len(bars), "context_bars": len(context),
            "ready": bool(features and features[-1].ready), "rows": len(setups),
            "latest_close_ms": bars[-1].close_ms if bars else None,"dataset_id":dataset_id,"dataset_manifest":audit}


async def run_job(uid: str, job_id: str):
    job = repo.get(uid, "scan", job_id)
    job = repo.put(uid, "scan", job_id, {**job, "status": "RUNNING"}, job["revision"])
    cfg = config_store.config(uid)
    try:
        client = await market_client(uid)
        total = max(1, len(cfg.symbols) * len(job["modes"]))
        done, results, errors = 0, [], []
        for symbol in cfg.symbols:
            for mode in job["modes"]:
                latest = repo.get(uid, "scan", job_id)
                if latest["status"] == "CANCELLED":
                    return
                if config_store.get(uid)["revision"] != job["config_revision"]:
                    raise repo.Conflict("SCAN_CONFIG_SUPERSEDED")
                try:
                    results.append(await scan_symbol(uid, symbol, mode, client=client))
                except Exception as exc:
                    errors.append({"symbol": symbol, "mode": mode, "reason": str(exc)})
                done += 1
                job = repo.put(uid, "scan", job_id, {**latest, "progress": round(done/total*100),
                    "results": results, "errors": errors}, latest["revision"])
        repo.put(uid, "scan", job_id, {**job, "status": "COMPLETED" if not errors else "FAILED",
                  "finished_ms": repo.now_ms()}, job["revision"])
    except asyncio.CancelledError:
        latest = repo.get(uid, "scan", job_id)
        repo.put(uid, "scan", job_id, {**latest, "status": "CANCELLED", "finished_ms": repo.now_ms()}, latest["revision"])
        raise
    except Exception as exc:
        latest = repo.get(uid, "scan", job_id)
        repo.put(uid, "scan", job_id, {**latest, "status": "FAILED", "errors": [{"reason": str(exc)}],
                  "finished_ms": repo.now_ms()}, latest["revision"])


def start_scan(uid: str, modes: list[str] | None, expected_revision: int):
    current = config_store.get(uid)
    if current["revision"] != expected_revision:
        raise repo.Conflict("STALE_REVISION")
    cfg = config_store.config(uid)
    selected = [canonical_mode(m) for m in (modes or cfg.modes)]
    if any(m not in cfg.modes for m in selected):
        raise ValueError("MODE_NOT_ENABLED")
    if not cfg.symbols:
        raise ValueError("SELECT_CASH_EQUITIES_FIRST")
    if any(j["status"] in {"QUEUED", "RUNNING"} for j in repo.list_documents(uid, "scan", limit=10)):
        raise repo.Conflict("SCAN_ALREADY_RUNNING")
    ident = stable_id(uid, "scan", repo.now_ms())
    job = repo.put(uid, "scan", ident, {"status": "QUEUED", "progress": 0,
        "modes": selected, "config_revision": expected_revision, "errors": [], "results": []}, 0)
    task = asyncio.create_task(run_job(uid, ident), name=f"ema-trend-scan-{ident}")
    _tasks[ident] = task
    task.add_done_callback(lambda _: _tasks.pop(ident, None))
    return job


def cancel_scan(uid: str, job_id: str):
    job = repo.get(uid, "scan", job_id)
    if job["status"] in {"QUEUED", "RUNNING"}:
        job = repo.put(uid, "scan", job_id, {**job, "status": "CANCELLED"}, job["revision"])
        if job_id in _tasks:
            _tasks[job_id].cancel()
    return job


async def search_instruments(uid: str, query: str, mode: str) -> dict:
    """Broker master search with explicit, server-owned execution capability."""
    from app.engines.ema_trend.instruments import entry_expiry_block
    client=await market_client(uid)
    rows=await client.search_instruments(query,"",limit=100)
    result=[]
    for raw in rows:
        ex=str(raw.get("exchange") or "").upper()
        if ex not in {"NSE","BSE","NFO","BFO","MCX"}:
            continue
        reason=None
        info=None
        try:
            info=contract(raw,mode)
            if int(raw.get("instrument_token") or 0)<=0:
                raise ValueError("VALID_INSTRUMENT_TOKEN_REQUIRED")
            if entry_expiry_block(info,datetime.now(IST).date()):
                raise ValueError("EXPIRY_DAY_OR_EXPIRED_CONTRACT")
        except (ValueError,TypeError) as exc:
            reason=str(exc)
        result.append({"symbol":f"{ex}:{raw['tradingsymbol']}","name":raw.get("name") or raw["tradingsymbol"],
            "instrument_type":raw.get("instrument_type"),"exchange":ex,"expiry":str(raw.get("expiry") or "")[:10],
            "strike":raw.get("strike"),"lot_size":raw.get("lot_size"),"tick_size":raw.get("tick_size"),
            "execution_supported":reason is None,"blocker":reason,"contract":info})
    return {"items":result,"source":"KITE_INSTRUMENT_MASTER","execution_mode":"PAPER","live_eligible":False}
