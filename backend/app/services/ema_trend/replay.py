"""Historical runs use only supplied immutable bars and the pure engine.

No live quote or transport is constructed in this path.
"""
from __future__ import annotations
import asyncio
from app.engines.ema_trend.config import EmaTrendConfig, canonical_mode, stable_id
from app.engines.ema_trend.contracts import Bar
from app.engines.ema_trend.dataset import pair_manifest
from app.engines.ema_trend.config import PROFILES
from . import repository as repo, config_store

_tasks: dict[str,asyncio.Task]={}


async def _run(uid: str, ident: str):
    from app.engines.ema_trend.backtest import run_replay
    row=repo.get(uid,"run",ident)
    row=repo.put(uid,"run",ident,{**row,"status":"RUNNING"},row["revision"])
    try:
        config=EmaTrendConfig.model_validate(row["config"])
        bars=[Bar.model_validate(b) for b in row["bars"]]
        context=[Bar.model_validate(b) for b in row["context_bars"]]
        result=await asyncio.to_thread(run_replay,row["symbol"],row["mode"],bars,context,config,
            uid=f"replay:{uid}",run_id=ident,instrument_meta=row.get("instrument"))
        current=repo.get(uid,"run",ident)
        repo.put(uid,"run",ident,{**current,"status":"COMPLETED","result":result,"finished_ms":repo.now_ms()},current["revision"])
    except Exception as exc:
        current=repo.get(uid,"run",ident)
        repo.put(uid,"run",ident,{**current,"status":"FAILED","error":str(exc),"finished_ms":repo.now_ms()},current["revision"])


def start(uid: str, symbol: str, mode: str, bars: list[Bar], context_bars: list[Bar],
          expected_config_revision: int):
    current=config_store.get(uid)
    if current["revision"]!=expected_config_revision:
        raise repo.Conflict("STALE_CONFIG_REVISION")
    mode=canonical_mode(mode)
    cfg=EmaTrendConfig.model_validate(current["config"])
    if mode not in cfg.modes or symbol not in cfg.symbols:
        raise ValueError("REPLAY_OUTSIDE_CONFIG_SCOPE")
    try:
        instrument_meta=repo.get(uid,"tape",f"{symbol}:{mode}:signal").get("instrument")
    except repo.NotFound:
        instrument_meta=None
    if symbol.split(":")[0] not in {"NSE","BSE"} and not instrument_meta:
        raise ValueError("DERIVATIVE_REPLAY_REQUIRES_RECORDED_INSTRUMENT_METADATA")
    ident=stable_id(uid,"replay",mode,symbol,cfg.config_hash,instrument_meta,
                    [b.model_dump(mode="json") for b in bars], [b.model_dump(mode="json") for b in context_bars])
    try:
        return repo.get(uid,"run",ident)
    except repo.NotFound:
        pass
    row=repo.put(uid,"run",ident,{"status":"QUEUED","mode":mode,"symbol":symbol,
        "instrument":instrument_meta,
        "config":cfg.model_dump(mode="json"),"config_revision":expected_config_revision,
        "bars":[b.model_dump(mode="json") for b in bars],
        "context_bars":[b.model_dump(mode="json") for b in context_bars],
        "dataset_manifest":pair_manifest(symbol,PROFILES[mode].signal_minutes,PROFILES[mode].context_minutes,bars,context_bars),
        "tenant_kind":"REPLAY","live_capable":False},0)
    task=asyncio.create_task(_run(uid,ident),name=f"ema-trend-replay-{ident}")
    _tasks[ident]=task
    task.add_done_callback(lambda _: _tasks.pop(ident,None))
    return row
