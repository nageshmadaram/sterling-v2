"""Broker-write capability guard (and the retired Family Mode switch).

Hiding a button is not a control. Every exposure-increasing broker write on a live
account must originate from the canonical execution authority, which takes an
explicit capability for the duration of the call. A direct `client.place_order()`
from any other engine is refused at the transport, so an accidental bypass is
impossible rather than merely unlikely. The guard is always on for live clients
(the transport only consults it for them); it does not depend on any environment
variable.

Family Mode (``STERLING_FAMILY_MODE``) is retired as a strategy gate. Sterling now
ships three strategies (Supertrend, Snapback, Gamma Unwind) and every one of their
loops runs. The variable is still read, but only to be *stricter*: if someone sets
it, GTT and cancel operations also need a matching capability. It never suppresses
a strategy loop and ``main.py`` no longer looks at it.
"""

from __future__ import annotations

import contextvars
import logging
import os
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator, Optional

log = logging.getLogger(__name__)

# Set only for the duration of a canonical execution call:
# (intent id, declared intent, optional ReduceBound).
_capability: contextvars.ContextVar[Optional[tuple]] = contextvars.ContextVar(
    "snapback_broker_capability", default=None
)

# Broker order placement is contextual: a verified canonical order can either
# increase exposure or reduce an existing position.
ORDER_OPERATIONS = {"place_order"}

# Mutations whose generic form can create, increase or alter exposure.
EXPOSURE_OPERATIONS = {
    "modify_order",          # can change quantity or price of a live order
    "place_mf_order",
    "place_mf_sip",
    "modify_mf_sip",
    "convert_position",
}

# GTT creation/modification is also contextual. Arbitrary GTTs can create exposure,
# while the canonical protection service uses the same transport to arm an exit-only
# stop/OCO derived from a persisted position.
PROTECTION_OPERATIONS = {
    "place_gtt",
    "modify_gtt",
}

# Risk-reducing/cancellation mutations. Still capability-bound: a raw transport
# caller cannot cancel broker state merely because cancellation is often safer.
PROTECTIVE_OPERATIONS = {
    "cancel_order",
    "cancel_order_unsafe",
    "cancel_gtt",
    "cancel_mf_order",
    "cancel_mf_sip",
    "exit_order",
}

GUARDED_OPERATIONS = (
    ORDER_OPERATIONS | EXPOSURE_OPERATIONS | PROTECTION_OPERATIONS | PROTECTIVE_OPERATIONS
)

# Intents a capability can declare.
INTENT_INCREASE = "INCREASE_EXPOSURE"
INTENT_REDUCE = "REDUCE_EXPOSURE"
INTENT_PROTECT = "PROTECT_POSITION"
INTENT_CANCEL = "CANCEL"


# Operations whose capability is enforced on every live client, with no switch.
# These are the ones that can put NEW exposure on the account (an entry, a changed
# live order, a mutual-fund or conversion write). Exits and protection are left to
# the stricter legacy switch below: refusing to place a stop or close a position
# because a capability was forgotten is how a safety control loses the money.
ALWAYS_GUARDED_OPERATIONS = ORDER_OPERATIONS | EXPOSURE_OPERATIONS


def family_mode_enabled() -> bool:
    """Whether the retired ``STERLING_FAMILY_MODE`` variable is set.

    Retired as a strategy gate: nothing is suppressed because of it. It only
    tightens the broker-write guard (see the module docstring).
    """
    return str(os.environ.get("STERLING_FAMILY_MODE", "")).strip().lower() in (
        "1", "true", "yes", "on",
    )


@dataclass(frozen=True)
class ReduceBound:
    """What a REDUCE_EXPOSURE capability may send: one contract, one side, a ceiling.

    ``side`` is the CLOSING transaction type (SELL to close a long, BUY to close a
    short). Without a bound a REDUCE capability only says "this is an exit", which
    leaves any exit-path bug (wrong side, stale quantity) free to open or flip exposure.
    """

    exchange: str
    symbol: str
    side: str
    max_quantity: int

    @classmethod
    def closing(cls, *, exchange: str, symbol: str, side: str, quantity: int) -> "ReduceBound":
        raw = str(symbol or "").strip()
        prefix, _, bare = raw.rpartition(":")
        return cls(
            exchange=str(exchange or prefix or "").strip().upper(),
            symbol=(bare or raw).strip().upper(),
            side=str(side or "").strip().upper(),
            max_quantity=max(int(quantity or 0), 0),
        )


@contextmanager
def canonical_broker_capability(
    intent_id: str, *, intent: str = INTENT_INCREASE, bound: Optional[ReduceBound] = None
) -> Iterator[str]:
    """Grant broker-write capability for one canonical execution intent.

    The declared intent bounds what the capability authorises: a REDUCE_EXPOSURE or
    CANCEL capability cannot be used to open a position. A REDUCE capability that
    also declares a ``bound`` is enforced order by order at the broker transport
    (:func:`guard_broker_reduction`).
    """
    token = _capability.set((intent_id, intent, bound))
    try:
        yield intent_id
    finally:
        _capability.reset(token)


def current_capability() -> Optional[str]:
    held = _capability.get()
    return held[0] if held else None


def current_intent() -> Optional[str]:
    held = _capability.get()
    return held[1] if held else None


