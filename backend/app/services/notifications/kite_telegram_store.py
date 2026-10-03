"""
Per-user Kite-specific Telegram alert targets — store + transport.

Kite supports dedicated Telegram bot and chat configurations. A
user can register multiple alert *targets*, each a ``{bot_token, chat_id}`` pair.

Persistence is JSON in the app DB config store (``db.set_config``/``get_config``)
under a per-user key (``kite_tg_targets:{user_id}``). Bot tokens are encrypted at
rest with the SAME util the Kite account credentials use
(:mod:`app.core.security`) — never invent encryption, never store a raw token.

Responses expose only ``bot_token_hint`` (last 6 chars) + ``bot_token_set`` — the
raw token never leaves this layer except to :func:`send_via`.
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import List, Optional

import httpx

from app.core.logging import get_logger
from app.core.security import decrypt, encrypt
from app.services import db

log = get_logger(__name__)

_KEY_PREFIX = "kite_tg_targets:"
_TG_API = "https://api.telegram.org/bot{token}/sendMessage"


def _now_ms() -> int:
    return int(time.time() * 1000)


def _key(user_id: str) -> str:
    return f"{_KEY_PREFIX}{user_id}"


@dataclass
class Target:
    """One Kite alert destination. ``bot_token_enc`` is encrypted at rest."""
    id: str
    label: str = ""
    chat_id: str = ""
    bot_token_enc: str = ""
    enabled: bool = True
    reachable: bool = False
    created_at_ms: int = field(default_factory=_now_ms)
    updated_at_ms: int = field(default_factory=_now_ms)

    # ── derived ──
    @property
    def bot_token(self) -> str:
        return decrypt(self.bot_token_enc)

    @property
    def bot_token_set(self) -> bool:
        return bool(self.bot_token_enc)

    def token_hint(self) -> str:
        tok = self.bot_token
        if not tok:
            return ""
        return tok[-6:]


# ─── persistence (JSON in system_config) ──────────────────────────────────────
def _load_raw(user_id: str) -> List[Target]:
    raw = db.get_config(_key(user_id), "")
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        log.warning("kite_tg_targets corrupt JSON for user %s — resetting", user_id)
        return []
    out: List[Target] = []
    for d in data if isinstance(data, list) else []:
        if not isinstance(d, dict) or not d.get("id"):
            continue
        out.append(Target(
            id=str(d.get("id")),
            label=str(d.get("label", "")),
            chat_id=str(d.get("chat_id", "")),
            bot_token_enc=str(d.get("bot_token_enc", "")),
            enabled=bool(d.get("enabled", True)),
            reachable=bool(d.get("reachable", False)),
            created_at_ms=int(d.get("created_at_ms") or _now_ms()),
            updated_at_ms=int(d.get("updated_at_ms") or _now_ms()),
        ))
    return out


def _save_raw(user_id: str, targets: List[Target]) -> None:
    db.set_config(_key(user_id), json.dumps([asdict(t) for t in targets]))


# ─── CRUD (user-scoped) ───────────────────────────────────────────────────────
def list_targets(user_id: str) -> List[Target]:
    return _load_raw(user_id)


def get(user_id: str, target_id: str) -> Optional[Target]:
    return next((t for t in _load_raw(user_id) if t.id == target_id), None)


def enabled_targets(user_id: str) -> List[Target]:
    return [t for t in _load_raw(user_id) if t.enabled and t.bot_token_enc and t.chat_id]


def add(user_id: str, *, label: str, bot_token: str, chat_id: str, enabled: bool = True) -> Target:
    targets = _load_raw(user_id)
    t = Target(
        id=uuid.uuid4().hex,
        label=(label or "").strip(),
        chat_id=(chat_id or "").strip(),
        bot_token_enc=encrypt((bot_token or "").strip()) if (bot_token or "").strip() else "",
        enabled=enabled,
    )
    targets.append(t)
    _save_raw(user_id, targets)
    return t


def update(user_id: str, target_id: str, *, label: Optional[str] = None,
           bot_token: Optional[str] = None, chat_id: Optional[str] = None,
           enabled: Optional[bool] = None) -> Optional[Target]:
    targets = _load_raw(user_id)
    t = next((x for x in targets if x.id == target_id), None)
    if t is None:
        return None
    destination_changed = False
    if label is not None:
        t.label = label.strip()
    if chat_id is not None:
        normalized_chat = chat_id.strip()
        if normalized_chat != t.chat_id:
            destination_changed = True
        t.chat_id = normalized_chat
    # Omitted OR empty bot_token keeps the existing stored token (don't wipe it).
    if bot_token and bot_token.strip():
        normalized_token = bot_token.strip()
        try:
            old_token = t.bot_token
        except Exception:  # noqa: BLE001
            old_token = ""
        if normalized_token != old_token:
            destination_changed = True
        t.bot_token_enc = encrypt(normalized_token)
    if enabled is not None:
        t.enabled = enabled
    if destination_changed:
        # A green reachability badge belongs to a specific token/chat pair.
        # Editing either endpoint invalidates that proof until a new delivery/test.
        t.reachable = False
    t.updated_at_ms = _now_ms()
    _save_raw(user_id, targets)
    return t


def set_reachable(user_id: str, target_id: str, reachable: bool) -> Optional[Target]:
    targets = _load_raw(user_id)
    t = next((x for x in targets if x.id == target_id), None)
    if t is None:
        return None
    t.reachable = reachable
    t.updated_at_ms = _now_ms()
    _save_raw(user_id, targets)
    return t


def delete(user_id: str, target_id: str) -> bool:
    targets = _load_raw(user_id)
    kept = [t for t in targets if t.id != target_id]
    if len(kept) == len(targets):
        return False
    _save_raw(user_id, kept)
    return True


# ─── transport ────────────────────────────────────────────────────────────────
async def send_via(token: str, chat_id: str, html: str) -> bool:
    """Deliver one HTML message with bounded retries.

    429, 5xx and transient network failures are retryable. Authentication/chat
    errors are not. A caller only marks a signal delivered after this returns
    True, so a temporary Telegram outage cannot permanently consume an alert.
    """
    if not (token and chat_id):
        return False
    async with httpx.AsyncClient(
        timeout=10.0,
        follow_redirects=False,
        trust_env=False,
    ) as client:
        for attempt in range(3):
            try:
                resp = await client.post(
                    _TG_API.format(token=token),
                    json={
                        "chat_id": chat_id,
                        "text": html,
                        "parse_mode": "HTML",
                        "disable_web_page_preview": True,
                    },
                )
            except Exception as exc:  # noqa: BLE001
                if attempt >= 2:
                    log.warning("kite telegram send_via error: %s", exc)
                    return False
                await asyncio.sleep(0.5 * (2 ** attempt))
                continue

            try:
                body = resp.json()
            except Exception:  # noqa: BLE001
                body = {}

            if resp.status_code == 200 and body.get("ok"):
                return True

            retryable = resp.status_code == 429 or 500 <= resp.status_code < 600
            if not retryable or attempt >= 2:
                log.warning(
                    "kite telegram send_via failed (%s): %s",
                    resp.status_code,
                    resp.text[:200],
                )
                return False

            retry_after = 0.5 * (2 ** attempt)
            if resp.status_code == 429:
                try:
                    retry_after = float(
                        (body.get("parameters") or {}).get("retry_after") or retry_after
                    )
                except (TypeError, ValueError):
                    pass
            await asyncio.sleep(max(0.1, min(retry_after, 5.0)))
    return False
