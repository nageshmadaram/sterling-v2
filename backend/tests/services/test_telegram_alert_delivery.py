"""Telegram lifecycle delivery: ordering, toggle independence, retries, formatting, auth."""
from __future__ import annotations

import asyncio
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services import db
from app.services.notifications import kite_telegram_store as kts
from app.services.notifications import lifecycle_format as lf
from app.services.notifications import telegram_bot as tb
from app.services.notifications import telegram_kite as tk
from app.services.notifications import telegram_lifecycle as outbox

pytestmark = pytest.mark.asyncio


@pytest.fixture()
def lifecycle_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "delivery.db")
    monkeypatch.setenv("STERLING_DB_PATH", db_path)
    monkeypatch.setattr(db, "_DB_PATH", db_path)
    monkeypatch.setattr(db, "_available", True)
    db.init()
    outbox.clear_for_tests()
    monkeypatch.setattr(tk, "_MIN_SEND_GAP_S", 0.0)
    tk._alerted.clear()
    tk._alerted_loaded.clear()
    tk._dest_last_send.clear()
    yield
    outbox.clear_for_tests()


def _target(tid="t1", token="tok1", chat="1"):
    return SimpleNamespace(id=tid, bot_token=token, bot_token_enc=f"enc-{tid}",
                           chat_id=chat, reachable=True)


def _wire(monkeypatch, *, targets, send, signals=(), alerts_on=True, users=("u1",)):
    monkeypatch.setattr(tk, "_alert_user_ids", lambda: list(users))
    monkeypatch.setattr(tk, "alerts_enabled", lambda uid=tk.UID: alerts_on)
    monkeypatch.setattr(tk, "_load_alerted", lambda uid: None)
    monkeypatch.setattr(tk, "_save_alerted", lambda uid: None)
    monkeypatch.setattr(tk, "_prune_alerted", lambda uid: False)
    monkeypatch.setattr(tk, "_strategy_alerts", lambda uid: list(signals))
    monkeypatch.setattr(tk, "_mode_label", lambda uid=tk.UID: "LIVE")
    monkeypatch.setattr(kts, "enabled_targets", lambda uid: list(targets))
    monkeypatch.setattr(kts, "set_reachable", lambda *a, **k: None)
    monkeypatch.setattr(kts, "send_via", send)


def _enqueue(key="k1", strategy="SuperTrend", severity="INFO", event="ENTRY FILLED", at=None):
    return outbox.enqueue(
        "u1", dedupe_key=key, strategy=strategy, event_type=event,
        title="NIFTY26OCT25000CE", detail="Qty 75 · Fill ₹100.00 · SL ₹80.00",
        severity=severity, created_at_ms=at or outbox._now_ms(),
    )


def _signal():
    return tk.StrategyAlert(
        strategy="kite_supertrend", strategy_label="SuperTrend", dedupe_key="s|1",
        timestamp_ms=1_790_000_000_000, title="NIFTY 50", direction="LONG",
        option_type="CE", option_symbol="NIFTY26OCT25000CE",
    )


async def test_lifecycle_drains_even_when_signal_alerts_are_switched_off(lifecycle_db, monkeypatch):
    sent = []

    async def send(token, chat, html):
        sent.append(html)
        return True

    _wire(monkeypatch, targets=[_target()], send=send, signals=[_signal()], alerts_on=False)
    event_id = _enqueue()

    await tk.push_kite_alerts()

    assert len(sent) == 1 and "ENTRY FILLED" in sent[0], "only the trade alert, no signal card"
    assert outbox.due("u1", now_ms=outbox._now_ms() + 10) == []
    assert event_id


async def test_lifecycle_is_delivered_before_signal_cards(lifecycle_db, monkeypatch):
    sent = []

    async def send(token, chat, html):
        sent.append("lifecycle" if "ENTRY FILLED" in html else "signal")
        return True

    _wire(monkeypatch, targets=[_target()], send=send, signals=[_signal()])
    _enqueue()

    await tk.push_kite_alerts()

    assert sent == ["lifecycle", "signal"]


