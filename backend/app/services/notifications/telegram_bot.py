"""Telegram transport and command dispatcher for the Zerodha/Kite desk."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

import httpx

from app.services.notifications import telegram as _tg

log = logging.getLogger(__name__)
_API = "https://api.telegram.org"
COMMANDS = [
    {"command": command, "description": description}
    for command, description in (
        # Home & Mini App
        ("menu",       "Home — status & navigation"),
        ("app",        "📱 Launch Sterling Mini App"),
        ("operator",   "Manage Sterling username/password or add a user"),
        ("url",        "Current app and Telegram desk address"),
        ("clear",      "Reset desk navigation"),
        # Market & Analytics
        ("signals",    "Active signals & signal board"),
        ("positions",  "Open positions & square off"),
        ("orders",     "Today's executed & pending orders"),
        ("pnl",        "P&L summary (realized + unrealized)"),
        ("pcr",        "PCR analytics & Put-Call Ratio"),
        ("pcr_nifty",  "📈 Nifty 15m PCR flow tape"),
        ("pcr_sensex", "📈 Sensex 15m PCR flow tape"),
        ("market",     "Current market signals and data availability"),
        ("scan",       "Run SuperTrend scan now"),
        ("funds",      "Broker account funds & margins"),
        ("ticker",     "Live quote feed & ticker health"),
        ("search",     "Search live stock & option quotes"),
        # Controls & Quick Order
        ("order",      "⚡ Quick Order Terminal"),
        ("algo",       "SuperTrend ALGO execution control"),
        ("alerts",     "Toggle real-time signal alerts"),
        ("panic",      "Emergency halt and attempt position exits"),
        # System
        ("health",     "System & broker health"),
        ("login",      "Kite connection and fresh login link"),
        ("strategies", "Strategy toggles & archived engines"),
        ("help",       "All commands & quick guide"),
    )
]
_poll_status = {"state": "starting", "error_code": "", "last_poll_at_ms": None,
                "last_update_at_ms": None, "commands_registered": False}


def poll_status() -> dict:
    status = dict(_poll_status)
    if status["state"] == "polling" and time.time() * 1000 - (status["last_poll_at_ms"] or 0) > 90_000:
        status["state"] = "stalled"
    return status


def failure_code(response: dict) -> str:
    status = response.get("status")
    return {401: "INVALID_TOKEN", 403: "ACCESS_DENIED", 409: "POLL_CONFLICT",
            429: "RATE_LIMITED"}.get(status, "NETWORK_ERROR" if not status else "TELEGRAM_ERROR")


async def _api(method: str, payload: dict, timeout: float = 35.0) -> dict:
    if not _tg.TELEGRAM_TOKEN:
        return {}
    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            response = await client.post(
                f"{_API}/bot{_tg.TELEGRAM_TOKEN}/{method}",
                json=payload,
            )
        try:
            body = response.json()
        except Exception:  # noqa: BLE001
            body = {}
        if response.status_code != 200 or not bool(body.get("ok")):
            description = str(body.get("description") or "")[:160]
            log.warning(
                "telegram API %s failed status=%s description=%s",
                method,
                response.status_code,
                description,
            )
            return {
                "ok": False,
                "status": response.status_code,
                "description": description,
                "parameters": body.get("parameters") or {},
            }
        return body
    except Exception as exc:
        # Never log ``exc`` verbatim: transport errors can embed the request URL,
        # and the URL carries the bot token.
        log.debug("telegram API %s error: %s", method, type(exc).__name__)
        return {}


_PARSE_ERROR_MARKERS = (
    "can't parse entities",
    "can't find end of the entity",
    "unsupported start tag",
    "unexpected end tag",
    "can't parse message text",
    "too long",
)


def _is_format_rejection(response: dict) -> bool:
    if not isinstance(response, dict) or response.get("ok") or response.get("status") != 400:
        return False
    lowered = str(response.get("description") or "").lower()
    return any(marker in lowered for marker in _PARSE_ERROR_MARKERS)


def _plain_payload(payload: dict) -> dict:
    """Same message as unformatted text, clipped to Telegram's limit."""
    from app.services.notifications import lifecycle_format as fmt

    plain = dict(payload)
    plain.pop("parse_mode", None)
    plain["text"] = fmt.to_plain(str(payload.get("text") or ""))[: fmt.TELEGRAM_MAX_CHARS - 1]
    return plain


