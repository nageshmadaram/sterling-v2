"""
Kite-specific Telegram alert targets — per-user CRUD + test.

Kite supports dedicated Telegram bot and chat configurations. Each
user can register multiple alert *targets* (``{bot_token, chat_id}``); enabled
targets receive the Kite engine's outbound signal alerts (see
``services/notifications/telegram_kite.py::push_kite_alerts``).

Every route is scoped to the calling user (``get_current_user``), mirroring
``kite.py``. Bot tokens are encrypted at rest (reusing the Kite-account credential
encryption) and NEVER returned raw — responses expose only a 6-char hint.

This part is OUTBOUND alerts + test + management only; the interactive ``/kite``
control bot remains isolated from execution alert bots.
"""
from __future__ import annotations

import asyncio
import html
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, StrictBool

from app.core.auth import UserContext, get_current_user
from app.core.logging import get_logger
from app.services.notifications import kite_telegram_store as store

log = get_logger(__name__)
router = APIRouter(prefix="/kite/telegram", tags=["kite"])


# ─── Schemas ──────────────────────────────────────────────────────────────────
class TargetIn(BaseModel):
    label: str
    bot_token: str
    chat_id: str
    enabled: bool = True


class TargetPatch(BaseModel):
    label: Optional[str] = None
    bot_token: Optional[str] = None
    chat_id: Optional[str] = None
    enabled: Optional[bool] = None


class TargetOut(BaseModel):
    id: str
    label: str
    chat_id: str
    bot_token_hint: str
    bot_token_set: bool
    enabled: bool
    reachable: bool
    credential_error: str = ""


class TargetListResponse(BaseModel):
    targets: List[TargetOut]


class OkResponse(BaseModel):
    ok: bool = True


class AlertPreferences(BaseModel):
    signal_alerts_enabled: StrictBool


@router.put("/preferences")
async def update_preferences(body: AlertPreferences, user: UserContext = Depends(get_current_user)) -> AlertPreferences:
    from app.services.notifications import telegram_kite
    from app.services import db

    db.set_config_strict(f"kite_tg_alerts_enabled:{user.user_id}", "1" if body.signal_alerts_enabled else "0")
    return body


@router.get("/status")
async def telegram_status(user: UserContext = Depends(get_current_user)) -> dict:
    from app.services.notifications import telegram as transport, telegram_bot, telegram_kite, telegram_lifecycle

    targets = store.list_targets(user.user_id)
    owner = user.user_id == telegram_kite.UID and user.role == "admin"
    control = telegram_bot.poll_status() if owner else {"state": "not_available_this_user"}
    if owner:
        control["configured"] = bool(transport.TELEGRAM_TOKEN and transport.TELEGRAM_CHAT_ID)
        control["private_chat"] = str(transport.TELEGRAM_CHAT_ID or "").isdigit()
    summary = await asyncio.to_thread(telegram_lifecycle.pending_summary, user.user_id)
    from app.services import public_access
    return {
        "public_access": public_access.status(),
        "control": control,
        "alert_targets": len(targets),
        "enabled_alert_targets": sum(t.enabled for t in targets),
        "unreadable_alert_targets": sum(bool(t.credential_error) for t in targets),
        "queued_lifecycle_alerts": int(summary.get("pending") or 0),
        "signal_alerts_enabled": telegram_kite.alerts_enabled(user.user_id),
    }


