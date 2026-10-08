"""Durable alert outbox.

Creating an alert and delivering it are different facts. Scheduling an async send and
returning is not delivery: a network failure after that point is invisible, so the
dispatcher counts a notification nobody received — and one silently lost CRITICAL is
enough to make every other alert untrustworthy.

Every alert is persisted first, claimed by a worker, and only marked DELIVERED when
the transport actually said so.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional

log = logging.getLogger(__name__)

_IST = timezone(timedelta(hours=5, minutes=30))


class OutboxStatus:
    PENDING = "PENDING"
    DELIVERING = "DELIVERING"
    DELIVERED = "DELIVERED"
    RETRY = "RETRY"
    DEAD = "DEAD"


# Bounded retry: a transport that has failed this many times needs a human, not
# another attempt.
BACKOFF_SECONDS = (0, 30, 120, 300, 900, 1800)
MAX_ATTEMPTS = len(BACKOFF_SECONDS)

# A claimed alert that has not been resolved inside this lease belonged to a worker
# that died (crash, restart, kill) between claiming it and recording the outcome.
# Left alone it would sit in DELIVERING forever: never retried, never DEAD, counted
# as "pending" and silent. It is made claimable again instead.
CLAIM_LEASE_SECONDS = 300

# A fault that clears and returns inside this window is a flap, not a new incident;
# one notification per flap would bury the operator.
RECURRENCE_MIN_GAP = timedelta(minutes=10)


@dataclass
class OutboxResult:
    delivered: int = 0
    failed: int = 0
    skipped: int = 0
    dead: int = 0


def dedupe_key_for(*, code: str, trading_session: Optional[str] = None,
                   entity: Optional[str] = None, occurrence: int = 0) -> str:
    """One fault, one session, one entity, one occurrence: one alert.

    Occurrence 0 keeps the historical key. A reminder or a recurrence of the same
    fault carries a higher occurrence and therefore its own key.
    """
    parts = [code]
    if trading_session:
        parts.append(trading_session)
    if entity:
        parts.append(entity)
    if occurrence and int(occurrence) > 0:
        parts.append(f"occ{int(occurrence)}")
    return ":".join(parts)


def _iso(value: datetime) -> str:
    return value.isoformat()


def _parse_iso(value: Any) -> Optional[datetime]:
    """Parse a stored timestamp; a naive one is UTC, which is how rows are written."""
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


class AlertOutbox:
    def __init__(self, warehouse=None) -> None:
        if warehouse is None:
            from app.services.snapback_prospective_collector import SnapbackObservationWarehouse

            warehouse = SnapbackObservationWarehouse()
        self.warehouse = warehouse

    # ---------------------------------------------------------------- enqueue

    def enqueue(
        self,
        alert: Any,
        *,
        trading_session: Optional[str] = None,
        opportunity_id: Optional[str] = None,
        now: Optional[datetime] = None,
        occurrence: int = 0,
    ) -> Optional[str]:
        """Persist one alert for delivery. A duplicate in the same session is dropped.

        ``occurrence`` > 0 marks a reminder or a recurrence; it is queued under its
        own key unless the previous notification for the code was queued less than
        ``RECURRENCE_MIN_GAP`` ago (a flapping fault).
        """
        now = now or datetime.now(timezone.utc)
        occurrence = max(0, int(occurrence or 0))
        code = str(getattr(alert, "code", "") or "")
        dedupe = dedupe_key_for(
            code=code, trading_session=trading_session, entity=opportunity_id,
            occurrence=occurrence,
        )

        payload = {
            "code": code,
            "severity": str(getattr(alert, "severity", "WARNING") or "WARNING"),
            "title": str(getattr(alert, "title", "") or ""),
            "message": str(getattr(alert, "message", "") or ""),
            "trading_session": trading_session,
            "opportunity_id": opportunity_id,
        }
        if occurrence:
            payload["occurrence"] = occurrence
        payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        payload_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        alert_id = f"ALERT-{uuid.uuid4().hex}"

        conn = self.warehouse._get_connection()
        try:
            with conn:
                if occurrence:
                    last = conn.execute(
                        "SELECT MAX(created_at) AS last_at FROM operational_alert_outbox "
                        "WHERE code = ? AND trading_session IS ? AND opportunity_id IS ?",
                        (code, trading_session, opportunity_id),
                    ).fetchone()
                    last_at = _parse_iso(last["last_at"]) if last and last["last_at"] else None
                    if last_at is not None and (now - last_at) < RECURRENCE_MIN_GAP:
                        return None

                # The unique index, not a read-then-write, is the dedupe authority:
                # two workers racing the same fault cannot both insert it.
                cur = conn.execute(
                    """
                    INSERT OR IGNORE INTO operational_alert_outbox (
                        alert_id, dedupe_key, code, severity, trading_session,
                        opportunity_id, payload_json, created_at, available_after,
                        status, payload_hash
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        alert_id, dedupe, code, payload["severity"], trading_session,
                        opportunity_id, payload_json, _iso(now), _iso(now),
                        OutboxStatus.PENDING, payload_hash,
                    ),
                )
                if cur.rowcount != 1:
                    return None
        finally:
            conn.close()

        return alert_id

    # ---------------------------------------------------------------- process

    async def process(
        self,
        *,
        send: Callable[[Dict[str, Any]], Awaitable[Any]],
        now: Optional[datetime] = None,
        limit: int = 20,
    ) -> OutboxResult:
        """Attempt delivery of every due item, recording what actually happened.

        SQLite work runs on a worker thread: this coroutine is driven from the
        scheduler's event loop, and a database lock held there stalls every other
        task in the process, including the liveness endpoint.
        """
        now = now or datetime.now(timezone.utc)
        result = OutboxResult()

        due = await asyncio.to_thread(self._claim_due, now=now, limit=limit)
        for row in due:
            payload = json.loads(row["payload_json"])
            # When the fault was detected, not when a retry finally got through.
            payload["created_at"] = row.get("created_at")
            attempt = int(row["attempt_count"] or 0) + 1
            try:
                message_id = await send(payload)
            except Exception as exc:
                # The transport failed: this is recorded as a failure, never as a
                # delivery, and retried with a bounded backoff.
                status = OutboxStatus.RETRY if attempt < MAX_ATTEMPTS else OutboxStatus.DEAD
                if status == OutboxStatus.DEAD:
                    result.dead += 1
                    log.error(
                        "Alert %s exhausted delivery attempts and is DEAD: %s",
                        row["code"], exc,
                    )
                await asyncio.to_thread(
                    self._record_failure, row["alert_id"], attempt, str(exc), status, now,
                )
                result.failed += 1
                continue

            await asyncio.to_thread(
                self._record_success, row["alert_id"], attempt, str(message_id or ""), now,
            )
            result.delivered += 1

        return result

    def _claim_due(self, *, now: datetime, limit: int) -> List[Dict[str, Any]]:
        """Atomically lease the due items to this worker.

        ``BEGIN IMMEDIATE`` takes the write lock before the read, so two workers
        cannot both select the same row. A DELIVERING row whose lease has run out is
        reclaimed; each reclaim counts as an attempt so an alert that keeps killing
        its worker ends DEAD instead of cycling.
        """
        lease_until = _iso(now + timedelta(seconds=CLAIM_LEASE_SECONDS))
        conn = self.warehouse._get_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                rows = conn.execute(
                    """
                    SELECT * FROM operational_alert_outbox
                    WHERE status IN (?, ?, ?) AND available_after <= ?
                    ORDER BY created_at ASC LIMIT ?
                    """,
                    (OutboxStatus.PENDING, OutboxStatus.RETRY, OutboxStatus.DELIVERING,
                     _iso(now), int(limit)),
                ).fetchall()
                claimed: List[Dict[str, Any]] = []
                for raw in rows:
                    row = dict(raw)
                    if row["status"] == OutboxStatus.DELIVERING:
                        attempts = int(row["attempt_count"] or 0) + 1
                        if attempts >= MAX_ATTEMPTS:
                            conn.execute(
                                "UPDATE operational_alert_outbox SET status = ?, "
                                "attempt_count = ?, last_attempt_at = ?, last_error = ? "
                                "WHERE alert_id = ?",
                                (OutboxStatus.DEAD, attempts, _iso(now),
                                 "delivery_interrupted", row["alert_id"]),
                            )
                            log.error("Alert %s is DEAD: delivery was interrupted %d times",
                                      row["code"], attempts)
                            continue
                        conn.execute(
                            "UPDATE operational_alert_outbox SET attempt_count = ?, "
                            "last_attempt_at = ?, last_error = ?, available_after = ? "
                            "WHERE alert_id = ?",
                            (attempts, _iso(now), "delivery_interrupted", lease_until,
                             row["alert_id"]),
                        )
                        row["attempt_count"] = attempts
                    else:
                        conn.execute(
                            "UPDATE operational_alert_outbox SET status = ?, "
                            "available_after = ? WHERE alert_id = ?",
                            (OutboxStatus.DELIVERING, lease_until, row["alert_id"]),
                        )
                    claimed.append(row)
                conn.commit()
                return claimed
            except BaseException:
                conn.rollback()
                raise
        finally:
            conn.close()

    def _record_success(self, alert_id: str, attempt: int, message_id: str,
                        now: datetime) -> None:
        conn = self.warehouse._get_connection()
        try:
            with conn:
                conn.execute(
                    """
                    UPDATE operational_alert_outbox
                    SET status = ?, delivered_at = ?, delivery_message_id = ?,
                        attempt_count = ?, last_attempt_at = ?, last_error = NULL
                    WHERE alert_id = ?
                    """,
                    (OutboxStatus.DELIVERED, _iso(now), message_id, attempt,
                     _iso(now), alert_id),
                )
        finally:
            conn.close()

    def _record_failure(self, alert_id: str, attempt: int, error: str, status: str,
                        now: datetime) -> None:
        backoff = BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS) - 1)]
        available_after = now + timedelta(seconds=backoff)
        conn = self.warehouse._get_connection()
        try:
            with conn:
                conn.execute(
                    """
                    UPDATE operational_alert_outbox
                    SET status = ?, attempt_count = ?, last_attempt_at = ?,
                        last_error = ?, available_after = ?
                    WHERE alert_id = ?
                    """,
                    (status, attempt, _iso(now), error, _iso(available_after), alert_id),
                )
        finally:
            conn.close()

    # ----------------------------------------------------------------- health

    def health(self) -> Dict[str, Any]:
        conn = self.warehouse._get_connection()
        try:
            with conn:
                rows = conn.execute(
                    "SELECT status, COUNT(*) AS n FROM operational_alert_outbox GROUP BY status"
                ).fetchall()
                counts = {str(r["status"]): int(r["n"]) for r in rows}

                last_ok = conn.execute(
                    "SELECT delivered_at FROM operational_alert_outbox "
                    "WHERE delivered_at IS NOT NULL ORDER BY delivered_at DESC LIMIT 1"
                ).fetchone()
                last_err = conn.execute(
                    "SELECT last_error, last_attempt_at FROM operational_alert_outbox "
                    "WHERE last_error IS NOT NULL ORDER BY last_attempt_at DESC LIMIT 1"
                ).fetchone()
        finally:
            conn.close()

        return {
            "alert_outbox_pending": counts.get(OutboxStatus.PENDING, 0)
            + counts.get(OutboxStatus.RETRY, 0)
            + counts.get(OutboxStatus.DELIVERING, 0),
            "alert_outbox_dead": counts.get(OutboxStatus.DEAD, 0),
            "last_alert_delivery_success": last_ok["delivered_at"] if last_ok else None,
            "last_alert_delivery_error": last_err["last_error"] if last_err else None,
            "last_alert_delivery_at": last_err["last_attempt_at"] if last_err else None,
        }


