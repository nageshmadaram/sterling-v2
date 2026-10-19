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
    #: The live path's own fault codes (broker_disconnected, dispatcher_stale, ...), so a
    #: blocker can say WHICH condition failed instead of a generic "not healthy".
    live_errors: List[str] = field(default_factory=list)
    broker_connected: bool = False


def _reconciliation_clean(uid: str = "default") -> bool:
    """Whether the last reconciliation found the two books in agreement.

    Unknown is never clean: if reconciliation cannot run, exposure stays blocked.
    """
    try:
        from app.services.simulation.identity import is_sim_uid

        if is_sim_uid(uid):
            # The family binding names a REAL broker account; a virtual tenant has no bound book to compare.
            # Its own Snapback positions are checked against its virtual book by ``_live_book_clean``.
            return True
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

        from app.services.snapback_health import live_health

        health = get_prospective_health(uid)
        # Live trading is judged on the live path's own health: the prospective
        # evidence runner is optional and must not gate real entries.
        live = live_health(health)
        system_status = str(live.get("status") or "HALTED")
        broker_connected = bool(health.get("broker_connected"))
        unresolved = list(live.get("errors") or [])
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
            live_errors=list(unresolved), broker_connected=broker_connected,
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
        live_errors=list(unresolved),
        broker_connected=broker_connected,
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


#: Live-health fault -> the blocker code that tells the operator what to do.
_LIVE_FAULT_BLOCKERS: Dict[str, str] = {
    "broker_disconnected": "no_broker_session",
    "dispatcher_stale": "dispatcher_not_running",
    "clock_unsynchronised": "clock_unsynchronised",
    "clock_unreadable": "clock_unsynchronised",
    "disk_low": "disk_low",
    "disk_unreadable": "disk_low",
    "database_unavailable": "database_unavailable",
    "calendar_unavailable": "calendar_unavailable",
    "position_reconciliation_mismatch": "broker_not_reconciled",
}


def gate_blockers(uid: str = "default") -> List[str]:
    """The retained family-gate conditions: system health and broker reconciliation.

    Each fault is reported as its own cause (not logged in to Kite, live loop not running,
    clock, disk, ...). The generic ``system_not_healthy`` / ``broker_not_reconciled`` codes
    remain only for a fault this map does not know, or when the two books really disagree.

    (Risk approval is request-bound and decided per order, so it is not here.)
    """
    state = readiness_state(uid)
    out: List[str] = []

    def add(code: str) -> None:
        if code not in out:
            out.append(code)

    unhealthy = str(state.system_status).upper() != "HEALTHY"
    if unhealthy:
        specific = [_LIVE_FAULT_BLOCKERS[e] for e in state.live_errors if e in _LIVE_FAULT_BLOCKERS]
        for code in specific:
            add(code)
        if not specific:
            add("system_not_healthy")
    if not state.broker_reconciled:
        if not state.broker_connected:
            add("no_broker_session")      # the cause; "books disagree" would mislead
        elif "broker_not_reconciled" not in out:
            add("broker_not_reconciled")
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