@router.post("/control/check")
async def check_control(user: UserContext = Depends(get_current_user)) -> dict:
    """Read-only Telegram probes; never poll, send messages or change trading mode."""
    from app.services.notifications import telegram as transport, telegram_bot, telegram_kite

    if user.user_id != telegram_kite.UID or user.role != "admin":
        raise HTTPException(403, "Control bot diagnostics belong to its configured operator.")
    if not transport.TELEGRAM_TOKEN or not transport.TELEGRAM_CHAT_ID:
        return {"ok": False, "error_code": "MISSING_CONFIGURATION", "message": "Configure the control bot token and private chat on the server."}
    for method, payload in (
        ("getMe", {}), ("getChat", {"chat_id": transport.TELEGRAM_CHAT_ID}),
        ("getWebhookInfo", {}), ("getMyCommands", {}),
    ):
        response = await telegram_bot._api(method, payload, timeout=10)
        if not response.get("ok"):
            code = telegram_bot.failure_code(response)
            messages = {"INVALID_TOKEN": "Bot token is invalid or revoked.", "ACCESS_DENIED": "The bot cannot access its configured chat.",
                        "NETWORK_ERROR": "Telegram could not be reached.", "POLL_CONFLICT": "Another bot process or webhook owns Telegram updates."}
            return {"ok": False, "error_code": code, "message": messages.get(code, "Telegram rejected the configuration check.")}
        result = response.get("result") or {}
        if method == "getChat" and result.get("type") != "private":
            return {"ok": False, "error_code": "PRIVATE_CHAT_REQUIRED", "message": "Trading controls require the operator's private chat. Groups can receive alerts."}
        if method == "getWebhookInfo" and result.get("url"):
            return {"ok": False, "error_code": "WEBHOOK_ACTIVE", "message": "A webhook is active for this bot. Use a dedicated bot for Sterling polling."}
        if method == "getMyCommands":
            installed = {row.get("command") for row in result}
            expected = {row["command"] for row in telegram_bot.COMMANDS}
            if installed != expected:
                return {"ok": False, "error_code": "STALE_COMMANDS", "message": "The Telegram command menu is out of date. The Sterling worker will refresh it; use /menu."}
    polling = telegram_bot.poll_status()
    # getMe/getChat/getMyCommands prove credentials and settings, not that a
    # Telegram worker is actually receiving updates. Report a 409 poll
    # conflict, stalled worker or missing worker as an actionable failure.
    if polling.get("state") != "polling" or not polling.get("commands_registered"):
        state = polling.get("state")
        code = polling.get("error_code") or (
            "POLL_STALLED" if state == "stalled" else
            "WORKER_STARTING" if state == "starting" else
            "MISSING_CONFIGURATION" if state == "waiting_configuration" else
            "POLLING_UNAVAILABLE"
        )
        messages = {
            "POLL_CONFLICT": "Another process is polling this bot token. Stop the duplicate poller; do not reset the bot token.",
            "POLL_STALLED": "Telegram polling has stalled. Check the backend and Telegram worker logs.",
            "RATE_LIMITED": "Telegram rate-limited this bot; polling will retry.",
            "NETWORK_ERROR": "The backend cannot reach Telegram; polling will retry.",
            "WORKER_STARTING": "Telegram worker is still starting. Check again after it begins polling.",
            "MISSING_CONFIGURATION": "Configure the control bot token and private chat on the backend.",
            "POLLING_UNAVAILABLE": "Telegram worker is not receiving updates; check backend logs and webhook/poll conflicts.",
        }
        return {"ok": False, "error_code": code,
                "message": messages.get(code, "Telegram polling is not healthy."),
                "polling": polling}
    return {"ok": True, "error_code": "",
            "message": "Bot credentials and live polling verified. Use /menu in Telegram.",
            "polling": polling}


def _to_out(t: "store.Target") -> TargetOut:
    return TargetOut(
        id=t.id,
        label=t.label,
        chat_id=t.chat_id,
        bot_token_hint=t.token_hint(),
        bot_token_set=t.bot_token_set,
        enabled=t.enabled,
        reachable=t.reachable and not getattr(t, "credential_error", ""),
        credential_error=getattr(t, "credential_error", ""),
    )


# ─── Routes ───────────────────────────────────────────────────────────────────
@router.get("")
async def list_targets(user: UserContext = Depends(get_current_user)) -> TargetListResponse:
    return TargetListResponse(targets=[_to_out(t) for t in store.list_targets(user.user_id)])


@router.post("")
async def create_target(body: TargetIn, user: UserContext = Depends(get_current_user)) -> TargetOut:
    t = store.add(
        user.user_id,
        label=body.label,
        bot_token=body.bot_token,
        chat_id=body.chat_id,
        enabled=body.enabled,
    )
    return _to_out(t)


@router.put("/{target_id}")
async def update_target(target_id: str, body: TargetPatch,
                        user: UserContext = Depends(get_current_user)) -> TargetOut:
    t = store.update(
        user.user_id, target_id,
        label=body.label, bot_token=body.bot_token,
        chat_id=body.chat_id, enabled=body.enabled,
    )
    if t is None:
        raise HTTPException(404, "Kite telegram target not found")
    return _to_out(t)


@router.delete("/{target_id}")
async def delete_target(target_id: str, user: UserContext = Depends(get_current_user)) -> OkResponse:
    if not store.delete(user.user_id, target_id):
        raise HTTPException(404, "Kite telegram target not found")
    return OkResponse(ok=True)


@router.post("/{target_id}/test")
async def test_target(target_id: str, user: UserContext = Depends(get_current_user)) -> TargetOut:
    t = store.get(user.user_id, target_id)
    if t is None:
        raise HTTPException(404, "Kite telegram target not found")
    if not (t.bot_token_set and t.chat_id):
        raise HTTPException(400, "Target is missing a bot token or chat id.")
    if getattr(t, "credential_error", ""):
        raise HTTPException(409, t.credential_error)
    label = t.label or "Kite alerts"
    ok = await store.send_via(
        t.bot_token, t.chat_id,
        f"✅ Kite alerts connected — <b>{html.escape(label)}</b>",
    )
    updated = store.set_reachable(user.user_id, target_id, ok) or t
    return _to_out(updated)