async def test_partial_failure_is_one_attempt_and_only_the_failed_target_retries(lifecycle_db, monkeypatch):
    calls = []
    healthy = {"tok2": False}

    async def send(token, chat, html):
        calls.append(token)
        return token == "tok1" or healthy["tok2"]

    _wire(monkeypatch, targets=[_target("t1", "tok1", "1"), _target("t2", "tok2", "2")], send=send)
    event_id = _enqueue(severity="CRITICAL")

    await tk.push_kite_alerts()
    assert calls == ["tok1", "tok2"]
    row = outbox.due("u1", now_ms=outbox._now_ms() + 31_000)[0]
    assert row.attempts == 1, "one failed cycle is one attempt however many targets failed"
    assert len(row.delivered_to) == 1

    # Not due again until the backoff elapses; then only the failed target is retried.
    calls.clear()
    healthy["tok2"] = True
    assert outbox.due("u1", now_ms=outbox._now_ms()) == []
    later = outbox._now_ms() + 61_000
    with patch.object(outbox, "_now_ms", return_value=later):
        await tk.push_kite_alerts()
    assert calls == ["tok2"]
    assert outbox.due("u1", now_ms=later + 10**6) == []
    assert event_id


async def test_ok_false_from_telegram_leaves_the_event_queued(lifecycle_db, monkeypatch):
    async def send(token, chat, html):
        return False  # what send_via returns for an {"ok": false} reply

    _wire(monkeypatch, targets=[_target()], send=send)
    _enqueue()
    await tk.push_kite_alerts()
    assert outbox.pending_user_ids() == ["u1"]
    assert outbox.pending_summary("u1")["max_attempts"] == 1


async def test_critical_gamma_alert_bypasses_signal_rate_limit_but_info_is_deferred(lifecycle_db, monkeypatch):
    sent = []

    async def send(token, chat, html):
        sent.append(html)
        return True

    _wire(monkeypatch, targets=[_target()], send=send)
    monkeypatch.setattr(tk, "_gamma_rate_slot", AsyncMock(return_value=False))
    _enqueue("crit", "Gamma-Unwind", "CRITICAL", "PROTECTION FAILED")
    _enqueue("info", "Gamma-Unwind", "INFO", "ENTRY FILLED")

    await tk.push_kite_alerts()

    assert len(sent) == 1 and "PROTECTION FAILED" in sent[0]
    deferred = outbox.due("u1", now_ms=outbox._now_ms() + 10)
    assert [e.dedupe_key for e in deferred] == ["info"]
    assert deferred[0].attempts == 0, "rate-limit deferral is not a delivery failure"


async def test_one_tenants_failure_does_not_block_the_next(lifecycle_db, monkeypatch):
    sent = []

    async def send(token, chat, html):
        sent.append(token)
        return True

    _wire(monkeypatch, targets=[_target()], send=send, users=("bad", "u1"))
    real = kts.enabled_targets

    def boom_for_bad(uid):
        if uid == "bad":
            raise RuntimeError("store exploded")
        return real(uid)

    monkeypatch.setattr(kts, "enabled_targets", boom_for_bad)
    _enqueue()
    await tk.push_kite_alerts()
    assert sent == ["tok1"]


async def test_events_without_any_destination_are_kept_not_dropped(lifecycle_db, monkeypatch):
    async def send(token, chat, html):  # pragma: no cover - must not be called
        raise AssertionError("no destination configured")

    _wire(monkeypatch, targets=[], send=send)
    monkeypatch.setattr(tk._tg, "TELEGRAM_TOKEN", "")
    monkeypatch.setattr(tk._tg, "TELEGRAM_CHAT_ID", "")
    _enqueue()
    await tk.push_kite_alerts()
    assert outbox.pending_user_ids() == ["u1"]