def _calendar_says_trading_day(day) -> bool:
    """Whether `day` is an NSE trading day. A calendar that cannot answer counts as
    one: for an alert about a missing login, a false alarm is cheaper than silence."""
    try:
        from app.services.navigator.calendar import is_trading_day

        return bool(is_trading_day(day))
    except Exception as exc:  # noqa: BLE001
        log.warning("Alert outbox: calendar unavailable, assuming a trading day: %s", exc)
        return True


def morning_login_alert(
    outbox: AlertOutbox,
    *,
    broker_connected: bool,
    now: datetime,
    trading_session: str,
    is_trading_day: Optional[Callable[[Any], bool]] = None,
) -> None:
    """Warn before the open, escalate if the session is about to start regardless.

    Keyed by session so yesterday's alert cannot suppress this morning's. Only
    meaningful on a trading day between 08:45 IST and the close: the previous form
    fired at 20:00 and on Saturdays, telling the operator that "the session is about
    to start" when it had ended or would not happen.
    """
    from app.services.snapback_alerts import CRITICAL, WARNING, OperationalAlert

    if broker_connected:
        return

    local = now.astimezone(_IST) if now.tzinfo else now
    minutes = local.hour * 60 + local.minute

    if minutes < 8 * 60 + 45 or minutes > 15 * 60 + 30:
        return
    if not (is_trading_day or _calendar_says_trading_day)(local.date()):
        return

    if minutes >= 9 * 60 + 10:
        started = minutes >= 9 * 60 + 15
        outbox.enqueue(
            OperationalAlert(
                code="broker_login_required_critical", severity=CRITICAL,
                title="Broker login required NOW",
                message=(
                    "The session has started and Sterling is not connected to the "
                    "broker. No entry or exit can be observed until you log in."
                    if started else
                    "The session is about to start and Sterling is not connected "
                    "to the broker. No entry can be observed until you log in."
                ),
            ),
            trading_session=trading_session, now=now,
        )
    else:
        outbox.enqueue(
            OperationalAlert(
                code="broker_login_required", severity=WARNING,
                title="Broker login required before today's session",
                message="Log in to the broker before 09:15 IST.",
            ),
            trading_session=trading_session, now=now,
        )