async def _send(text: str, chat_id: str, reply_markup: Optional[dict] = None) -> dict:
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    response = await _api("sendMessage", payload)
    if _is_format_rejection(response):
        # A formatting defect may degrade an alert's look, never suppress it.
        response = await _api("sendMessage", _plain_payload(payload))
    return response


async def _edit(
    chat_id: str,
    message_id: int,
    text: str,
    reply_markup: Optional[dict] = None,
) -> dict:
    payload = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    response = await _api("editMessageText", payload)
    if _is_format_rejection(response):
        response = await _api("editMessageText", _plain_payload(payload))
    return response


async def _delete(chat_id: str, message_id: int) -> dict:
    return await _api("deleteMessage", {"chat_id": chat_id, "message_id": message_id})


async def _answer_cb(callback_id: str, text: str = "") -> None:
    await _api("answerCallbackQuery", {
        "callback_query_id": callback_id,
        "text": text,
    })


def _btn(text: str, data: str) -> dict:
    return {"text": text, "callback_data": data}


def _authorized_controller(chat_id: str, sender_id: str) -> bool:
    """The control bot is private-chat only.

    Telegram group chat IDs identify the room, not the person pressing an order
    button. Requiring both chat and sender to equal the configured private-chat
    ID prevents every group member from inheriting trading authority.
    """
    configured = str(_tg.TELEGRAM_CHAT_ID or "").strip()
    return bool(
        configured
        and str(chat_id or "").strip() == configured
        and str(sender_id or "").strip() == configured
    )


async def _handle_update(update: dict) -> None:
    configured_chat = str(_tg.TELEGRAM_CHAT_ID or "").strip()
    if not configured_chat:
        return

    if "message" in update:
        message = update["message"]
        chat_id = str(message.get("chat", {}).get("id", ""))
        sender_id = str(message.get("from", {}).get("id", ""))
        if not _authorized_controller(chat_id, sender_id):
            if chat_id and sender_id and chat_id == sender_id:
                await _send(
                    f"🔒 <b>STERLING SECURITY CONTROL</b>\n\n"
                    f"This bot is currently configured for operator chat ID: <code>{configured_chat}</code>.\n"
                    f"Your Telegram Chat ID is: <code>{chat_id}</code>\n\n"
                    f"<i>To authorize this chat ID, update TELEGRAM_CHAT_ID={chat_id} in your Sterling settings.</i>",
                    chat_id
                )
            return
        from app.services.notifications import telegram_kite
        text = str(message.get("text") or "").strip()
        msg_id = message.get("message_id")
        if text:
            await telegram_kite.handle_text_command(chat_id, text, msg_id)
        else:
            await telegram_kite.handle_kite_command(chat_id)
        return

    if "callback_query" in update:
        callback = update["callback_query"]
        chat_id = str(callback.get("message", {}).get("chat", {}).get("id", ""))
        sender_id = str(callback.get("from", {}).get("id", ""))
        callback_id = callback.get("id", "")
        if not _authorized_controller(chat_id, sender_id):
            await _answer_cb(callback_id, "🔒 Chat ID not authorized")
            return
        from app.services.notifications import telegram_kite
        await telegram_kite.handle_kite_callback(
            chat_id,
            callback["message"]["message_id"],
            callback_id,
            callback.get("data", ""),
        )


def _backoff_delay(response: dict) -> float:
    retry_after = (response.get("parameters") or {}).get("retry_after")
    try:
        delay = float(retry_after or 5.0)
    except (TypeError, ValueError):
        delay = 5.0
    return max(1.0, min(delay, 30.0))


