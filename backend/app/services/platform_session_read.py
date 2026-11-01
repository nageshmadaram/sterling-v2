"""Account-scoped read projection over existing Kite session/feed managers.

No broker client is acquired and no ticker is ensured, connected, subscribed,
stopped or replaced. Never expose credentials, instrument tokens, account IDs or
subscription-owner tags from this cross-surface discovery contract.
"""
from __future__ import annotations

from typing import TypedDict

from app.core.auth import UserContext
from app.services.exchanges.kite import accounts, ticker_manager
from app.services.simulation import identity as simulation_identity


class SessionFeedSnapshot(TypedDict):
    view: str
    account_configured: bool
    stored_session_present: bool
    shared_owner_feed: bool
    active_account_feed_bound: bool
    feed_connected: bool
    subscribed_count: int
    last_tick_ms: int


def _scope_owner(principal: UserContext) -> tuple[str, bool]:
    """Require the identity seam's exact virtual tenant and owner relationship."""
    if principal.view == "simulation":
        owner = str(principal.owner_uid or "").strip()
        if (not owner or principal.role != "admin"
                or principal.user_id != simulation_identity.sim_uid(owner)):
            raise ValueError("simulation_owner_scope_unverified")
        return owner, True
    if principal.view or simulation_identity.is_sim_uid(principal.user_id):
        raise ValueError("invalid_live_principal_scope")
    owner = str(principal.user_id or "").strip()
    if not owner:
        raise ValueError("principal_identity_missing")
    return owner, False


def session_feed_snapshot(principal: UserContext) -> SessionFeedSnapshot:
    """Read the *existing* account and ticker state without creating state.

    Simulation shares only its authenticated owner's live feed, never a second
    broker client. A disconnected, paper, foreign-account or stale ticker
    cannot be presented as a verified active stream. Token presence is NOT
    broker-login validation.
    """
    owner, shared = _scope_owner(principal)
    account = accounts.get_active(owner)
    if account is not None and (
        getattr(account, "user_id", None) != owner
        or bool(getattr(account, "is_paper", True))
        or bool(getattr(account, "is_sim", False))
    ):
        # An inconsistent store result cannot authorize a broker identity.
        raise ValueError("active_broker_account_scope_inconsistent")

    configured = account is not None
    stored_token = bool(account.connected) if configured else False
    # Do not read the ticker map if the caller has no active scoped account.
    # Reading status() is passive; ensure() would create a broker WebSocket.
    stream = ticker_manager.status(principal.user_id) if configured else {}
    if not isinstance(stream, dict):
        # Malformed broker-feed state cannot be treated as a healthy session.
        raise ValueError("scoped_ticker_status_invalid")
    account_id = getattr(account, "id", None) if configured else None
    feed_account_id = stream.get("account_id")
    # Equal *missing* or blank identifiers do not prove account binding.
    # A session and its ticker must each identify the same nonblank account.
    bound = bool(
        configured and stored_token
        and isinstance(account_id, str) and bool(account_id.strip())
        and isinstance(feed_account_id, str) and bool(feed_account_id.strip())
        and feed_account_id == account_id
    )
    subscribed = stream.get("subscribed")
    valid_subscriptions = subscribed if isinstance(subscribed, (list, tuple, set)) else ()
    raw_tick_ms = stream.get("last_tick_ms", 0)
    try:
        tick_ms = max(0, int(raw_tick_ms or 0))
    except (ValueError, TypeError, OverflowError):
        tick_ms = 0
    return {
        "view": "simulation" if shared else "live",
        "account_configured": configured,
        "stored_session_present": stored_token,
        "shared_owner_feed": shared,
        "active_account_feed_bound": bound,
        "feed_connected": bool(bound and stream.get("connected")),
        "subscribed_count": len(valid_subscriptions) if bound else 0,
        "last_tick_ms": tick_ms if bound else 0,
    }