def guard_broker_write(operation: str) -> None:
    """Refuse a broker write that did not come through canonical execution.

    Entry and exposure-changing operations are always guarded. The remaining
    guarded operations (GTT, cancel, exit) are guarded when the legacy strict
    switch is set.
    """
    if operation not in GUARDED_OPERATIONS:
        return
    if operation not in ALWAYS_GUARDED_OPERATIONS and not family_mode_enabled():
        return
    held = _capability.get()
    if held is None:
        log.error(
            "Broker write guard: refused %s — broker writes must pass through canonical execution",
            operation,
        )
        raise PermissionError(
            f"Broker write guard: {operation} must pass through the canonical execution "
            "authority; direct broker writes are refused"
        )

    intent = held[1]
    if operation in ORDER_OPERATIONS and intent != INTENT_INCREASE:
        log.error("Broker write guard: refused %s under a %s capability", operation, intent)
        raise PermissionError(
            f"Broker write guard: {operation} is the generic entry transport and requires "
            f"an increase-exposure capability, not {intent}"
        )
    if operation in EXPOSURE_OPERATIONS and intent != INTENT_INCREASE:
        log.error("Broker write guard: refused %s under a %s capability", operation, intent)
        raise PermissionError(
            f"Broker write guard: {operation} can increase exposure and cannot run under a "
            f"{intent} capability"
        )
    if operation in PROTECTION_OPERATIONS and intent not in {INTENT_INCREASE, INTENT_PROTECT}:
        log.error("Broker write guard: refused %s under a %s capability", operation, intent)
        raise PermissionError(
            f"Broker write guard: {operation} requires a canonical entry/protection capability, "
            f"not {intent}"
        )

    # Cancellation is not one undifferentiated permission.  Cancelling a broker
    # entry, removing a protective GTT, and sending an explicit exit have different
    # exposure effects.  In particular an INCREASE_EXPOSURE capability must never
    # become a generic authority to tear down broker-side protection merely because
    # all three operations live in PROTECTIVE_OPERATIONS.
    if operation in {"cancel_order", "cancel_order_unsafe", "cancel_mf_order", "cancel_mf_sip"}:
        if intent != INTENT_CANCEL:
            log.error("Broker write guard: refused %s under a %s capability", operation, intent)
            raise PermissionError(
                f"Broker write guard: {operation} requires an explicit cancel capability, "
                f"not {intent}"
            )
    if operation == "cancel_gtt" and intent not in {
        INTENT_CANCEL, INTENT_PROTECT, INTENT_REDUCE
    }:
        log.error("Broker write guard: refused %s under a %s capability", operation, intent)
        raise PermissionError(
            f"Broker write guard: {operation} requires cancel/protection/reduce authority, "
            f"not {intent}"
        )
    if operation == "exit_order" and intent != INTENT_REDUCE:
        log.error("Broker write guard: refused %s under a %s capability", operation, intent)
        raise PermissionError(
            f"Broker write guard: {operation} requires a reduce-exposure capability, "
            f"not {intent}"
        )


def guard_broker_reduction(
    *, exchange: str, tradingsymbol: str, transaction_type: str, quantity: int
) -> None:
    """Refuse an exit order that is not the one its REDUCE capability declared.

    Always on for a live transport (it does not wait for STERLING_FAMILY_MODE). Only a
    capability that declared a :class:`ReduceBound` is checked: an exit site that has
    not declared one yet is logged, never refused, because refusing a close on a
    forgotten declaration is how a safety control loses the money.
    """
    held = _capability.get()
    if held is None or held[1] != INTENT_REDUCE:
        return
    bound = held[2] if len(held) > 2 else None
    if bound is None:
        log.warning("Broker write guard: exit %s %s x%s under %r declares no bound",
                    transaction_type, tradingsymbol, quantity, held[0])
        return
    symbol = str(tradingsymbol or "").strip().upper().rpartition(":")[2]
    exchange = str(exchange or "").strip().upper()
    side = str(transaction_type or "").strip().upper()
    problems = []
    if symbol != bound.symbol or (bound.exchange and exchange != bound.exchange):
        problems.append(f"contract {exchange}:{symbol} is not {bound.exchange}:{bound.symbol}")
    if side != bound.side:
        problems.append(f"side {side} is not the closing side {bound.side}")
    try:
        qty = int(quantity)
    except (TypeError, ValueError):
        qty = -1
    if qty <= 0 or qty > bound.max_quantity:
        problems.append(f"quantity {quantity} exceeds the declared remainder {bound.max_quantity}")
    if problems:
        log.error("Broker write guard: refused exit under %r: %s", held[0], "; ".join(problems))
        raise PermissionError(
            "Broker write guard: this exit is not what its reduce capability declared: "
            + "; ".join(problems)
        )


# The strategies that may originate new exposure in this build. The canonical
# allowlist lives in ``app.core.release_scope``; this is a view of it kept for the
# callers that predate it. Every other engine is maintenance-only: it may manage
# and exit what it already owns, never open something new.
def release_strategies() -> frozenset:
    from app.core.release_scope import RELEASE_ORIGINATORS

    return frozenset(RELEASE_ORIGINATORS)


def family_visible_strategies() -> frozenset:
    """Retired name for :func:`release_strategies`."""
    return release_strategies()


def guard_family_strategy(strategy_id: str) -> None:
    """Refuse an originator that is not one of the release strategies.

    Not tied to any environment variable any more: Family Mode no longer limits
    the product to one strategy, and the release allowlist is always the rule.
    """
    from app.core.release_scope import may_originate

    if not may_originate(strategy_id):
        raise PermissionError(
            f"{strategy_id} is not a release strategy and cannot originate new "
            f"exposure. Release strategies: {sorted(release_strategies())}."
        )
