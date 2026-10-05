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
