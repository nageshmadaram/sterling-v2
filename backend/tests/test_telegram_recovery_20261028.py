"""Telegram outage recovery: old env spelling, safe 409 diagnostics, no secrets."""
from unittest.mock import AsyncMock

import pytest

from app.services.notifications import telegram as transport, telegram_bot as bot


@pytest.fixture()
def isolated_transport(monkeypatch):
    before = (transport.TELEGRAM_TOKEN, transport.TELEGRAM_CHAT_ID,
              transport.TELEGRAM_REACHABLE)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.setattr("app.services.db.get_config", lambda _key, *args: "")
    yield
    transport.TELEGRAM_TOKEN, transport.TELEGRAM_CHAT_ID, transport.TELEGRAM_REACHABLE = before


def test_old_token_env_alias_restores_control_bot(isolated_transport, monkeypatch):
    # load_sterling_env already allowed this historical name; telegram.py did not.
    monkeypatch.setenv("TELEGRAM_TOKEN", "synthetic-old-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123456")
    transport.TELEGRAM_TOKEN = ""
    transport.TELEGRAM_CHAT_ID = ""
    transport.restore_legacy_config_from_store()
    assert transport.TELEGRAM_TOKEN == "synthetic-old-token"
    assert transport.TELEGRAM_CHAT_ID == "123456"


def test_explicit_bot_token_overrides_old_alias_and_database(isolated_transport, monkeypatch):
    monkeypatch.setenv("TELEGRAM_TOKEN", "older-value")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "preferred-new-value")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123456")
    monkeypatch.setattr("app.services.db.get_config",
                        lambda key, *_: "synthetic-ciphertext" if key == "telegram_bot_token" else "")
    transport.restore_legacy_config_from_store()
    assert transport.TELEGRAM_TOKEN == "preferred-new-value"


@pytest.mark.asyncio
async def test_poll_409_identifies_webhook_without_deleting_or_consuming_updates(monkeypatch):
    api = AsyncMock(return_value={"ok": True, "result": {"url": "https://example.test/old-hook"}})
    monkeypatch.setattr(bot, "_api", api)
    assert await bot._poll_failure_code({"ok": False, "status": 409}) == "WEBHOOK_ACTIVE"
    api.assert_awaited_once_with("getWebhookInfo", {}, timeout=8)
    # Diagnostic must be read-only; no destructive deleteWebhook/getUpdates.
    assert all(call.args[0] not in {"deleteWebhook", "getUpdates", "setWebhook"}
               for call in api.await_args_list)


@pytest.mark.asyncio
async def test_poll_409_without_webhook_reports_competing_poller(monkeypatch):
    api = AsyncMock(return_value={"ok": True, "result": {"url": ""}})
    monkeypatch.setattr(bot, "_api", api)
    assert await bot._poll_failure_code({"ok": False, "status": 409}) == "POLL_CONFLICT"


@pytest.mark.asyncio
async def test_nonconflict_network_failure_never_probes_webhook(monkeypatch):
    api = AsyncMock()
    monkeypatch.setattr(bot, "_api", api)
    assert await bot._poll_failure_code({"ok": False}) == "NETWORK_ERROR"
    api.assert_not_awaited()
