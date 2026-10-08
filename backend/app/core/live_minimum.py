"""The day-one exposure envelope every live entry has to fit inside.

Sterling ships as a live-money product, and its first days are deliberately the
most constrained: a fixed daily-loss budget, a fixed gross exposure, no
averaging down, and (when the operator asks for it) a lot cap. The point is not
that a small loss is acceptable. It is that a sizing bug, a stale input or a
runaway loop should meet a wall long before it meets the whole account.

This module is the pure rule. :mod:`app.services.live_minimum_gate` reads the
facts (inventory, realised P&L, verified capital) and
:class:`~app.services.execution_service.CanonicalExecutionService` asks it for a
verdict before any exposure-increasing intent is journaled.

Limits come from the environment, with defaults derived from facts the system
already holds rather than invented numbers:

``STERLING_LIVE_MINIMUM_DAILY_LOSS_INR``
    Realised loss, in rupees, at which new entries stop for the day. Default:
    the absolute value of the account's own daily-loss hard halt (INR 1,500
    unless the operator changed it).

``STERLING_LIVE_MINIMUM_GROSS_EXPOSURE_INR``
    Ceiling on capital deployed in open positions plus the new order. Default:
    the capital the broker has just verified (available funds plus what is
    already deployed), scaled by ``STERLING_LIVE_MINIMUM_MAX_DEPLOYED_PCT``
    (default 100). Set an absolute rupee figure to hold day one tighter.

``STERLING_LIVE_MINIMUM_MAX_DEPLOYED_PCT``
    Share of verified capital that may be deployed at once. 1-100, default 100.

``STERLING_LIVE_MINIMUM_MAX_LOTS``
    Optional cap on the lots in one entry. Unset means the engines' own
    ``execution_lots`` / ``max_lots`` knobs are the only lot cap.

A limit that is present but unusable (not a number, zero, negative) is NOT
replaced by its default: someone who typed it meant to restrict something, and
quietly widening the limit on a typo is the wrong way to fail. The envelope is
reported as invalid and the entry is refused until it is fixed.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Final, Mapping

__all__ = [
    "LiveMinimumEnvelope", "LiveMinimumVerdict", "configured_envelope",
    "check_order", "DAILY_LOSS_ENV", "GROSS_EXPOSURE_ENV", "MAX_LOTS_ENV",
    "MAX_DEPLOYED_PCT_ENV", "DEFAULT_MAX_DEPLOYED_PCT", "BLOCKER_TEXT",
    "configured_deployed_pct",
]

DAILY_LOSS_ENV: Final[str] = "STERLING_LIVE_MINIMUM_DAILY_LOSS_INR"
GROSS_EXPOSURE_ENV: Final[str] = "STERLING_LIVE_MINIMUM_GROSS_EXPOSURE_INR"
MAX_LOTS_ENV: Final[str] = "STERLING_LIVE_MINIMUM_MAX_LOTS"
MAX_DEPLOYED_PCT_ENV: Final[str] = "STERLING_LIVE_MINIMUM_MAX_DEPLOYED_PCT"

#: Share of verified capital that may be deployed when the operator has not set
#: an absolute gross-exposure figure.
DEFAULT_MAX_DEPLOYED_PCT: Final[float] = 100.0


@dataclass(frozen=True)
class LiveMinimumEnvelope:
    """The configured limits. ``None`` means not configured."""

    daily_loss_budget: float | None = None
    gross_exposure_limit: float | None = None
    #: Optional lot cap. ``None`` leaves lot counts to the engines' own knobs.
    max_lots: int | None = None
    #: Environment variables that were set but unusable. Never defaulted over.
    invalid: tuple[str, ...] = ()
    #: Where each limit came from, for the operator ("env", "derived: ...").
    sources: Mapping[str, str] = field(default_factory=dict, compare=False)

    @property
    def configured(self) -> bool:
        return (self.daily_loss_budget is not None
                and self.gross_exposure_limit is not None
                and not self.invalid)

    def as_dict(self) -> dict[str, Any]:
        return {
            "daily_loss_budget": self.daily_loss_budget,
            "gross_exposure_limit": self.gross_exposure_limit,
            "max_lots": self.max_lots,
            "invalid": list(self.invalid),
            "sources": dict(self.sources),
            "configured": self.configured,
        }


def _positive(source: Mapping[str, str], name: str) -> float | None:
    raw = (source.get(name) or "").strip()
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def _present(source: Mapping[str, str], name: str) -> bool:
    return bool((source.get(name) or "").strip())


def configured_envelope(env: Mapping[str, str] | None = None) -> LiveMinimumEnvelope:
    """The limits the operator set explicitly. Derived defaults are not applied here."""
    source = env if env is not None else os.environ
    invalid: list[str] = []
    sources: dict[str, str] = {}

    daily = _positive(source, DAILY_LOSS_ENV)
    if daily is not None:
        sources["daily_loss_budget"] = DAILY_LOSS_ENV
    elif _present(source, DAILY_LOSS_ENV):
        invalid.append(DAILY_LOSS_ENV)

    gross = _positive(source, GROSS_EXPOSURE_ENV)
    if gross is not None:
        sources["gross_exposure_limit"] = GROSS_EXPOSURE_ENV
    elif _present(source, GROSS_EXPOSURE_ENV):
        invalid.append(GROSS_EXPOSURE_ENV)

    max_lots: int | None = None
    lots = _positive(source, MAX_LOTS_ENV)
    if lots is not None and lots == int(lots):
        max_lots = int(lots)
        sources["max_lots"] = MAX_LOTS_ENV
    elif _present(source, MAX_LOTS_ENV):
        invalid.append(MAX_LOTS_ENV)

    pct = _positive(source, MAX_DEPLOYED_PCT_ENV)
    if _present(source, MAX_DEPLOYED_PCT_ENV) and (pct is None or pct > 100.0):
        invalid.append(MAX_DEPLOYED_PCT_ENV)

    return LiveMinimumEnvelope(
        daily_loss_budget=daily,
        gross_exposure_limit=gross,
        max_lots=max_lots,
        invalid=tuple(invalid),
        sources=sources,
    )


#: What an operator reads for each refusal code.
BLOCKER_TEXT: Final[Mapping[str, str]] = {
    "LIVE_MINIMUM_NOT_CONFIGURED": "the day-one exposure limits could not be established",
    "LIVE_MINIMUM_CONFIG_INVALID": "a STERLING_LIVE_MINIMUM_* limit is set but unusable",
    "MINIMUM_QUANTITY_UNKNOWN": "the contract's lot size could not be established",
    "QUANTITY_ABOVE_MINIMUM": "the order is larger than the permitted lot count",
    "ORDER_VALUE_UNKNOWN": "the order's capital requirement is unknown",
    "GROSS_EXPOSURE_UNKNOWN": "open exposure could not be read",
    "GROSS_EXPOSURE_EXCEEDED": "the order would take deployed capital past the limit",
    "DAILY_LOSS_UNKNOWN": "today's realised loss could not be read",
    "DAILY_LOSS_BUDGET_SPENT": "today's loss budget is spent",
    "AVERAGING_DOWN_UNKNOWN": "cannot tell whether this adds to a losing position",
    "AVERAGING_DOWN_PROHIBITED": "this would add to a position that is losing",
    "LANE_PERMISSION_UNKNOWN": "lane permission could not be read",
    "LANE_NOT_PERMITTED": "the lane may not send entries",
    "RELEASE_CERTIFICATION_UNKNOWN": "release certification could not be read",
    "RELEASE_NOT_CERTIFIED": "the release is not certified",
    "ACCOUNT_BINDING_UNKNOWN": "account binding could not be read",
    "ACCOUNT_BINDING_NOT_READY": "no usable broker account binding",
    "SAFETY_STATE_UNKNOWN": "the safety state could not be read",
    "SAFETY_BLOCKS_EXPOSURE": "the safety supervisor refuses new exposure",
    "RECOVERY_STATE_UNKNOWN": "the recovery state could not be read",
    "RECOVERY_REQUIRED": "recovery is required before new exposure",
    "BROKER_SESSION_UNKNOWN": "the broker session could not be read",
    "BROKER_SESSION_NOT_READY": "the broker session is not ready",
    "MARKET_FRESHNESS_UNKNOWN": "market data freshness could not be read",
    "MARKET_DATA_STALE": "market data is stale",
    "UNRESOLVED_EXPOSURE_UNKNOWN": "unresolved exposure could not be read",
    "UNRESOLVED_EXPOSURE_OPEN": "unresolved exposure is open",
    "DAILY_LOSS_REMAINING_UNKNOWN": "remaining loss budget could not be read",
}


@dataclass(frozen=True)
class LiveMinimumVerdict:
    """Whether one order fits the envelope, and every reason it does not."""

    allowed: bool
    blockers: tuple[str, ...] = ()
    envelope: LiveMinimumEnvelope = LiveMinimumEnvelope()

    def as_dict(self) -> dict[str, Any]:
        return {"allowed": self.allowed, "blockers": list(self.blockers),
                "envelope": self.envelope.as_dict()}

    def message(self) -> str:
        """One line for the operator: every blocker, in words."""
        if self.allowed:
            return "within the live-minimum envelope"
        return "; ".join(
            f"{code} ({BLOCKER_TEXT.get(code, 'refused')})" for code in self.blockers
        )


def configured_deployed_pct(env: Mapping[str, str] | None = None) -> float:
    """Share of verified capital that may be deployed (1-100, default 100).

    An unusable value is reported by :func:`configured_envelope` as invalid and
    refuses the entry there; this only has to return something sensible.
    """
    source = env if env is not None else os.environ
    pct = _positive(source, MAX_DEPLOYED_PCT_ENV)
    if pct is None or pct > 100.0:
        return DEFAULT_MAX_DEPLOYED_PCT
    return pct


class _Unset:
    def __repr__(self) -> str:  # pragma: no cover - display only
        return "<unset>"


_UNSET: Final = _Unset()


def check_order(
    *,
    quantity: int,
    minimum_executable_quantity: int | None,
    order_value: float | None,
    open_gross_exposure: float | None,
    realised_loss_today: float | None,
    is_averaging_down: bool | None,
    envelope: LiveMinimumEnvelope | None = None,
    # Section 21.1: the state of the system the order is being sent from. Each
    # is tri-state and each None refuses, because an order sent from a machine
    # whose safety state could not be read is not a safe order, it is an
    # unexamined one.
    lane_permission: bool | None = None,
    release_certification: bool | None = None,
    account_binding: bool | None = None,
    safety_state: bool | None = None,
    recovery_state: bool | None = None,
    broker_session: bool | None = None,
    market_freshness: bool | None = None,
    unresolved_exposure: int | None = None,
    daily_loss_remaining: float | None = None,
    # Lot policy. Left unset it is the strict first-order rule: the envelope's
    # ``max_lots`` if it has one, otherwise exactly one lot. ``None`` means the
    # lot count is not capped here (the engines' own sizing caps apply).
    max_lots: int | None | _Unset = _UNSET,
    # The system-state inputs above are owned by the SafetySupervisor, which the
    # canonical executor has already asked (twice) before this runs. Production
    # passes ``False`` so one authority is not asked to answer the same
    # question in two places; the strict form stays for the standalone check.
    require_system_state: bool = True,
) -> LiveMinimumVerdict:
    """Does this order fit inside the live-minimum envelope?

    Every input is nullable and every ``None`` refuses. An order whose value
    could not be computed, against an exposure that could not be read, is not a
    small order -- it is an unmeasured one, and the whole point of the envelope
    is that every order is measured.

    All blockers are returned, never only the first: an operator fixing one
    condition at a time, discovering the next only after the next attempt, is
    how a careful person ends up disabling checks.
    """
    limits = envelope if envelope is not None else configured_envelope()
    blockers: list[str] = []

    if limits.invalid:
        blockers.append("LIVE_MINIMUM_CONFIG_INVALID")
    elif not limits.configured:
        blockers.append("LIVE_MINIMUM_NOT_CONFIGURED")

    lots_allowed: int | None
    if isinstance(max_lots, _Unset):
        lots_allowed = limits.max_lots if limits.max_lots is not None else 1
    else:
        lots_allowed = max_lots

    if lots_allowed is not None:
        if minimum_executable_quantity is None:
            blockers.append("MINIMUM_QUANTITY_UNKNOWN")
        elif (
            minimum_executable_quantity <= 0
            or quantity <= 0
            or quantity % minimum_executable_quantity != 0
            or quantity > lots_allowed * minimum_executable_quantity
        ):
            # With the strict default this is "exactly one lot": a larger first
            # order is a different experiment, and a part-lot cannot be executed.
            blockers.append("QUANTITY_ABOVE_MINIMUM")

    if order_value is None or order_value <= 0:
        blockers.append("ORDER_VALUE_UNKNOWN")
    if open_gross_exposure is None:
        blockers.append("GROSS_EXPOSURE_UNKNOWN")
    if (order_value is not None and order_value > 0 and open_gross_exposure is not None
            and limits.gross_exposure_limit is not None
            and order_value + open_gross_exposure > limits.gross_exposure_limit):
        blockers.append("GROSS_EXPOSURE_EXCEEDED")

    if realised_loss_today is None:
        blockers.append("DAILY_LOSS_UNKNOWN")
    elif (limits.daily_loss_budget is not None
          and realised_loss_today >= limits.daily_loss_budget):
        blockers.append("DAILY_LOSS_BUDGET_SPENT")

    if is_averaging_down is None:
        blockers.append("AVERAGING_DOWN_UNKNOWN")
    elif is_averaging_down:
        blockers.append("AVERAGING_DOWN_PROHIBITED")

    if require_system_state:
        for value, unknown_code, refusal_code in (
            (lane_permission, "LANE_PERMISSION_UNKNOWN", "LANE_NOT_PERMITTED"),
            (release_certification, "RELEASE_CERTIFICATION_UNKNOWN", "RELEASE_NOT_CERTIFIED"),
            (account_binding, "ACCOUNT_BINDING_UNKNOWN", "ACCOUNT_BINDING_NOT_READY"),
            (safety_state, "SAFETY_STATE_UNKNOWN", "SAFETY_BLOCKS_EXPOSURE"),
            (recovery_state, "RECOVERY_STATE_UNKNOWN", "RECOVERY_REQUIRED"),
            (broker_session, "BROKER_SESSION_UNKNOWN", "BROKER_SESSION_NOT_READY"),
            (market_freshness, "MARKET_FRESHNESS_UNKNOWN", "MARKET_DATA_STALE"),
        ):
            if value is None:
                blockers.append(unknown_code)
            elif not value:
                blockers.append(refusal_code)

        if unresolved_exposure is None:
            blockers.append("UNRESOLVED_EXPOSURE_UNKNOWN")
        elif unresolved_exposure:
            blockers.append("UNRESOLVED_EXPOSURE_OPEN")

        if daily_loss_remaining is None:
            blockers.append("DAILY_LOSS_REMAINING_UNKNOWN")
        elif daily_loss_remaining <= 0:
            blockers.append("DAILY_LOSS_BUDGET_SPENT")

    return LiveMinimumVerdict(
        allowed=not blockers, blockers=tuple(blockers), envelope=limits
    )
