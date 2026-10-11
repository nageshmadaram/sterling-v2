"""Read-only broker login check; no password, secret, session or order is sent."""
from __future__ import annotations
import httpx
from . import session

INVALID_KEY_MESSAGE = (
    "Zerodha rejected the saved Kite API key. Update the API key and matching "
    "secret in Sterling → Connect using your Kite developer console, then retry. "
    "Do not enter passwords or tokens in Telegram."
)

async def check_login_key(api_key: str) -> None:
    if not api_key.strip():
        raise ValueError(INVALID_KEY_MESSAGE)
    try:
        async with httpx.AsyncClient(timeout=5.0, follow_redirects=False, trust_env=False) as client:
            response = await client.get(session.login_url(api_key))
    except httpx.HTTPError:
        # Transient network failure is not evidence of bad credentials. The user
        # can still try the broker page directly.
        return
    try:
        body = response.json()
    except ValueError:
        return
    if isinstance(body, dict) and body.get("status") == "error" and "api_key" in str(body.get("message", "")).lower():
        raise ValueError(INVALID_KEY_MESSAGE)
