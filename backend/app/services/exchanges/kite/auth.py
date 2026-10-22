"""
Kite session lifecycle — the one place that answers "do we have a usable session?".

Zerodha's login is deliberately non-renewable for most API subscriptions: a
``request_token`` from the browser redirect becomes an ``access_token`` that dies
at 06:00 IST the next morning, and only accounts whose app is provisioned for it
receive a ``refresh_token``. There is no headless way to mint the first token of
the day — the 2FA/TOTP step is Zerodha's, by design.

What *is* avoidable is everything after that first login. This module removes the
three things that made the app feel like it needed a fresh login constantly:

  * **Blind revalidation.** ``/status`` used to call Kite's ``/user/profile`` on
    every 30-second poll. Since the validity window is knowable locally (see
    :func:`session.token_expiry_ms`), we ask Kite once per window and cache the
    answer for :data:`VALIDATION_TTL_MS`.
  * **Dead ends on expiry.** A stale token used to surface as a bare 401. When a
    ``refresh_token`` exists we now renew silently and retry, so the caller never
    sees the gap.
  * **Losing the token on restart.** ``seed_from_env`` adopts credentials (and
    optionally a token) from the environment, so a fresh database or a new machine
    starts already connected.

Everything here is best-effort and never raises for "not connected" — callers get
a :class:`SessionHealth` describing reality and decide what to do about it.
"""
from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass
from typing import Optional

from app.core.logging import get_logger

from . import accounts as kite_accounts
from . import session as kite_session
from .errors import KiteTokenError

log = get_logger(__name__)

# How long a successful Kite validation is trusted before we ask again. Bounded so
# a token revoked out-of-band (logged out on the Kite app) is noticed promptly,
# but long enough that a 30s status poll costs nothing 90% of the time.
VALIDATION_TTL_MS = 5 * 60 * 1000

# Renew this far ahead of the 06:00 IST reset. Only meaningful for accounts that
# actually have a refresh_token; the rest need the daily interactive login.
RENEW_MARGIN_MS = 15 * 60 * 1000


@dataclass
class SessionHealth:
    """What we know about an account's session, and how we came to know it."""
    connected: bool
    message: str
    validated: bool = False          # a live Kite call confirmed the token
    auto_renewed: bool = False       # we silently minted a new token
    kite_user_id: str = ""
    user_name: str = ""
    expires_at_ms: Optional[int] = None
    transient: bool = False
    """The check failed, not the session.

    `connected=False` has meant two completely different things: "Kite rejected
    this token, log in again" and "we could not reach Kite to ask". The stored
    token is deliberately kept in the second case — the comment on that branch
    has said so for a long time — but the answer sent to the UI was identical, so
    a dropped request surfaced as "Kite session expired" over a session that was
    entirely fine. That is the modal appearing with a valid token, and the reason
    a request_token then got pasted for no reason.

    Only the network branch sets this. A token Kite has actually refused is not
    transient, and treating it as one would leave the operator waiting for a
    recovery that is never coming.
    """


# ─── Silent renewal ───────────────────────────────────────────────────────────
async def renew(user_id: str, acct) -> Optional[dict]:
    """Exchange the stored ``refresh_token`` for a fresh ``access_token``.

    Returns Kite's session payload on success, ``None`` when renewal is impossible
    or refused — the caller falls back to the interactive login. Never raises:
    a failed renewal is an expected outcome for accounts Zerodha never issued a
    refresh_token to.
    """
    refresh_token = acct.refresh_token
    if not refresh_token or not acct.api_key or not acct.api_secret:
        return None
    client = kite_accounts.build_client(acct)
    try:
        data = await client.renew_access_token(refresh_token)
    except Exception as exc:  # noqa: BLE001 — renewal is best-effort by contract
        log.info("Kite silent renew failed for %s: %s", acct.id, exc)
        return None
    finally:
        await client.close()
    if not (data or {}).get("access_token"):
        return None
    try:
        kite_accounts.save_session(
            user_id, acct.id,
            access_token=data.get("access_token", ""),
            refresh_token=data.get("refresh_token", ""),   # Kite may rotate it
            public_token=data.get("public_token", ""),
            kite_user_id=data.get("user_id", ""),
            user_name=data.get("user_name", ""),
        )
    except Exception as exc:  # noqa: BLE001
        # A renewal payload that cannot be bound/persisted is not a usable
        # session. Quarantine the old authority as well: the refresh token that
        # produced an identity conflict must never be retried indefinitely.
        log.error("Kite renewed session could not be trusted for %s: %s", acct.id, exc)
        try:
            kite_accounts.clear_session(user_id, acct.id)
        except Exception as clear_exc:  # noqa: BLE001
            log.error("Kite session quarantine persistence failed for %s: %s",
                      acct.id, clear_exc)
        await kite_accounts.release_client(acct.id)
        return None

    # The cached client holds the *old* token; drop it so the next acquire rebuilds.
    await kite_accounts.release_client(acct.id)
    log.info("Kite session silently renewed for account %s", acct.id)
    return data


