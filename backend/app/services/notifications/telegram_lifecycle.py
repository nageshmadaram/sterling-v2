"""Durable per-user Telegram lifecycle outbox.

Trade state transitions must never wait on Telegram HTTP. Writers persist a small
event locally; the existing Telegram alert worker drains the queue and records
acknowledgement per destination. A failed destination remains pending and is
retried with bounded exponential backoff.

This queue is for the release trading lifecycle (entry/protection/trail/exit),
not strategy signal generation. Signal dedupe remains in telegram_kite.py.
"""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from typing import Iterable

_TABLE = "telegram_lifecycle_outbox"
_MAX_AGE_MS = 7 * 24 * 60 * 60 * 1000
_MAX_ROWS_PER_USER = 1000


@dataclass(frozen=True)
class LifecycleEvent:
    event_id: str
    uid: str
    dedupe_key: str
    strategy: str
    event_type: str
    title: str
    detail: str
    severity: str
    created_at_ms: int
    delivered_to: tuple[str, ...]
    attempts: int
    next_attempt_ms: int
    last_error: str


def _now_ms() -> int:
    return int(time.time() * 1000)


def _init() -> None:
    from app.services import db

    if not db.is_available():
        raise RuntimeError("lifecycle alert outbox database unavailable")
    with db._conn() as conn:
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {_TABLE} (
                event_id        TEXT PRIMARY KEY,
                uid             TEXT NOT NULL,
                dedupe_key      TEXT NOT NULL,
                strategy        TEXT NOT NULL,
                event_type      TEXT NOT NULL,
                title           TEXT NOT NULL,
                detail          TEXT NOT NULL,
                severity        TEXT NOT NULL,
                created_at_ms   INTEGER NOT NULL,
                delivered_json  TEXT NOT NULL DEFAULT '[]',
                attempts        INTEGER NOT NULL DEFAULT 0,
                next_attempt_ms INTEGER NOT NULL DEFAULT 0,
                last_error      TEXT NOT NULL DEFAULT '',
                UNIQUE(uid, dedupe_key)
            )
            """
        )
        conn.execute(
            f"CREATE INDEX IF NOT EXISTS ix_{_TABLE}_due "
            f"ON {_TABLE}(uid, next_attempt_ms, created_at_ms)"
        )


def enqueue(
    uid: str,
    *,
    dedupe_key: str,
    strategy: str,
    event_type: str,
    title: str,
    detail: str,
    severity: str = "INFO",
    created_at_ms: int | None = None,
) -> str | None:
    """Persist one lifecycle event; duplicate identities are ignored.

    Notification persistence is non-authoritative for trading. Callers should
    catch failures and continue managing the position; this helper intentionally
    raises so the caller can log that alert durability was unavailable.
    """
    _init()
    owner = str(uid or "").strip()
    key = str(dedupe_key or "").strip()
    if not owner or not key:
        raise ValueError("lifecycle alert uid and dedupe_key are required")
    event_id = "TGL-" + uuid.uuid4().hex
    now = int(created_at_ms or _now_ms())
    from app.services import db

    with db._conn() as conn:
        cur = conn.execute(
            f"""
            INSERT OR IGNORE INTO {_TABLE} (
                event_id, uid, dedupe_key, strategy, event_type, title, detail,
                severity, created_at_ms, delivered_json, attempts,
                next_attempt_ms, last_error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '[]', 0, 0, '')
            """,
            (
                event_id,
                owner,
                key,
                str(strategy or "Sterling"),
                str(event_type or "UPDATE"),
                str(title or ""),
                str(detail or ""),
                str(severity or "INFO").upper(),
                now,
            ),
        )
        if cur.rowcount != 1:
            return None

        # Bound a pathological undeliverable queue without touching fresh rows.
        cutoff = now - _MAX_AGE_MS
        conn.execute(
            f"DELETE FROM {_TABLE} WHERE uid = ? AND created_at_ms < ?",
            (owner, cutoff),
        )
        count = conn.execute(
            f"SELECT COUNT(*) FROM {_TABLE} WHERE uid = ?", (owner,)
        ).fetchone()[0]
        overflow = max(0, int(count) - _MAX_ROWS_PER_USER)
        if overflow:
            conn.execute(
                f"""
                DELETE FROM {_TABLE}
                WHERE event_id IN (
                    SELECT event_id FROM {_TABLE}
                    WHERE uid = ?
                    ORDER BY created_at_ms ASC
                    LIMIT ?
                )
                """,
                (owner, overflow),
            )
    return event_id


def _event(row) -> LifecycleEvent:
    try:
        delivered = tuple(
            str(v) for v in json.loads(row["delivered_json"] or "[]") if str(v)
        )
    except Exception:
        delivered = ()
    return LifecycleEvent(
        event_id=str(row["event_id"]),
        uid=str(row["uid"]),
        dedupe_key=str(row["dedupe_key"]),
        strategy=str(row["strategy"]),
        event_type=str(row["event_type"]),
        title=str(row["title"]),
        detail=str(row["detail"]),
        severity=str(row["severity"]),
        created_at_ms=int(row["created_at_ms"] or 0),
        delivered_to=delivered,
        attempts=int(row["attempts"] or 0),
        next_attempt_ms=int(row["next_attempt_ms"] or 0),
        last_error=str(row["last_error"] or ""),
    )


def due(uid: str, *, now_ms: int | None = None, limit: int = 50) -> list[LifecycleEvent]:
    _init()
    now = int(now_ms or _now_ms())
    from app.services import db

    with db._conn() as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM {_TABLE}
            WHERE uid = ? AND next_attempt_ms <= ?
            ORDER BY created_at_ms ASC
            LIMIT ?
            """,
            (str(uid), now, max(1, min(int(limit), 200))),
        ).fetchall()
    return [_event(row) for row in rows]


