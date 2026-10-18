"""Durable Telegram trade-lifecycle outbox invariants."""
from __future__ import annotations

import pytest

from app.services import db
from app.services.notifications import telegram_lifecycle as outbox


@pytest.fixture()
def lifecycle_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "lifecycle.db")
    monkeypatch.setenv("STERLING_DB_PATH", db_path)
    monkeypatch.setattr(db, "_DB_PATH", db_path)
    monkeypatch.setattr(db, "_available", True)
    db.init()
    outbox.clear_for_tests()
    yield
    outbox.clear_for_tests()


def test_completed_delivery_retains_dedupe_identity(lifecycle_db):
    event_id = outbox.enqueue(
        "u1",
        dedupe_key="supertrend:entry:NIFTY:OID1",
        strategy="SuperTrend",
        event_type="ENTRY FILLED",
        title="NIFTY26OCT25000CE",
        detail="Qty 75",
        created_at_ms=1000,
    )
    assert event_id
    assert [event.event_id for event in outbox.due("u1", now_ms=2000)] == [event_id]

    outbox.mark_delivered(event_id, "target-a")
    assert outbox.complete_if_delivered(event_id, ["target-a"]) is True

    assert outbox.due("u1", now_ms=3000) == []
    assert outbox.pending_user_ids() == []
    # A repeated postback/reconcile of the same transition must not recreate it.
    assert outbox.enqueue(
        "u1",
        dedupe_key="supertrend:entry:NIFTY:OID1",
        strategy="SuperTrend",
        event_type="ENTRY FILLED",
        title="NIFTY26OCT25000CE",
        detail="Qty 75",
        created_at_ms=4000,
    ) is None


def test_event_waits_for_every_current_destination(lifecycle_db):
    event_id = outbox.enqueue(
        "u1",
        dedupe_key="gamma:t1:SIG1",
        strategy="Gamma-Unwind",
        event_type="T1 / BREAKEVEN",
        title="RELIANCE26OCT3000CE",
        detail="TSL moved to entry",
        created_at_ms=1000,
    )
    assert event_id

    outbox.mark_delivered(event_id, "target-a")
    assert outbox.complete_if_delivered(
        event_id, ["target-a", "target-b"]
    ) is False
    remaining = outbox.due("u1", now_ms=2000)
    assert len(remaining) == 1
    assert remaining[0].delivered_to == ("target-a",)

    outbox.mark_delivered(event_id, "target-b")
    assert outbox.complete_if_delivered(
        event_id, ["target-a", "target-b"]
    ) is True
    assert outbox.due("u1", now_ms=3000) == []


def test_delivery_failure_uses_retry_backoff(lifecycle_db):
    event_id = outbox.enqueue(
        "u1",
        dedupe_key="gamma:protection-failed:SIG1",
        strategy="Gamma-Unwind",
        event_type="PROTECTION FAILED",
        title="RELIANCE26OCT3000CE",
        detail="broker stop unavailable",
        severity="CRITICAL",
        created_at_ms=1000,
    )
    assert event_id

    outbox.mark_failure(event_id, "telegram unavailable", now_ms=2000)
    assert outbox.due("u1", now_ms=31_999) == []
    due = outbox.due("u1", now_ms=32_000)
    assert len(due) == 1
    assert due[0].attempts == 1
    assert due[0].last_error == "telegram unavailable"


def test_no_destination_is_not_completion(lifecycle_db):
    event_id = outbox.enqueue(
        "u1",
        dedupe_key="supertrend:stop:NIFTY:100",
        strategy="SuperTrend",
        event_type="TSL UPDATED",
        title="NIFTY26OCT25000CE",
        detail="SL 90 -> 100",
        created_at_ms=1000,
    )
    assert outbox.complete_if_delivered(event_id, []) is False
    assert outbox.pending_user_ids() == ["u1"]