# ─── Health ───────────────────────────────────────────────────────────────────
def _connected_message(acct) -> str:
    """Operator-facing status for a healthy session. Every account Sterling creates
    is live; a stored row an older build left in a non-live mode says so plainly
    until the boot conversion (or the operator) deals with it."""
    if getattr(acct, "is_paper", False):
        return "Connected - account is not in live mode; restart to convert it"
    return "Connected"


async def ensure_session(user_id: str, acct, *, force_validate: bool = False) -> SessionHealth:
    """Return the account's session health, repairing it silently where possible.

    Ladder, cheapest first:
      1. No credentials at all → nothing to do.
      2. No stored token, or the window has closed → try a silent renew.
      3. Token inside its window *and* Kite accepted it recently → trust it, no I/O.
      4. Otherwise ask Kite; on token rejection try one renew before giving up.

    The virtual Simulation account has no Kite session to prove: it is always connected.
    """
    if kite_accounts.is_sim_account(acct):
        return SessionHealth(True, "Simulation - virtual account", validated=True,
                             kite_user_id=acct.kite_user_id or "", user_name=acct.user_name or "")
    if not acct.api_key:
        return SessionHealth(False, "Set the Kite API key on this account first.")

    expires_at = acct.token_expires_at

    # (2) Nothing usable stored — a renew is the only non-interactive way back.
    if not acct.access_token_enc or kite_session.is_expired(expires_at):
        stale = bool(acct.access_token_enc)
        data = await renew(user_id, acct)
        if data:
            return SessionHealth(
                True, "Session renewed automatically", validated=True, auto_renewed=True,
                kite_user_id=data.get("user_id", "") or acct.kite_user_id or "",
                user_name=data.get("user_name", "") or acct.user_name or "",
                expires_at_ms=acct.token_expires_at,
            )
        if stale:
            # Drop the corpse so `connected` reads false everywhere and the UI
            # offers "Log in" rather than "Log out".
            kite_accounts.clear_session(user_id, acct.id)
            await kite_accounts.release_client(acct.id)
            return SessionHealth(
                False, "Session expired at 06:00 IST — reconnect via Kite login.",
            )
        return SessionHealth(False, "Not logged in — complete the Kite login flow")

    # (3) Inside the window and proven recently: answer without touching the network.
    age = kite_accounts.validated_age_ms(acct.id)
    if not force_validate and age is not None and age < VALIDATION_TTL_MS:
        return SessionHealth(
            True, _connected_message(acct),
            validated=False, kite_user_id=acct.kite_user_id or "",
            user_name=acct.user_name or "", expires_at_ms=expires_at,
        )

    # (4) Prove it against Kite.
    client = await kite_accounts.acquire_client(acct)
    try:
        profile = await client.get_profile()
    except KiteTokenError:
        data = await renew(user_id, acct)
        if data:
            return SessionHealth(
                True, "Session renewed automatically", validated=True, auto_renewed=True,
                kite_user_id=data.get("user_id", "") or acct.kite_user_id or "",
                user_name=data.get("user_name", "") or acct.user_name or "",
                expires_at_ms=acct.token_expires_at,
            )
        kite_accounts.clear_session(user_id, acct.id)
        await kite_accounts.release_client(acct.id)
        return SessionHealth(
            False, "Session rejected by Kite — reconnect via Kite login "
                   "(tokens reset ~6 AM IST daily).",
        )
    except Exception as exc:  # noqa: BLE001 — network/venue trouble, not auth
        # Do NOT clear the token: an unreachable Kite is not an invalid session.
        # And say which this was, so the UI stops calling it an expiry.
        return SessionHealth(
            False, f"Could not reach Kite to check the session: {exc}",
            expires_at_ms=expires_at, transient=True,
        )

    # Backfill/verify identity BEFORE declaring the token healthy. A valid token
    # for a different Kite user is not a valid session for this Sterling account.
    try:
        kite_accounts.set_identity(
            user_id, acct.id,
            user_name=profile.get("user_name", "") or "",
            kite_user_id=profile.get("user_id", "") or "",
        )
    except Exception as exc:  # noqa: BLE001
        log.error("Kite profile identity rejected for %s: %s", acct.id, exc)
        try:
            kite_accounts.clear_session(user_id, acct.id)
        except Exception as clear_exc:  # noqa: BLE001
            log.error("Kite session quarantine persistence failed for %s: %s",
                      acct.id, clear_exc)
        await kite_accounts.release_client(acct.id)
        return SessionHealth(
            False,
            f"Kite session identity could not be verified: {exc}",
            validated=False,
        )

    kite_accounts.mark_validated(acct.id)
    return SessionHealth(
        True, _connected_message(acct),
        validated=True,
        kite_user_id=profile.get("user_id", "") or acct.kite_user_id or "",
        user_name=profile.get("user_name", "") or acct.user_name or "",
        expires_at_ms=expires_at,
    )


