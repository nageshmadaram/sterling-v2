"""Platform-level operator alerts delivered through the durable lifecycle outbox.

Strategy engines alert their own position lifecycle.  The events here cut across
all three strategies and used to reach nobody:

* control plane: kill switch engaged/released, trading halted/resumed,
  RECOVERY_REQUIRED (unknown-outcome order, protection uncertainty, startup),
* SAFE_MODE entered/released,
* daily-loss circuit breaker halt,
* broker session expired / login required,
* every order submission outcome (entry/exit submitted, outcome unknown,
  rejected or blocked) for any strategy,
* service start (with the live-order switch state).

Every function is best effort and never raises: a notification problem must not
change a trading decision.  Rows are written to the same SQLite outbox the trade
lifecycle uses, so an alert raised by the CLI (``sterlingctl``) or while Telegram
is down is still delivered by the application's drain loop later.
"""
from __future__ import annotations

import hashlib
import logging
import os
import time
from collections import OrderedDict
from datetime import datetime
from typing import Iterable, Optional

from app.services.notifications import lifecycle_format as lf

log = logging.getLogger(__name__)

SYSTEM = "Sterling"
_SEEN_MAX = 4096
_seen: "OrderedDict[str, None]" = OrderedDict()


def _suppressed_for_tests() -> bool:
    """Unit tests must not enqueue into a real database that a live app drains."""
    return bool(os.environ.get("PYTEST_CURRENT_TEST")) and not os.environ.get(
        "STERLING_ALERTS_IN_TESTS"
    )


def _tenants() -> list[str]:
    users = {"default"}
    try:
        from app.services.exchanges.kite import accounts as kite_accounts

        users.update(
            str(a.user_id) for a in kite_accounts.all_accounts()
            if getattr(a, "is_active", False) and getattr(a, "user_id", "")
        )
    except Exception as exc:  # noqa: BLE001
        log.debug("system alert tenant discovery unavailable: %s", exc)
    return sorted(users)


def _emit(
    uids: Iterable[str],
    *,
    key: str,
    event_type: str,
    title: str,
    detail: str,
    severity: str = "INFO",
    strategy: str = SYSTEM,
    created_at_ms: Optional[int] = None,
) -> int:
    """Enqueue one alert for each uid; per-process memo avoids repeat DB writes."""
    from app.services.notifications import telegram_lifecycle

    written = 0
    for uid in uids:
        memo = f"{uid}\0{key}"
        if memo in _seen:
            continue
        _seen[memo] = None
        while len(_seen) > _SEEN_MAX:
            _seen.popitem(last=False)
        try:
            if telegram_lifecycle.enqueue(
                uid,
                dedupe_key=key,
                strategy=strategy,
                event_type=event_type,
                title=title,
                detail=detail,
                severity=severity,
                created_at_ms=created_at_ms,
            ):
                written += 1
        except Exception as exc:  # noqa: BLE001
            # Not persisted: forget the memo so the next occurrence can retry.
            _seen.pop(memo, None)
            log.warning(
                "system alert could not be persisted [%s]: %s", event_type, type(exc).__name__
            )
    return written


def _digest(text: str) -> str:
    return hashlib.sha1(str(text).encode("utf-8", "replace")).hexdigest()[:8]


def reset_for_tests() -> None:
    _seen.clear()


def _ist_day(ms: Optional[int] = None) -> str:
    return datetime.fromtimestamp((ms or time.time() * 1000) / 1000, lf.IST).strftime("%Y-%m-%d")