def test_busy_database_never_blocks_the_trading_caller_and_is_not_lost(lifecycle_db, monkeypatch):
    """A locked writer must neither stall the event loop nor drop the alert."""
    import sqlite3
    import time

    monkeypatch.setattr(outbox, "_FAST_TIMEOUT_S", 0.05)
    blocker = sqlite3.connect(db._DB_PATH, timeout=0.05)
    blocker.execute("BEGIN IMMEDIATE")  # hold the write lock
    try:
        started = time.monotonic()
        event_id = outbox.enqueue(
            "u1",
            dedupe_key="supertrend:exit:NIFTY:1",
            strategy="SuperTrend",
            event_type="POSITION CLOSED",
            title="NIFTY26OCT25000CE",
            detail="Qty 75",
            severity="CRITICAL",
            created_at_ms=1000,
        )
        elapsed = time.monotonic() - started
        assert event_id
        assert elapsed < 2.0, "enqueue must give up quickly while the DB is busy"
        assert outbox.spooled_count() == 1
    finally:
        blocker.rollback()
        blocker.close()

    # The next drain cycle persists the parked event.
    assert outbox.pending_user_ids() == ["u1"]
    assert outbox.spooled_count() == 0
    assert [e.event_id for e in outbox.due("u1", now_ms=2000)] == [event_id]


def test_overflow_evicts_delivered_rows_before_undelivered_alerts(lifecycle_db, monkeypatch):
    monkeypatch.setattr(outbox, "_MAX_ROWS_PER_USER", 3)
    first = outbox.enqueue("u1", dedupe_key="k1", strategy="SuperTrend", event_type="A",
                           title="t", detail="d", created_at_ms=1000)
    second = outbox.enqueue("u1", dedupe_key="k2", strategy="SuperTrend", event_type="B",
                            title="t", detail="d", created_at_ms=2000)
    third = outbox.enqueue("u1", dedupe_key="k3", strategy="SuperTrend", event_type="C",
                           title="t", detail="d", created_at_ms=3000)
    for event_id in (second, third):
        outbox.mark_delivered(event_id, "d")
        assert outbox.complete_if_delivered(event_id, ["d"])

    assert outbox.enqueue("u1", dedupe_key="k4", strategy="SuperTrend", event_type="D",
                          title="t", detail="d", created_at_ms=4000)

    due_ids = {e.event_id for e in outbox.due("u1", now_ms=5000)}
    assert first in due_ids, "the oldest UNDELIVERED alert must survive overflow"
    assert second not in due_ids and third not in due_ids


def test_backoff_is_bounded_tighter_for_critical_events():
    assert outbox.backoff_seconds(1, "INFO") == 30
    assert outbox.backoff_seconds(2, "INFO") == 60
    assert outbox.backoff_seconds(30, "INFO") == 900
    assert outbox.backoff_seconds(1, "CRITICAL") == 30
    assert outbox.backoff_seconds(30, "CRITICAL") == 120


def test_telegram_flood_wait_hint_extends_the_retry_delay_within_a_cap(lifecycle_db):
    event_id = outbox.enqueue("u1", dedupe_key="k", strategy="Snapback", event_type="X",
                              title="t", detail="d", created_at_ms=1000)
    outbox.mark_failure(event_id, "429", now_ms=10_000, retry_after_s=90)
    assert outbox.due("u1", now_ms=10_000 + 90_000) == []
    assert len(outbox.due("u1", now_ms=10_000 + 91_000)) == 1
    outbox.mark_failure(event_id, "429", now_ms=200_000, retry_after_s=100_000)
    # A hostile/garbled hint is capped at ten minutes.
    assert len(outbox.due("u1", now_ms=200_000 + 601_000)) == 1


def test_undelivered_events_older_than_retention_are_retired(lifecycle_db):
    outbox.enqueue("u1", dedupe_key="old", strategy="Snapback", event_type="X",
                   title="t", detail="d", created_at_ms=1000)
    far_future = 1000 + outbox._MAX_AGE_MS + 5000
    assert outbox.due("u1", now_ms=far_future) == []
    assert outbox.pending_user_ids() == []


