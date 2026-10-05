"""One source of truth for what Snapback is allowed to do right now.

    OBSERVE       the system cannot be read well enough to trade
    DEGRADED      running, but a component is unhealthy; no new exposure
    LIVE_ELIGIBLE the system is healthy, the books agree and nothing is halted
    HALTED        stopped on purpose; protective actions only
    RECONCILING   state is unknown and must be resolved before new exposure

The recorded evidence verdict is reported alongside the state but does not decide
it: the owner has waived the evidence/promotion requirement for this release.
Every input is derived on the server. Nothing here trusts a caller.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List

log = logging.getLogger(__name__)

OBSERVE = "OBSERVE"
DEGRADED = "DEGRADED"
LIVE_ELIGIBLE = "LIVE_ELIGIBLE"
HALTED = "HALTED"
RECONCILING = "RECONCILING"


@dataclass
class ReadinessState:
    state: str
    system_status: str
    evidence_verdict: str
    broker_reconciled: bool
    reasons: List[str] = field(default_factory=list)


def _reconciliation_clean(uid: str = "default") -> bool:
    """Whether the last reconciliation found the two books in agreement.

    Unknown is never clean: if reconciliation cannot run, exposure stays blocked.
    """
    try:
        from app.services.snapback_family_account import binding_configured

        if not binding_configured():
            # No family account is bound, so there is no bound book to compare.
            # The live Snapback book is checked on its own by the dispatcher.
            return True

        from app.services.snapback_family_account import configured_binding
        from app.services.snapback_reconciliation import (
            latest_reconciliation, snapshot_is_fresh,
        )

        account_id = configured_binding().account_id
        snapshot = latest_reconciliation(account_id=account_id)

        # Absent is not clean. "We have never checked" and "we checked and the
        # books agree" were previously the same answer, which behind the
        # economic gate is a live race: a restarted process with PASSED
        # evidence and a connected broker could read LIVE_ELIGIBLE before any
        # reconciliation had run.
        if snapshot is None:
            log.info("Readiness: no reconciliation snapshot for %s yet", account_id)
            return False

        # Reconciling a different account proves nothing about this one.
        if str(snapshot.account_id) != str(account_id):
            log.warning(
                "Readiness: reconciliation snapshot is for %s, not the bound %s",
                snapshot.account_id, account_id,
            )
            return False

        # A stale snapshot describes a book that may have moved since.
        if not snapshot_is_fresh(snapshot):
            log.info("Readiness: reconciliation snapshot is stale")
            return False

        return snapshot.clean is True
    except Exception as exc:
        log.warning("Readiness: reconciliation state unavailable: %s", exc)
        return False


def _live_book_clean(uid: str) -> bool:
    """Snapback's own live positions agree with the broker. Unknown is not clean."""
    try:
        from app.services.snapback_live_wiring import live_book_clean

        return bool(live_book_clean(uid))
    except Exception as exc:
        log.warning("Readiness: live book state unavailable: %s", exc)
        return False


def readiness_state(uid: str = "default") -> ReadinessState:
    """Derive the current readiness. Unknown never becomes LIVE_ELIGIBLE."""
    reasons: List[str] = []

    system_status = "HALTED"
    broker_connected = False
    unresolved: List[str] = []
    try:
        from app.services.snapback_health import get_prospective_health

        health = get_prospective_health(uid)
        system_status = str(health.get("status") or "HALTED")
        broker_connected = bool(health.get("broker_connected"))
        unresolved = list(health.get("unresolved_errors") or [])
    except Exception as exc:
        reasons.append(f"health_unavailable:{exc}")

    # The verdict is the recorded PromotionService result, not a live re-evaluation.
    verdict = "INCONCLUSIVE"
    try:
        from app.services.snapback_family_ops import get_family_evidence_verdict

        verdict = str(get_family_evidence_verdict().get("verdict") or "INCONCLUSIVE")
    except Exception as exc:
        reasons.append(f"evidence_unavailable:{exc}")

    halted_by_family = False
    try:
        from app.services.snapback_family_ops import new_trades_halted

        halted_by_family = bool(new_trades_halted())
    except Exception as exc:
        reasons.append(f"halt_state_unavailable:{exc}")
        halted_by_family = True

    # Reconciliation is only claimed when the broker's book and Sterling's agree:
    # the bound family account (when one is configured) AND Snapback's own live
    # positions against the broker.
    books_agree = _reconciliation_clean(uid) and _live_book_clean(uid)
    broker_reconciled = broker_connected and books_agree and not any(
        code in unresolved
        for code in ("position_reconciliation_mismatch", "recovery_required",
                     "startup_preflight_failed", "database_unavailable")
    )

    if not books_agree:
        state = RECONCILING
        reasons.append("broker_books_disagree")
        return ReadinessState(
            state=state, system_status=system_status, evidence_verdict=verdict,
            broker_reconciled=False, reasons=reasons,
        )

    if any(code in unresolved for code in ("recovery_required", "position_reconciliation_mismatch")):
        state = RECONCILING
    elif halted_by_family or system_status == "HALTED":
        state = HALTED
    elif system_status == "HEALTHY" and broker_reconciled:
        # Evidence is waived: a healthy, reconciled, un-halted system is eligible.
        state = LIVE_ELIGIBLE
    elif system_status in ("HEALTHY", "DEGRADED"):
        state = DEGRADED
        reasons.append(f"system_{system_status.lower()}")
        if not broker_reconciled:
            reasons.append("broker_not_reconciled")
    else:
        state = OBSERVE

    if verdict != "PASSED":
        # Advisory only: shown so the operator can see what has not been proven.
        reasons.append(f"advisory_evidence_{verdict.lower()}")

    return ReadinessState(
        state=state,
        system_status=system_status,
        evidence_verdict=verdict,
        broker_reconciled=broker_reconciled,
        reasons=reasons,
    )