async def test_delivered_text_has_no_paper_or_simulation_wording(lifecycle_db, monkeypatch):
    monkeypatch.undo()  # use the real _mode_label against a non-live account
    event = SimpleNamespace(
        strategy="Snapback", event_type="ENTRY FILLED", title="simulated paper fill",
        detail="paper account SIMULATION", severity="INFO", created_at_ms=1_790_000_000_000,
    )
    with patch.object(tk, "_active_account", return_value=SimpleNamespace(is_paper=True)):
        html = tk._lifecycle_html(event, "u1")
        card = tk._alert_html(_signal(), "u1")
    for text in (html, card):
        assert not re.search(r"(?i)paper|simulat", text)
        assert len(text) <= 4096


async def test_signal_card_uses_the_same_header_and_ist_footer(lifecycle_db, monkeypatch):
    monkeypatch.setattr(tk, "_mode_label", lambda uid=tk.UID: "LIVE")
    card = tk._alert_html(_signal(), "u1")
    assert card.startswith("🟢 <b>STERLING · SuperTrend · SIGNAL</b>")
    assert re.search(r"<i>LIVE · \d{2} \w{3} \d{2}:\d{2}:\d{2} IST", card)
    assert "CE" in card


async def test_stray_markup_in_a_symbol_cannot_break_the_message(lifecycle_db):
    event = SimpleNamespace(
        strategy="SuperTrend", event_type="PROTECTION FAILED", title="A&B_<c>*",
        detail="x < y && z > w", severity="CRITICAL", created_at_ms=1_790_000_000_000,
    )
    with patch.object(tk, "_mode_label", return_value="LIVE"):
        html = tk._lifecycle_html(event, "u1")
    assert re.sub(r"</?[bi]>", "", html).count("<") == 0


async def test_staged_buttons_and_confirmations_expire_and_are_purged(monkeypatch):
    clock = {"now": 100.0}
    monkeypatch.setattr(tk, "time", SimpleNamespace(time=lambda: 0.0, monotonic=lambda: clock["now"]))
    tk._sig_actions.clear(); tk._pos_actions.clear(); tk._action_created.clear()
    tk._pending.clear(); tk._pending_created.clear()
    tk._sig_actions["1"] = {"option_symbol": "X"}
    tk._action_created[("sig", "1")] = clock["now"]
    tk._pos_actions["2"] = {"option_symbol": "Y"}
    tk._action_created[("pos", "2")] = clock["now"]
    token = tk._stage({"kind": "auto", "to": "ON"})
    assert tk._fresh_action("sig", "1") is not None

    clock["now"] += tk._ACTION_TTL_S + 1
    assert tk._fresh_action("sig", "1") is None
    # An abandoned confirmation is purged by the next stage; maps never grow forever.
    tk._stage({"kind": "auto", "to": "OFF"})
    assert token not in tk._pending and token not in tk._pending_created
    assert "2" not in tk._pos_actions


async def test_poll_loop_discards_backlog_queued_while_offline(monkeypatch):
    calls = []

    async def api(method, payload, timeout=35.0):
        calls.append((method, dict(payload)))
        if method in {"setMyCommands", "setChatMenuButton"}:
            return {"ok": True, "result": True}
        if sum(method == "getUpdates" for method, _ in calls) == 1:
            return {"ok": True, "result": [{"update_id": 41}, {"update_id": 42}]}
        raise asyncio.CancelledError()

    monkeypatch.setattr(tb, "_api", api)
    handled = AsyncMock()
    monkeypatch.setattr(tb, "_handle_update", handled)
    with patch.object(tb._tg, "TELEGRAM_TOKEN", "token"), patch.object(
        tb._tg, "TELEGRAM_CHAT_ID", "1"
    ):
        with pytest.raises(asyncio.CancelledError):
            await tb.poll_loop()

    assert calls[0] == ("setMyCommands", {"commands": tb.COMMANDS})
    assert calls[1][0] == "setChatMenuButton"
    assert calls[2] == ("getUpdates", {"offset": -1, "timeout": 0})
    assert calls[3][1]["offset"] == 43
    handled.assert_not_awaited()


