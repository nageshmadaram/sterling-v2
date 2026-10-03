"""Adapter from OperationalAlert to Sterling's existing encrypted Kite Telegram
targets. No new notification system, no new bot credentials, and no P&L messages.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.services.snapback_alerts import OperationalAlert
from app.services.snapback_alert_telegram import (
    TelegramAlertSink,
    format_alert_html,
    transport_configured,
)


def _alert(code="backup_failed", severity="CRITICAL"):
    return OperationalAlert(
        code=code,
        severity=severity,
        title="Evidence backup failed",
        message="The daily backup did not complete.",
    )


def test_format_contains_severity_strategy_fault_and_action():
    html = format_alert_html(_alert(), system_status="HALTED")

    assert "STERLING CRITICAL" in html
    assert "Frozen Snapback prospective runtime" in html
    assert "Evidence backup failed" in html
    assert "HALTED" in html
    assert "Family Operations" in html


def test_warning_severity_is_labelled_warning():
    html = format_alert_html(_alert(severity="WARNING"), system_status="DEGRADED")

    assert "STERLING WARNING" in html


def test_sink_sends_to_every_enabled_target(monkeypatch):
    sent = []

    async def fake_send_via(token, chat_id, html):
        sent.append((token, chat_id, html))
        return True

    targets = [
        SimpleNamespace(bot_token="tok-a", chat_id="1"),
        SimpleNamespace(bot_token="tok-b", chat_id="2"),
    ]

    monkeypatch.setattr(
        "app.services.notifications.kite_telegram_store.enabled_targets",
        lambda uid: targets,
    )
    monkeypatch.setattr(
        "app.services.notifications.kite_telegram_store.send_via",
        fake_send_via,
    )

    sink = TelegramAlertSink(user_id="default")
    sink.send(_alert())

    assert len(sent) == 2
    assert {chat for _, chat, _ in sent} == {"1", "2"}


def test_no_targets_configured_is_not_an_error(monkeypatch):
    monkeypatch.setattr(
        "app.services.notifications.kite_telegram_store.enabled_targets",
        lambda uid: [],
    )

    sink = TelegramAlertSink(user_id="default")

    # Logging remains the fallback; this must not raise.
    sink.send(_alert())

    assert transport_configured("default") is False


def test_transport_configured_reports_true_with_targets(monkeypatch):
    monkeypatch.setattr(
        "app.services.notifications.kite_telegram_store.enabled_targets",
        lambda uid: [SimpleNamespace(bot_token="t", chat_id="1")],
    )

    assert transport_configured("default") is True


def test_transport_probe_failure_reports_false(monkeypatch):
    def broken(uid):
        raise RuntimeError("store unavailable")

    monkeypatch.setattr(
        "app.services.notifications.kite_telegram_store.enabled_targets",
        broken,
    )

    assert transport_configured("default") is False


def test_async_delivery_fails_when_every_configured_target_rejects(monkeypatch):
    monkeypatch.setattr(
        "app.services.notifications.kite_telegram_store.enabled_targets",
        lambda uid: [SimpleNamespace(id="t1", bot_token="t", chat_id="1")],
    )

    async def rejected(*_args, **_kwargs):
        return False

    monkeypatch.setattr(
        "app.services.notifications.kite_telegram_store.send_via",
        rejected,
    )

    sink = TelegramAlertSink(user_id="default")
    with pytest.raises(RuntimeError, match="every configured target"):
        asyncio.run(sink.deliver(_alert()))


def test_telegram_failure_never_propagates(monkeypatch):
    async def broken_send(token, chat_id, html):
        raise RuntimeError("telegram unreachable")

    monkeypatch.setattr(
        "app.services.notifications.kite_telegram_store.enabled_targets",
        lambda uid: [SimpleNamespace(bot_token="t", chat_id="1")],
    )
    monkeypatch.setattr(
        "app.services.notifications.kite_telegram_store.send_via",
        broken_send,
    )

    sink = TelegramAlertSink(user_id="default")

    # The scheduler must survive a broken transport.
    sink.send(_alert())


def test_composite_sink_configuration_aggregates_children():
    from app.services.snapback_alert_telegram import CompositeAlertSink

    a = SimpleNamespace(targets_configured=lambda: False)
    b = SimpleNamespace(targets_configured=lambda: True)
    assert CompositeAlertSink(a, b).targets_configured() is True


def test_composite_sink_deliver_uses_async_and_sync_children():
    from app.services.snapback_alert_telegram import CompositeAlertSink

    seen = []

    class AsyncSink:
        async def deliver(self, alert):
            seen.append(("async", alert.code))

    class SyncSink:
        def send(self, alert):
            seen.append(("sync", alert.code))

    asyncio.run(CompositeAlertSink(AsyncSink(), SyncSink()).deliver(_alert()))
    assert seen == [("async", "backup_failed"), ("sync", "backup_failed")]


def test_sink_works_from_inside_a_running_event_loop(monkeypatch):
    sent = []

    async def fake_send_via(token, chat_id, html):
        sent.append(chat_id)
        return True

    monkeypatch.setattr(
        "app.services.notifications.kite_telegram_store.enabled_targets",
        lambda uid: [SimpleNamespace(bot_token="t", chat_id="9")],
    )
    monkeypatch.setattr(
        "app.services.notifications.kite_telegram_store.send_via",
        fake_send_via,
    )

    sink = TelegramAlertSink(user_id="default")

    async def main():
        # The ops scheduler dispatches synchronously from inside an async task.
        sink.send(_alert())
        await asyncio.sleep(0.05)

    asyncio.run(main())

    assert sent == ["9"]