def pending_user_ids() -> list[str]:
    try:
        _init()
        from app.services import db

        with db._conn() as conn:
            rows = conn.execute(
                f"SELECT DISTINCT uid FROM {_TABLE} ORDER BY uid"
            ).fetchall()
        return [str(row[0]) for row in rows if str(row[0] or "").strip()]
    except Exception:
        return []


def mark_delivered(event_id: str, destination: str) -> None:
    _init()
    from app.services import db

    with db._conn() as conn:
        row = conn.execute(
            f"SELECT delivered_json FROM {_TABLE} WHERE event_id = ?",
            (str(event_id),),
        ).fetchone()
        if row is None:
            return
        try:
            delivered = {str(v) for v in json.loads(row[0] or "[]") if str(v)}
        except Exception:
            delivered = set()
        delivered.add(str(destination))
        conn.execute(
            f"""
            UPDATE {_TABLE}
            SET delivered_json = ?, last_error = ''
            WHERE event_id = ?
            """,
            (json.dumps(sorted(delivered), separators=(",", ":")), str(event_id)),
        )


def mark_failure(event_id: str, error: str, *, now_ms: int | None = None) -> None:
    _init()
    now = int(now_ms or _now_ms())
    from app.services import db

    with db._conn() as conn:
        row = conn.execute(
            f"SELECT attempts FROM {_TABLE} WHERE event_id = ?",
            (str(event_id),),
        ).fetchone()
        if row is None:
            return
        attempts = int(row[0] or 0) + 1
        delay_s = min(1800, max(30, 30 * (2 ** min(attempts - 1, 6))))
        conn.execute(
            f"""
            UPDATE {_TABLE}
            SET attempts = ?, next_attempt_ms = ?, last_error = ?
            WHERE event_id = ?
            """,
            (attempts, now + delay_s * 1000, str(error or "")[:500], str(event_id)),
        )


def complete_if_delivered(event_id: str, destinations: Iterable[str]) -> bool:
    """Delete the event once every currently configured destination acknowledged it."""
    wanted = {str(v) for v in destinations if str(v)}
    if not wanted:
        return False
    _init()
    from app.services import db

    with db._conn() as conn:
        row = conn.execute(
            f"SELECT delivered_json FROM {_TABLE} WHERE event_id = ?",
            (str(event_id),),
        ).fetchone()
        if row is None:
            return True
        try:
            delivered = {str(v) for v in json.loads(row[0] or "[]") if str(v)}
        except Exception:
            delivered = set()
        if not wanted.issubset(delivered):
            return False
        conn.execute(f"DELETE FROM {_TABLE} WHERE event_id = ?", (str(event_id),))
        return True


def clear_for_tests(uid: str | None = None) -> None:
    try:
        _init()
        from app.services import db
        with db._conn() as conn:
            if uid is None:
                conn.execute(f"DELETE FROM {_TABLE}")
            else:
                conn.execute(f"DELETE FROM {_TABLE} WHERE uid = ?", (str(uid),))
    except Exception:
        pass


__all__ = [
    "LifecycleEvent",
    "clear_for_tests",
    "complete_if_delivered",
    "due",
    "enqueue",
    "mark_delivered",
    "mark_failure",
    "pending_user_ids",
]
