"""Focus policy for the Snapback/SuperTrend lane-evidence subsystem.

This is not the production broker allowlist. The production origination scope
lives in :mod:`app.core.release_scope` and also includes Gamma-Unwind.

The lane subsystem is intentionally narrower because its five-mode evidence
framework exists only for Snapback and SuperTrend. Legacy engines that still
hold a position must keep reconciling, marking and exiting it; focus never
strands existing exposure.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Final, Literal, get_args

FocusedStrategy = Literal["snapback", "supertrend"]

#: Every strategy the focus rule can name. A value outside this set is a
#: configuration error, not a new strategy.
FOCUSABLE: Final[frozenset[str]] = frozenset(get_args(FocusedStrategy))

ENV_VAR: Final[str] = "STERLING_FOCUSED_STRATEGIES"

#: Default when the operator has said nothing. Matches the declared policy.
DEFAULT_FOCUS: Final[frozenset[str]] = frozenset(FOCUSABLE)


class FocusConfigurationError(ValueError):
    """The focus setting could not be understood.

    Deliberately not recoverable by falling back to the default: an operator
    who typed ``snapbak`` meant to *restrict* origination, and silently
    restoring both strategies would widen risk on a typo.
    """


@dataclass(frozen=True)
class FocusPolicy:
    """The resolved origination policy."""

    #: Strategies allowed to originate. Empty means nothing may originate.
    originators: frozenset[str]
    #: Where the policy came from, for the evidence row and the doctor report.
    source: str

    def may_originate(self, strategy: str) -> bool:
        """May ``strategy`` open new exposure?

        An unknown strategy name is refused rather than passed through: the
        whole point of focus mode is that an engine nobody remembered cannot
        quietly keep trading.
        """
        return (strategy or "").strip().lower() in self.originators

    def may_manage(self, strategy: str) -> bool:
        """May ``strategy`` monitor, reduce, protect or exit what it already holds?

        Always yes. Focus never strands an open position.
        """
        return True

    def refusal_reason(self, strategy: str) -> str | None:
        """Why origination was refused, or ``None`` if it was allowed."""
        name = (strategy or "").strip().lower()
        if name in self.originators:
            return None
        if name not in FOCUSABLE:
            return (
                f"STRATEGY_NOT_FOCUSED: {strategy!r} is outside the focused set "
                f"{sorted(self.originators)}; it may manage existing positions only"
            )
        return (
            f"STRATEGY_NOT_FOCUSED: {name} is focusable but not enabled by "
            f"{ENV_VAR} ({self.source})"
        )


def parse_focus(raw: str | None, *, source: str = ENV_VAR) -> FocusPolicy:
    """Parse a comma-separated focus setting.

    An unset or blank value yields the default policy. A value naming anything
    outside :data:`FOCUSABLE` raises.
    """
    if raw is None or not raw.strip():
        return FocusPolicy(originators=DEFAULT_FOCUS, source=f"{source} (unset: default)")

    names = [part.strip().lower() for part in raw.split(",")]
    names = [n for n in names if n]
    if not names:
        return FocusPolicy(originators=DEFAULT_FOCUS, source=f"{source} (blank: default)")

    unknown = sorted(set(names) - FOCUSABLE)
    if unknown:
        raise FocusConfigurationError(
            f"{source}: unknown strateg{'y' if len(unknown) == 1 else 'ies'} "
            f"{unknown}; expected any of {sorted(FOCUSABLE)}"
        )
    return FocusPolicy(originators=frozenset(names), source=source)


def focus_policy(env: dict[str, str] | None = None) -> FocusPolicy:
    """Resolve the policy from the environment. Cheap; call it per decision."""
    source = env if env is not None else os.environ
    return parse_focus(source.get(ENV_VAR))
