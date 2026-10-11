"""Validate Telegram Mini App signatures before accepting its private controller."""
from __future__ import annotations
import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl
from fastapi import HTTPException, Request
from app.core.auth import DEFAULT_USER_ID, UserContext, get_current_user


def verify_init_data(raw: str, token: str, controller: str, *, now: float | None = None) -> None:
    try:
        if not raw or len(raw) > 8192 or not token or not controller.isdigit():
            raise ValueError()
        pairs = parse_qsl(raw, keep_blank_values=True, strict_parsing=True)
        fields = dict(pairs)
        if len(fields) != len(pairs): raise ValueError()
        supplied = fields.pop('hash')
        check = '\n'.join(f'{key}={value}' for key, value in sorted(fields.items()))
        secret = hmac.new(b'WebAppData', token.encode(), hashlib.sha256).digest()
        expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, supplied): raise ValueError()
        age = (time.time() if now is None else now) - int(fields['auth_date'])
        if not -30 <= age <= 3600: raise ValueError()
        user = json.loads(fields['user'])
        ident = user.get('id')
        if type(ident) is not int or ident <= 0 or str(ident) != controller:
            raise ValueError()
    except (ValueError, KeyError, TypeError, AttributeError):
        raise HTTPException(401, 'Mini App authorization expired or invalid. Reopen it from your Sterling bot.') from None


async def tma_user(request: Request) -> UserContext:
    if request.headers.get('X-Sterling-View', '').lower() == 'simulation':
        raise HTTPException(403, 'Mini App broker controls are available in Live view only.')
    raw = request.headers.get('X-Telegram-Init-Data', '')
    if raw:
        from app.services.notifications import telegram
        verify_init_data(raw, telegram.TELEGRAM_TOKEN, str(telegram.TELEGRAM_CHAT_ID or ''))
        from app.services.auth import user_store
        user_store.bootstrap()
        user = user_store.get_by_id(DEFAULT_USER_ID)
        if user is None or not user.is_active or user.role != 'admin':
            raise HTTPException(403, 'Sterling operator account is unavailable.')
        return UserContext(user_id=user.id, username=user.username, role=user.role)
    if not request.headers.get('Authorization', '').startswith('Bearer '):
        raise HTTPException(401, 'Open this Mini App from Telegram or sign into Sterling.')
    return await get_current_user(request)