# ── control plane (kill switch / halts / recovery) ───────────────────────────
def control_plane_changed(prev: Optional[dict], new: dict) -> None:
    """Alert on an operator-state or recovery-state *transition* (not every write)."""
    try:
        scope = str(new.get("scope") or "global")
        owner = str(new.get("uid") or "default")
        account = str(new.get("account_id") or "default")
        revision = int(new.get("revision") or 0)
        reason_code = str(new.get("reason_code") or "")
        reason = str(new.get("reason") or "").strip()
        uids = _tenants() if scope == "global" else [owner]
        where = "" if scope == "global" else f"{scope} {account if account != 'default' else owner}"

        prev_op = (prev or {}).get("operator_state") or "RUNNING"
        prev_rec = (prev or {}).get("recovery_state")
        new_op = str(new.get("operator_state") or "RUNNING")
        new_rec = str(new.get("recovery_state") or "CLEAN")

        if new_op != prev_op:
            if new_op == "HALTED":
                kill = reason_code in {"kill_switch", ""} or "kill" in reason.lower()
                _emit(
                    uids,
                    key=f"control:{scope}:{owner}:{account}:{revision}:halt",
                    event_type="KILL SWITCH ENGAGED" if kill else "TRADING HALTED",
                    title="ALL STRATEGIES",
                    detail=lf.detail(notes=[
                        "New entries blocked; exits and protection continue",
                        reason or "manual halt", where,
                    ]),
                    severity="CRITICAL",
                    created_at_ms=int(new.get("updated_ms") or 0) or None,
                )
            else:
                _emit(
                    uids,
                    key=f"control:{scope}:{owner}:{account}:{revision}:resume",
                    event_type="TRADING RESUMED",
                    title="ALL STRATEGIES",
                    detail=lf.detail(notes=[reason or "operator released the halt", where]),
                    severity="INFO",
                    created_at_ms=int(new.get("updated_ms") or 0) or None,
                )

        boot_marker = (
            reason_code.upper() == "STARTUP"
            or str((prev or {}).get("reason_code") or "").upper() == "STARTUP"
        )
        if new_rec != prev_rec and not (prev_rec is None and new_rec == "CLEAN") and not boot_marker:
            if new_rec == "RECOVERY_REQUIRED":
                _emit(
                    uids,
                    key=f"control:{scope}:{owner}:{account}:{revision}:recovery",
                    event_type="RECOVERY REQUIRED",
                    title="ALL STRATEGIES",
                    detail=lf.detail(notes=[
                        "New entries blocked until broker reconciliation completes",
                        reason_code or "", reason, where,
                    ]),
                    severity="CRITICAL",
                    created_at_ms=int(new.get("updated_ms") or 0) or None,
                )
            else:
                _emit(
                    uids,
                    key=f"control:{scope}:{owner}:{account}:{revision}:clean",
                    event_type="RECOVERY CLEAN",
                    title="ALL STRATEGIES",
                    detail=lf.detail(notes=["Broker reconciled; new entries allowed", where]),
                    severity="INFO",
                    created_at_ms=int(new.get("updated_ms") or 0) or None,
                )
    except Exception as exc:  # noqa: BLE001
        log.warning("control-plane alert skipped: %s", type(exc).__name__)


def control_plane_changed_from_db(prev: Optional[dict], new: dict) -> None:
    """Hook called by ``db.set_execution_control`` after its transaction commits."""
    if _suppressed_for_tests():
        return
    control_plane_changed(prev, new)


