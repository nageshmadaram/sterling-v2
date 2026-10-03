"""Telegram Kite desk: text builders, callback separation, and the
two-tap confirm → shared-order-service path. Network TX (_send/_edit) no-ops
without a token, so handlers run safely offline."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import asyncio
import pytest

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
    assert ("u2", t2_dest, "101|123456") in tk._alerted
    assert ("u1", t1_dest, "101|123456") not in tk._alerted

    await tk.push_kite_alerts()
    assert [c[0] for c in calls] == ["tok1", "tok2", "tok1"]
    assert ("u1", t1_dest, "101|123456") in tk._alerted


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


def test_target_edit_normalizes_and_invalidates_reachability(monkeypatch):
    from app.services.notifications import kite_telegram_store as store

    values = {}
    monkeypatch.setattr(
        store.db, "get_config", lambda key, default="": values.get(key, default)
    )
    monkeypatch.setattr(
        store.db, "set_config", lambda key, value: values.__setitem__(key, value)
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


async def test_target_test_escapes_html_label(monkeypatch):
    from app.api.v1.endpoints import kite_telegram as endpoint

    target = SimpleNamespace(
        id="t1",
        label="<Desk & Ops>",
        chat_id="1",
        bot_token="token",
        bot_token_set=True,
        bot_token_hint="token",
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
    out = await endpoint.test_target("t1", SimpleNamespace(user_id="u1"))

    assert out.reachable is True
    assert "&lt;Desk &amp; Ops&gt;" in sent["html"]
    assert "<Desk & Ops>" not in sent["html"]


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
    monkeypatch.setattr(
        adaptive_edge_runner,
        "scan_state",
        lambda _uid: {
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
        },
    )

    alerts = tk._adaptive_edge_alerts("u1")

    assert len(alerts) == 1
    assert alerts[0].strategy == "adaptive_edge"
    assert alerts[0].dedupe_key == (
        "adaptive_edge:NIFTY26OCT25000CE:2026-10-29:trace-a|123456"
    )
    assert alerts[0].option_symbol == "NIFTY26OCT25000CE"


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


async def test_legacy_non_supertrend_alert_has_no_trading_keyboard(monkeypatch):
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
