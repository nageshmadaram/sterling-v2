from html import escape
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlencode, urlparse

import pytest

from app.services.exchanges.kite import session
from app.services.notifications import telegram_kite as desk


@pytest.fixture
def account(monkeypatch):
    from app.services import public_access

    acct = SimpleNamespace(
        id="KITE-MOBILE", api_key="synthetic", api_secret="synthetic-secret",
        label="Mobile", user_name="", kite_user_id="", last_login_at_ms=None,
        token_expires_at_ms=None,
    )
    monkeypatch.setattr(desk, "_active_account", lambda: acct)
    monkeypatch.setattr(desk, "connection_state", lambda: "OFFLINE")
    monkeypatch.setattr(desk, "_mode_label", lambda: "LIVE")
    monkeypatch.setattr(public_access, "status", lambda: {"app_url": "https://sterling.example"})
    from app.services.exchanges.kite import accounts
    monkeypatch.setattr(accounts, "list_accounts", lambda uid: [acct] if uid == desk.UID else [])
    return acct


def _redirect(acct, **query):
    values = {"request_token": "synthetic-request-token", "state": session.make_state(desk.UID, acct.id), "status": "success", "action": "login"}
    values.update(query)
    return "http://localhost:5173/api/v1/kite/callback?" + urlencode(values)


@pytest.mark.parametrize("entry", ["command", "menu", "copy_fallback"])
async def test_each_login_menu_has_matching_open_and_copy_urls(monkeypatch, account, entry):
    send = AsyncMock()
    monkeypatch.setattr(desk, "_send", send)
    monkeypatch.setattr(desk, "_answer_cb", AsyncMock())
    monkeypatch.setattr(desk, "checked_broker_login_card", AsyncMock(side_effect=desk.broker_login_card))

    if entry == "command":
        await desk.handle_text_command("123", "/login")
    else:
        await desk.handle_kite_callback("123", 1, "callback", "klogin" if entry == "menu" else "klogincopy")

    text, _, keyboard = send.await_args.args
    buttons = [button for row in keyboard["inline_keyboard"] for button in row]
    login_url = next(button["url"] for button in buttons if "url" in button)
    copied = next(button["copy_text"]["text"] for button in buttons if "copy_text" in button)
    assert copied == login_url
    query = parse_qs(urlparse(login_url).query)
    assert urlparse(login_url).netloc == "sterling.example"
    assert query == {"sterling_login": ["1"], "broker_login": ["1"]}
    assert "Sign in to Sterling first" in text
    assert account.api_secret not in str((text, keyboard))


def test_broker_api_key_is_never_put_in_telegram_login_link(account):
    account.api_key = 'private-key' * 40
    text, keyboard = desk.broker_login_card()
    buttons = [button for row in keyboard['inline_keyboard'] for button in row]
    url = next(button['url'] for button in buttons if 'url' in button)
    assert account.api_key not in str((text, keyboard))
    assert next(button['copy_text']['text'] for button in buttons if 'copy_text' in button) == url


async def test_finish_mobile_login_button_explains_full_browser_address(monkeypatch):
    send = AsyncMock()
    monkeypatch.setattr(desk, "_send", send)
    monkeypatch.setattr(desk, "_answer_cb", AsyncMock())
    await desk.handle_kite_callback("123", 1, "callback", "kloginrecover")
    text = send.await_args.args[0]
    assert "entire address" in text
    assert "request_token" in text and "state" in text


@pytest.mark.parametrize("prefix", ["", "/login ", "/login@SterlingBot "])
async def test_pasted_mobile_callback_finishes_bound_account(monkeypatch, account, prefix):
    from app.services.exchanges.kite import login_recovery

    complete = AsyncMock(return_value={"user_name": "Mobile <Account>"})
    send = AsyncMock()
    monkeypatch.setattr(login_recovery, "complete_login", complete)
    monkeypatch.setattr(desk, "_send", send)
    await desk.handle_text_command("123", prefix + _redirect(account))
    complete.assert_awaited_once_with(
        desk.UID, account, "synthetic-request-token", login_state=complete.await_args.kwargs["login_state"],
    )
    assert session.parse_state(complete.await_args.kwargs["login_state"]) == (desk.UID, account.id)
    result = send.await_args.args[0]
    assert "KITE LOGIN SUCCESSFUL" in result and "Mobile &lt;Account&gt;" in result
    assert "synthetic-request-token" not in result


