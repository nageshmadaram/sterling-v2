"""Read-only strategy projection. Historical dates never hide held exposure."""
from __future__ import annotations
from datetime import datetime

from app.engines.ema_trend.config import descriptor, canonical_mode
from . import config_store, repository as repo, health
from .execution import public_campaign
from .scanner import IST
from .signal_admission import current as signal_admission


def snapshot(uid: str, mode: str | None=None, day: str | None=None, cursor: int=0) -> dict:
    selected=canonical_mode(mode) if mode else None
    selected_day=datetime.fromisoformat(day).date() if day else None
    current=config_store.response(uid)
    now=datetime.now(IST)
    rows=[]
    page=(repo.list_signals_by_mode(uid,selected,limit=1001,cursor=cursor)
          if selected else repo.list_documents(uid,"signal",limit=1001,cursor=cursor))
    # Keep mode-local pagination consistent with the repository ordering.
    # Day-filtered snapshots remain read-only historical views.
    for row in page[:1000]:
        if selected_day and datetime.fromtimestamp(row["close_ms"]/1000,IST).date()!=selected_day:
            continue
        rows.append({**{k:v for k,v in row.items() if k not in {"metadata"}},
                     **signal_admission(row,current,now)})
    campaigns=[public_campaign(c) for c in repo.list_documents(uid,"campaign",limit=2000)
               if not selected or c["mode"]==selected]
    scans=repo.list_documents(uid,"scan",limit=1)
    return {**current,"rows":rows,"campaigns":campaigns,"supervision":health.summary(uid),
            "scan":scans[0] if scans else {"status":"IDLE","progress":0,"errors":[]},
            "next_cursor":cursor+1000 if len(page)>1000 else None,
            "generated_ms":repo.now_ms(),"historical_view":bool(day),
            "tenant_kind":"SIMULATION","execution_mode":"PAPER",
            "protection":"PAPER_MONITOR_ONLY","evidence_status":"INCONCLUSIVE"}


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
