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
import logging
import os
import sqlite3
import threading
import time
import uuid
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterable

_TABLE = "telegram_lifecycle_outbox"
_MAX_AGE_MS = 7 * 24 * 60 * 60 * 1000
_MAX_ROWS_PER_USER = 1000
#: Writers run on trading coroutines.  The shared ``db._conn()`` waits up to 15 s
#: for a busy writer; an alert must never hold the event loop that long, so the
#: outbox uses a short lock timeout and spools in memory on contention instead.
_FAST_TIMEOUT_S = 1.5
_SPOOL_MAX = 500
log = logging.getLogger(__name__)

_init_lock = threading.Lock()
_initialised_paths: set[str] = set()
_spool: "deque[dict]" = deque(maxlen=_SPOOL_MAX)
_spool_lock = threading.Lock()


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


@contextmanager
def _fast_conn(timeout_s: float | None = None):
    """Short-timeout connection to the shared SQLite store (see ``_FAST_TIMEOUT_S``)."""
    from app.services import db

    timeout_s = _FAST_TIMEOUT_S if timeout_s is None else timeout_s
    conn = sqlite3.connect(db._DB_PATH, timeout=timeout_s)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute(f"PRAGMA busy_timeout={int(timeout_s * 1000)}")
        conn.execute("PRAGMA synchronous=NORMAL")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _init() -> None:
    """Create the table once per database path (not on every alert)."""
    from app.services import db

    if not db.is_available():
        raise RuntimeError("lifecycle alert outbox database unavailable")
    path = str(db._DB_PATH)
    if path in _initialised_paths and os.path.exists(path):
        return
    with _init_lock:
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
                    completed       INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(uid, dedupe_key)
                )
                """
            )
            columns = {
                str(row[1])
                for row in conn.execute(f"PRAGMA table_info({_TABLE})").fetchall()
            }
            if "completed" not in columns:
                conn.execute(
                    f"ALTER TABLE {_TABLE} ADD COLUMN completed INTEGER NOT NULL DEFAULT 0"
                )
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS ix_{_TABLE}_due "
                f"ON {_TABLE}(uid, completed, next_attempt_ms, created_at_ms)"
            )
        _initialised_paths.add(path)


def _insert(conn, row: dict) -> bool:
    cur = conn.execute(
        f"""
        INSERT OR IGNORE INTO {_TABLE} (
            event_id, uid, dedupe_key, strategy, event_type, title, detail,
            severity, created_at_ms, delivered_json, attempts,
            next_attempt_ms, last_error, completed
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, '[]', 0, 0, '', 0)
        """,
        (
            row["event_id"], row["uid"], row["dedupe_key"], row["strategy"],
            row["event_type"], row["title"], row["detail"], row["severity"],
            row["created_at_ms"],
        ),
    )
    if cur.rowcount != 1:
        return False

    # Bound a pathological undeliverable queue.  Aged rows go first; on count
    # overflow, already-delivered rows (kept only for dedupe) are evicted before
    # any event that has not reached Telegram yet.
    owner = row["uid"]
    conn.execute(
        f"DELETE FROM {_TABLE} WHERE uid = ? AND created_at_ms < ?",
        (owner, row["created_at_ms"] - _MAX_AGE_MS),
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
                ORDER BY completed DESC, created_at_ms ASC
                LIMIT ?
            )
            """,
            (owner, overflow),
        )
    return True


def _flush_spool() -> int:
    """Persist events that could not be written while the database was busy."""
    if not _spool:
        return 0
    flushed = 0
    while True:
        with _spool_lock:
            if not _spool:
                break
            row = _spool[0]
        try:
            _init()
            with _fast_conn() as conn:
                _insert(conn, row)
        except sqlite3.OperationalError:
            break  # still contended; keep the rest spooled for the next cycle
        except Exception as exc:  # noqa: BLE001
            log.warning("Telegram lifecycle spool entry dropped: %s", exc)
        with _spool_lock:
            if _spool and _spool[0] is row:
                _spool.popleft()
        flushed += 1
    return flushed


