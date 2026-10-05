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
