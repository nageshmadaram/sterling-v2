"""Telegram Kite desk: text builders, callback separation, and the
two-tap confirm → shared-order-service path. Network TX (_send/_edit) no-ops
without a token, so handlers run safely offline."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.services.notifications import telegram_kite as tk
from app.services.notifications import telegram_bot as tb


def test_signals_text_handles_empty_scan():
    txt = tk.build_kite_signals_text()
    assert "Kite signals" in txt


def test_status_text_reports_mode_and_toggles():
    txt = tk.build_kite_status_text()
    assert "Auto-trade" in txt and "Alerts" in txt


async def test_callbacks_route_to_kite_module():
    """All callbacks are delegated to the Kite command surface.

    Drives `_handle_update`, the actual update-loop entry point. The private
    `_handle_callback` hop this used to call was inlined when the crypto command
    surface was removed — only the Kite namespace is left to route to, so the
    dispatch no longer needs a branch of its own.
    """
    def _cb(data: str) -> dict:
        return {"callback_query": {"id": "cb", "data": data,
                                   "message": {"message_id": 1, "chat": {"id": "1"}}}}

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
    row = SimpleNamespace(
        token=101,
        timestamp_ms=123456,
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
    assert ("u2", "t2", "101|123456") in tk._alerted
    assert ("u1", "t1", "101|123456") not in tk._alerted

    await tk.push_kite_alerts()
    assert [c[0] for c in calls] == ["tok1", "tok2", "tok1"]
    assert ("u1", "t1", "101|123456") in tk._alerted


async def test_legacy_single_bot_never_receives_another_users_signal(monkeypatch):
    tk._alerted.clear()
    row = SimpleNamespace(
        token=202, timestamp_ms=88, direction="short", legs=[],
        underlying="BANKNIFTY", option_type="PE",
    )
    monkeypatch.setattr(tk, "_alert_user_ids", lambda: ["other-user"])
    monkeypatch.setattr(tk, "alerts_enabled", lambda uid=tk.UID: True)
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


async def test_order_requires_two_tap_confirm_then_places():
    """Tapping Buy stages a pending order (no placement); only Confirm executes it
    via the shared service."""
    tk._sig_actions.clear()
    tk._pending.clear()
    tk._sig_actions["1"] = {"option_symbol": "NIFTY26JUN25000CE", "exchange": "NFO", "qty": 75}

    placed = AsyncMock(return_value={"status": "ok", "order_id": "X1", "message": "ok"})
    with patch("app.services.kite_engine.service.place_manual_order", new=placed):
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
    with patch("app.services.kite_engine.service.place_manual_order", new=AsyncMock()) as p2:
        await tk.handle_kite_callback("1", 1, "cb", "kno|zz")
        assert p2.await_count == 0
        assert "zz" not in tk._pending


async def test_expired_signal_action_does_not_place():
    tk._sig_actions.clear()
    tk._pending.clear()
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
