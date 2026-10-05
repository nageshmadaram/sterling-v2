"""Telegram transport and command dispatcher for the Zerodha/Kite desk."""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

import httpx

from app.services.notifications import telegram as _tg

log = logging.getLogger(__name__)
_API = "https://api.telegram.org"


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
            return
        from app.services.notifications import telegram_kite
        await telegram_kite.handle_kite_command(chat_id)
        return

    if "callback_query" in update:
        callback = update["callback_query"]
        chat_id = str(callback.get("message", {}).get("chat", {}).get("id", ""))
        sender_id = str(callback.get("from", {}).get("id", ""))
        callback_id = callback.get("id", "")
        if not _authorized_controller(chat_id, sender_id):
            await _answer_cb(callback_id)
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
    log.info("Kite Telegram poll loop started")
    while True:
        if not _tg.TELEGRAM_TOKEN or not str(_tg.TELEGRAM_CHAT_ID or "").strip():
            await asyncio.sleep(15)
            continue
        try:
            if offset is None:
                offset, backlog = await _skip_backlog()
                if offset is None:
                    await asyncio.sleep(_backoff_delay(backlog))
                    continue
            response = await _api(
                "getUpdates",
                {"offset": offset, "timeout": 30},
                timeout=40,
            )
            if not response.get("ok"):
                await asyncio.sleep(_backoff_delay(response))
                continue
            for update in response.get("result", []):
                offset = max(offset, update.get("update_id", 0) + 1)
                try:
                    await _handle_update(update)
                except Exception as exc:
                    log.warning("Telegram update handler error: %s", exc)
        except Exception as exc:
            log.debug("Telegram getUpdates error: %s", exc)
            await asyncio.sleep(5)