#: What each short reason code means to an operator.
REASON_TEXT: Dict[str, str] = {
    "engine_disabled": "Snapback is switched off.",
    "auto_off": "Automatic entry is not switched on (per-strategy AUTO and the shared master AUTO).",
    "kill_switch": "New entries are halted by the operator kill switch (STERLING_LIVE_EXECUTION).",
    "safe_mode": "Safe mode is engaged.",
    "recovery_required": "An order outcome is unresolved; broker recovery must finish first.",
    "daily_loss_halt": "The daily-loss limit has been reached.",
    "new_trades_halted": "The Snapback stop switch is engaged.",
    "control_plane_unreadable": "The execution control state could not be read.",
    "system_not_healthy": "The Snapback live loop is not reporting healthy (see the Operations screen for the fault).",
    "broker_not_reconciled": "Snapback's positions and the broker's do not agree yet; reconciliation is running.",
    "no_broker_session": "Not logged in to Kite. Log in to the broker, then Snapback can trade.",
    "clock_unsynchronised": "This computer's clock is not synchronised; fix the system time before trading.",
    "disk_low": "The disk is nearly full; free space before trading.",
    "database_unavailable": "The Sterling database cannot be read or written.",
    "calendar_unavailable": "The exchange calendar could not be read.",
    "lane_not_supported": "This trading mode is not a live lane (use swing or scalp).",
    "spreads_not_supported_live": "Spread entries are not supported live (short_leg_delta must be 0).",
    "exit_mode_not_supported_live": "Only the horizon exit mode is supported live.",
    "premium_trail_not_supported_live": "The premium trail is not supported live.",
    "regime_flip_exit_not_supported_live": "The regime-flip exit is not supported live.",
    "broker_stop_required": "A live position needs a broker-side stop; stop mode cannot be monitor-only.",
    "session_closed": "The market is closed.",
    "cash_signal_auction": "The cash market is in its closing auction; new entries resume in the next continuous session.",
    "entry_close_buffer": "Too close to the close to open a new position.",
    "row_not_armed": "The signal is not armed.",
    "row_stale": "The signal's quote is stale; rescan.",
    "no_live_quote": "No live quote for the contract.",
    "contract_unresolved": "No listed contract was resolved for the signal.",
    "signal_not_for_today": "The swing signal's entry session is not today.",
    "signal_stale": "The signal is older than its bar.",
    "outside_swing_entry_window": "Outside the swing entry window.",
    "outside_scalp_entry_window": "Outside the scalp entry window.",
    "conditions_unreadable": "Live conditions could not be read.",
    "hedge_opposes_open_hedge": "This hedge would net against an open Snapback hedge on the same futures contract.",
    "hedge_incomplete": "An entry is still waiting for its futures hedge; no new position is opened until it fills.",
    "max_open_positions": "The open-position cap is reached.",
    "underlying_already_open": "This underlying already has an open or pending Snapback position.",
    "dispatcher_not_running": "The Snapback live loop is not running, so a new position would not be managed.",
}


def reason_text(code: str) -> str:
    text = REASON_TEXT.get(str(code))
    if text:
        return text
    if str(code).startswith("lane_"):
        return f"The lane may not originate ({str(code)[5:].lower().replace('_', ' ')})."
    return str(code)


def row_execution_state(row: Dict[str, Any], cfg: Any, conditions: "LiveConditions | None",
                        now: Any = None, *, manual: bool = True) -> "tuple[bool, List[str]]":
    """Could a click on this row be accepted right now, and if not, why not?

    One function decides it for the board and for the plan producer, so a row
    never advertises what the producer would refuse.
    """
    from app.services.snapback_plan_producer import (
        freshness_reasons, unsupported_config_reasons,
    )

    reasons: List[str] = []
    if conditions is None:
        reasons.append("conditions_unreadable")
    else:
        reasons.extend(conditions.blockers)
        if not manual:
            reasons.extend(conditions.session)
    reasons.extend(unsupported_config_reasons(cfg))
    reasons.extend(freshness_reasons(row, cfg, now, manual=manual))
    seen: List[str] = []
    for code in reasons:
        if code not in seen:
            seen.append(code)
    return (not seen, seen)


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
    from app.services.snapback import get_effective_config as get_config

    cfg = cfg or get_config(uid)
    key = (uid, account_id, bool(cfg.enabled), bool(cfg.auto_execute), str(cfg.trading_mode),
           float(getattr(cfg, "short_leg_delta", 0.0)), str(getattr(cfg, "stop_mode", "")))
    hit = _conditions_cache.get(key)
    if hit and time.monotonic() - hit[0] < _CONDITIONS_TTL_S:
        return hit[1]
    every = dispatcher.origination_blockers(uid, cfg, account_id=account_id, auto=True)
    out = LiveConditions(
        blockers=[b for b in every if b != "auto_off" and not b.startswith(("session_", "entry_", "cash_signal_"))],
        session=[b for b in every if b.startswith(("session_", "entry_", "cash_signal_"))],
        auto_off="auto_off" in every,
    )
    _conditions_cache.clear()
    _conditions_cache[key] = (time.monotonic(), out)
    return out