def spooled_count() -> int:
    return len(_spool)


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

    The write never waits longer than ``_FAST_TIMEOUT_S`` for the database: on
    writer contention the event is parked in a bounded in-memory spool and
    flushed by the next drain cycle, so a busy database cannot stall the trading
    coroutine that raised the alert.
    """
    _init()
    owner = str(uid or "").strip()
    key = str(dedupe_key or "").strip()
    if not owner or not key:
        raise ValueError("lifecycle alert uid and dedupe_key are required")
    row = {
        "event_id": "TGL-" + uuid.uuid4().hex,
        "uid": owner,
        "dedupe_key": key,
        "strategy": str(strategy or "Sterling"),
        "event_type": str(event_type or "UPDATE"),
        "title": str(title or ""),
        "detail": str(detail or ""),
        "severity": str(severity or "INFO").upper(),
        "created_at_ms": int(created_at_ms or _now_ms()),
    }
    try:
        with _fast_conn() as conn:
            inserted = _insert(conn, row)
    except sqlite3.OperationalError as exc:
        # "database is locked": do not block the caller, do not lose the alert.
        with _spool_lock:
            _spool.append(row)
        log.warning(
            "Telegram lifecycle alert spooled (database busy) [%s/%s]: %s",
            row["strategy"], row["event_type"], exc,
        )
        return row["event_id"]
    return row["event_id"] if inserted else None


def enqueue_best_effort(
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
    """Persist an alert without allowing notification failure to alter trading."""
    try:
        return enqueue(
            uid,
            dedupe_key=dedupe_key,
            strategy=strategy,
            event_type=event_type,
            title=title,
            detail=detail,
            severity=severity,
            created_at_ms=created_at_ms,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning(
            "Telegram lifecycle alert could not be persisted [%s/%s]: %s",
            strategy, event_type, exc,
        )
        return None


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


_DRAIN_TIMEOUT_S = 5.0


def due(uid: str, *, now_ms: int | None = None, limit: int = 50) -> list[LifecycleEvent]:
    _init()
    _flush_spool()
    now = int(now_ms or _now_ms())

    with _fast_conn(_DRAIN_TIMEOUT_S) as conn:
        # An alert this old is history, not news; stop retrying it forever.
        conn.execute(
            f"""
            UPDATE {_TABLE}
            SET completed = 1, next_attempt_ms = 0, last_error = 'expired undelivered'
            WHERE uid = ? AND completed = 0 AND created_at_ms < ?
            """,
            (str(uid), now - _MAX_AGE_MS),
        )
        rows = conn.execute(
            f"""
            SELECT * FROM {_TABLE}
            WHERE uid = ? AND completed = 0 AND next_attempt_ms <= ?
            ORDER BY created_at_ms ASC
            LIMIT ?
            """,
            (str(uid), now, max(1, min(int(limit), 200))),
        ).fetchall()
    return [_event(row) for row in rows]


def pending_user_ids() -> list[str]:
    try:
        _init()
        _flush_spool()
        with _fast_conn() as conn:
            rows = conn.execute(
                f"SELECT DISTINCT uid FROM {_TABLE} WHERE completed = 0 ORDER BY uid"
            ).fetchall()
        return [str(row[0]) for row in rows if str(row[0] or "").strip()]
    except Exception:
        return []


def pending_summary(uid: str, *, now_ms: int | None = None) -> dict:
    """Operator-facing queue health: how many alerts are waiting and for how long."""
    now = int(now_ms or _now_ms())
    try:
        _init()
        with _fast_conn() as conn:
            row = conn.execute(
                f"""
                SELECT COUNT(*) AS n, MIN(created_at_ms) AS oldest,
                       COALESCE(MAX(attempts), 0) AS max_attempts
                FROM {_TABLE} WHERE uid = ? AND completed = 0
                """,
                (str(uid),),
            ).fetchone()
        n = int(row["n"] or 0)
        oldest = int(row["oldest"] or 0)
        return {
            "pending": n,
            "oldest_age_s": max(0, (now - oldest) // 1000) if n and oldest else 0,
            "max_attempts": int(row["max_attempts"] or 0),
            "spooled": spooled_count(),
        }
    except Exception:  # noqa: BLE001
        return {"pending": 0, "oldest_age_s": 0, "max_attempts": 0, "spooled": spooled_count()}


def mark_delivered(event_id: str, destination: str) -> None:
    _init()

    with _fast_conn(_DRAIN_TIMEOUT_S) as conn:
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


def backoff_seconds(attempts: int, severity: str = "INFO") -> int:
    """Retry delay: 30 s doubling per attempt, capped so a halt alert is never parked.

    CRITICAL/ERROR events (protection failed, kill switch, unknown outcome) cap at
    2 minutes; everything else at 15 minutes.
    """
    cap = 120 if str(severity or "").upper() in {"CRITICAL", "ERROR"} else 900
    return min(cap, max(30, 30 * (2 ** min(max(int(attempts), 1) - 1, 6))))


def mark_failure(
    event_id: str,
    error: str,
    *,
    now_ms: int | None = None,
    retry_after_s: float | None = None,
) -> None:
    _init()
    now = int(now_ms or _now_ms())

    with _fast_conn(_DRAIN_TIMEOUT_S) as conn:
        row = conn.execute(
            f"SELECT attempts, severity FROM {_TABLE} WHERE event_id = ?",
            (str(event_id),),
        ).fetchone()
        if row is None:
            return
        attempts = int(row[0] or 0) + 1
        delay_s = backoff_seconds(attempts, str(row[1] or "INFO"))
        if retry_after_s:
            # Honour Telegram's own flood-wait hint, bounded so one bad reply
            # cannot park an alert for hours.
            delay_s = max(delay_s, min(int(retry_after_s) + 1, 600))
        conn.execute(
            f"""
            UPDATE {_TABLE}
            SET attempts = ?, next_attempt_ms = ?, last_error = ?
            WHERE event_id = ?
            """,
            (attempts, now + delay_s * 1000, str(error or "")[:500], str(event_id)),
        )


def complete_if_delivered(event_id: str, destinations: Iterable[str]) -> bool:
    """Mark the event done once every currently configured destination acknowledged it.

    The row is retained (``completed = 1``) so its dedupe identity keeps
    suppressing a replay of the same transition.
    """
    wanted = {str(v) for v in destinations if str(v)}
    if not wanted:
        return False
    _init()

    with _fast_conn(_DRAIN_TIMEOUT_S) as conn:
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
        conn.execute(
            f"""
            UPDATE {_TABLE}
            SET completed = 1, next_attempt_ms = 0, last_error = ''
            WHERE event_id = ?
            """,
            (str(event_id),),
        )
        return True


def clear_for_tests(uid: str | None = None) -> None:
    with _spool_lock:
        _spool.clear()
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
    "backoff_seconds",
    "clear_for_tests",
    "complete_if_delivered",
    "due",
    "enqueue",
    "enqueue_best_effort",
    "mark_delivered",
    "mark_failure",
    "pending_summary",
    "pending_user_ids",
    "spooled_count",
]
