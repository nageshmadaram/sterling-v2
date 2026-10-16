"""Broker login confirmations are explicit, tenant-scoped and durably retried."""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import db
from app.services.exchanges.kite import auth
from app.services.exchanges.kite import login_recovery, session
from app.services.notifications import kite_telegram_store as targets
from app.services.notifications import telegram_kite as desk
from app.services.notifications import telegram_lifecycle as outbox


@pytest.fixture
def account(monkeypatch):
    acct = SimpleNamespace(
        id="KITE-A", label="Phone broker", api_key="synthetic-key", api_secret="synthetic-secret",
        access_token="synthetic-access", token_expires_at=int(time.time() * 1000) + 60_000,
        token_expires_at_ms=int(time.time() * 1000) + 60_000, last_login_at_ms=None,
        user_name="Phone Trader", kite_user_id="PHONE123", is_paper=False,
    )
    monkeypatch.setattr(desk, "_active_account", lambda uid=desk.UID: acct)
    return acct


async def test_status_button_confirms_current_account_with_kite(account, monkeypatch):
    verify = AsyncMock(return_value=auth.SessionHealth(
        True, "Connected", validated=True, user_name="Phone <Trader>",
        kite_user_id="PHONE123", expires_at_ms=account.token_expires_at,
    ))
    monkeypatch.setattr(auth, "ensure_session", verify)

    text = await desk.broker_login_status_text()

    verify.assert_awaited_once_with(desk.UID, account, force_validate=True)
    assert "KITE LOGIN SUCCESSFUL" in text
    assert "Phone &lt;Trader&gt;" in text and "PHONE123" in text and "Phone broker" in text
    assert "Session expires" in text and "saved in Sterling" in text
    assert "synthetic-access" not in text and "synthetic-secret" not in text


@pytest.mark.parametrize("expired,transient,heading", [
    (False, False, "KITE LOGIN NOT CONNECTED"),
    (True, False, "KITE SESSION EXPIRED"),
    (False, True, "KITE LOGIN CHECK UNAVAILABLE"),
])
async def test_status_does_not_claim_success_for_unusable_or_unchecked_session(
    account, monkeypatch, expired, transient, heading,
):
    if expired:
        account.token_expires_at = 1
    monkeypatch.setattr(auth, "ensure_session", AsyncMock(return_value=auth.SessionHealth(
        False, "Network error containing synthetic-secret", transient=transient,
    )))
    text = await desk.broker_login_status_text()
    assert heading in text and "KITE LOGIN SUCCESSFUL" not in text
    assert "synthetic-secret" not in text
    if transient:
        assert "does not mean your login failed" in text


async def test_check_exception_is_unavailable_not_invalid_login(account, monkeypatch):
    monkeypatch.setattr(auth, "ensure_session", AsyncMock(side_effect=RuntimeError("secret")))
    text = await desk.broker_login_status_text()
    assert "CHECK UNAVAILABLE" in text and "secret" not in text


async def test_missing_account_cannot_confirm_login(monkeypatch):
    monkeypatch.setattr(desk, "_active_account", lambda uid=desk.UID: None)
    verify = AsyncMock()
    monkeypatch.setattr(auth, "ensure_session", verify)
    assert "LOGIN NOT COMPLETED" in await desk.broker_login_status_text()
    verify.assert_not_awaited()
    _, keyboard = desk.broker_login_card()
    assert any(button.get("callback_data") == "kloginstatus"
               for row in keyboard["inline_keyboard"] for button in row)


def test_broker_menu_always_offers_status_check(account, monkeypatch):
    monkeypatch.setattr(desk, "connection_state", lambda uid=desk.UID: "SESSION STORED · broker validation in app")
    monkeypatch.setattr(desk, "_mode_label", lambda uid=desk.UID: "LIVE")
    monkeypatch.setattr("app.services.public_access.status", lambda: {})
    text, keyboard = desk.broker_login_card()
    assert any(button.get("callback_data") == "kloginstatus"
               for row in keyboard["inline_keyboard"] for button in row)
    assert "public HTTPS address" in text


async def test_status_callback_sends_verified_result_to_requesting_chat(monkeypatch):
    monkeypatch.setattr(desk, "_answer_cb", AsyncMock())
    monkeypatch.setattr(desk, "broker_login_status_text", AsyncMock(return_value="verified result"))
    send = AsyncMock()
    monkeypatch.setattr(desk, "_send", send)
    await desk.handle_kite_callback("controller-chat", 1, "callback", "kloginstatus")
    assert send.await_args.args[:2] == ("verified result", "controller-chat")


