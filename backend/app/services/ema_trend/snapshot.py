"""Read-only strategy projection. Historical dates never hide held exposure."""
from __future__ import annotations
from datetime import datetime

from app.engines.ema_trend.config import descriptor, canonical_mode
from . import config_store, repository as repo, health
from .execution import public_campaign
from .scanner import IST
from .signal_admission import current as signal_admission


def snapshot(uid: str, mode: str | None=None, day: str | None=None, cursor: int=0,
             *, modes: list[str] | None=None, row_limit: int=1000) -> dict:
    """One tenant-scoped projection, with independent limits for each EMA mode.

    A batch reads shared configuration, campaigns, scan state and supervision
    exactly once. Its saved signal rows are independently bounded by mode,
    so busy ultra-scalping history cannot starve sparse swing history.
    """
    if not 1 <= row_limit <= 1000:
        raise ValueError("snapshot row_limit must be between 1 and 1000")
    if modes is not None:
        if mode or not modes or len(modes) > 4:
            raise ValueError("Provide one mode or 1-4 batched modes")
        selected_modes = [canonical_mode(item) for item in modes]
        if len(set(selected_modes)) != len(selected_modes):
            raise ValueError("Duplicate EMA snapshot modes")
    else:
        selected_modes = [canonical_mode(mode) if mode else None]
    batched = modes is not None
    selected_day=datetime.fromisoformat(day).date() if day else None
    current=config_store.response(uid)
    now=datetime.now(IST)
    rows=[]
    truncated=[]
    next_cursor=None
    for selected in selected_modes:
        page=(repo.list_signals_by_mode(uid,selected,limit=row_limit+1,cursor=cursor)
              if selected else repo.list_documents(uid,"signal",limit=row_limit+1,cursor=cursor))
        # Filter only after selecting the latest bounded page. Never invent a
        # trading signal or pull a previous session's bar into the current day.
        for row in page[:row_limit]:
            if selected_day and datetime.fromtimestamp(row["close_ms"]/1000,IST).date()!=selected_day:
                continue
            rows.append({**{k:v for k,v in row.items() if k not in {"metadata"}},
                         **signal_admission(row,current,now)})
        if len(page)>row_limit:
            if batched:
                truncated.append(selected)
            else:
                next_cursor=cursor+row_limit
    # Open and unresolved exposure must survive profile filters and pagination.
    selected=selected_modes[0] if not batched else None
    campaigns=[public_campaign(c) for c in repo.list_documents(uid,"campaign",limit=2000)
               if not selected or c["mode"]==selected]
    scans=repo.list_documents(uid,"scan",limit=1)
    result={**current,"rows":rows,"campaigns":campaigns,"supervision":health.summary(uid),
            "scan":scans[0] if scans else {"status":"IDLE","progress":0,"errors":[]},
            "next_cursor":next_cursor,
            "generated_ms":repo.now_ms(),"historical_view":bool(day),
            "tenant_kind":"SIMULATION","execution_mode":"PAPER",
            "protection":"PAPER_MONITOR_ONLY","evidence_status":"INCONCLUSIVE"}
    if batched:
        result.update(truncatedModes=truncated,failedModes=[])
    return result


def export(uid: str):
    return {"schema_version":1,"strategy":descriptor(),"exported_ms":repo.now_ms(),
            "settings":config_store.get(uid),"supervision":health.summary(uid),
            "campaigns":repo.list_documents(uid,"campaign",limit=2000),
            "plans":repo.list_documents(uid,"plan",limit=2000),
            "outbox":repo.list_documents(uid,"outbox",limit=2000),
            "runs":repo.list_documents(uid,"run",limit=2000),
            "datasets":repo.list_documents(uid,"dataset",limit=2000),
            "acceptance":repo.list_documents(uid,"acceptance",limit=2000),
            "events":repo.events(uid,limit=2000),
            "limitations":["PAPER_ONLY","PAPER_MONITOR_ONLY","EXPORT_LIMIT_2000_PER_COLLECTION"]}


def operations(uid: str) -> dict:
    """Owned virtual exposure only; no provider calls or admission decisions."""
    current=config_store.get(uid)
    campaigns=[public_campaign(c) for c in repo.list_documents(uid,"campaign",limit=2000)]
    return {"status":"AVAILABLE","execution_mode":"PAPER","tenant_kind":"SIMULATION",
            "live_eligible":False,"paused":current.get("paused",False),
            "campaigns":campaigns,"supervision":health.summary(uid),
            "protection":"PAPER_MONITOR_ONLY"}
