"""Durable evidence of completed supervision, never a broker protection claim."""
from __future__ import annotations
from . import repository as repo

HEARTBEAT_INTERVAL_MS=10000
STALE_AFTER_MS=30000


def record(uid: str, result: dict) -> dict:
    now=repo.now_ms()
    errors=result.get("errors") or []
    status="DEGRADED" if errors else "RUNNING"
    try:
        current=repo.get(uid,"health","supervision")
    except repo.NotFound:
        current=None
    if current and now-int(current["completed_ms"])<HEARTBEAT_INTERVAL_MS and current["status"]==status and current.get("errors")==errors:
        return current
    return repo.put(uid,"health","supervision",{"status":status,"completed_ms":now,
        "errors":errors[:20],"error_count":len(errors),"protection":"PAPER_MONITOR_ONLY"},current["revision"] if current else 0)


def summary(uid: str) -> dict:
    try:
        value=repo.get(uid,"health","supervision")
    except repo.NotFound:
        return {"status":"UNKNOWN","age_ms":None,"errors":[],"reason":"NO_COMPLETED_SUPERVISION_CYCLE",
                "protection":"PAPER_MONITOR_ONLY","live_eligible":False}
    age=max(0,repo.now_ms()-int(value["completed_ms"]))
    return {"status":"STALE" if age>STALE_AFTER_MS else value["status"],"age_ms":age,
            "completed_ms":value["completed_ms"],"errors":value.get("errors",[]),
            "error_count":value.get("error_count",0),"reason":"SUPERVISION_HEARTBEAT_STALE" if age>STALE_AFTER_MS else "LAST_COMPLETED_CYCLE",
            "protection":"PAPER_MONITOR_ONLY","live_eligible":False}
