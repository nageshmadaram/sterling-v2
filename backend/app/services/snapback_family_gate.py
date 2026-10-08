"""Family live gate for Snapback.

Any exposure-increasing live Snapback order requires ALL of:

    canonical RiskEngine approval
    AND system HEALTHY
    AND broker reconciled

The owner has waived the evidence/promotion requirements for this release, so the
recorded evidence verdict and the LIVE_ELIGIBLE promotion state are reported as
``advisories`` on the decision and never block. Everything that describes the live
system right now (risk approval, health, reconciliation) still does.

A manual UI toggle cannot bypass it. Protective actions — exiting, flattening and
reconciliation — stay permitted while halted, because blocking them would trap
capital rather than protect it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, List

log = logging.getLogger(__name__)


class LiveIntent(str, Enum):
    ENTER = "ENTER"
    ADD = "ADD"
    EXIT = "EXIT"
    FLATTEN = "FLATTEN"
    RECONCILE = "RECONCILE"


_EXPOSURE_INCREASING = {LiveIntent.ENTER, LiveIntent.ADD}
_PROTECTIVE = {LiveIntent.EXIT, LiveIntent.FLATTEN, LiveIntent.RECONCILE}


@dataclass
class LiveGateDecision:
    allowed: bool
    protective: bool = False
    reasons: List[str] = field(default_factory=list)
    #: Waived checks that did not hold. Shown to the operator, never blocking.
    advisories: List[str] = field(default_factory=list)


def is_exposure_increasing(intent: Any) -> bool:
    try:
        return LiveIntent(intent) in _EXPOSURE_INCREASING
    except Exception:
        # An intent we do not recognise is treated as exposure-increasing.
        return True


def evaluate_live_gate(
    *,
    intent: Any,
    evidence_verdict: str,
    live_eligible: bool,
    risk_approved: bool,
    system_status: str,
    broker_reconciled: bool,
    operator_override: bool = False,
) -> LiveGateDecision:
    """Decide whether one live Snapback action may proceed."""
    try:
        parsed = LiveIntent(intent)
    except Exception:
        log.warning("Snapback family gate: unknown live intent %r denied", intent)
        return LiveGateDecision(allowed=False, reasons=["unknown_intent"])

    if parsed in _PROTECTIVE:
        # Protective work continues regardless of evidence or health state.
        return LiveGateDecision(allowed=True, protective=True)

    # Waived by the owner: the evidence verdict and the promotion-state flag are
    # advisory. They are recorded so an operator can see what has not been proven,
    # but a verdict of INCONCLUSIVE or an unpromoted state never refuses an order.
    advisories: List[str] = []
    if str(evidence_verdict).upper() != "PASSED":
        advisories.append("evidence_not_passed")
    if not live_eligible:
        advisories.append("not_live_eligible")

    reasons: List[str] = []
    if not risk_approved:
        reasons.append("risk_engine_denied")
    if str(system_status).upper() != "HEALTHY":
        reasons.append("system_not_healthy")
    if not broker_reconciled:
        reasons.append("broker_not_reconciled")

    if reasons and operator_override:
        # Recorded deliberately: an override is never allowed to clear these.
        log.warning(
            "Snapback family gate: operator override ignored; blocking reasons=%s", reasons
        )

    return LiveGateDecision(
        allowed=not reasons, protective=False, reasons=reasons, advisories=advisories,
    )
