"""Complete a signed Kite handoff, including URLs copied from a phone browser.

Kite uses the redirect registered in its developer console. A phone cannot reach
an operator's localhost, but its address bar still contains the signed handoff.
We parse that address locally; we never fetch a caller-supplied URL.
"""
from __future__ import annotations

from datetime import datetime
import uuid
from urllib.parse import parse_qs, urlsplit

from app.core.logging import get_logger
from . import accounts, session

log = get_logger(__name__)


class LoginIdentityError(ValueError):
    """The broker identity could not be persisted for this account."""


def parse_callback_url(
    value: str, *, expected_user_id: str, expected_account_id: str,
) -> str:
    """Return the request token only for this user's signed account handoff."""
    return parse_callback_handoff(value, expected_user_id=expected_user_id,
                                  expected_account_id=expected_account_id)[0]


def parse_callback_handoff(
    value: str, *, expected_user_id: str, expected_account_id: str,
) -> tuple[str, str]:
    """Validate a copied callback and return its request token and signed state."""
    try:
        if len(value) > 8192:
            raise ValueError
        parsed = urlsplit(value.strip())
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username or parsed.password or parsed.fragment):
            raise ValueError
        query = parse_qs(parsed.query, keep_blank_values=True, max_num_fields=32)
        if any(len(query.get(key, [])) != 1 for key in ("request_token", "state", "status")):
            raise ValueError
        if len(query.get("action", [])) > 1 or query.get("action", ["login"])[0] != "login":
            raise ValueError
        if query["status"][0] != "success":
            raise ValueError
        token = query["request_token"][0]
        if not token or len(token) > 1024 or any(c.isspace() or ord(c) < 32 for c in token):
            raise ValueError
    except (ValueError, UnicodeError) as exc:
        raise ValueError("Paste the complete successful Kite redirect URL from your browser's address bar.") from exc

    if session.parse_state(query["state"][0]) != (expected_user_id, expected_account_id):
        raise ValueError("This login link expired or belongs to another account. Open Kite Login again for this account.")
    return token, query["state"][0]


async def complete_login(user_id: str, acct, request_token: str, *, login_state: str = "", expected_kite_user_id: str = "") -> dict:
    """Exchange on the server and publish the same session for every login surface."""
    if not acct.api_key or not acct.api_secret:
        raise ValueError("API key and secret are required before login.")
    if login_state and session.parse_state(login_state) != (user_id, acct.id):
        raise ValueError("Login link expired or belongs to another account.")
    client = accounts.build_client(acct)
    try:
        data = await client.generate_session(request_token)
    finally:
        await client.close()
    if expected_kite_user_id and str(data.get("user_id", "")).upper() != expected_kite_user_id.upper():
        raise LoginIdentityError("Kite returned a different user. The broker session was not saved.")
    try:
        saved = accounts.save_session(
            user_id, acct.id,
            access_token=data.get("access_token", ""),
            refresh_token=data.get("refresh_token", ""),
            public_token=data.get("public_token", ""),
            kite_user_id=data.get("user_id", ""),
            user_name=data.get("user_name", ""),
            login_confirmation_id=session.login_confirmation_id(login_state or uuid.uuid4().hex),
        )
    except ValueError as exc:
        raise LoginIdentityError(str(exc)) from exc
    if saved is None:
        raise LoginIdentityError("The account no longer exists. Add an account in Connect and retry.")
    await accounts.release_client(acct.id)
    from app.services.notifications import telegram_lifecycle
    detail = ["Your Kite broker login was successful and the session is saved in Sterling.",
              f"Account: {saved.label or 'Kite account'}"]
    if saved.user_name:
        detail.append(f"Name: {saved.user_name}")
    if saved.kite_user_id:
        detail.append(f"Kite ID: {saved.kite_user_id}")
    if saved.token_expires_at:
        expires = datetime.fromtimestamp(saved.token_expires_at / 1000, tz=session.IST)
        detail.append(f"Valid until: {expires.strftime('%H:%M IST · %a %d %b')}")
    detail.append("You can return to Telegram or Sterling. Your trading settings were preserved.")
    event_id = telegram_lifecycle.enqueue_best_effort(
        user_id, dedupe_key=f"kite_login:{acct.id}:{saved.last_login_at_ms}",
        strategy="all", event_type="BROKER CONNECTED", title="Kite login successful",
        detail="\n".join(detail),
    )
    if isinstance(event_id, str):
        try:
            from app.services.notifications.telegram_kite import schedule_broker_login_confirmation
            schedule_broker_login_confirmation(user_id, event_id)
        except Exception as exc:  # notification problems must not turn a saved login into a failure
            log.warning("Kite login confirmation will use queued retry: %s", type(exc).__name__)
    return {**data, "_telegram_confirmation_event_id": event_id if isinstance(event_id, str) else None}