@pytest.fixture
def login_outbox(tmp_path, monkeypatch):
    path = str(tmp_path / "login-confirmation.db")
    monkeypatch.setattr(db, "_DB_PATH", path)
    monkeypatch.setattr(db, "_available", True)
    db.init()
    outbox.clear_for_tests()
    monkeypatch.setattr(desk, "_lifecycle_delivery_locks", {})
    monkeypatch.setattr(desk, "_last_welcome_sent_time", {})
    monkeypatch.setattr(desk, "_pace", AsyncMock())
    monkeypatch.setattr(desk, "_mode_label", lambda uid=desk.UID: "LIVE")
    monkeypatch.setattr(desk, "kite_menu_kb", lambda: {})
    monkeypatch.setattr(desk, "alerts_enabled", lambda uid: False)
    monkeypatch.setattr(desk._tg, "TELEGRAM_TOKEN", "")
    monkeypatch.setattr(desk._tg, "TELEGRAM_CHAT_ID", "")
    target = SimpleNamespace(id="TG-A", bot_token_enc="encrypted", bot_token="synthetic-token", chat_id="123")
    monkeypatch.setattr(targets, "enabled_targets", lambda uid: [target])
    yield target
    outbox.clear_for_tests()


def _login_event(uid=desk.UID):
    return outbox.enqueue(
        uid, dedupe_key="kite-login:account:session", strategy="all", event_type="BROKER CONNECTED",
        title="Kite login complete", detail="Phone Trader · Kite ID PHONE123 · saved in Sterling",
    )


async def test_prompt_confirmation_is_not_hidden_by_welcome_throttle_or_signal_toggle(login_outbox, monkeypatch):
    event_id = _login_event()
    desk._last_welcome_sent_time[desk._target_delivery_id(login_outbox)] = time.time()
    send = AsyncMock(return_value=True)
    monkeypatch.setattr(targets, "send_via", send)

    await desk._deliver_broker_login_confirmation(desk.UID, event_id)
    await desk._push_user(desk.UID, targets, False, False)

    send.assert_awaited_once()
    assert "KITE LOGIN SUCCESSFUL" in send.await_args.args[2]
    assert "PHONE123" in send.await_args.args[2]
    assert "kloginstatus" in str(send.await_args.kwargs["reply_markup"])
    assert outbox.due(desk.UID) == []


async def test_login_confirmation_bypasses_old_event_backlog(login_outbox, monkeypatch):
    for index in range(60):
        outbox.enqueue(desk.UID, dedupe_key=f"old-{index}", strategy="SuperTrend",
                       event_type="UPDATE", title="older event", detail="older", created_at_ms=int(time.time() * 1000) - 1000)
    event_id = _login_event()
    send = AsyncMock(return_value=True)
    monkeypatch.setattr(targets, "send_via", send)

    await desk._deliver_broker_login_confirmation(desk.UID, event_id)

    send.assert_awaited_once()
    assert "LOGIN SUCCESSFUL" in send.await_args.args[2]
    assert len(outbox.due(desk.UID)) == 50


async def test_failed_confirmation_remains_queued_for_normal_worker_retry(login_outbox, monkeypatch):
    event_id = _login_event()
    send = AsyncMock(side_effect=[False, True])
    monkeypatch.setattr(targets, "send_via", send)
    await desk._deliver_broker_login_confirmation(desk.UID, event_id)
    assert outbox.pending_user_ids() == [desk.UID]
    monkeypatch.setattr(outbox, "_now_ms", lambda: int(time.time() * 1000) + 60_000)

    await desk._push_user(desk.UID, targets, False, False)

    assert send.await_count == 2
    assert outbox.due(desk.UID) == []


async def test_simultaneous_prompt_and_periodic_drain_send_only_once(login_outbox, monkeypatch):
    event_id = _login_event()

    async def acknowledge(*args, **kwargs):
        await asyncio.sleep(0)
        return True

    send = AsyncMock(side_effect=acknowledge)
    monkeypatch.setattr(targets, "send_via", send)
    await asyncio.gather(
        desk._deliver_broker_login_confirmation(desk.UID, event_id),
        desk._push_user(desk.UID, targets, False, False),
    )
    send.assert_awaited_once()


async def test_recovery_and_scheduler_send_one_durable_controller_confirmation(
    login_outbox, account, monkeypatch,
):
    from urllib.parse import urlencode

    event_id = _login_event()
    login_state = session.make_state(desk.UID, account.id)
    monkeypatch.setattr(desk._tg, "TELEGRAM_TOKEN", login_outbox.bot_token)
    monkeypatch.setattr(desk._tg, "TELEGRAM_CHAT_ID", login_outbox.chat_id)
    monkeypatch.setattr(login_recovery, "complete_login", AsyncMock(return_value={
        "user_name": "Phone Trader", "_telegram_confirmation_event_id": event_id,
    }))
    send = AsyncMock(return_value={"ok": True})
    target_send = AsyncMock(return_value=True)
    monkeypatch.setattr(desk, "_send", send)
    monkeypatch.setattr(targets, "send_via", target_send)
    url = "https://public.test/api/v1/kite/callback?" + urlencode({
        "request_token": "synthetic-request-token", "state": login_state, "status": "success",
    })

    await asyncio.gather(
        desk._recover_broker_login(login_outbox.chat_id, url),
        desk._deliver_broker_login_confirmation(desk.UID, event_id),
    )
    await desk._push_user(desk.UID, targets, True, False)

    send.assert_awaited_once()
    target_send.assert_not_awaited()
    assert "KITE LOGIN SUCCESSFUL" in send.await_args.args[0]
    assert outbox.due(desk.UID) == []
    login_recovery.complete_login.assert_awaited_once_with(
        desk.UID, account, "synthetic-request-token", login_state=login_state,
    )


