"""Durable per-day Gamma-Unwind board history.

The reference E1 is live-tick driven. We archive what the live runtime
actually observed instead of inventing historical E1s from OHLC bars.
"""
from __future__ import annotations

import json
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable

IST = timezone(timedelta(hours=5, minutes=30))
_MAX_DAYS = 120


def _day_str(value: date | str) -> str:
    if isinstance(value, date):
        return value.isoformat()
    text = str(value or "").strip()
    parsed = date.fromisoformat(text)
    return parsed.isoformat()


def _day_key(uid: str, day: date | str) -> str:
    return f"gamma_unwind_history:{uid}:{_day_str(day)}"


def _index_key(uid: str) -> str:
    return f"gamma_unwind_history_days:{uid}"


def _load_json(key: str, default):
    from app.services import db
    raw = db.get_config(key)
    if raw in (None, ""):
        return default
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except Exception:
            return default
    return raw


def _save_json(key: str, value) -> None:
    from app.services import db
    db.set_config(
        key,
        json.dumps(value, separators=(",", ":"), allow_nan=False),
    )


def _empty(day: str) -> dict[str, Any]:
    return {
        "day": day,
        "recorded": True,
        "source": "live_recorded",
        "updated_ms": 0,
        "scan": {},
        "issue": "",
        "coverage": [],
        "replay": {},
        "watchlist": [],
        "signals": [],
    }


def read_day(uid: str, day: date | str) -> dict[str, Any] | None:
    key = _day_str(day)
    row = _load_json(_day_key(uid, key), None)
    if not isinstance(row, dict):
        return None
    out = _empty(key)
    out.update(row)
    out["day"] = key
    out["recorded"] = True
    out["watchlist"] = list(out.get("watchlist") or [])
    out["signals"] = list(out.get("signals") or [])
    out["scan"] = dict(out.get("scan") or {})
    out["issue"] = str(out.get("issue") or "")
    out["coverage"] = list(out.get("coverage") or [])
    out["replay"] = dict(out.get("replay") or {})
    return out


def _touch_index(uid: str, day: str) -> None:
    rows = _load_json(_index_key(uid), [])
    days = [str(v) for v in rows if isinstance(v, str)]
    days = [v for v in days if v != day]
    days.append(day)
    days = sorted(set(days), reverse=True)[:_MAX_DAYS]
    _save_json(_index_key(uid), days)


def _write(uid: str, row: dict[str, Any]) -> dict[str, Any]:
    day = _day_str(row["day"])
    row = dict(row)
    row["day"] = day
    row["recorded"] = True
    row["source"] = str(row.get("source") or "live_recorded")
    row["updated_ms"] = int(time.time() * 1000)
    _save_json(_day_key(uid, day), row)
    _touch_index(uid, day)
    return row


def record_scan(
    uid: str,
    day: date | str,
    *,
    watchlist: list[dict] | None = None,
    scan: dict | None = None,
    issue: str | None = None,
    coverage: list[str] | None = None,
) -> dict[str, Any]:
    """Persist cumulative board-visible watch evidence for a trading day.

    The reference client builds more than once per day. Do not erase the 09:20
    watch evidence when 13:15 selects a different set (or none). Rows with the
    same watch id are updated to the latest state; distinct contracts survive.
    """
    day_s = _day_str(day)
    row = read_day(uid, day_s) or _empty(day_s)
    # A real live observation always supersedes a reconstructed replay for that
    # same day. Never let a later replay overwrite actual runtime evidence.
    row["source"] = "live_recorded"
    row["replay"] = {}
    row["coverage"] = []
    if watchlist is not None:
        merged = {
            str(item.get("id") or ""): item
            for item in row.get("watchlist") or []
            if isinstance(item, dict) and item.get("id")
        }
        for item in watchlist:
            if not isinstance(item, dict):
                continue
            key = str(item.get("id") or "")
            if key:
                merged[key] = item
        row["watchlist"] = list(merged.values())
    if scan is not None:
        row["scan"] = dict(scan)
    if issue is not None:
        row["issue"] = str(issue or "")
    if coverage is not None:
        row["coverage"] = list(dict.fromkeys(str(v) for v in coverage if v))
    return _write(uid, row)


def append_signals(uid: str, signals: Iterable[Any]) -> None:
    """Merge live E1 signals into their actual event day, keyed by signal id."""
    grouped: dict[str, list[dict]] = {}
    for signal in signals:
        data = signal.as_dict() if hasattr(signal, "as_dict") else dict(signal)
        at_ms = int(data.get("at_ms") or 0)
        if at_ms <= 0:
            continue
        day = datetime.fromtimestamp(at_ms / 1000, IST).date().isoformat()
        grouped.setdefault(day, []).append(data)

    for day, items in grouped.items():
        row = read_day(uid, day) or _empty(day)
        row["source"] = "live_recorded"
        row["replay"] = {}
        row["coverage"] = []
        merged = {
            str(item.get("id") or ""): item
            for item in row.get("signals") or []
            if isinstance(item, dict) and item.get("id")
        }
        for item in items:
            key = str(item.get("id") or "")
            if key:
                merged[key] = item
        row["signals"] = list(merged.values())
        _write(uid, row)



def record_replay(uid: str, result: dict[str, Any]) -> dict[str, Any]:
    """Cache one read-only historical reconstruction.

    Existing live_recorded evidence wins permanently for that day.
    """
    day = _day_str(result["day"])
    existing = read_day(uid, day)
    if existing is not None and existing.get("source") == "live_recorded":
        return existing
    row = existing or _empty(day)
    row.update({
        "source": "historical_replay",
        "watchlist": list(result.get("watchlist") or []),
        "signals": list(result.get("signals") or []),
        "scan": dict(result.get("scan") or {}),
        "issue": str(result.get("issue") or ""),
        "coverage": list(result.get("coverage") or []),
        "replay": {
            "resolution": str(result.get("resolution") or "1minute_close"),
            "complete": bool(result.get("complete")),
        },
    })
    return _write(uid, row)


def list_days(uid: str, limit: int = 60) -> list[dict[str, Any]]:
    limit = max(1, min(int(limit or 60), _MAX_DAYS))
    raw = _load_json(_index_key(uid), [])
    days = sorted(
        {str(v) for v in raw if isinstance(v, str)},
        reverse=True,
    )[:limit]
    out: list[dict[str, Any]] = []
    for day in days:
        row = read_day(uid, day)
        if row is None:
            continue
        out.append({
            "day": day,
            "signals": len(row.get("signals") or []),
            "watches": len(row.get("watchlist") or []),
            "last_run_ms": int((row.get("scan") or {}).get("last_run_ms") or 0),
            "updated_ms": int(row.get("updated_ms") or 0),
            "issue": str(row.get("issue") or ""),
            "source": str(row.get("source") or "live_recorded"),
            "resolution": str((row.get("replay") or {}).get("resolution") or ""),
        })
    return out