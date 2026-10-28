"""Telegram Kite desk: text builders, callback separation, and the
two-tap confirm → shared-order-service path. Network TX (_send/_edit) no-ops
without a token, so handlers run safely offline."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import asyncio
import pytest

from app.services.notifications import telegram_kite as tk
from app.services.notifications import telegram_bot as tb


@pytest.fixture(autouse=True)
def isolate_connection_monitor(monkeypatch,tmp_path):
    from app.services import db
    monkeypatch.setattr(db,"_DB_PATH",str(tmp_path/"telegram.db"))
    assert db.init()
    # Connection transition behavior is covered separately; signal transport
    # tests should not leave unrelated lifecycle notifications in the shared DB.
    monkeypatch.setattr(tk, "check_connection_alert", lambda: None)


@pytest.mark.asyncio
async def test_signals_text_handles_empty_scan():
    txt = await tk.build_kite_signals_text()
    assert "SIGNALS" in txt or "signals" in txt


def test_ready_rows_require_fresh_current_session_and_not_replay(monkeypatch):
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    ist = ZoneInfo("Asia/Kolkata")
    now = datetime.now(ist)
    current_ms = int(now.timestamp() * 1000)
    old_ms = int((now - timedelta(days=1)).timestamp() * 1000)

    fresh = SimpleNamespace(
        is_active=True, is_fresh=True, is_replay=False,
        timestamp_ms=current_ms, legs=[],
    )
    stale = SimpleNamespace(
        is_active=True, is_fresh=False, is_replay=False,
        timestamp_ms=current_ms, legs=[],
    )
    replay = SimpleNamespace(
        is_active=True, is_fresh=True, is_replay=True,
        timestamp_ms=current_ms, legs=[],
    )
    prior = SimpleNamespace(
        is_active=True, is_fresh=True, is_replay=False,
        timestamp_ms=old_ms, legs=[],
    )
    monkeypatch.setattr(
        tk,
        "_snapshot",
        lambda _uid=tk.UID: SimpleNamespace(rows=[fresh, stale, replay, prior]),
    )

    assert tk._ready_rows() == [fresh]


@pytest.mark.asyncio
async def test_signal_keyboard_is_buy_only_and_carries_signal_identity(monkeypatch):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    now_ms = int(datetime.now(ZoneInfo("Asia/Kolkata")).timestamp() * 1000)
    leg = SimpleNamespace(
        option_symbol="NIFTY26OCT25000CE",
        lot_size=75,
    )
    row = SimpleNamespace(
        is_active=True,
        is_fresh=True,
        is_replay=False,
        timestamp_ms=now_ms,
        legs=[leg],
        exchange="NFO",
        underlying="NIFTY 50",
    )
    monkeypatch.setattr(tk, "_active_rows", lambda _uid=tk.UID: [row])

    keyboard = await tk.signals_kb()

    buttons = [
        button
        for row_buttons in keyboard["inline_keyboard"]
        for button in row_buttons
    ]
    callback_data = [button["callback_data"] for button in buttons]
    assert any(value.startswith("kbuy|") for value in callback_data)
    assert not any(value.startswith("ksell|") for value in callback_data)

    assert len(tk._sig_actions) == 1
    action = next(iter(tk._sig_actions.values()))
    assert action["signal_timestamp_ms"] == now_ms


@pytest.mark.asyncio
async def test_staged_signal_is_revalidated_before_order_submission(monkeypatch):
    place = AsyncMock()
    monkeypatch.setattr(
        "app.services.kite_engine.service.place_manual_order",
        place,
    )
    monkeypatch.setattr(tk, "_signal_action_current", lambda _action: False)

    result = await tk._run_action({
        "kind": "order",
        "origin": "signal",
        "side": "BUY",
        "option_symbol": "NIFTY26OCT25000CE",
        "exchange": "NFO",
        "qty": 75,
        "signal_timestamp_ms": 123,
    })

    assert result == "🚫 Signal expired. Refresh signals."
    place.assert_not_awaited()


def test_status_text_reports_mode_and_toggles():
    txt = tk.build_kite_status_text()
    assert "ALGO" in txt and "Alerts" in txt


async def test_callbacks_route_to_kite_module():
    """All callbacks are delegated to the Kite command surface.

    Drives `_handle_update`, the actual update-loop entry point. The private
    `_handle_callback` hop this used to call was inlined when the crypto command
    surface was removed — only the Kite namespace is left to route to, so the
    dispatch no longer needs a branch of its own.
    """
    def _cb(data: str) -> dict:
        return {"callback_query": {
            "id": "cb",
            "data": data,
            "from": {"id": "1"},
            "message": {"message_id": 1, "chat": {"id": "1"}},
        }}

    with patch.object(tb._tg, "TELEGRAM_CHAT_ID", "1"), patch(
        "app.services.notifications.telegram_kite.handle_kite_callback",
        new=AsyncMock(),
    ) as kite_cb:
        await tb._handle_update(_cb("ksig"))
        await tb._handle_update(_cb("menu_kite"))
        await tb._handle_update(_cb("menu_unknown"))
        assert kite_cb.await_count == 3


async def test_control_bot_requires_an_explicit_authorized_chat():
    message = {"message": {"chat": {"id": "attacker"}}}
    with patch.object(tb._tg, "TELEGRAM_CHAT_ID", ""), patch(
        "app.services.notifications.telegram_kite.handle_kite_command",
        new=AsyncMock(),
    ) as command:
        await tb._handle_update(message)
        assert command.await_count == 0


async def test_push_alerts_are_tenant_scoped_and_failed_delivery_retries(monkeypatch):
    tk._alerted.clear()
    tk._alerted_loaded.clear()
    monkeypatch.setattr(tk, "_MIN_SEND_GAP_S", 0.0)
    from app.services.notifications import telegram_lifecycle
    telegram_lifecycle.clear_for_tests()
    signal_ms = int(tk.datetime.now(tk.IST).timestamp() * 1000)
    row = SimpleNamespace(
        token=101,
        timestamp_ms=signal_ms,
        direction="long",
        legs=[],
        underlying="NIFTY 50",
        option_type="CE",
    )
    targets = {
        "u1": [SimpleNamespace(id="t1", bot_token="tok1", chat_id="1", reachable=False)],
        "u2": [SimpleNamespace(id="t2", bot_token="tok2", chat_id="2", reachable=False)],
    }
    calls = []
    u1_attempts = 0

    monkeypatch.setattr(tk, "_alert_user_ids", lambda: ["u1", "u2"])
    monkeypatch.setattr(tk, "alerts_enabled", lambda uid=tk.UID: True)
    monkeypatch.setattr(tk, "_load_alerted", lambda uid: None)
    monkeypatch.setattr(tk, "_save_alerted", lambda uid: None)
    monkeypatch.setattr(tk, "_ready_rows", lambda uid=tk.UID: [row])
    monkeypatch.setattr(tk, "_mode_label", lambda uid=tk.UID: f"PAPER-{uid}")
    monkeypatch.setattr(
        "app.services.notifications.kite_telegram_store.enabled_targets",
        lambda uid: targets[uid],
    )
    monkeypatch.setattr(
        "app.services.notifications.kite_telegram_store.set_reachable",
        lambda *_args, **_kwargs: None,
    )

    async def send(token, chat_id, html):
        nonlocal u1_attempts
        calls.append((token, chat_id, html))
        if token == "tok1":
            u1_attempts += 1
            return u1_attempts > 1
        return True

    monkeypatch.setattr(
        "app.services.notifications.kite_telegram_store.send_via",
        send,
    )

    await tk.push_kite_alerts()
    assert [c[0] for c in calls] == ["tok1", "tok2"]
    t2_dest = tk._target_delivery_id(targets["u2"][0])
    t1_dest = tk._target_delivery_id(targets["u1"][0])
    assert ("u2", t2_dest, f"101|{signal_ms}") in tk._alerted
    assert ("u1", t1_dest, f"101|{signal_ms}") not in tk._alerted

    await tk.push_kite_alerts()
    assert [c[0] for c in calls] == ["tok1", "tok2", "tok1"]
    assert ("u1", t1_dest, f"101|{signal_ms}") in tk._alerted


@pytest.mark.kite_features('default', 'u1')
def test_target_update_with_same_token_preserves_endpoint_identity(monkeypatch):
    from app.services import db
    from app.services.notifications import kite_telegram_store as kts

    values = {}
    monkeypatch.setattr(db, "get_config", lambda key, default="": values.get(key, default))
    monkeypatch.setattr(db, "set_config_strict", lambda key, value: values.__setitem__(key, value))

    encrypt_calls = []

    def fake_encrypt(value):
        encrypt_calls.append(value)
        return f"enc-{len(encrypt_calls)}:{value}"

    def fake_decrypt(value):
        return str(value).split(":", 1)[1] if ":" in str(value) else ""

    monkeypatch.setattr(kts, "encrypt", fake_encrypt)
    monkeypatch.setattr(kts, "decrypt", fake_decrypt)

    target = kts.add(
        "u1", label="desk", bot_token="same-token", chat_id="123", enabled=True
    )
    kts.set_reachable("u1", target.id, True)
    before = kts.get("u1", target.id)
    assert before is not None
    ciphertext = before.bot_token_enc

    unchanged = kts.update(
        "u1", target.id, bot_token="same-token", chat_id="123"
    )
    assert unchanged is not None
    assert unchanged.bot_token_enc == ciphertext
    assert unchanged.reachable is True
    assert encrypt_calls == ["same-token"]

    changed = kts.update("u1", target.id, bot_token="new-token")
    assert changed is not None
    assert changed.bot_token_enc != ciphertext
    assert changed.reachable is False
    assert encrypt_calls == ["same-token", "new-token"]


async def test_legacy_single_bot_never_receives_another_users_signal(monkeypatch):
    tk._alerted.clear()
    tk._alerted_loaded.clear()
    row = SimpleNamespace(
        token=202, timestamp_ms=88, direction="short", legs=[],
        underlying="BANKNIFTY", option_type="PE",
    )
    monkeypatch.setattr(tk, "_alert_user_ids", lambda: ["other-user"])
    monkeypatch.setattr(tk, "alerts_enabled", lambda uid=tk.UID: True)
    monkeypatch.setattr(tk, "_load_alerted", lambda uid: None)
    monkeypatch.setattr(tk, "_save_alerted", lambda uid: None)
    monkeypatch.setattr(tk, "_ready_rows", lambda uid=tk.UID: [row])
    monkeypatch.setattr(
        "app.services.notifications.kite_telegram_store.enabled_targets",
        lambda uid: [],
    )
    with patch.object(tk._tg, "TELEGRAM_TOKEN", "legacy-token"), patch.object(
        tk._tg, "TELEGRAM_CHAT_ID", "legacy-chat"
    ), patch("app.services.notifications.telegram_kite._send", new=AsyncMock()) as send:
        await tk.push_kite_alerts()
        assert send.await_count == 0


async def test_group_chat_member_cannot_inherit_trading_controls():
    update = {
        "message": {
            "chat": {"id": "-100123", "type": "group"},
            "from": {"id": "777"},
        }
    }
    with patch.object(tb._tg, "TELEGRAM_CHAT_ID", "-100123"), patch(
        "app.services.notifications.telegram_kite.handle_kite_command",
        new=AsyncMock(),
    ) as command:
        await tb._handle_update(update)
        assert command.await_count == 0


def test_delivery_dedupe_survives_process_restart(monkeypatch):
    import json

    values = {}
    monkeypatch.setattr(
        "app.services.db.get_config",
        lambda key, default="": values.get(key, default),
    )
    monkeypatch.setattr(
        "app.services.db.set_config",
        lambda key, value: values.__setitem__(key, value),
    )

    tk._alerted.clear()
    tk._alerted_loaded.clear()
    tk._alerted.add(("u1", "target-1", "101|123"))
    tk._save_alerted("u1")

    assert json.loads(values["kite_tg_delivered:u1"]) == [["target-1", "101|123"]]

    tk._alerted.clear()
    tk._alerted_loaded.clear()
    tk._load_alerted("u1")
    assert ("u1", "target-1", "101|123") in tk._alerted


def test_legacy_target_ack_migrates_to_current_endpoint_identity(monkeypatch):
    import json
    from app.services.notifications import kite_telegram_store as store

    values = {
        "kite_tg_delivered:u1": json.dumps([["target-1", "101|123"]]),
    }
    target = SimpleNamespace(
        id="target-1",
        bot_token_enc="enc-token",
        chat_id="123",
    )
    monkeypatch.setattr(
        "app.services.db.get_config",
        lambda key, default="": values.get(key, default),
    )
    monkeypatch.setattr(
        "app.services.db.set_config",
        lambda key, value: values.__setitem__(key, value),
    )
    monkeypatch.setattr(store, "list_targets", lambda _uid: [target])

    tk._alerted.clear()
    tk._alerted_loaded.clear()
    tk._load_alerted("u1")

    destination = tk._target_delivery_id(target)
    assert ("u1", destination, "101|123") in tk._alerted
    assert json.loads(values["kite_tg_delivered:u1"]) == [
        [destination, "101|123"]
    ]


async def test_editing_target_endpoint_realerts_an_active_signal(monkeypatch):
    tk._alerted.clear()
    tk._alerted_loaded.clear()
    old_target = SimpleNamespace(
        id="t1",
        bot_token_enc="enc-old",
        bot_token="old-token",
        chat_id="1",
        reachable=True,
    )
    new_target = SimpleNamespace(
        id="t1",
        bot_token_enc="enc-new",
        bot_token="new-token",
        chat_id="2",
        reachable=False,
    )
    alert = tk.StrategyAlert(
        strategy="adaptive_edge",
        strategy_label="Adaptive Edge",
        dedupe_key="adaptive:sig|123",
        timestamp_ms=123,
        title="NIFTY",
        direction="BULLISH",
    )
    tk._alerted.add(
        ("u1", tk._target_delivery_id(old_target), alert.dedupe_key)
    )

    monkeypatch.setattr(tk, "_alert_user_ids", lambda: ["u1"])
    monkeypatch.setattr(tk, "alerts_enabled", lambda _uid=tk.UID: True)
    monkeypatch.setattr(tk, "_load_alerted", lambda _uid: None)
    monkeypatch.setattr(tk, "_save_alerted", lambda _uid: None)
    monkeypatch.setattr(tk, "_prune_alerted", lambda _uid: False)
    monkeypatch.setattr(tk, "_strategy_alerts", lambda _uid: [alert])
    monkeypatch.setattr(
        "app.services.notifications.kite_telegram_store.enabled_targets",
        lambda _uid: [new_target],
    )
    monkeypatch.setattr(
        "app.services.notifications.kite_telegram_store.set_reachable",
        lambda *_args, **_kwargs: None,
    )
    sent = []

    async def send(token, chat_id, html):
        sent.append((token, chat_id, html))
        return True

    monkeypatch.setattr(
        "app.services.notifications.kite_telegram_store.send_via",
        send,
    )

    await tk.push_kite_alerts()

    assert [(token, chat) for token, chat, _html in sent] == [
        ("new-token", "2")
    ]
    assert (
        "u1",
        tk._target_delivery_id(new_target),
        alert.dedupe_key,
    ) in tk._alerted


def test_alert_preference_is_persisted_and_user_scoped(monkeypatch):
    values = {}

    monkeypatch.setattr(
        "app.services.db.get_config",
        lambda key, default="": values.get(key, default),
    )
    monkeypatch.setattr(
        "app.services.db.set_config",
        lambda key, value: values.__setitem__(key, value),
    )

    assert tk.alerts_enabled("u1") is True
    assert tk.alerts_enabled("u2") is True

    tk.set_alerts_enabled("u1", False)
    assert tk.alerts_enabled("u1") is False
    assert tk.alerts_enabled("u2") is True

    tk.set_alerts_enabled("u1", True)
    assert tk.alerts_enabled("u1") is True


async def test_empty_scan_does_not_forget_successful_delivery(monkeypatch):
    """A transient empty scanner snapshot must not cause duplicate alerts."""
    tk._alerted.clear()
    tk._alerted_loaded.clear()
    ts = 1_800_000_000_000
    row = SimpleNamespace(
        token=101, timestamp_ms=ts, direction="long", legs=[],
        underlying="NIFTY 50", option_type="CE",
    )
    target = SimpleNamespace(
        id="t1", bot_token="tok1", chat_id="1", reachable=True,
    )
    rows = [[], [row]]
    calls = []

    monkeypatch.setattr(tk, "_alert_user_ids", lambda: ["u1"])
    monkeypatch.setattr(tk, "alerts_enabled", lambda uid=tk.UID: True)
    monkeypatch.setattr(tk, "_load_alerted", lambda uid: None)
    monkeypatch.setattr(tk, "_save_alerted", lambda uid: None)
    monkeypatch.setattr(tk, "_ready_rows", lambda uid=tk.UID: rows.pop(0))
    monkeypatch.setattr(tk, "_prune_alerted", lambda uid: False)
    monkeypatch.setattr(
        "app.services.notifications.kite_telegram_store.enabled_targets",
        lambda uid: [target],
    )
    monkeypatch.setattr(
        "app.services.notifications.kite_telegram_store.set_reachable",
        lambda *_args, **_kwargs: None,
    )

    async def send(*args):
        calls.append(args)
        return True

    monkeypatch.setattr(
        "app.services.notifications.kite_telegram_store.send_via", send
    )
    target_dest = tk._target_delivery_id(target)
    tk._alerted.add(("u1", target_dest, f"101|{ts}"))

    await tk.push_kite_alerts()  # transient empty scan
    await tk.push_kite_alerts()  # same signal returns

    assert calls == []
    assert ("u1", target_dest, f"101|{ts}") in tk._alerted


@pytest.mark.kite_features('default', 'u1')
def test_target_edit_normalizes_and_invalidates_reachability(monkeypatch):
    from app.services.notifications import kite_telegram_store as store

    values = {}
    monkeypatch.setattr(
        store.db, "get_config", lambda key, default="": values.get(key, default)
    )
    monkeypatch.setattr(
        store.db, "set_config_strict", lambda key, value: values.__setitem__(key, value)
    )
    monkeypatch.setattr(store, "encrypt", lambda value: f"enc:{value}")
    monkeypatch.setattr(
        store, "decrypt",
        lambda value: value.removeprefix("enc:") if value else "",
    )

    target = store.add(
        "u1",
        label=" Alerts ",
        bot_token=" token-1 ",
        chat_id=" 123 ",
    )
    store.set_reachable("u1", target.id, True)
    updated = store.update(
        "u1", target.id, chat_id=" 456 ", bot_token=" token-2 "
    )

    assert updated is not None
    assert updated.chat_id == "456"
    assert updated.bot_token == "token-2"
    assert updated.reachable is False


@pytest.mark.kite_features('default', 'u1')
async def test_target_test_escapes_html_label(monkeypatch):
    from app.api.v1.endpoints import kite_telegram as endpoint

    target = SimpleNamespace(
        id="t1",
        label="<Desk & Ops>",
        chat_id="1",
        bot_token="token",
        bot_token_set=True,
        bot_token_hint="token",
        token_hint=lambda: "token",
        enabled=True,
        reachable=False,
    )
    sent = {}

    monkeypatch.setattr(endpoint.store, "get", lambda uid, target_id: target)

    async def send(token, chat_id, html):
        sent["html"] = html
        return True

    monkeypatch.setattr(endpoint.store, "send_via", send)

    def mark(uid, target_id, ok):
        target.reachable = ok
        return target

    monkeypatch.setattr(endpoint.store, "set_reachable", mark)
    from app.services.auth import account_policy, configuration_policy
    configuration_policy.replace_section('default', 'u1', 'telegram', visible=True,
        grants=[{'action': 'test', 'resource_id': 't1', 'expires_at_ms': None}],
        expected_revision=account_policy.read('u1').revision)
    out = await endpoint.test_target("t1", SimpleNamespace(user_id="u1"))

    assert out.reachable is True
    assert "&lt;Desk &amp; Ops&gt;" in sent["html"]
    assert "<Desk & Ops>" not in sent["html"]


async def test_order_requires_two_tap_confirm_then_places():
    """Tapping Buy stages a pending order (no placement); only Confirm executes it
    via the shared service."""
    tk._sig_actions.clear()
    tk._pending.clear()
    tk._pending_created.clear()
    now_ms = int(tk.datetime.now(tk.IST).timestamp() * 1000)
    row = SimpleNamespace(timestamp_ms=now_ms, exchange="NFO", legs=[SimpleNamespace(option_symbol="NIFTY26JUN25000CE")])
    tk._sig_actions["1"] = {"option_symbol": "NIFTY26JUN25000CE", "exchange": "NFO", "qty": 75, "signal_timestamp_ms": now_ms}

    placed = AsyncMock(return_value={"status": "ok", "order_id": "X1", "message": "ok"})
    with patch("app.services.kite_engine.service.place_manual_order", new=placed), patch.object(tk, "_ready_rows", return_value=[row]):
        # first tap: stage only — must NOT place yet
        await tk.handle_kite_callback("1", 1, "cb", "kbuy|1")
        assert placed.await_count == 0
        assert len(tk._pending) == 1
        ptok = next(iter(tk._pending))
        # confirm: now it places
        await tk.handle_kite_callback("1", 1, "cb", f"kok|{ptok}")
        assert placed.await_count == 1
        args = placed.await_args.args
        assert "NIFTY26JUN25000CE" in args and "BUY" in args
    # cancel path leaves nothing pending and never places
    tk._pending["zz"] = {"kind": "order", "side": "BUY", "option_symbol": "X", "exchange": "NFO", "qty": 1}
    tk._pending_created["zz"] = 0.0
    with patch("app.services.kite_engine.service.place_manual_order", new=AsyncMock()) as p2:
        await tk.handle_kite_callback("1", 1, "cb", "kno|zz")
        assert p2.await_count == 0
        assert "zz" not in tk._pending




@pytest.mark.asyncio
async def test_staged_confirmation_expires_before_it_can_place(monkeypatch):
    import time as time_module

    tk._pending.clear()
    tk._pending_created.clear()
    clock = {"now": 10.0}
    monkeypatch.setattr(time_module, "monotonic", lambda: clock["now"])

    token = tk._stage({
        "kind": "order",
        "side": "BUY",
        "option_symbol": "NIFTY26OCT25000CE",
        "exchange": "NFO",
        "qty": 75,
    })
    clock["now"] += tk._CONFIRM_TTL_S + 1

    with patch("app.services.kite_engine.service.place_manual_order", new=AsyncMock()) as place:
        await tk.handle_kite_callback("1", 1, "cb", f"kok|{token}")

    place.assert_not_awaited()
    assert token not in tk._pending
    assert token not in tk._pending_created


async def test_expired_signal_action_does_not_place():
    tk._sig_actions.clear()
    tk._pending.clear()
    tk._pending_created.clear()
    with patch("app.services.kite_engine.service.place_manual_order", new=AsyncMock()) as p:
        await tk.handle_kite_callback("1", 1, "cb", "kbuy|999")  # unknown sid
        assert p.await_count == 0
        assert len(tk._pending) == 0


def test_legacy_restore_decrypts_db_fallback_and_env_wins(monkeypatch):
    from app.services.notifications import telegram as legacy

    values = {
        "telegram_bot_token": "fernet:ciphertext",
        "telegram_chat_id": "db-chat",
        "telegram_verified": "1",
    }
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.setattr("app.services.db.get_config", lambda key, *args: values.get(key, ""))
    monkeypatch.setattr("app.core.security.decrypt", lambda value: "decrypted-token")

    legacy.TELEGRAM_TOKEN = ""
    legacy.TELEGRAM_CHAT_ID = ""
    legacy.TELEGRAM_REACHABLE = False
    legacy.restore_legacy_config_from_store()

    assert legacy.TELEGRAM_TOKEN == "decrypted-token"
    assert legacy.TELEGRAM_CHAT_ID == "db-chat"
    assert legacy.TELEGRAM_REACHABLE is True

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "env-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "env-chat")
    legacy.restore_legacy_config_from_store()
    assert legacy.TELEGRAM_TOKEN == "env-token"
    assert legacy.TELEGRAM_CHAT_ID == "env-chat"


async def test_send_via_retries_transient_server_failure(monkeypatch):
    from app.services.notifications import kite_telegram_store as store

    class Response:
        def __init__(self, status, body, text):
            self.status_code = status
            self._body = body
            self.text = text

        def json(self):
            return self._body

    responses = [
        Response(503, {"ok": False}, "temporarily unavailable"),
        Response(200, {"ok": True}, "ok"),
    ]
    calls = []

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, json):
            calls.append((url, json))
            return responses.pop(0)

    monkeypatch.setattr(store.httpx, "AsyncClient", Client)
    monkeypatch.setattr(store.asyncio, "sleep", AsyncMock())

    assert await store.send_via("token", "chat", "<b>x</b>") is True
    assert len(calls) == 2


async def test_poll_loop_backs_off_when_telegram_rejects_poll(monkeypatch):
    delays = []

    async def rejected(*_args, **_kwargs):
        return {"ok": False, "parameters": {"retry_after": 2}}

    async def stop_after_backoff(delay):
        delays.append(delay)
        raise asyncio.CancelledError()

    monkeypatch.setattr(tb, "_api", rejected)
    monkeypatch.setattr(tb.asyncio, "sleep", stop_after_backoff)
    with patch.object(tb._tg, "TELEGRAM_TOKEN", "token"), patch.object(
        tb._tg, "TELEGRAM_CHAT_ID", "1"
    ):
        with pytest.raises(asyncio.CancelledError):
            await tb.poll_loop()

    assert delays == [2.0]


async def test_legacy_send_requires_telegram_ok_and_clears_reachability(monkeypatch):
    from app.services.notifications import telegram as legacy

    init_kwargs = {}

    class Response:
        status_code = 200
        text = '{"ok":false}'

        def json(self):
            return {"ok": False, "description": "chat not found"}

    class Client:
        def __init__(self, *args, **kwargs):
            init_kwargs.update(kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, json):
            return Response()

    monkeypatch.setattr(legacy.httpx, "AsyncClient", Client)
    legacy.TELEGRAM_TOKEN = "token"
    legacy.TELEGRAM_CHAT_ID = "1"
    legacy.TELEGRAM_REACHABLE = True

    assert await legacy.send("x") is False
    assert legacy.TELEGRAM_REACHABLE is False
    assert init_kwargs["trust_env"] is False
    assert init_kwargs["follow_redirects"] is False


def test_adaptive_alerts_require_current_admitted_trace(monkeypatch):
    from app.services import adaptive_edge_runner

    monkeypatch.setattr(tk, "_is_recent", lambda *_args, **_kwargs: True)
    state = {
        "server_time_ms": 123456,
        "signals": [
            {
                "signal_id": "NIFTY26OCT25000CE:2026-10-29",
                "trace_hash": "trace-a",
                "entry_ok": True,
                "underlying": "NIFTY",
                "symbol": "NIFTY26OCT25000CE",
                "option_type": "CE",
                "reason": "F-110 passed",
            },
            {
                "signal_id": "NIFTY26OCT25000PE:2026-10-29",
                "trace_hash": "trace-b",
                "entry_ok": False,
                "underlying": "NIFTY",
                "symbol": "NIFTY26OCT25000PE",
                "option_type": "PE",
            },
            {
                "signal_id": "BANKNIFTY26OCT52000CE:2026-10-29",
                "trace_hash": "",
                "entry_ok": True,
                "underlying": "BANKNIFTY",
                "symbol": "BANKNIFTY26OCT52000CE",
                "option_type": "CE",
            },
        ],
    }
    monkeypatch.setattr(adaptive_edge_runner, "scan_state", lambda _uid: state)

    first = tk._adaptive_edge_alerts("u1")
    state["server_time_ms"] = 223456
    second = tk._adaptive_edge_alerts("u1")

    assert len(first) == 1
    assert len(second) == 1
    assert first[0].strategy == "adaptive_edge"
    assert first[0].dedupe_key == (
        "adaptive_edge:NIFTY26OCT25000CE:2026-10-29:trace-a"
    )
    assert second[0].dedupe_key == first[0].dedupe_key
    assert first[0].timestamp_ms == 123456
    assert second[0].timestamp_ms == 223456
    assert first[0].option_symbol == "NIFTY26OCT25000CE"

def test_opening_volume_alerts_use_latest_live_execution_gate(monkeypatch):
    from app.services import opening_volume_execution
    from app.services import opening_volume_leaders

    scan = {
        "as_of": "2026-10-03T09:30:00+05:30",
        "leaders": [
            {
                "signal_key": "opening-volume:2026-10-03:RELIANCE:UP",
                "symbol": "RELIANCE",
                "direction": "UP",
                "actionable_signal_time": "2026-10-03T09:20:00+05:30",
                "option": {
                    "option_type": "CE",
                    "tradingsymbol": "RELIANCE26OCT3000CE",
                },
                "decision": {"score": {"lower_bound": 72}},
            }
        ],
    }
    monkeypatch.setattr(tk, "_is_recent", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(opening_volume_leaders, "latest_live_scan", lambda _uid: scan)
    monkeypatch.setattr(
        opening_volume_execution,
        "get_config",
        lambda _uid: SimpleNamespace(),
    )
    monkeypatch.setattr(
        opening_volume_execution,
        "eligible_candidates",
        lambda supplied_scan, _cfg: supplied_scan["leaders"],
    )

    alerts = tk._opening_volume_alerts("u1")

    assert len(alerts) == 1
    assert alerts[0].strategy == "opening_volume"
    assert alerts[0].option_symbol == "RELIANCE26OCT3000CE"
    assert alerts[0].dedupe_key.startswith(
        "opening_volume:opening-volume:2026-10-03:RELIANCE:UP|"
    )


@pytest.mark.asyncio
async def test_gamma_alert_rate_limit_resets_each_minute(monkeypatch):
    import time as time_module

    tk._gamma_alert_rate.clear()
    clock = {"wall": 120.0, "mono": 10.0}
    monkeypatch.setattr(tk, "_rate_wall_time", lambda: clock["wall"])
    monkeypatch.setattr(tk, "_rate_monotonic", lambda: clock["mono"])
    monkeypatch.setattr(
        "app.services.gamma_move.get_config",
        lambda _uid=None: SimpleNamespace(
            alert_group_rate_per_min=2,
            alert_per_chat_rate_per_sec=1000.0,
        ),
    )
    monkeypatch.setattr("asyncio.sleep", AsyncMock())

    assert await tk._gamma_rate_slot("u1", "dest") is True
    assert await tk._gamma_rate_slot("u1", "dest") is True
    assert await tk._gamma_rate_slot("u1", "dest") is False

    clock["wall"] = 180.0
    assert await tk._gamma_rate_slot("u1", "dest") is True


@pytest.mark.asyncio
async def test_gamma_alert_per_chat_rate_paces_consecutive_cards(monkeypatch):
    import time as time_module
    from unittest.mock import Mock

    tk._gamma_alert_rate.clear()
    monkeypatch.setattr(tk, "_rate_wall_time", lambda: 120.0)
    monkeypatch.setattr(
        tk,
        "_rate_monotonic",
        Mock(side_effect=[10.0, 10.2, 11.0]),
    )
    monkeypatch.setattr(
        "app.services.gamma_move.get_config",
        lambda _uid=None: SimpleNamespace(
            alert_group_rate_per_min=20,
            alert_per_chat_rate_per_sec=1.0,
        ),
    )
    sleeper = AsyncMock()
    monkeypatch.setattr("asyncio.sleep", sleeper)

    assert await tk._gamma_rate_slot("u1", "dest") is True
    assert await tk._gamma_rate_slot("u1", "dest") is True

    sleeper.assert_awaited_once()
    assert sleeper.await_args.args[0] == pytest.approx(0.8)


async def test_legacy_non_supertrend_alert_has_no_trading_keyboard(monkeypatch):
    monkeypatch.setattr(tk, "check_connection_alert", lambda: None)
    tk._alerted.clear()
    tk._alerted_loaded.clear()
    alert = tk.StrategyAlert(
        strategy="adaptive_edge",
        strategy_label="Adaptive Edge",
        dedupe_key="adaptive:S|123",
        timestamp_ms=123,
        title="NIFTY",
        direction="BULLISH",
        option_type="CE",
        option_symbol="NIFTY26OCT25000CE",
        detail="advisory",
    )

    monkeypatch.setattr(tk, "_alert_user_ids", lambda: [tk.UID])
    monkeypatch.setattr(tk, "alerts_enabled", lambda _uid=tk.UID: True)
    monkeypatch.setattr(tk, "_load_alerted", lambda _uid: None)
    monkeypatch.setattr(tk, "_save_alerted", lambda _uid: None)
    monkeypatch.setattr(tk, "_prune_alerted", lambda _uid: False)
    monkeypatch.setattr(tk, "_strategy_alerts", lambda _uid: [alert])
    monkeypatch.setattr(tk, "_mode_label", lambda _uid=tk.UID: "PAPER")
    monkeypatch.setattr(
        "app.services.notifications.kite_telegram_store.enabled_targets",
        lambda _uid: [],
    )

    with patch.object(tk._tg, "TELEGRAM_TOKEN", "legacy-token"), patch.object(
        tk._tg, "TELEGRAM_CHAT_ID", "legacy-chat"
    ), patch("app.services.notifications.telegram_kite._send", new=AsyncMock(
        return_value={"ok": True}
    )) as send:
        await tk.push_kite_alerts()

    assert send.await_count == 1
    args = send.await_args.args
    assert args[1] == "legacy-chat"
    assert len(args) == 3 and args[2] is None
    assert ("default", "legacy", "adaptive:S|123") in tk._alerted


def test_snapback_blocked_alert_uses_stable_signal_id(monkeypatch):
    monkeypatch.setattr(
        "app.services.snapback.snapshot",
        lambda _uid: {
            "strategy": {"enabled": True},
            "capabilities": {
                "manual_execution": {
                    "single_leg": False,
                    "spread": False,
                    "hedge": False,
                    "protection_lifecycle": False,
                    "reason": (
                        "Snapback strategy-owned manual execution/protection "
                        "is not connected"
                    ),
                },
            },
            "rows": [{
                "signal_id": "snapback:fade_up:RELIANCE:1791063000000",
                "timestamp_ms": 1791063000000,
                "state": "armed",
                "execution_eligible": True,
                "origin": "live_quote",
                "symbol": "RELIANCE",
                "direction": "BEARISH",
                "opt_type": "PE",
                "contract": {"symbol": "RELIANCE26OCT3000PE"},
                "reason": None,
            }],
        },
    )

    alerts = tk._snapback_alerts("u1")

    assert len(alerts) == 1
    alert = alerts[0]
    assert alert.strategy == "snapback"
    assert alert.strategy_label == "Snapback"
    assert alert.dedupe_key == "snapback:snapback:fade_up:RELIANCE:1791063000000"
    assert alert.option_symbol == "RELIANCE26OCT3000PE"
    assert alert.direction == "BEARISH"
    assert "ENTRY BLOCKED" in alert.detail
    assert "MANUAL ELIGIBLE" not in alert.detail
    assert "ALGO ELIGIBLE" not in alert.detail

def test_snapback_alert_reports_auto_eligible_without_adding_trade_callback(monkeypatch):
    monkeypatch.setattr(
        "app.services.snapback.snapshot",
        lambda _uid: {
            "strategy": {"enabled": True},
            "capabilities": {
                "manual_execution": {"single_leg": True},
                "auto_execution": {"available": True, "enabled": True},
            },
            "rows": [{
                "signal_id": "snapback:fade_down:NIFTY:1791063060000",
                "timestamp_ms": 1791063060000,
                "state": "armed",
                "execution_eligible": True,
                "origin": "live_quote",
                "symbol": "NIFTY",
                "direction": "BULLISH",
                "opt_type": "CE",
                "contract": {"symbol": "NIFTY26OCT25000CE"},
                "reason": None,
            }],
        },
    )

    alerts = tk._snapback_alerts("u1")

    assert len(alerts) == 1
    assert "ALGO ELIGIBLE" in alerts[0].detail
    # StrategyAlert is a notification payload. Snapback execution remains
    # plan/arm/dispatcher-owned; no generic Telegram order callback is attached.
    assert not hasattr(alerts[0], "callback")


def test_supertrend_alert_preserves_historical_dedupe_key(monkeypatch):
    row = SimpleNamespace(
        token=987,
        timestamp_ms=654321,
        direction="long",
        legs=[],
        underlying="NIFTY 50",
        option_type="CE",
    )
    monkeypatch.setattr(tk, "_ready_rows", lambda _uid=tk.UID: [row])

    alerts = tk._supertrend_alerts("u1")

    assert len(alerts) == 1
    assert alerts[0].strategy == "kite_supertrend"
    assert alerts[0].dedupe_key == "987|654321"


@pytest.mark.asyncio
async def test_telegram_auto_on_runs_the_same_preflight_as_the_rest_route(monkeypatch):
    from app.services.kite_engine import service, state

    cfg = state.get_config(tk.UID)
    state.set_config(tk.UID, cfg.model_copy(update={"auto_execute": False}))
    monkeypatch.setattr(
        service, "autoexec_preflight",
        lambda _uid: ["Unresolved durable order intents: NIFTY26OCT25000CE"])

    result = await tk._run_action({"kind": "auto", "to": "ON"})

    assert result.startswith("🚫 ALGO not enabled")
    assert "Unresolved durable order intents" in result
    assert state.get_config(tk.UID).auto_execute is False


@pytest.mark.asyncio
async def test_telegram_auto_on_proceeds_when_the_preflight_is_clear(monkeypatch):
    from app.services.kite_engine import service, state

    state.set_config(tk.UID, state.get_config(tk.UID).model_copy(update={"auto_execute": False}))
    monkeypatch.setattr(service, "autoexec_preflight", lambda _uid: [])

    result = await tk._run_action({"kind": "auto", "to": "ON"})

    assert "ALGO" in result and "ON" in result
    assert state.get_config(tk.UID).auto_execute is True


@pytest.mark.asyncio
async def test_telegram_auto_off_is_never_gated(monkeypatch):
    from app.services.kite_engine import service, state

    state.set_config(tk.UID, state.get_config(tk.UID).model_copy(update={"auto_execute": True}))
    monkeypatch.setattr(service, "autoexec_preflight", lambda _uid: ["anything"])

    result = await tk._run_action({"kind": "auto", "to": "OFF"})

    assert "OFF" in result
    assert state.get_config(tk.UID).auto_execute is False


@pytest.mark.asyncio
async def test_build_kite_search_text_case_and_space_insensitive(monkeypatch):
    class FakeClient:
        async def search_instruments(self, query, exchange="", limit=50):
            if "ICICI" in query.upper():
                return [{"exchange": "NSE", "tradingsymbol": "ICICIBANK"}]
            return []

        async def quote(self, keys):
            res = {}
            for k in keys:
                if k in {"NSE:NIFTY 50", "NSE:ICICIBANK", "BSE:SENSEX"}:
                    res[k] = {
                        "last_price": 25000.5,
                        "ohlc": {"open": 24900.0, "high": 25100.0, "low": 24850.0, "close": 24950.0},
                        "volume": 1000000,
                    }
            return res

        async def ltp(self, keys):
            return await self.quote(keys)

    monkeypatch.setattr(tk, "_active_account", lambda _uid: SimpleNamespace(id="acct1"))
    from app.services.exchanges.kite import accounts as kite_accounts
    async def _async_acquire(_acct):
        return FakeClient()
    monkeypatch.setattr(kite_accounts, "acquire_client", _async_acquire)

    # Empty query
    prompt = await tk.build_kite_search_text("")
    assert "LIVE MARKET QUOTE SEARCH" in prompt

    # Index search lowercase
    nifty_res = await tk.build_kite_search_text("nifty")
    assert "25,000.50" in nifty_res
    assert "NSE:NIFTY 50" in nifty_res

    # Stock search with spaces and lowercase
    icici_res1 = await tk.build_kite_search_text("icicibank")
    assert "ICICIBANK" in icici_res1
    assert "25,000.50" in icici_res1

    icici_res2 = await tk.build_kite_search_text("  icici   bank  ")
    assert "ICICIBANK" in icici_res2
    assert "25,000.50" in icici_res2


@pytest.mark.asyncio
async def test_handle_clear_chat(monkeypatch):
    deleted_mids = []
    sent_messages = []

    async def fake_delete(chat_id, mid):
        deleted_mids.append(mid)

    async def fake_send(text, chat_id, kb=None):
        sent_messages.append((text, kb))

    monkeypatch.setattr(tk, "_delete", fake_delete)
    monkeypatch.setattr(tk, "_send", fake_send)

    await tk.handle_clear_chat("chat123", message_id=100)

    assert deleted_mids == []
    assert len(sent_messages) == 1
    assert "Desk reset" in sent_messages[0][0]
    assert "trade alerts are preserved" in sent_messages[0][0]
    assert sent_messages[0][1] == tk.kite_menu_kb()


def test_pcr_alerts_collection(monkeypatch):
    from app.api.v1.endpoints import pcr
    fake_cache = {
        "payload": {
            "series": {
                "NIFTY": {
                    "latest": {
                        "time": "03:30 PM",
                        "pcr": 1.28,
                        "change": -0.02,
                        "signal": "WAIT",
                        "note": "Puts cooled off.",
                    },
                },
                "SENSEX": {
                    "latest": {
                        "time": "03:30 PM",
                        "pcr": 1.61,
                        "change": 0.05,
                        "signal": "BUY CE",
                        "note": "Puts are being sold.",
                    },
                },
            }
        }
    }
    monkeypatch.setattr(pcr, "_CACHE", fake_cache)

    alerts = tk._pcr_alerts("u1")
    assert len(alerts) == 1
    assert alerts[0].strategy == "pcr_flow"
    assert "pcr_alert:" in alerts[0].dedupe_key
    assert "03:30 PM" in alerts[0].dedupe_key
    assert "Nifty" in alerts[0].detail
    assert "Sensex" in alerts[0].detail
    assert "🟢 Buy CE" in alerts[0].detail