def test_flush_failure_does_not_drop_spooled_lifecycle_alert(lifecycle_db, monkeypatch):
    row = dict(
        event_id="TGL-SP-RETAIN", uid="u1", dedupe_key="retain-on-error",
        strategy="Snapback", event_type="PROTECTION FAILED", title="OPT",
        detail="Broker stop uncertain", severity="CRITICAL", created_at_ms=1000,
    )
    with outbox._spool_lock:
        outbox._spool.append(row)

    original_insert = outbox._insert

    def write_fails(_conn, _row):
        raise RuntimeError("transient I/O failure")

    monkeypatch.setattr(outbox, "_insert", write_fails)
    assert outbox._flush_spool() == 0
    assert outbox.spooled_count() == 1

    monkeypatch.setattr(outbox, "_insert", original_insert)
    assert outbox.pending_user_ids() == ["u1"]
    events = outbox.due("u1", now_ms=2000)
    assert [e.event_id for e in events] == ["TGL-SP-RETAIN"]
    assert outbox.spooled_count() == 0


def test_full_spool_keeps_first_alert_and_refuses_new_event(
    lifecycle_db, monkeypatch
):
    import sqlite3

    monkeypatch.setattr(outbox, "_FAST_TIMEOUT_S", 0.03)
    monkeypatch.setattr(outbox, "_SPOOL_MAX", 1)
    blocker = sqlite3.connect(db._DB_PATH, timeout=0.03)
    blocker.execute("BEGIN IMMEDIATE")
    try:
        first = outbox.enqueue(
            "u1", dedupe_key="critical-first", strategy="Gamma-Unwind",
            event_type="PROTECTION FAILED", title="OPT", detail="stop uncertain",
            severity="CRITICAL", created_at_ms=1000,
        )
        assert first is not None
        assert outbox.spooled_count() == 1

        with pytest.raises(RuntimeError, match="spool_full"):
            outbox.enqueue(
                "u1", dedupe_key="second", strategy="SuperTrend",
                event_type="ENTRY FILLED", title="OPT", detail="filled",
                created_at_ms=2000,
            )
        assert outbox.spooled_count() == 1
    finally:
        blocker.rollback()
        blocker.close()

    assert outbox.pending_user_ids() == ["u1"]
    delivered = outbox.due("u1", now_ms=3000)
    assert [e.dedupe_key for e in delivered] == ["critical-first"]


def test_unreadable_lifecycle_queue_health_is_unknown_not_zero(
    lifecycle_db, monkeypatch
):
    from contextlib import contextmanager

    @contextmanager
    def unavailable(*args, **kwargs):
        raise OSError("queue storage unreadable")
        yield

    monkeypatch.setattr(outbox, "_fast_conn", unavailable)
    status = outbox.pending_summary("u1")
    assert status["readable"] is False
    assert status["pending"] is None
    assert status["error"] == "OSError"


def test_readable_empty_lifecycle_queue_explicitly_reports_zero(lifecycle_db):
    status = outbox.pending_summary("u1")
    assert status["readable"] is True
    assert status["pending"] == 0


