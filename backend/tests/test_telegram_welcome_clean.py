"""Verification tests for Telegram single welcome message and debounced health status."""
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from app.services.notifications import telegram_kite as tk


def test_welcome_card_contains_login_broker_and_only_enabled_strategies(monkeypatch):
    """Welcome card must show clean login info, correct broker icon, and ONLY enabled strategies."""
    monkeypatch.setattr(tk, "connection_state", lambda uid=tk.UID: "CONNECTED")
    mock_acct = MagicMock()
    mock_acct.user_name = "Nagesh Madaram"
    mock_acct.kite_user_id = "NM1234"
    monkeypatch.setattr(tk, "_active_account", lambda uid=tk.UID: mock_acct)
    monkeypatch.setattr(tk, "_get_tma_url", lambda: "https://test.trycloudflare.com/tma")
    monkeypatch.setattr(tk, "alerts_enabled", lambda uid=tk.UID: True)
    monkeypatch.setattr(tk, "_mode_label", lambda uid=tk.UID: "PAPER")

    # SuperTrend, Snapback, Gamma Unwind enabled; others disabled
    def _is_strat_enabled(uid, strat):
        return strat in {"snapback", "gamma_move"}
    monkeypatch.setattr(tk, "_strategy_enabled", _is_strat_enabled)

    card = tk.build_welcome_text()

    # 1. Header & Mode
    assert "🏠 <b>STERLING DESK</b>" in card
    assert "<code>PAPER</code>" in card

    # 2. Sterling Login & Access
    assert "🌐  <b>Sterling Access</b>" in card
    assert "https://test.trycloudflare.com/tma" in card
    assert "👤  <b>Sterling User</b>: <code>default (admin)</code>" in card

    # 3. Broker (Kite) Connection & Account - MUST be 🟢 Connected, NOT 🔴
    assert "🟢  <b>Broker (Kite)</b>: <b>Connected · Active</b>" in card
    assert "🔴  Broker Connection" not in card
    assert "👤  <b>Broker Account</b>: <b>Nagesh Madaram</b> <code>(NM1234)</code>" in card

    # 4. Only ENABLED strategies listed
    assert "🎯 <b>Active Strategies</b>" in card
    assert "• 🟢 <b>SuperTrend</b> — Active" in card
    assert "• 🟢 <b>Snapback</b> — Active" in card
    assert "• 🟢 <b>Gamma Unwind</b> — Active" in card

    # 5. Inactive strategies MUST NOT appear with Disabled
    assert "Adaptive Edge — Disabled" not in card
    assert "Intraday Pack — Disabled" not in card
    assert "Opening Vol Leaders — Disabled" not in card
    assert "Value-Flow Navigator — Disabled" not in card
    assert "• ⚪" not in card  # No disabled white dots in strategy list

    # 6. Informative controls
    assert "⚡ <b>System Controls</b>" in card
    assert "• Status:" in card
    assert "• Telegram Alerts: 🟢 <b>Active</b>" in card


def test_live_health_and_kite_status_use_welcome_card(monkeypatch):
    monkeypatch.setattr(tk, "build_welcome_text", lambda uid=tk.UID: "MOCK_WELCOME_TEXT")
    assert tk.build_kite_status_text() == "MOCK_WELCOME_TEXT"
    assert tk.live_health_text() == "MOCK_WELCOME_TEXT"


@pytest.mark.asyncio
async def test_lifecycle_delivery_attaches_menu_for_welcome_events(monkeypatch):
    """When a LIVE BOOT or LIVE STATUS event is delivered, it must attach kite_menu_kb()."""
    mock_event = MagicMock()
    mock_event.event_id = "EVT-100"
    mock_event.event_type = "LIVE STATUS"
    mock_event.severity = "INFO"
    mock_event.strategy = "Sterling"
    mock_event.delivered_to = []

    monkeypatch.setattr("app.services.notifications.telegram_lifecycle.due", lambda uid, **kw: [mock_event])
    marked = []
    monkeypatch.setattr("app.services.notifications.telegram_lifecycle.mark_delivered", lambda eid, dest: marked.append((eid, dest)))
    monkeypatch.setattr("app.services.notifications.telegram_lifecycle.complete_if_delivered", lambda *a: None)

    sent_messages = []
    async def _mock_send(text, chat_id, reply_markup=None):
        sent_messages.append({"text": text, "chat_id": chat_id, "reply_markup": reply_markup})
        return {"ok": True}

    monkeypatch.setattr(tk, "_send", _mock_send)
    monkeypatch.setattr(tk._tg, "TELEGRAM_TOKEN", "mock_tok")
    monkeypatch.setattr(tk._tg, "TELEGRAM_CHAT_ID", "12345")
    tk._last_welcome_sent_time.clear()

    # Drain lifecycle
    await tk._drain_lifecycle(tk.UID, [], use_legacy=True)

    assert len(sent_messages) == 1
    # Menu must be present
    assert sent_messages[0]["reply_markup"] is not None
    # Verify buttons in menu
    keyboard = sent_messages[0]["reply_markup"]["inline_keyboard"]
    all_btn_texts = [btn["text"] for row in keyboard for btn in row]
    assert "📡 Signals" in all_btn_texts
    assert "📈 PCR" in all_btn_texts
    assert "📊 Positions" in all_btn_texts
    assert "⚙️ Strategies" in all_btn_texts
    assert "🔑 Broker Login" in all_btn_texts

    # Second drain immediately afterwards must be debounced (no second send to chat)
    mock_event2 = MagicMock()
    mock_event2.event_id = "EVT-101"
    mock_event2.event_type = "LIVE READY"
    mock_event2.severity = "INFO"
    mock_event2.strategy = "Sterling"
    mock_event2.delivered_to = []
    monkeypatch.setattr("app.services.notifications.telegram_lifecycle.due", lambda uid, **kw: [mock_event2])

    await tk._drain_lifecycle(tk.UID, [], use_legacy=True)
    # Still 1 send! Duplicate suppressed.
    assert len(sent_messages) == 1