@pytest.mark.parametrize("bad_input", ["unsigned", "other_user", "other_account", "expired", "duplicate_state", "duplicate_token", "raw_token"])
async def test_recovery_rejects_unverified_input_before_broker_exchange(monkeypatch, account, bad_input):
    from app.services.exchanges.kite import login_recovery

    callback_url = _redirect(account)
    if bad_input == "unsigned":
        callback_url = _redirect(account, state="")
    elif bad_input == "other_user":
        callback_url = _redirect(account, state=session.make_state("another-user", account.id))
    elif bad_input == "other_account":
        callback_url = _redirect(account, state=session.make_state(desk.UID, "KITE-OTHER"))
    elif bad_input == "expired":
        callback_url = _redirect(account, state=session.make_state(desk.UID, account.id, issued_at_ms=1))
    elif bad_input == "duplicate_state":
        callback_url += "&state=invalid"
    elif bad_input == "duplicate_token":
        callback_url += "&request_token=another"
    elif bad_input == "raw_token":
        callback_url = "synthetic-request-token"

    complete, send = AsyncMock(), AsyncMock()
    monkeypatch.setattr(login_recovery, "complete_login", complete)
    monkeypatch.setattr(desk, "_send", send)
    await desk.handle_text_command("123", "/login " + callback_url)
    complete.assert_not_awaited()
    assert "could not be verified" in send.await_args.args[0]
    assert "synthetic-request-token" not in send.await_args.args[0]


async def test_recovery_broker_failure_does_not_echo_callback_or_exception(monkeypatch, account):
    from app.services.exchanges.kite import login_recovery

    send = AsyncMock()
    monkeypatch.setattr(desk, "_send", send)
    monkeypatch.setattr(login_recovery, "complete_login", AsyncMock(side_effect=RuntimeError("synthetic-request-token synthetic-secret")))
    await desk.handle_text_command("123", _redirect(account))
    text = send.await_args.args[0]
    assert "Could not finish Kite login" in text
    assert "synthetic-request-token" not in text and "synthetic-secret" not in text


@pytest.mark.parametrize("chat_id,sender_id", [("-100123", "777"), ("777", "777")])
async def test_signed_callback_cannot_bypass_telegram_controller_authorization(monkeypatch, account, chat_id, sender_id):
    from app.services.notifications import telegram_bot

    recover = AsyncMock()
    monkeypatch.setattr(telegram_bot._tg, "TELEGRAM_CHAT_ID", "123")
    monkeypatch.setattr(telegram_bot, "_send", AsyncMock())
    monkeypatch.setattr(desk, "_recover_broker_login", recover)
    await telegram_bot._handle_update({"message": {
        "chat": {"id": chat_id}, "from": {"id": sender_id},
        "text": _redirect(account), "message_id": 1,
    }})
    recover.assert_not_awaited()


async def test_authorized_dispatcher_completes_pasted_signed_callback(monkeypatch, account):
    from app.services.exchanges.kite import login_recovery
    from app.services.notifications import telegram_bot

    complete = AsyncMock(return_value={"user_name": "Mobile Account"})
    send = AsyncMock()
    monkeypatch.setattr(telegram_bot._tg, "TELEGRAM_CHAT_ID", "123")
    monkeypatch.setattr(desk, "_send", send)
    monkeypatch.setattr(login_recovery, "complete_login", complete)
    await telegram_bot._handle_update({"message": {
        "chat": {"id": "123", "type": "private"}, "from": {"id": "123"},
        "text": _redirect(account), "message_id": 1,
    }})
    complete.assert_awaited_once_with(
        desk.UID, account, "synthetic-request-token", login_state=complete.await_args.kwargs["login_state"],
    )
    assert session.parse_state(complete.await_args.kwargs["login_state"]) == (desk.UID, account.id)
    assert "KITE LOGIN SUCCESSFUL" in send.await_args.args[0]


async def test_telegram_defers_broker_choices_until_sterling_authentication(monkeypatch, account):
    from app.services.exchanges.kite import accounts
    lookup = AsyncMock()
    monkeypatch.setattr(accounts, 'list_accounts', lookup)
    text, keyboard = await desk.checked_broker_login_card()
    links = [button['url'] for row in keyboard['inline_keyboard'] for button in row if 'url' in button]
    assert len(links) == 1
    assert parse_qs(urlparse(links[0]).query) == {'sterling_login':['1'], 'broker_login':['1']}
    lookup.assert_not_called()
    assert account.id not in str((text, keyboard))