def test_durable_outbox_full_refuses_new_alert_without_deleting_pending(
    lifecycle_db, monkeypatch
):
    monkeypatch.setattr(outbox, "_MAX_ROWS_PER_USER", 2)
    first = outbox.enqueue(
        "u1", dedupe_key="first-critical", strategy="Snapback",
        event_type="PROTECTION FAILED", title="OPT", detail="stop unknown",
        severity="CRITICAL", created_at_ms=1000,
    )
    second = outbox.enqueue(
        "u1", dedupe_key="second-critical", strategy="Gamma-Unwind",
        event_type="PROTECTION FAILED", title="OPT", detail="stop unknown",
        severity="CRITICAL", created_at_ms=2000,
    )
    assert first and second

    with pytest.raises(RuntimeError, match="outbox_full"):
        outbox.enqueue(
            "u1", dedupe_key="third", strategy="SuperTrend",
            event_type="ENTRY FILLED", title="OPT", detail="new event",
            created_at_ms=3000,
        )

    events = outbox.due("u1", now_ms=4000)
    assert {event.event_id for event in events} == {first, second}
    assert {event.dedupe_key for event in events} == {
        "first-critical", "second-critical",
    }
    # Best-effort producers get an explicit failed enqueue (not a fake ID).
    assert outbox.enqueue_best_effort(
        "u1", dedupe_key="fourth", strategy="SuperTrend",
        event_type="TRAIL UPDATED", title="OPT", detail="alert",
        created_at_ms=5000,
    ) is None
    assert {event.event_id for event in outbox.due("u1", now_ms=6000)} == {
        first, second,
    }


def test_new_alert_does_not_prune_undelivered_old_alert_by_age(lifecycle_db):
    old = outbox.enqueue(
        "u1", dedupe_key="old-pending", strategy="Gamma-Unwind",
        event_type="PROTECTION FAILED", title="OPT",
        detail="not yet delivered", severity="CRITICAL", created_at_ms=1000,
    )
    new = outbox.enqueue(
        "u1", dedupe_key="new-pending", strategy="SuperTrend",
        event_type="ENTRY FILLED", title="OPT", detail="new",
        created_at_ms=1000 + outbox._MAX_AGE_MS + 60_000,
    )
    assert old and new
    # Check stored rows before due() intentionally marks old entries expired.
    with outbox._fast_conn() as conn:
        rows = conn.execute(
            f"SELECT event_id FROM {outbox._TABLE} WHERE uid = ? AND completed = 0",
            ("u1",),
        ).fetchall()
    assert {row[0] for row in rows} == {old, new}


def test_pending_user_discovery_preserves_spooled_tenants_when_db_unavailable(
    lifecycle_db, monkeypatch,
):
    # Persisted queue discovery may fail after a disk-full or DB outage.
    # Users whose events remain in this process's spool must still be visible.
    row = {
        "event_id": "TGL-DISCOVERY", "uid": "owner-in-spool",
        "dedupe_key": "risk:unknown", "strategy": "Gamma-Unwind",
        "event_type": "PROTECTION FAILED", "title": "OPT",
        "detail": "outcome unknown", "severity": "CRITICAL",
        "created_at_ms": 1000,
    }
    with outbox._spool_lock:
        outbox._spool.append(row)

    def fail_init():
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(outbox, "_init", fail_init)
    assert outbox.pending_user_ids() == ["owner-in-spool"]
    assert outbox.spooled_count() == 1


def test_lifecycle_outbox_cannot_recreate_missing_canonical_database(
    lifecycle_db, monkeypatch, tmp_path
):
    missing = tmp_path / "deleted-canonical.db"
    assert not missing.exists()
    monkeypatch.setattr(db, "_DB_PATH", str(missing))
    monkeypatch.setattr(db, "_available", True)

    with pytest.raises(FileNotFoundError, match="durable database missing"):
        outbox.enqueue(
            "u1", dedupe_key="alert-after-database-loss",
            strategy="Gamma-Unwind", event_type="PROTECTION FAILED",
            title="OPT", detail="cannot confirm protection",
            severity="CRITICAL",
        )
    assert not missing.exists(), "alert transport must never create a fresh empty journal"


def test_lifecycle_queue_read_reports_unknown_if_database_file_disappears(
    lifecycle_db, monkeypatch, tmp_path
):
    missing = tmp_path / "deleted-canonical.db"
    monkeypatch.setattr(db, "_DB_PATH", str(missing))
    monkeypatch.setattr(db, "_available", True)
    summary = outbox.pending_summary("u1")
    assert summary["readable"] is False
    assert summary["pending"] is None
    assert not missing.exists()
