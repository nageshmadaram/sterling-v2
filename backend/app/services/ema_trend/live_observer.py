"""Authenticated READ-ONLY observation of one canonical EMA live broker order.

This is not an execution or projection authority. It does not send, cancel or
modify orders, arm a stop, mutate the order journal, or record a broker fill.
It exists to let an operator inspect whether persisted EMA intent facts agree
with the actual broker. Disagreement means recovery, not resubmission.
"""
from __future__ import annotations

from decimal import Decimal

from app.services.exchanges.kite import accounts, auth
from app.services.kite_engine import order_journal
from . import repository as repo
from .live_order_facts import (
    LiveOrderIdentity, LiveOrderFact, LiveOrderEvidenceError,
    broker_order_fact, incremental_confirmed_fill,
)


async def observe_live_ema_order(uid: str, intent_key: str) -> dict:
    owner = repo.owner(uid)
    if not intent_key or intent_key != intent_key.strip():
        raise ValueError("CANONICAL_ORDER_INTENT_REQUIRED")
    intent = order_journal.get(intent_key)
    if intent is None or intent.uid != owner or intent.strategy_id != "ema_trend":
        raise repo.NotFound("EMA_LIVE_INTENT_NOT_FOUND")
    # A simulated journal must never be treated as real broker evidence.
    account = accounts.get_active(owner)
    if (account is None or account.is_paper or accounts.is_sim_account(account)
            or account.id != intent.account_id or not account.connected):
        raise PermissionError("BOUND_LIVE_KITE_ACCOUNT_REQUIRED")
    health = await auth.ensure_session(owner, account, force_validate=True)
    if not health.connected or not health.validated or health.transient or not health.kite_user_id:
        raise PermissionError("FRESH_KITE_LOGIN_VERIFICATION_REQUIRED")
    refreshed = accounts.get_active(owner)
    if refreshed is None or refreshed.id != intent.account_id:
        raise PermissionError("BROKER_ACCOUNT_CHANGED_DURING_VERIFICATION")
    client = await accounts.acquire_client(refreshed)
    if getattr(client, "_is_paper", None) is not False or str(getattr(client, "_account_id", "")) != intent.account_id:
        raise PermissionError("LIVE_KITE_CLIENT_IDENTITY_UNVERIFIED")
    if not intent.order_id:
        return {
            "status": "UNRESOLVED", "intent_key": intent_key,
            "account_id": intent.account_id, "canonical_state": intent.state,
            "reason": "NO_CONFIRMED_BROKER_ORDER_ID_RECONCILE_ORDERBOOK",
            "read_only": True, "live_orders_enabled": False,
        }
    # The journal's immutable order identity, not request payload from the UI,
    # selects exactly which broker history is admissible evidence.
    identity = LiveOrderIdentity(
        uid=owner, account_id=intent.account_id, order_id=intent.order_id,
        exchange=intent.exchange, symbol=intent.symbol,
        product=str(intent.payload.get("product") or ""),
        side=intent.side, quantity=intent.quantity,
    )
    history = await client.get_order_history(intent.order_id)
    fact = broker_order_fact(identity, history,
        broker_account_id=client._account_id, broker_is_paper=client._is_paper)
    already = int(intent.filled_quantity)
    old_value = Decimal(str(intent.filled_value))
    if already < 0 or already > identity.quantity or old_value < 0 or not old_value.is_finite():
        raise LiveOrderEvidenceError("CANONICAL_INTENT_FILL_CHECKPOINT_INVALID")
    previous = LiveOrderFact(identity, intent.state, already, old_value)
    delta = incremental_confirmed_fill(previous, fact)
    blockers = []
    if intent.reconciliation_required:
        blockers.append("CANONICAL_ORDER_RECONCILIATION_REQUIRED")
    if intent.projection_pending:
        blockers.append("CANONICAL_FILL_PROJECTION_PENDING")
    if delta.quantity:
        blockers.append("UNPROJECTED_BROKER_FILL_PRESENT")
    if not fact.terminal:
        blockers.append("BROKER_ORDER_NOT_TERMINAL")
    # No claim that a terminal order means there is no held position.
    return {
        "status": "RECONCILIATION_REQUIRED" if blockers else "OBSERVED",
        "intent_key": intent_key, "account_id": identity.account_id,
        "order_id": identity.order_id, "symbol": f"{identity.exchange}:{identity.symbol}",
        "product": identity.product, "side": identity.side,
        "canonical_state": intent.state, "broker_status": fact.status,
        "requested_quantity": identity.quantity,
        "broker_confirmed_filled_quantity": fact.filled_quantity,
        "canonical_recorded_filled_quantity": already,
        "unprojected_quantity": delta.quantity, "unprojected_value": str(delta.value),
        "blockers": blockers, "read_only": True, "live_orders_enabled": False,
    }