# ── SAFE_MODE ────────────────────────────────────────────────────────────────
def safe_mode_engaged(*, triggers: Iterable[str], reason: str, entered_at: str = "") -> None:
    if _suppressed_for_tests():
        return
    try:
        names = ", ".join(sorted(str(t) for t in triggers))
        _emit(
            _tenants(),
            key=f"safe-mode:{entered_at}:{names}",
            event_type="SAFE MODE ENGAGED",
            title="ALL STRATEGIES",
            detail=lf.detail(notes=[
                "New entries blocked; exits and protection continue",
                names, reason,
            ]),
            severity="CRITICAL",
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("safe-mode alert skipped: %s", type(exc).__name__)


def safe_mode_released(*, note: str = "") -> None:
    if _suppressed_for_tests():
        return
    try:
        _emit(
            _tenants(),
            key=f"safe-mode-released:{int(time.time() * 1000)}",
            event_type="SAFE MODE RELEASED",
            title="ALL STRATEGIES",
            detail=lf.detail(notes=[note or "released by operator"]),
            severity="INFO",
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("safe-mode release alert skipped: %s", type(exc).__name__)


# ── daily loss ───────────────────────────────────────────────────────────────
def daily_loss_halt(uid: str, pnl_inr: float, hard_halt_inr: float) -> None:
    """Once per account per IST day: the circuit breaker has blocked new entries."""
    if _suppressed_for_tests():
        return
    try:
        _emit(
            [uid or "default"],
            key=f"daily-loss-halt:{uid or 'default'}:{_ist_day()}",
            event_type="DAILY LOSS HALT",
            title="ALL STRATEGIES",
            detail=lf.detail(
                pnl=pnl_inr,
                notes=[f"Limit {lf.inr(hard_halt_inr)}", "New entries blocked for the rest of the day"],
            ),
            severity="CRITICAL",
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("daily-loss alert skipped: %s", type(exc).__name__)


# ── broker session ───────────────────────────────────────────────────────────
def broker_session_lost(uid: str, account_id: str, reason: str) -> None:
    """The Kite session is gone: no orders, no stops, no ticks until someone logs in."""
    if _suppressed_for_tests():
        return
    try:
        now = int(time.time() * 1000)
        _emit(
            [uid or "default"],
            # One alert per account per 30-minute window; a flapping session must
            # not turn the chat into a siren, but every real outage is reported.
            key=f"broker-session:{account_id}:{now // 1_800_000}",
            event_type="BROKER LOGIN REQUIRED",
            title=f"Kite {account_id}" if account_id else "Kite",
            detail=lf.detail(notes=[
                "Session expired or rejected; log in to Kite to resume trading",
                reason,
            ]),
            severity="CRITICAL",
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("broker-session alert skipped: %s", type(exc).__name__)


def check_broker_sessions(now: Optional[datetime] = None) -> int:
    """During the trading window, alert when a live account has no usable Kite session.

    Kite access tokens die at ~06:00 IST daily; unless a refresh token renews
    them, nobody can trade until a human logs in.  Checked from the Telegram drain
    loop (every cycle) so the warning arrives before the open, not after the first
    missed signal.  One alert per account per hour while the condition persists.
    """
    if _suppressed_for_tests():
        return 0
    try:
        local = (now or datetime.now(lf.IST)).astimezone(lf.IST)
        if not (8 * 60 + 45 <= local.hour * 60 + local.minute <= 15 * 60 + 40):
            return 0
        try:
            from app.services.kite_engine import market_hours

            if not market_hours.is_trading_day(local.date()):
                return 0
        except Exception:  # noqa: BLE001 - unknown calendar: weekday only
            if local.weekday() >= 5:
                return 0
        from app.services.exchanges.kite import accounts as kite_accounts
        from app.services.exchanges.kite import session as kite_session

        raised = 0
        for acct in kite_accounts.all_accounts():
            if not getattr(acct, "is_active", False) or getattr(acct, "is_paper", False):
                continue
            usable = bool(acct.access_token_enc) and not kite_session.is_expired(
                acct.token_expires_at
            )
            if usable:
                continue
            key_hour = int(local.timestamp() // 3600)
            raised += _emit(
                [acct.user_id],
                key=f"broker-session-missing:{acct.id}:{key_hour}",
                event_type="BROKER LOGIN REQUIRED",
                title=f"Kite {acct.id}",
                detail=lf.detail(notes=[
                    "No valid Kite session; orders, stops and live prices are unavailable",
                    "Log in to Kite (tokens reset ~06:00 IST)",
                ]),
                severity="CRITICAL",
            )
        return raised
    except Exception as exc:  # noqa: BLE001
        log.warning("broker-session check skipped: %s", type(exc).__name__)
        return 0


# ── order submissions (all strategies) ───────────────────────────────────────
_OK_STATUSES = {"ACKNOWLEDGED", "SUBMITTED", "FILLED", "PARTIAL"}


def order_submission(request, result) -> None:
    """One alert per order outcome from the canonical execution service."""
    if _suppressed_for_tests():
        return
    try:
        effect = str(getattr(getattr(request, "exposure_effect", ""), "value", request.exposure_effect))
        increasing = effect == "INCREASE_EXPOSURE"
        strategy = getattr(request, "strategy_id", "")
        symbol = str(getattr(request, "symbol", ""))
        status = str(getattr(result, "status", "") or "").upper()
        error = str(getattr(result, "error", "") or "")
        intent = str(getattr(result, "intent_key", "") or "")
        signal = str(getattr(request, "signal_id", "") or "")
        qty = int(getattr(request, "quantity", 0) or 0)
        side_note = (
            [f"Side {str(request.side).upper()}"]
            if lf.instrument_kind(symbol) not in {"CE", "PE"} else []
        )
        uid = str(getattr(request, "uid", "") or "default")
        base = {
            "uid": [uid], "strategy": lf.strategy_label(strategy), "title": symbol,
        }

        if getattr(result, "success", False) and status in _OK_STATUSES:
            order_id = str(getattr(result, "order_id", "") or "")
            fill = float(getattr(result, "filled_price", 0.0) or 0.0)
            _emit(
                base["uid"], strategy=base["strategy"], title=symbol,
                key=f"order:{intent or signal}:{status}",
                event_type="ENTRY SUBMITTED" if increasing else "EXIT SUBMITTED",
                detail=lf.detail(
                    entry=fill if increasing and fill > 0 else None,
                    exit=fill if not increasing and fill > 0 else None,
                    qty=qty, notes=side_note + ([f"Order {order_id}"] if order_id else []),
                ),
                severity="INFO",
            )
        elif status == "UNKNOWN":
            _emit(
                base["uid"], strategy=base["strategy"], title=symbol,
                key=f"order-unknown:{intent or signal}",
                event_type="ORDER OUTCOME UNKNOWN",
                detail=lf.detail(qty=qty, notes=side_note + [
                    "Broker acknowledgement not received; reconcile before retry; new entries halted",
                    error,
                ]),
                severity="CRITICAL",
            )
        elif increasing:
            _emit(
                base["uid"], strategy=base["strategy"], title=symbol,
                key=f"order-blocked:{signal or intent}:{status}:{_digest(error)}",
                event_type="ENTRY REJECTED" if intent else "ENTRY BLOCKED",
                detail=lf.detail(qty=qty, notes=side_note + [status, error]),
                severity="WARNING",
            )
        else:
            _emit(
                base["uid"], strategy=base["strategy"], title=symbol,
                key=f"order-exit-failed:{intent or signal}:{status}:{_digest(error)}",
                event_type="EXIT NOT PLACED",
                detail=lf.detail(qty=qty, notes=side_note + [status, error]),
                severity="CRITICAL",
            )
    except Exception as exc:  # noqa: BLE001
        log.warning("order alert skipped: %s", type(exc).__name__)


# ── broker fill evidence (journal level, strategy-agnostic) ──────────────────
_EXIT_EFFECTS = {"CLOSE_POSITION", "REDUCE_EXPOSURE"}


def order_observed(observation) -> None:
    """Alert on broker fill evidence recorded in the canonical order journal.

    SuperTrend and Gamma Unwind project their own fills and alert from their own
    position stores, so accepted fills are announced here only for Snapback, whose
    position state is owned elsewhere.  Evidence the journal had to quarantine is
    announced for every strategy: it halts new entries until reconciled.
    """
    if _suppressed_for_tests():
        return
    try:
        intent = observation.intent
        strategy = lf.strategy_label(intent.strategy_id)
        payload = dict(intent.payload or {})
        is_exit = (
            payload.get("intent_type") == "EXIT"
            or payload.get("exposure_effect") in _EXIT_EFFECTS
        )
        uid = [intent.uid or "default"]
        filled = int(intent.filled_quantity or 0)
        avg = (float(intent.filled_value) / filled) if filled > 0 else 0.0
        side_note = (
            [f"Side {str(intent.side).upper()}"]
            if lf.instrument_kind(intent.symbol) not in {"CE", "PE"} else []
        )

        if observation.accepted:
            if strategy == "Snapback" and int(observation.delta_quantity or 0) > 0:
                complete = intent.state == "FILLED" and filled >= int(intent.quantity)
                event = (
                    ("EXIT FILLED" if complete else "PARTIAL EXIT")
                    if is_exit
                    else ("ENTRY FILLED" if complete else "PARTIAL ENTRY FILL")
                )
                _emit(
                    uid, strategy=strategy, title=intent.symbol,
                    key=f"snapback:fill:{intent.intent_key}:{filled}:{avg:.2f}",
                    event_type=event,
                    detail=lf.detail(
                        entry=None if is_exit else avg,
                        exit=avg if is_exit else None,
                        qty=filled, qty_total=intent.quantity, notes=side_note,
                    ),
                    severity="INFO",
                )
            elif filled == 0 and intent.state in {"REJECTED", "CANCELLED"} and (
                not is_exit or strategy == "Snapback"
            ):
                _emit(
                    uid, strategy=strategy, title=intent.symbol,
                    key=f"order-final:{intent.intent_key}:{intent.state}",
                    event_type=(
                        ("EXIT " if is_exit else "ENTRY ") + intent.state
                    ),
                    detail=lf.detail(qty=intent.quantity, notes=side_note + [
                        "Broker ended the order with no fill",
                        "position is still open" if is_exit else "",
                    ]),
                    severity="CRITICAL" if is_exit else "WARNING",
                )
        elif intent.reconciliation_required and observation.reason not in {
            "duplicate", "reconciliation_pending", "unclaimed_order_evidence",
        }:
            _emit(
                uid, strategy=strategy, title=intent.symbol,
                key=f"order-conflict:{intent.intent_key}:{observation.reason}",
                event_type="BROKER EVIDENCE CONFLICT",
                detail=lf.detail(qty=intent.quantity, notes=side_note + [
                    observation.reason,
                    "Order quarantined; new entries blocked until reconciled",
                ]),
                severity="CRITICAL",
            )
    except Exception as exc:  # noqa: BLE001
        log.warning("order-evidence alert skipped: %s", type(exc).__name__)


__all__ = [
    "broker_session_lost",
    "check_broker_sessions",
    "control_plane_changed",
    "control_plane_changed_from_db",
    "daily_loss_halt",
    "order_observed",
    "order_submission",
    "reset_for_tests",
    "safe_mode_engaged",
    "safe_mode_released",
]
