"""Operational alerts for the frozen Snapback prospective runtime.

Alerts cover actionable operational failures only: the system cannot observe, cannot
persist, cannot reconcile, or has halted. Ordinary signals and ordinary losing trades
are never alerted — economic P&L is deliberately not an input here, because alert
fatigue is what makes people ignore the alerts that matter.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Protocol

log = logging.getLogger(__name__)

CRITICAL = "CRITICAL"
WARNING = "WARNING"

DEFAULT_REPEAT_AFTER = timedelta(hours=4)

_IST = timezone(timedelta(hours=5, minutes=30))


@dataclass(frozen=True)
class OperationalAlert:
    code: str
    severity: str
    title: str
    message: str
    #: 0 for the first notification of a fault in this process; >0 for a reminder
    #: or for the same fault returning after it had recovered. The durable outbox
    #: keys on it so a recurrence is not swallowed by an earlier delivered alert.
    occurrence: int = 0


@dataclass
class DispatchResult:
    sent: int = 0
    suppressed: int = 0
    failed: int = 0
    codes_sent: List[str] = field(default_factory=list)


class AlertSink(Protocol):
    def send(self, alert: OperationalAlert) -> Any:  # pragma: no cover - protocol
        ...


def derive_operational_alerts(
    *,
    health: Dict[str, Any],
    backup_ok: bool,
    report_ok: bool = True,
    exit_pending_stale: bool = False,
    processing_entry_stale: bool = False,
    session_evidence_incomplete: bool = False,
    session_evidence_gaps: Optional[List[str]] = None,
    daily_loss_breached: bool = False,
    drawdown_breached: bool = False,
    reconciliation_mismatch: bool = False,
) -> List[OperationalAlert]:
    """Map observed operational state to actionable alerts."""
    alerts: List[OperationalAlert] = []
    health = health or {}
    errors = set(health.get("unresolved_errors") or [])
    # The prospective evidence runner is optional for the live release. When it was
    # deliberately not started, its absence is not an operational fault.
    evidence_runner_off = "prospective_runner_unavailable" in errors

    def add(code: str, severity: str, title: str, message: str) -> None:
        alerts.append(
            OperationalAlert(code=code, severity=severity, title=title, message=message)
        )

    if not health.get("database_ok", True) or "database_unavailable" in errors:
        add(
            "database_unavailable",
            CRITICAL,
            "Sterling database unavailable",
            "The Snapback evidence database cannot be read or written. Evidence collection has stopped.",
        )

    if not evidence_runner_off and (not health.get("manifest_ok", True) or "manifest_mismatch" in errors):
        add(
            "manifest_mismatch",
            CRITICAL,
            "Frozen strategy manifest mismatch",
            "The running configuration does not match the frozen Snapback manifest. Stop and investigate before trusting any new evidence.",
        )

    if not evidence_runner_off and (not health.get("runner_alive", True) or "runner_stale" in errors):
        add(
            "runner_stopped",
            CRITICAL,
            "Prospective runner stopped",
            "The background runner has missed multiple cycles. Entries, risk checks and end-of-day processing are not running.",
        )

    # A weekend or exchange holiday needs no broker session; `trading_day` is None
    # when the calendar could not answer, and an unknown calendar never silences this.
    broker_needed = health.get("trading_day") is not False
    if broker_needed and (
        not health.get("broker_connected", True) or "broker_disconnected" in errors
    ):
        add(
            "broker_disconnected",
            WARNING,
            "Broker login required",
            "Broker disconnected. Log in to restore market data.",
        )

    # Stale data is only a fault while the market is open; a quiet evening is not one.
    market_open = bool(health.get("market_open", True))
    if "market_data_stale" in errors or (
        not health.get("market_data_fresh", True) and market_open
    ):
        add(
            "market_data_stale",
            WARNING,
            "Market data stale",
            "No fresh executable quote has been observed. Sterling fails closed and will not act on stale data.",
        )

    if not health.get("calendar_ok", True) or "calendar_unavailable" in errors:
        add(
            "calendar_failure",
            CRITICAL,
            "Trading calendar unavailable",
            "The NSE trading calendar could not be verified. Sterling fails closed until it is available.",
        )

    if reconciliation_mismatch or "position_reconciliation_mismatch" in errors:
        add(
            "position_reconciliation_mismatch",
            CRITICAL,
            "Position reconciliation mismatch",
            "Recorded positions do not match the ledger. Do not increase exposure until this is resolved.",
        )

    if exit_pending_stale:
        count = health.get("exit_pending")
        subject = (
            f"{count} position(s) latched for exit have"
            if count else "A position latched for exit has"
        )
        add(
            "exit_pending_unresolved",
            CRITICAL,
            "Exit pending unresolved",
            f"{subject} not been liquidated. Liquidation evidence is missing.",
        )

    if processing_entry_stale:
        count = health.get("processing_entries")
        subject = f"{count} entry lease(s) have" if count else "An entry lease has"
        add(
            "pending_entry_stuck",
            CRITICAL,
            "Pending entry stuck",
            f"{subject} not completed. An entry may be half-processed.",
        )

    if health.get("stop_monitor_gap"):
        add(
            "stop_observation_gap",
            CRITICAL,
            "Stop monitoring gap",
            "An open position is not being observed, so a stop breach could pass "
            "unseen. New entries are blocked until observation resumes.",
        )

    if not backup_ok:
        add(
            "backup_failed",
            CRITICAL,
            "Evidence backup failed",
            "The daily backup of the prospective evidence database did not complete. The evidence is currently unprotected.",
        )

    if not report_ok:
        add(
            "evidence_report_failed",
            CRITICAL,
            "Evidence report failed",
            "The daily Snapback prospective evidence report could not be generated. Do not treat today's evidence package as complete.",
        )

    if session_evidence_incomplete:
        gaps = ", ".join(session_evidence_gaps or []) or "phase status incomplete"
        add(
            "session_evidence_incomplete",
            CRITICAL,
            "Session evidence incomplete",
            f"The trading session cannot be packaged because its market evidence is "
            f"incomplete: {gaps}. This is not a quiet market; something did not observe.",
        )

    if daily_loss_breached:
        add(
            "daily_loss_breaker",
            CRITICAL,
            "Daily loss breaker hit",
            "The session loss limit was breached. New exposure is blocked.",
        )

    if drawdown_breached:
        add(
            "drawdown_breaker",
            CRITICAL,
            "Drawdown breaker hit",
            "The drawdown limit was breached. The system is halted for new exposure.",
        )

    from app.services.snapback_health import EVIDENCE_ONLY_ERRORS

    if str(health.get("status")) == "HALTED" and (
        not evidence_runner_off or any(e not in EVIDENCE_ONLY_ERRORS for e in errors)
    ):
        add(
            "system_halted",
            CRITICAL,
            "Sterling halted",
            "Sterling is HALTED. No new trades will be taken until the underlying fault is cleared.",
        )

    # Preserve order, keep the first occurrence of each code.
    deduped: List[OperationalAlert] = []
    seen = set()
    for alert in alerts:
        if alert.code in seen:
            continue
        seen.add(alert.code)
        deduped.append(alert)
    return deduped


class AlertDispatcher:
    """Send alerts once per fault episode, re-sending only after repeat_after.

    An alert that disappears from a later poll is treated as recovered, so the same
    fault occurring again is a new episode and alerts again.

    Every send after the first for a code carries a higher ``occurrence``. A durable
    sink uses it to tell "the same notification, already queued" from "a reminder or
    a recurrence": without it, the second episode of a fault in one session was
    dropped as a duplicate of the first while the dispatcher counted it as sent.
    """

    def __init__(
        self,
        *,
        sink: AlertSink,
        repeat_after: timedelta = DEFAULT_REPEAT_AFTER,
    ) -> None:
        self.sink = sink
        self.repeat_after = repeat_after
        self._last_sent: Dict[str, datetime] = {}
        # Notifications actually accepted per code, kept across recoveries.
        self._occurrences: Dict[str, int] = {}

    def dispatch(
        self,
        alerts: Iterable[OperationalAlert],
        *,
        now: Optional[datetime] = None,
    ) -> DispatchResult:
        now = now or datetime.now(timezone.utc)
        alerts = list(alerts or [])
        active_codes = {a.code for a in alerts}
        result = DispatchResult()

        # Codes no longer present have recovered; forget them.
        for code in list(self._last_sent):
            if code not in active_codes:
                self._last_sent.pop(code, None)

        for alert in alerts:
            last = self._last_sent.get(alert.code)
            if last is not None and (now - last) < self.repeat_after:
                result.suppressed += 1
                continue
            occurrence = self._occurrences.get(alert.code, 0)
            outgoing = alert
            if occurrence:
                try:
                    outgoing = replace(alert, occurrence=occurrence)
                except TypeError:  # not a dataclass alert; send it unchanged
                    outgoing = alert
            try:
                self.sink.send(outgoing)
            except Exception as exc:
                # A broken transport must never stop monitoring. The alert is NOT
                # remembered as sent, so the next poll tries again.
                log.warning("Snapback alert transport failed for %s: %s", alert.code, exc)
                result.failed += 1
                continue
            self._last_sent[alert.code] = now
            self._occurrences[alert.code] = occurrence + 1
            result.sent += 1
            result.codes_sent.append(alert.code)

        return result


class OutboxAlertSink:
    """Persist the alert for durable delivery instead of firing and forgetting.

    The dispatcher's job ends at "this fault is recorded"; whether a human received
    it is the outbox worker's fact to establish.

    When the outbox itself cannot be written (the evidence database is the usual
    reason) a CRITICAL alert is attempted directly over Telegram once, and the
    failure is raised if even that cannot go out, so the dispatcher retries instead
    of recording a notification nobody can ever receive.
    """

    def __init__(self, outbox=None, *, trading_session: Optional[str] = None,
                 direct_fallback=None) -> None:
        self._outbox = outbox
        self.trading_session = trading_session
        self._direct_fallback = direct_fallback

    @property
    def outbox(self):
        if self._outbox is None:
            from app.services.snapback_alert_outbox import AlertOutbox

            self._outbox = AlertOutbox()
        return self._outbox

    def _fallback(self, alert: Any) -> bool:
        if self._direct_fallback is not None:
            return bool(self._direct_fallback(alert))
        from app.services.snapback_alert_telegram import TelegramAlertSink

        return TelegramAlertSink().send_now(alert)

    def send(self, alert: Any) -> None:
        session = self.trading_session or datetime.now(_IST).date().isoformat()
        try:
            self.outbox.enqueue(
                alert,
                trading_session=session,
                occurrence=int(getattr(alert, "occurrence", 0) or 0),
            )
        except Exception as exc:
            if str(getattr(alert, "severity", "")).upper() == CRITICAL:
                try:
                    if self._fallback(alert):
                        log.error(
                            "Snapback alert outbox unavailable (%s); %s sent directly",
                            exc, getattr(alert, "code", ""),
                        )
                        return
                except Exception as fallback_exc:  # noqa: BLE001
                    log.error("Snapback direct alert fallback failed: %s", fallback_exc)
            raise


class LoggingAlertSink:
    """Default sink: write alerts to the application log."""

    def send(self, alert: OperationalAlert) -> None:
        level = logging.ERROR if alert.severity == CRITICAL else logging.WARNING
        log.log(level, "[SNAPBACK ALERT][%s] %s — %s", alert.severity, alert.title, alert.message)