async def test_controller_confirmation_retries_when_alerts_route_elsewhere(login_outbox, monkeypatch):
    event_id = _login_event()
    monkeypatch.setattr(desk._tg, "TELEGRAM_TOKEN", "controller-token")
    monkeypatch.setattr(desk._tg, "TELEGRAM_CHAT_ID", "controller-chat")
    send = AsyncMock(side_effect=[{"ok": False}, {"ok": True}])
    target_send = AsyncMock(return_value=True)
    monkeypatch.setattr(desk, "_send", send)
    monkeypatch.setattr(targets, "send_via", target_send)

    await desk._deliver_broker_login_confirmation(desk.UID, event_id)
    assert outbox.pending_user_ids() == [desk.UID]
    monkeypatch.setattr(outbox, "_now_ms", lambda: int(time.time() * 1000) + 60_000)
    await desk._push_user(desk.UID, targets, True, False)

    assert send.await_count == 2
    target_send.assert_awaited_once()
    assert outbox.due(desk.UID) == []


async def test_new_login_preempts_an_already_running_trade_alert_batch(login_outbox, monkeypatch):
    current_ms = int(time.time() * 1000)
    for index in range(8):
        outbox.enqueue(desk.UID, dedupe_key=f"trade-{index}", strategy="SuperTrend",
                       event_type="UPDATE", title=f"trade {index}", detail="older", created_at_ms=current_ms - 1000)
    first_started, release_first, login_sent = asyncio.Event(), asyncio.Event(), asyncio.Event()
    prompt_waiting = asyncio.Event()
    sent = []

    async def send(token, chat_id, text, **kwargs):
        sent.append(text)
        if len(sent) == 1:
            first_started.set()
            await release_first.wait()
        if "KITE LOGIN SUCCESSFUL" in text:
            login_sent.set()
        return True

    monkeypatch.setattr(targets, "send_via", AsyncMock(side_effect=send))
    original_drain = desk._drain_lifecycle

    async def observe_drain(uid, selected_targets, use_legacy, *, event_id=None):
        if event_id is not None:
            prompt_waiting.set()
        return await original_drain(uid, selected_targets, use_legacy, event_id=event_id)

    monkeypatch.setattr(desk, "_drain_lifecycle", observe_drain)
    batch = asyncio.create_task(desk._push_user(desk.UID, targets, False, False))
    await first_started.wait()
    event_id = _login_event()
    prompt = asyncio.create_task(desk._deliver_broker_login_confirmation(desk.UID, event_id))
    await prompt_waiting.wait()
    release_first.set()
    await asyncio.wait_for(login_sent.wait(), timeout=2)
    await asyncio.gather(batch, prompt)

    assert "KITE LOGIN SUCCESSFUL" in sent[1]


def test_periodic_due_prioritizes_login_over_older_ordinary_alerts(login_outbox):
    for index in range(60):
        outbox.enqueue(desk.UID, dedupe_key=f"earlier-{index}", strategy="SuperTrend",
                       event_type="UPDATE", title="old", detail="older", created_at_ms=int(time.time() * 1000) - 1000)
    event_id = _login_event()
    assert outbox.due(desk.UID)[0].event_id == event_id


async def test_event_id_cannot_cross_tenants(login_outbox, monkeypatch):
    event_id = _login_event("other-tenant")
    send = AsyncMock(return_value=True)
    monkeypatch.setattr(targets, "send_via", send)
    await desk._deliver_broker_login_confirmation(desk.UID, event_id)
    send.assert_not_awaited()
    assert len(outbox.due("other-tenant")) == 1


async def test_nondefault_tenant_cannot_fall_back_to_legacy_chat(login_outbox, monkeypatch):
    event_id = _login_event("family")
    monkeypatch.setattr(targets, "enabled_targets", lambda uid: [])
    monkeypatch.setattr(desk._tg, "TELEGRAM_TOKEN", "synthetic-token")
    monkeypatch.setattr(desk._tg, "TELEGRAM_CHAT_ID", "operator-chat")
    send = AsyncMock()
    monkeypatch.setattr(desk, "_send", send)
    await desk._deliver_broker_login_confirmation("family", event_id)
    send.assert_not_awaited()
    assert len(outbox.due("family")) == 1


async def test_scheduling_does_not_wait_for_telegram(monkeypatch):
    started, finish = asyncio.Event(), asyncio.Event()

    async def deliver(uid, event_id):
        started.set()
        await finish.wait()

    monkeypatch.setattr(desk, "_deliver_broker_login_confirmation", deliver)
    monkeypatch.setattr(desk, "_login_confirmation_tasks", set())
    desk.schedule_broker_login_confirmation(desk.UID, "TGL-synthetic")
    await started.wait()
    assert len(desk._login_confirmation_tasks) == 1
    tasks = list(desk._login_confirmation_tasks)
    finish.set()
    await asyncio.gather(*tasks)
    await asyncio.sleep(0)
    assert desk._login_confirmation_tasks == set()