async def _skip_backlog() -> tuple[Optional[int], dict]:
    """Next offset past every update queued while the process was down.

    Telegram re-delivers unconfirmed updates for ~24 h.  Replaying them after a
    restart would re-run stale button presses (toggle alerts, re-open a confirm
    screen for a position that no longer exists).  Commands issued while the bot
    was offline are intentionally dropped; the operator simply presses again.
    Returns ``(None, response)`` when the backlog could not be read.
    """
    response = await _api("getUpdates", {"offset": -1, "timeout": 0}, timeout=15)
    if not response.get("ok"):
        return None, response
    result = response.get("result") or []
    if not result:
        return 0, response
    return max(int(u.get("update_id", 0)) for u in result) + 1, response


async def poll_loop() -> None:
    """Long-poll Telegram and dispatch every authorized update to the Kite desk."""
    offset: Optional[int] = None
    configuration = None
    next_registration = 0.0
    menu_url = None
    next_menu_registration = 0.0
    next_menu_refresh = 0.0
    log.info("Kite Telegram poll loop started")
    while True:
        if not _tg.TELEGRAM_TOKEN or not str(_tg.TELEGRAM_CHAT_ID or "").strip():
            _poll_status.update(state="waiting_configuration", error_code="MISSING_CONFIGURATION")
            await asyncio.sleep(15)
            continue
        current = (_tg.TELEGRAM_TOKEN, str(_tg.TELEGRAM_CHAT_ID).strip())
        if current != configuration:
            configuration, offset, next_registration = current, None, 0.0
            menu_url, next_menu_registration = None, 0.0
            next_menu_refresh = 0.0
            _poll_status.update(commands_registered=False, last_poll_at_ms=None, last_update_at_ms=None)
        try:
            if not _poll_status["commands_registered"] and time.monotonic() >= next_registration:
                registration = await _api("setMyCommands", {"commands": COMMANDS}, timeout=10)
                _poll_status["commands_registered"] = bool(registration.get("ok"))
                next_registration = time.monotonic() + 60
            from app.services.notifications import telegram_kite
            current_menu_url = telegram_kite._get_tma_url()
            if (current_menu_url != menu_url or time.monotonic() >= next_menu_refresh) and time.monotonic() >= next_menu_registration:
                button = ({"type":"web_app", "text":"Sterling desk", "web_app":{"url":current_menu_url}}
                          if current_menu_url else {"type":"commands"})
                changed = await _api("setChatMenuButton", {"menu_button":button}, timeout=10)
                if changed.get("ok"):
                    menu_url = current_menu_url
                    next_menu_refresh = time.monotonic() + 300
                    _poll_status["mini_app_menu_registered"] = bool(current_menu_url)
                next_menu_registration = time.monotonic() + (0 if changed.get("ok") else 60)
            if offset is None:
                offset, backlog = await _skip_backlog()
                if offset is None:
                    _poll_status.update(state="error", error_code=failure_code(backlog))
                    await asyncio.sleep(_backoff_delay(backlog))
                    continue
            response = await _api(
                "getUpdates",
                {"offset": offset, "timeout": 30, "allowed_updates": ["message", "callback_query"]},
                timeout=40,
            )
            if not response.get("ok"):
                _poll_status.update(state="error", error_code=failure_code(response))
                await asyncio.sleep(_backoff_delay(response))
                continue
            _poll_status.update(state="polling", error_code="", last_poll_at_ms=int(time.time() * 1000))
            for update in response.get("result", []):
                offset = max(offset, update.get("update_id", 0) + 1)
                _poll_status["last_update_at_ms"] = int(time.time() * 1000)
                try:
                    await _handle_update(update)
                except Exception as exc:
                    log.warning("Telegram update handler error: %s", type(exc).__name__)
        except Exception as exc:
            _poll_status.update(state="error", error_code="HANDLER_ERROR")
            log.debug("Telegram getUpdates error: %s", type(exc).__name__)
            await asyncio.sleep(5)