# ─── Environment seeding (dev / redeploy) ─────────────────────────────────────
def _env_flag(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


_PLACEHOLDER_KEY = re.compile(r"paper|placeholder|example|your|changeme|dummy|xxx", re.IGNORECASE)


def seed_from_env() -> Optional[str]:
    """Adopt Kite credentials — and optionally a live token — from the environment.

    Point of this: a fresh clone, a wiped database, or a new dev machine should not
    demand that credentials be retyped into the UI before anything works. Set
    ``KITE_API_KEY`` / ``KITE_API_SECRET`` once in ``.env`` and the account exists
    at boot. Add ``KITE_ACCESS_TOKEN`` (the value from one browser login) and the
    process comes up already connected.

    Idempotent, and deliberately non-destructive: an account matching the env
    ``api_key`` is updated rather than duplicated, and an env token is only adopted
    when the stored one is absent or already past its window — so a token obtained
    through the browser today is never clobbered by a stale value in ``.env``.

    Returns the account id it seeded, or ``None`` when no credentials were set.
    """
    # ZERODHA_* aliases accepted for parity with add_kite.py.
    api_key = (os.environ.get("KITE_API_KEY")
               or os.environ.get("ZERODHA_API_KEY") or "").strip()
    api_secret = (os.environ.get("KITE_API_SECRET")
                  or os.environ.get("ZERODHA_API_SECRET") or "").strip()
    if not api_key or not api_secret:
        return None
    if _PLACEHOLDER_KEY.search(api_key):
        # A template value such as "paper_dev_key" would otherwise become a
        # broken account (shown as "Kite (Paper Mode)") whenever the store is empty.
        log.warning("Ignoring KITE_API_KEY from the environment: it is a placeholder, not a Kite API key")
        return None

    from .models import KiteAccountCreate, KiteAccountUpdate

    user_id = os.environ.get("KITE_APP_USER_ID", "").strip() or "default"
    label = os.environ.get("KITE_LABEL", "").strip() or "Kite (env)"
    if _env_flag("KITE_PAPER", False):
        # Sterling has no non-live mode. Say so loudly rather than silently
        # downgrading an operator's expectation in either direction.
        log.warning(
            "KITE_PAPER is no longer honoured: Sterling only creates LIVE accounts. "
            "Remove it from the environment."
        )
    access_token = os.environ.get("KITE_ACCESS_TOKEN", "").strip()
    kite_user_id = (
        os.environ.get("KITE_USER_ID")
        or os.environ.get("ZERODHA_USER_ID")
        or ""
    ).strip()

    existing = next(
        (a for a in kite_accounts.list_accounts(user_id) if a.api_key == api_key), None,
    )
    if existing is None and kite_accounts.list_accounts(user_id):
        # Environment is a first-run bootstrap, never a second account source
        # after the operator has configured Connect (including key corrections).
        return None
    if existing is None:
        acct = kite_accounts.add(user_id, KiteAccountCreate(
            label=label, api_key=api_key, api_secret=api_secret, is_paper=False,
        ))
        # Never displace the account that is active now: switching accounts goes
        # through the activate route, which checks the outgoing account is flat.
        current = kite_accounts.get_active(user_id)
        if current is None:
            # Provision only when there is no selected account. A saved UI credential
            # correction must survive even before its first browser login.
            kite_accounts.set_active(user_id, acct.id)
        else:
            log.warning(
                "Seeded Kite account %s from environment but left the active account "
                "unchanged; activate it from the app if you intend to switch.", acct.id)
        log.info("Seeded Kite account %s from environment (user=%s)", acct.id, user_id)
    else:
        acct = existing
        # Environment credentials bootstrap missing configuration only. Saved
        # credential corrections in Connect take precedence on subsequent boots.
        if not acct.api_secret_enc:
            kite_accounts.update(user_id, acct.id, KiteAccountUpdate(api_secret=api_secret))

    if access_token and not acct.token_is_live:
        if not kite_user_id:
            # A token without its broker user identity cannot be bound to a
            # tenant safely. Seed the credentials, but leave the account
            # disconnected until interactive login or an explicit KITE_USER_ID
            # proves who owns the token.
            log.warning(
                "KITE_ACCESS_TOKEN was not adopted for live account %s because "
                "KITE_USER_ID/ZERODHA_USER_ID is missing",
                acct.id,
            )
        else:
            kite_accounts.save_session(
                user_id,
                acct.id,
                access_token=access_token,
                kite_user_id=kite_user_id,
            )
            # save_session marks the token validated (it assumes Kite just issued it).
            # An env-supplied token has no such provenance, so force the first real
            # request to prove it rather than trusting .env.
            kite_accounts.forget_validation(acct.id)
            log.info(
                "Adopted KITE_ACCESS_TOKEN from environment for account %s",
                acct.id,
            )
    return acct.id


# ─── Background keeper ───────────────────────────────────────────────────────
_login_reminded: dict = {}


async def _remind_daily_login(acct) -> None:
    """Telegram once per trading morning when a live account has no usable session.

    Accounts without a refresh token need Zerodha's daily login. On 22 Oct 2026 nobody
    logged in until 11:42, so nothing traded for the first 2.5 hours and nothing said so.
    From 08:30 to 15:30 IST on weekdays, a missing or expired session sends one reminder.
    """
    from datetime import datetime, timedelta, timezone
    ist = datetime.now(timezone(timedelta(hours=5, minutes=30)))
    if acct.is_paper or ist.weekday() >= 5 or not (8 * 60 + 30 <= ist.hour * 60 + ist.minute <= 15 * 60 + 30):
        return
    expires_at = acct.token_expires_at
    if acct.access_token_enc and expires_at is not None and not kite_session.is_expired(expires_at):
        return
    day = ist.date().isoformat()
    if _login_reminded.get(acct.id) == day:
        return
    _login_reminded[acct.id] = day
    try:
        from app.services.notifications import telegram
        await telegram.send(
            f"Kite is not logged in ({acct.label or acct.id}). No prices and no orders until you log in: "
            f"Sterling → Tools → Connect → Log in to Kite. Do it before 09:15.")
    except Exception as exc:  # noqa: BLE001 - a reminder must never break the keeper
        log.warning("Kite login reminder could not be sent: %s", exc)


async def session_keeper_loop(interval_s: int = 600) -> None:
    """Keep every stored account's session alive without a browser in the loop.

    Matters because the strategy engines run headless: if a token dies at 06:00
    IST and nobody opens the UI before the 09:15 open, the ORB and Navigator scans
    have no session. This renews accounts that *can* be renewed shortly before
    they lapse. Accounts without a refresh_token are skipped in silence — nothing
    here can substitute for Zerodha's daily 2FA.
    """
    await asyncio.sleep(20)     # let startup settle
    while True:
        try:
            for acct in kite_accounts.all_accounts():
                if not acct.has_refresh_token or not acct.api_key:
                    await _remind_daily_login(acct)
                    continue
                expires_at = acct.token_expires_at
                due = (
                    not acct.access_token_enc
                    or expires_at is None
                    or kite_session.is_expired(expires_at - RENEW_MARGIN_MS)
                )
                if due:
                    await renew(acct.user_id, acct)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — a keeper must never die
            log.warning("Kite session keeper tick failed: %s", exc)
        await asyncio.sleep(interval_s)
