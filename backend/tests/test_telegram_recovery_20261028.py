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


@pytest.mark.asyncio
async def test_legacy_sender_falls_back_to_plain_on_html_parse_rejection(
        isolated_transport, monkeypatch):
    import json

    transport.TELEGRAM_TOKEN = "synthetic-test-token"
    transport.TELEGRAM_CHAT_ID = "123456"
    sent = []

    class Response:
        def __init__(self, code, data):
            self.status_code = code
            self._body = data
            self.text = json.dumps(data)

        def json(self):
            return self._body

    class Client:
        def __init__(self, **kwargs):
            assert kwargs["follow_redirects"] is False
            assert kwargs["trust_env"] is False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, *, json):
            assert "synthetic-test-token" in url
            sent.append(dict(json))
            if len(sent) == 1:
                return Response(400, {"ok": False, "description": "Bad Request: can't parse entities"})
            return Response(200, {"ok": True, "result": {"message_id": 10}})

    monkeypatch.setattr(transport.httpx, "AsyncClient", Client)
    assert await transport.send("<b>unclosed alert") is True
    assert len(sent) == 2
    assert sent[0]["parse_mode"] == "HTML"
    assert "parse_mode" not in sent[1]
    assert sent[1]["chat_id"] == "123456"
    assert transport.TELEGRAM_REACHABLE is True


@pytest.mark.asyncio
async def test_legacy_sender_does_not_retry_denied_chat(isolated_transport, monkeypatch):
    import json

    transport.TELEGRAM_TOKEN = "synthetic-test-token"
    transport.TELEGRAM_CHAT_ID = "123456"
    sent = []

    class Response:
        status_code = 403
        text = '{"ok":false,"description":"Forbidden"}'

        def json(self):
            return json.loads(self.text)

    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, *, json):
            sent.append(json)
            return Response()

    monkeypatch.setattr(transport.httpx, "AsyncClient", Client)
    assert await transport.send("<b>test</b>") is False
    assert len(sent) == 1
    assert transport.TELEGRAM_REACHABLE is False
