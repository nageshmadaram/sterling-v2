"""Normalized, passive broker READ contract built from Sterling's session/feed view.

Normalization must never infer broker login, authority to order, or a fill.
It neither reaches Kite nor owns accounts, subscriptions or order state.
"""
from __future__ import annotations

from typing import Literal, TypedDict

from app.services.platform_session_read import SessionFeedSnapshot

SessionState = Literal[
    "not_configured", "no_stored_token", "stored_token_unverified",
    "feed_disconnected", "feed_connected",
]


class BrokerReadModel(TypedDict):
    version: str
    source: str
    view: str
    session_state: SessionState
    broker_login_verified: bool
    order_execution_authorized: bool
    feed_bound_to_active_account: bool
    feed_connected: bool
    shared_owner_feed: bool
    subscriptions_count: int
    last_tick_ms: int


def normalize_broker_read(snapshot: SessionFeedSnapshot) -> BrokerReadModel:
    """Derive one named status, without creating or validating a Kite session."""
    configured = snapshot["account_configured"]
    token = snapshot["stored_session_present"]
    bound = snapshot["active_account_feed_bound"]
    connected = snapshot["feed_connected"]
    shared = snapshot["shared_owner_feed"]
    view = snapshot["view"]
    subscriptions = snapshot["subscribed_count"]
    last_tick_ms = snapshot["last_tick_ms"]

    # A malformed feed snapshot must not become a plausible status response.
    # bool is a subclass of int; check exact types to reject fabricated counts.
    if (any(type(flag) is not bool for flag in (configured, token, bound, connected, shared))
            or type(view) is not str
            or type(subscriptions) is not int or subscriptions < 0
            or type(last_tick_ms) is not int or last_tick_ms < 0
            or (not bound and (subscriptions != 0 or last_tick_ms != 0))):
        raise ValueError("inconsistent_canonical_broker_read_evidence")

    if (view not in {"live", "simulation"}
            or shared != (view == "simulation")
            or (not configured and (token or bound or connected))
            or (bound and not token)
            or (connected and not bound)):
        raise ValueError("inconsistent_canonical_broker_read_evidence")

    if not configured:
        state: SessionState = "not_configured"
    elif not token:
        state = "no_stored_token"
    elif not bound:
        state = "stored_token_unverified"
    elif not connected:
        state = "feed_disconnected"
    else:
        state = "feed_connected"

    return {
        "version": "0.1.0",
        "source": "sterling_existing_account_and_ticker_managers",
        "view": view,
        "session_state": state,
        # Presence of a local token or connected feed does not prove the
        # broker will accept this session, much less permit execution.
        "broker_login_verified": False,
        "order_execution_authorized": False,
        "feed_bound_to_active_account": bound,
        "feed_connected": connected,
        "shared_owner_feed": shared,
        "subscriptions_count": subscriptions if bound else 0,
        "last_tick_ms": last_tick_ms if bound else 0,
    }
