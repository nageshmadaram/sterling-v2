"""Snapback alert delivery audit: recurrence, crashed workers, wrong-day alerts, rendering.

The failure each test pins is a notification the operator did not get, or got wrong:
a recurrence swallowed as a duplicate, a claimed alert stranded by a crash, a login
warning on a Saturday, a message Telegram rejects.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from datetime import date, datetime, timedelta, timezone

import pytest

from app.services.snapback_alert_outbox import (
    CLAIM_LEASE_SECONDS,
    MAX_ATTEMPTS,
    RECURRENCE_MIN_GAP,
    AlertOutbox,
    OutboxStatus,
    morning_login_alert,
)
from app.services.snapback_alert_telegram import CompositeAlertSink, format_alert_html
from app.services.snapback_alerts import (
    CRITICAL,
    AlertDispatcher,
    OperationalAlert,
    OutboxAlertSink,
    derive_operational_alerts,
)
from app.services.snapback_observation_warehouse import SnapbackObservationWarehouse

_IST = timezone(timedelta(hours=5, minutes=30))
NOW = datetime(2026, 10, 15, 9, 0, tzinfo=timezone.utc)


@pytest.fixture
def warehouse():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as fh:
        path = fh.name
    wh = SnapbackObservationWarehouse(db_path=path)
    yield wh
    if os.path.exists(path):
        os.remove(path)


@pytest.fixture
def outbox(warehouse):
    return AlertOutbox(warehouse=warehouse)


def _alert(code="runner_stalled", severity="WARNING", **kw):
    return OperationalAlert(code=code, severity=severity, title="Runner stalled",
                            message="The background runner has missed cycles.", **kw)


def _rows(warehouse):
    return warehouse.get_records_by_table("operational_alert_outbox")


# ------------------------------------------------------------------- recurrence

def test_a_recurrence_after_recovery_is_queued_not_dropped_as_a_duplicate(outbox, warehouse):
    outbox.enqueue(_alert(), trading_session="2026-10-15", now=NOW)
    # Same fault, same session, but a later episode (occurrence 1), well past the flap gap.
    outbox.enqueue(_alert(occurrence=1), trading_session="2026-10-15", occurrence=1,
                   now=NOW + RECURRENCE_MIN_GAP + timedelta(minutes=1))
    assert len(_rows(warehouse)) == 2


def test_a_flapping_fault_is_not_queued_every_time(outbox, warehouse):
    outbox.enqueue(_alert(), trading_session="2026-10-15", now=NOW)
    outbox.enqueue(_alert(occurrence=1), trading_session="2026-10-15", occurrence=1,
                   now=NOW + timedelta(minutes=2))
    assert len(_rows(warehouse)) == 1


def test_dispatcher_numbers_each_send_so_the_durable_sink_can_tell_them_apart():
    sent = []

    class Sink:
        def send(self, alert):
            sent.append(alert.occurrence)

    dispatcher = AlertDispatcher(sink=Sink(), repeat_after=timedelta(hours=4))
    fault = _alert()
    t0 = datetime(2026, 10, 15, 10, 0, tzinfo=_IST)
    dispatcher.dispatch([fault], now=t0)
    dispatcher.dispatch([fault], now=t0 + timedelta(minutes=10))     # same episode: quiet
    dispatcher.dispatch([], now=t0 + timedelta(minutes=30))          # recovered
    dispatcher.dispatch([fault], now=t0 + timedelta(minutes=45))     # and back
    assert sent == [0, 1]


def test_a_failed_send_is_retried_not_remembered_as_sent():
    attempts = []

    class Flaky:
        def send(self, alert):
            attempts.append(1)
            if len(attempts) == 1:
                raise RuntimeError("down")

    dispatcher = AlertDispatcher(sink=Flaky(), repeat_after=timedelta(hours=4))
    t0 = datetime(2026, 10, 15, 10, 0, tzinfo=_IST)
    first = dispatcher.dispatch([_alert()], now=t0)
    second = dispatcher.dispatch([_alert()], now=t0 + timedelta(seconds=30))
    assert (first.failed, first.sent) == (1, 0)
    assert (second.failed, second.sent) == (0, 1)


# ----------------------------------------------------------- crashed delivery

def test_an_alert_claimed_by_a_worker_that_died_is_delivered_after_the_lease(outbox, warehouse):
    outbox.enqueue(_alert(), trading_session="2026-10-15", now=NOW)
    first = outbox._claim_due(now=NOW, limit=10)
    assert len(first) == 1
    # Worker dies. Inside the lease nobody else may take it...
    assert outbox._claim_due(now=NOW + timedelta(seconds=CLAIM_LEASE_SECONDS - 1), limit=10) == []
    # ...after the lease it is claimable again, and the interruption counts as an attempt.
    again = outbox._claim_due(now=NOW + timedelta(seconds=CLAIM_LEASE_SECONDS + 1), limit=10)
    assert len(again) == 1 and again[0]["attempt_count"] == 1


def test_an_alert_that_keeps_killing_its_worker_ends_dead_not_pending_forever(outbox, warehouse):
    outbox.enqueue(_alert(), trading_session="2026-10-15", now=NOW)
    t = NOW
    for _ in range(MAX_ATTEMPTS + 2):
        outbox._claim_due(now=t, limit=10)
        t += timedelta(seconds=CLAIM_LEASE_SECONDS + 1)
    assert _rows(warehouse)[0]["status"] == OutboxStatus.DEAD


def test_delivery_reports_when_the_fault_was_detected_not_when_it_got_through(outbox):
    outbox.enqueue(_alert(), trading_session="2026-10-15", now=NOW)
    seen = []

    async def send(payload):
        seen.append(payload)
        return "m1"

    result = asyncio.run(outbox.process(send=send, now=NOW + timedelta(hours=1)))
    assert result.delivered == 1
    assert seen[0]["created_at"] == NOW.isoformat()


# ---------------------------------------------------------- wrong-day alerts

def _login_rows(warehouse):
    return [r["code"] for r in _rows(warehouse)]


@pytest.mark.parametrize("hour,minute", [(20, 0), (7, 0), (15, 45)])
def test_no_login_alert_outside_the_trading_session(outbox, warehouse, hour, minute):
    at = datetime(2026, 10, 15, hour, minute, tzinfo=_IST)
    morning_login_alert(outbox, broker_connected=False, now=at, trading_session="2026-10-15",
                        is_trading_day=lambda d: True)
    assert _login_rows(warehouse) == []


def test_no_login_alert_on_a_weekend(outbox, warehouse):
    saturday = datetime(2026, 10, 17, 9, 12, tzinfo=_IST)
    morning_login_alert(outbox, broker_connected=False, now=saturday,
                        trading_session="2026-10-17",
                        is_trading_day=lambda d: d.weekday() < 5)
    assert _login_rows(warehouse) == []


def test_login_alert_escalates_and_says_the_session_has_started_after_the_open(outbox, warehouse):
    at = datetime(2026, 10, 15, 9, 20, tzinfo=_IST)
    morning_login_alert(outbox, broker_connected=False, now=at, trading_session="2026-10-15",
                        is_trading_day=lambda d: True)
    rows = _rows(warehouse)
    assert [r["code"] for r in rows] == ["broker_login_required_critical"]
    assert "has started" in rows[0]["payload_json"]


def test_broker_disconnect_is_not_a_fault_on_a_holiday():
    health = {"broker_connected": False, "trading_day": False, "runner_alive": True,
              "database_ok": True, "manifest_ok": True, "calendar_ok": True,
              "market_data_fresh": True}
    assert "broker_disconnected" not in [
        a.code for a in derive_operational_alerts(health=health, backup_ok=True)]
    health["trading_day"] = None            # calendar could not answer: never silence it
    assert "broker_disconnected" in [
        a.code for a in derive_operational_alerts(health=health, backup_ok=True)]


# ---------------------------------------------------------------- rendering

def test_rendered_alert_is_escaped_dated_in_ist_and_within_telegram_limits():
    alert = _alert(severity=CRITICAL)
    object.__setattr__(alert, "message", "<b>boom</b> & " + "x" * 6000)
    object.__setattr__(alert, "title", "Runner <stalled>")
    html = format_alert_html(alert, now=datetime(2026, 10, 15, 4, 0, tzinfo=timezone.utc))
    assert "&lt;b&gt;boom&lt;/b&gt;" in html and "Runner &lt;stalled&gt;" in html
    assert "2026-10-15 09:30:00 IST" in html
    assert len(html) < 4096


def test_a_failing_sink_is_reported_to_the_dispatcher_not_swallowed():
    class Down:
        def send(self, alert):
            raise RuntimeError("outbox locked")

    class Up:
        def __init__(self):
            self.got = []

        def send(self, alert):
            self.got.append(alert)

    up = Up()
    with pytest.raises(RuntimeError, match="outbox locked"):
        CompositeAlertSink(Down(), up).send(_alert())
    assert up.got, "the healthy sink must still be offered the alert"


def test_critical_alert_goes_out_directly_when_the_outbox_cannot_be_written():
    class BrokenOutbox:
        def enqueue(self, *a, **k):
            raise sqlite_locked()

    def sqlite_locked():
        import sqlite3
        return sqlite3.OperationalError("database is locked")

    direct = []
    sink = OutboxAlertSink(BrokenOutbox(), trading_session="2026-10-15",
                           direct_fallback=lambda a: direct.append(a) or True)
    sink.send(_alert(severity=CRITICAL))
    assert len(direct) == 1
    # A WARNING is not worth bypassing the durable path: the failure is surfaced for retry.
    with pytest.raises(Exception):
        sink.send(_alert(severity="WARNING"))
    # And a CRITICAL that cannot go out at all must raise, not look delivered.
    dead = OutboxAlertSink(BrokenOutbox(), trading_session="2026-10-15",
                           direct_fallback=lambda a: False)
    with pytest.raises(Exception):
        dead.send(_alert(severity=CRITICAL))


# ------------------------------------------------------------- who is told

def test_alerts_go_to_the_family_account_owner_not_a_hard_coded_user(monkeypatch):
    from app.services.snapback_alert_telegram import (
        TelegramAlertSink, alert_user_id, transport_configured)

    monkeypatch.delenv("STERLING_FAMILY_USER_ID", raising=False)
    monkeypatch.delenv("STERLING_FAMILY_ACCOUNT_ID", raising=False)
    assert alert_user_id() == "default"
    monkeypatch.setenv("STERLING_FAMILY_USER_ID", "owner-7")
    monkeypatch.setenv("STERLING_FAMILY_ACCOUNT_ID", "AB1234")
    assert alert_user_id() == "owner-7"
    assert TelegramAlertSink().user_id == "owner-7"
    asked = []
    monkeypatch.setattr("app.services.snapback_alert_telegram._targets",
                        lambda uid: asked.append(uid) or [])
    assert transport_configured() is False
    assert asked == ["owner-7"]