@pytest.mark.parametrize(
    "chat,sender",
    [("-100123", "1"), ("1", "2"), ("2", "1"), ("", ""), ("1", "")],
)
async def test_only_the_owners_private_chat_can_command_or_confirm(chat, sender):
    message = {"message": {"chat": {"id": chat}, "from": {"id": sender}}}
    callback = {
        "callback_query": {
            "id": "cb", "data": "kok|1", "from": {"id": sender},
            "message": {"chat": {"id": chat}, "message_id": 9},
        }
    }
    with patch.object(tb._tg, "TELEGRAM_CHAT_ID", "1"), patch(
        "app.services.notifications.telegram_kite.handle_kite_command", new=AsyncMock()
    ) as command, patch(
        "app.services.notifications.telegram_kite.handle_kite_callback", new=AsyncMock()
    ) as confirm, patch.object(tb, "_answer_cb", new=AsyncMock()):
        await tb._handle_update(message)
        await tb._handle_update(callback)
    if chat == "1" and sender == "1":
        command.assert_awaited_once()
        confirm.assert_awaited_once()
    else:
        command.assert_not_awaited()
        confirm.assert_not_awaited()


class _Resp:
    def __init__(self, status, body, text=""):
        self.status_code, self._body, self.text = status, body, text

    def json(self):
        return self._body


def _client_returning(responses, seen):
    class Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json):
            seen.append(json)
            return responses.pop(0)

    return Client


async def test_send_via_degrades_to_plain_text_when_telegram_rejects_the_markup(monkeypatch):
    seen = []
    responses = [
        _Resp(400, {"ok": False, "description": "Bad Request: can't parse entities: Unsupported start tag"}),
        _Resp(200, {"ok": True}),
    ]
    monkeypatch.setattr(kts.httpx, "AsyncClient", _client_returning(responses, seen))
    assert await kts.send_via("tok", "1", "<b>A &amp; B</b>") is True
    assert seen[0]["parse_mode"] == "HTML"
    assert "parse_mode" not in seen[1] and seen[1]["text"] == "A & B"


async def test_send_via_never_posts_an_oversized_message(monkeypatch):
    seen = []
    responses = [_Resp(200, {"ok": True})]
    monkeypatch.setattr(kts.httpx, "AsyncClient", _client_returning(responses, seen))
    assert await kts.send_via("tok", "1", "<b>" + "x" * 9000 + "</b>") is True
    assert len(seen[0]["text"]) <= lf.TELEGRAM_MAX_CHARS


async def test_send_via_ok_false_with_200_is_not_delivery(monkeypatch):
    seen = []
    responses = [_Resp(200, {"ok": False, "description": "nope"})]
    monkeypatch.setattr(kts.httpx, "AsyncClient", _client_returning(responses, seen))
    assert await kts.send_via("tok", "1", "<b>x</b>") is False


async def test_send_via_never_logs_the_bot_token_on_transport_errors(monkeypatch, caplog):
    secret = "123456789:" + "S" * 35

    class Boom:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json):
            raise RuntimeError(f"cannot reach {url}")

    monkeypatch.setattr(kts.httpx, "AsyncClient", Boom)
    monkeypatch.setattr(kts.asyncio, "sleep", AsyncMock())
    with caplog.at_level("DEBUG"):
        assert await kts.send_via(secret, "1", "<b>x</b>") is False
    assert secret not in caplog.text


async def test_bot_send_retries_once_as_plain_text_on_a_parse_error(monkeypatch):
    calls = []

    async def api(method, payload, timeout=35.0):
        calls.append(dict(payload))
        if len(calls) == 1:
            return {"ok": False, "status": 400, "description": "Bad Request: can't parse entities"}
        return {"ok": True}

    monkeypatch.setattr(tb, "_api", api)
    result = await tb._send("<b>hi &amp; bye</b>", "1")
    assert result == {"ok": True}
    assert calls[0]["parse_mode"] == "HTML" and "parse_mode" not in calls[1]
    assert calls[1]["text"] == "hi & bye"