# --------------------------------------------------------------------------
# Real conditions: what actually stops Snapback opening risk right now
# --------------------------------------------------------------------------

def safety_blockers(uid: str, account_id: str = "default") -> List[str]:
    """The SafetySupervisor's refusal codes for NEW Snapback exposure.

    Safe mode, control-plane recovery state, unresolved journal intents, the kill
    switch, the daily-loss hard halt, foreign broker exposure and the Snapback stop
    switch all arrive here as one list of short codes. Unreadable state is itself
    a blocker, never a pass.
    """
    from app.services.safety_supervisor import SafetySupervisor

    try:
        snap = SafetySupervisor().snapshot(
            uid=uid, account_id=account_id or "default", strategy_id="snapback")
    except Exception as exc:  # noqa: BLE001
        return [f"safety_unreadable:{type(exc).__name__}"]
    return list(snap.blockers)


def gate_blockers(uid: str = "default") -> List[str]:
    """The retained family-gate conditions: system health and broker reconciliation.

    (Risk approval is request-bound and decided per order, so it is not here.)
    """
    state = readiness_state(uid)
    out: List[str] = []
    if str(state.system_status).upper() != "HEALTHY":
        out.append("system_not_healthy")
    if not state.broker_reconciled:
        out.append("broker_not_reconciled")
    return out


@dataclass
class LiveConditions:
    """Whether Snapback may open new risk, and exactly what stops it when it may not.

    Three groups, because they mean different things to an operator:

    * ``blockers``   real faults or switches (kill switch, safe mode, recovery state,
                     unresolved orders, daily-loss halt, foreign exposure, an unhealthy
                     or unreconciled system, a lane that may not originate, no broker);
    * ``session``    the clock (market closed, entry cutoff) -- not a fault;
    * ``auto_off``   unattended entry is not switched on (a click is unaffected).
    """

    blockers: List[str] = field(default_factory=list)
    session: List[str] = field(default_factory=list)
    auto_off: bool = False

    @property
    def live_eligible(self) -> bool:
        """The system could trade live: nothing is faulted or switched off."""
        return not self.blockers

    @property
    def manual_eligible_now(self) -> bool:
        """A click would be accepted this minute."""
        return not self.blockers and not self.session

    @property
    def auto_available(self) -> bool:
        """AUTO could place entries (it may or may not be switched on yet)."""
        return not self.blockers

    def reason(self) -> str:
        every = [*self.blockers, *self.session]
        return ", ".join(every[:4]) if every else ""

    def as_dict(self) -> Dict[str, Any]:
        return {"blockers": list(self.blockers), "session": list(self.session),
                "auto_off": self.auto_off, "live_eligible": self.live_eligible,
                "manual_eligible_now": self.manual_eligible_now,
                "auto_available": self.auto_available}


_CONDITIONS_TTL_S = 3.0
_conditions_cache: Dict[Any, Any] = {}


def live_conditions(uid: str, cfg=None, *, account_id: str = "") -> LiveConditions:
    """The single answer behind ``is_live_eligible``, ``execution_eligible`` and
    ``auto_execution.available``. Reads durable state only; never the broker.

    Cached for a few seconds: the board polls this, and it reads the safety stores,
    the health probe and the reconciliation state.
    """
    import time

    from app.services import snapback_dispatcher as dispatcher
    from app.services.snapback import get_config

    cfg = cfg or get_config(uid)
    key = (uid, account_id, id(cfg))
    hit = _conditions_cache.get(key)
    if hit and time.monotonic() - hit[0] < _CONDITIONS_TTL_S:
        return hit[1]
    every = dispatcher.origination_blockers(uid, cfg, account_id=account_id, auto=True)
    out = LiveConditions(
        blockers=[b for b in every if b != "auto_off" and not b.startswith(("session_", "entry_"))],
        session=[b for b in every if b.startswith(("session_", "entry_"))],
        auto_off="auto_off" in every,
    )
    _conditions_cache.clear()
    _conditions_cache[key] = (time.monotonic(), out)
    return out
