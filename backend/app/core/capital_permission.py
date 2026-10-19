"""What has to hold before a lane may send a live entry, and what is only advisory.

Sterling is a live-money product with one execution mode. There is an operator
KILL SWITCH (``STERLING_LIVE_EXECUTION=0``) and nothing else that "turns live
on": a lane that is declared live and clears the real safety checks sends
entries.

The decision is split in two, and the split is the point of this module:

* **Blocking checks** are real safety facts, each owned by a different part of
  the system and each able only to *remove* permission: the kill switch, the
  broker account binding, the SafetySupervisor (safe mode, recovery state,
  unresolved intents, daily-loss halt, external exposure), the risk and
  exposure admission that supervisor reports, the lane's own live state, and
  whether the lane's execution vehicle can describe a real order at all.
* **Advisory checks** are the evidence and promotion gates the owner has
  waived for this release: release certification, the lane's forward-sample
  verdict, frozen-identity drift and the shadow-execution gate. They are still
  *measured and reported* -- an operator can see exactly what has and has not
  been proven -- but a failing or unreadable one never blocks an entry.

Two design choices matter more than the lists themselves:

* **Unknown is never permission, for a blocking check.** Each blocking input is
  a tri-state where that is honest -- ``None`` means "could not be determined"
  -- and ``None`` refuses. A safety authority that could not be read is not a
  pass.
* **Every refusal is reported, not just the first.** An operator asking "why is
  this lane not live?" needs the whole answer; fixing one blocker only to meet
  the next is how a safety review turns into a game of whack-a-mole.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Final, Mapping

__all__ = [
    "CapitalPermission",
    "EntryPermissionInputs",
    "may_send_entry",
    "LIVE_LANE_STATES",
    "BLOCKING_CHECKS",
    "ADVISORY_CHECKS",
]

#: Lane states in which real capital is at stake.
LIVE_LANE_STATES: Final[frozenset[str]] = frozenset({"LIVE_MINIMUM", "LIVE_SCALED"})

#: Human text for each code, so the dashboard never shows a bare token.
_REASONS: Final[Mapping[str, str]] = {
    # -- blocking: real safety facts ------------------------------------------
    "GLOBAL_LIVE_DISABLED": (
        "the operator kill switch is engaged (STERLING_LIVE_EXECUTION): "
        "new entries are halted, exits and protection still run"
    ),
    "ACCOUNT_BINDING_NOT_READY": "no ACTIVE, live-ready broker account binding",
    "SAFETY_BLOCKS_EXPOSURE": "the safety supervisor refuses new exposure",
    "LANE_NOT_LIVE_STATE": "the lane is not in LIVE_MINIMUM or LIVE_SCALED",
    "RISK_GATE_NOT_PASSED": "the daily-loss / kill-switch risk admission refuses new exposure",
    "EXPOSURE_GATE_NOT_PASSED": "unresolved or unexplained broker exposure refuses new exposure",
    "VEHICLE_NOT_LIVE_CAPABLE": "the lane's execution vehicle cannot describe a real order",
    "UNKNOWN_INPUT": "a required permission input could not be determined",
    # -- advisory: waived for this release, reported but never blocking -------
    "RELEASE_NOT_CERTIFIED": "this build has no passing release certification",
    "LANE_PROMOTION_NOT_PASSED": "the lane's economic (forward-sample) gate has not passed",
    "LANE_IDENTITY_DRIFT": "the running lane identity is not the frozen one",
    "REHEARSAL_GATE_NOT_PASSED": "the lane's execution-rehearsal gate has not passed",
}


@dataclass(frozen=True)
class EntryPermissionInputs:
    """Every fact the decision needs, supplied by its owner.

    The blocking booleans default to the refusing value: a caller that forgets
    to pass one gets "no", which is the only safe direction for a default to
    point. The advisory ones default the same way for symmetry, but they are
    reported, never enforced.
    """

    lane_key: str

    # blocking
    global_live_switch: bool | None = False
    account_binding_ready: bool | None = False
    safety_may_increase_exposure: bool | None = False
    lane_state: str = ""
    risk_gate_passed: bool | None = False
    exposure_gate_passed: bool | None = False
    vehicle_live_capable: bool | None = False

    # advisory (waived)
    release_certified: bool | None = False
    lane_promotion_passed: bool | None = False
    lane_identity_matches_frozen: bool | None = False
    shadow_gate_passed: bool | None = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "lane_key": self.lane_key,
            "global_live_switch": self.global_live_switch,
            "release_certified": self.release_certified,
            "account_binding_ready": self.account_binding_ready,
            "safety_may_increase_exposure": self.safety_may_increase_exposure,
            "lane_state": self.lane_state,
            "lane_promotion_passed": self.lane_promotion_passed,
            "lane_identity_matches_frozen": self.lane_identity_matches_frozen,
            "shadow_gate_passed": self.shadow_gate_passed,
            "risk_gate_passed": self.risk_gate_passed,
            "exposure_gate_passed": self.exposure_gate_passed,
            "vehicle_live_capable": self.vehicle_live_capable,
        }


@dataclass(frozen=True)
class CapitalPermission:
    """Whether one lane may send a live entry, and every reason it may not."""

    allowed: bool
    lane_key: str
    blockers: tuple[str, ...] = field(default_factory=tuple)
    unknowns: tuple[str, ...] = field(default_factory=tuple)
    #: Waived gates that did not pass (or could not be read). Informational only.
    advisories: tuple[str, ...] = field(default_factory=tuple)
    lane_state_detail: str = ''

    def __bool__(self) -> bool:  # pragma: no cover - convenience
        return self.allowed

    @property
    def reasons(self) -> tuple[str, ...]:
        return tuple(
            self.lane_state_detail if code == 'LANE_NOT_LIVE_STATE' and self.lane_state_detail
            else _REASONS.get(code, code)
            for code in self.blockers
        )

    @property
    def advisory_reasons(self) -> tuple[str, ...]:
        return tuple(_REASONS.get(code, code) for code in self.advisories)

    def as_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "lane_key": self.lane_key,
            "blockers": list(self.blockers),
            "reasons": list(self.reasons),
            "unknowns": list(self.unknowns),
            "advisories": list(self.advisories),
            "advisory_reasons": list(self.advisory_reasons),
            "advisory_status": "waived",
        }

    def render(self) -> str:
        if self.allowed:
            lines = [f"{self.lane_key}: LIVE ENTRIES PERMITTED"]
        else:
            lines = [f"{self.lane_key}: LIVE ENTRY BLOCKED"]
            for code, reason in zip(self.blockers, self.reasons):
                lines.append(f"  {code:<28}{reason}")
            if self.unknowns:
                lines.append(
                    "  unknown: " + ", ".join(self.unknowns) + " (unknown never permits)"
                )
        if self.advisories:
            lines.append(
                "  waived (advisory, never blocking): " + ", ".join(self.advisories)
            )
        return "\n".join(lines)


#: Real safety facts. (input attribute, refusal code), in the order an operator
#: should read them: the kill switch, then the account, then the safety state.
BLOCKING_CHECKS: Final[tuple[tuple[str, str], ...]] = (
    ("global_live_switch", "GLOBAL_LIVE_DISABLED"),
    ("account_binding_ready", "ACCOUNT_BINDING_NOT_READY"),
    ("safety_may_increase_exposure", "SAFETY_BLOCKS_EXPOSURE"),
    ("risk_gate_passed", "RISK_GATE_NOT_PASSED"),
    ("exposure_gate_passed", "EXPOSURE_GATE_NOT_PASSED"),
    ("vehicle_live_capable", "VEHICLE_NOT_LIVE_CAPABLE"),
)

#: Evidence and promotion gates the owner waived for this release. Measured and
#: reported, never enforced.
ADVISORY_CHECKS: Final[tuple[tuple[str, str], ...]] = (
    ("release_certified", "RELEASE_NOT_CERTIFIED"),
    ("lane_promotion_passed", "LANE_PROMOTION_NOT_PASSED"),
    ("lane_identity_matches_frozen", "LANE_IDENTITY_DRIFT"),
    ("shadow_gate_passed", "REHEARSAL_GATE_NOT_PASSED"),
)

_BLOCKING_ORDER: Final[tuple[str, ...]] = (
    "GLOBAL_LIVE_DISABLED",
    "ACCOUNT_BINDING_NOT_READY",
    "SAFETY_BLOCKS_EXPOSURE",
    "LANE_NOT_LIVE_STATE",
    "RISK_GATE_NOT_PASSED",
    "EXPOSURE_GATE_NOT_PASSED",
    "VEHICLE_NOT_LIVE_CAPABLE",
)


def may_send_entry(inputs: EntryPermissionInputs) -> CapitalPermission:
    """Evaluate the blocking checks. Every ``False`` and every ``None`` refuses.

    A blocking input that is ``None`` (could not be determined) refuses and is
    reported as unknown. The advisory (waived) inputs are evaluated only to be
    reported: a ``False`` or ``None`` there lands in ``advisories`` and never in
    ``blockers``.
    """
    blockers: list[str] = []
    unknowns: list[str] = []
    advisories: list[str] = []

    state = str(inputs.lane_state or "").strip().upper()
    if state not in LIVE_LANE_STATES:
        blockers.append("LANE_NOT_LIVE_STATE")

    for attribute, code in BLOCKING_CHECKS:
        value = getattr(inputs, attribute)
        if value is None:
            unknowns.append(attribute)
            blockers.append(code)
        elif not value:
            blockers.append(code)

    for attribute, code in ADVISORY_CHECKS:
        value = getattr(inputs, attribute)
        if value is None or not value:
            advisories.append(code)

    blockers.sort(
        key=lambda code: _BLOCKING_ORDER.index(code) if code in _BLOCKING_ORDER
        else len(_BLOCKING_ORDER)
    )

    return CapitalPermission(
        allowed=not blockers,
        lane_key=inputs.lane_key,
        blockers=tuple(blockers),
        unknowns=tuple(unknowns),
        advisories=tuple(advisories),
    )
