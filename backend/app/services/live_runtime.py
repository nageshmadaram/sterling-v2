"""Boot wiring that makes live entries reachable at runtime, and keeps them safe.

Sterling ships as a real-money live trading app. Every individual safety control
fails closed, which is correct, but a control that fails closed on a machine that
nothing ever initialises is a control that blocks every order for ever. This
module is the one place that initialises them, in a fixed and conservative order:

* **Safe mode (O1).** A missing ``safe_mode.json`` reads as SAFE_MODE. On first
  boot nothing wrote it, so no entry could ever pass. The boot step initialises
  NORMAL only when the system is *provably* clean (control plane readable, no
  unresolved journal intents, no open position with protection pending). Anything
  less is left as SAFE_MODE and reported loudly. An operator can lift an
  operator-set SAFE_MODE through the authenticated API, with an explicit
  confirmation and a recorded name and time.
* **Startup recovery (O2).** ``execution_control`` starts empty, which reads as
  RECOVERY_REQUIRED. Recovery is run per live account as soon as that account has
  a broker session, retried with backoff until it succeeds, and re-run
  periodically so one transient broker error cannot leave trading halted until
  somebody restarts the process. Recovery is observe-only: it reconciles broker
  evidence against the journal and never resubmits an order.
* **Exposure observation (O3).** Admission refuses new exposure unless the broker
  was asked what it holds in the last 60 seconds, and nothing refreshed that
  record. A resilient asyncio refresher now does, using the async
  ``external_exposure()`` so the event loop is never blocked.

None of this relaxes a safety control. It supplies the evidence the controls were
already asking for. Exits, protection and reconciliation were never gated and
still are not.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping, Optional

log = logging.getLogger(__name__)

__all__ = [
    "ACTOR",
    "LIVE_EXECUTION_ENV",
    "RESUME_CONFIRMATION",
    "CleanCheck",
    "SafeModeBootResult",
    "SafeModeResumeRefused",
    "live_execution_state",
    "provably_clean",
    "initialise_safe_mode_if_clean",
    "safe_mode_status",
    "resume_safe_mode",
    "run_recovery_cycle",
    "recovery_loop",
    "refresh_exposure_once",
    "exposure_refresher_loop",
    "live_status",
    "summary_line",
    "announce_boot",
    "start_live_runtime",
    "stop_live_runtime",
    "reset_for_tests",
]

#: Who this module is in audit trails and control-plane rows.
ACTOR = "live_runtime"

#: Operator kill switch for new-entry origination. Exits, protection and
#: reconciliation keep working whatever it says.
LIVE_EXECUTION_ENV = "STERLING_LIVE_EXECUTION"
#: Anything blank or in this set leaves live execution on. Every other value is
#: the kill switch, matching ``lane_registry.parse_live_execution``: a typo in a
#: safety control must not leave real money flowing.
_ENABLED_VALUES = frozenset({"1", "true", "on", "yes", "enabled"})

#: The phrase an operator has to send to lift SAFE_MODE. A bare ``true`` is too
#: easy to send by accident from a script or a stray click.
RESUME_CONFIRMATION = "RESUME TRADING"

RECOVERY_INITIAL_DELAY_S = 3.0
RECOVERY_BACKOFF_START_S = 5.0
RECOVERY_PERIODIC_S = 60.0
EXPOSURE_INITIAL_DELAY_S = 2.0
EXPOSURE_REFRESH_S = 20.0
#: Well under the 60s admission TTL, so a hung broker call becomes a recorded
#: UNREADABLE observation rather than a silently stale one.
EXPOSURE_CALL_TIMEOUT_S = 25.0
#: The boot alert waits for the first recovery pass so it states something true.
BOOT_ALERT_MAX_WAIT_S = 30.0

# Recovery states reported per account.
PENDING = "PENDING"
WAITING_FOR_SESSION = "WAITING_FOR_SESSION"
CLEAN = "CLEAN"
RECOVERY_REQUIRED = "RECOVERY_REQUIRED"
ERROR = "ERROR"


def _now_ms() -> int:
    return int(time.time() * 1000)


def _iso(ms: int) -> str | None:
    if not ms:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# runtime state (one process, one instance)
# ---------------------------------------------------------------------------


@dataclass
class AccountRecovery:
    uid: str
    account_id: str
    state: str = PENDING
    detail: str = ""
    attempts: int = 0
    last_attempt_ms: int = 0
    last_clean_ms: int = 0
    unresolved: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "account_id": self.account_id,
            "state": self.state,
            "detail": self.detail,
            "attempts": self.attempts,
            "last_attempt_at": _iso(self.last_attempt_ms),
            "last_clean_at": _iso(self.last_clean_ms),
            "unresolved_intents": self.unresolved,
        }


@dataclass
class ExposureRefresh:
    last_attempt_ms: int = 0
    outcome: str = "never"  # never | ok | unreadable | skipped | error
    detail: str = ""
    consecutive_failures: int = 0


@dataclass
class _RuntimeState:
    boot_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    booted_ms: int = field(default_factory=_now_ms)
    safe_mode_boot: Optional["SafeModeBootResult"] = None
    accounts: dict[tuple[str, str], AccountRecovery] = field(default_factory=dict)
    recovery_cycles: int = 0
    exposure: ExposureRefresh = field(default_factory=ExposureRefresh)
    last_summary: str = ""
    root_scope: str = "not yet settled"
    boot_announced: bool = False
    ready_announced: bool = False
    was_open: bool = False


_STATE = _RuntimeState()


def reset_for_tests() -> None:
    """Forget everything this process learned. Test hook only."""
    global _STATE
    _STATE = _RuntimeState()


# ---------------------------------------------------------------------------
# live execution state (the operator kill switch)
# ---------------------------------------------------------------------------


def live_execution_state() -> dict[str, Any]:
    """Whether new live entries may originate, and why.

    ``STERLING_LIVE_EXECUTION=0|false|off|no|disabled`` is the kill switch (so is
    any unrecognised value, as in ``lane_registry``). It turns new-entry origination
    off; it never touches exits or protection.
    """
    raw = os.environ.get(LIVE_EXECUTION_ENV)
    text = (raw or "").strip().lower()
    killed = bool(text) and text not in _ENABLED_VALUES
    flag = True
    source = "default"
    try:
        from app.core import lane_registry

        probe = getattr(lane_registry, "live_execution_enabled", None)
        flag = bool(probe()) if callable(probe) else bool(lane_registry.LIVE_EXECUTION_ENABLED)
        source = "lane_registry"
    except Exception as exc:  # noqa: BLE001 - an unreadable master floor is not "enabled"
        flag = False
        source = f"lane_registry unreadable: {type(exc).__name__}"
    return {
        "enabled": bool(flag and not killed),
        "kill_switch": killed,
        "kill_switch_env": LIVE_EXECUTION_ENV,
        "kill_switch_value": (raw if raw is not None else None),
        "master_floor": bool(flag),
        "source": source,
    }


# ---------------------------------------------------------------------------
# live accounts
# ---------------------------------------------------------------------------


def _kite_accounts():
    from app.services.exchanges.kite import accounts as kite_accounts

    return kite_accounts


def _live_accounts(kite_accounts: Any = None) -> list[Any]:
    """Every live broker account Sterling can trade through. An unknown mode is not live.

    Only a user's ACTIVE account counts, exactly as in
    ``external_positions.live_account_ids``: a stored-but-inactive login (a second
    family member's, or a converted account that never logged in) cannot receive
    an order, and requiring it to reconcile would hold the process-wide recovery
    marker, and with it every entry for every account, for ever.
    """
    kite_accounts = kite_accounts or _kite_accounts()
    kite_accounts.bootstrap()
    return [
        a for a in kite_accounts.all_accounts()
        if getattr(a, "is_paper", True) is False and getattr(a, "is_active", True)
    ]


def _has_session(account: Any) -> bool:
    """A stored access token that is not known to have expired."""
    if not getattr(account, "connected", False):
        return False
    if getattr(account, "token_expires_at", None) is None:
        return True  # unknown expiry: let the broker decide
    return bool(getattr(account, "token_is_live", True))


# ---------------------------------------------------------------------------
# O1: safe mode
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CleanCheck:
    """Whether the system is *provably* clean. ``clean`` is never a default."""

    clean: bool
    blockers: tuple[str, ...] = ()
    #: Safe-mode triggers the blockers map to, for an explicit engaged state.
    triggers: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {"clean": self.clean, "blockers": list(self.blockers)}


def provably_clean() -> CleanCheck:
    """Prove there is nothing unresolved, or say exactly what is.

    The conditions are the ones a human would check before trusting a fresh
    safety state: the control plane answers, no order intent is mid-flight, and
    no open position is waiting on its protective stop. A store that cannot be
    read is a blocker, never a pass.
    """
    from app.services.safe_mode import SafeModeTrigger

    blockers: list[str] = []
    triggers: set[str] = set()

    try:
        from app.services import db

        if not db.is_available():
            raise RuntimeError("database unavailable")
        db.get_execution_control(uid="default", account_id="default")
    except Exception as exc:  # noqa: BLE001
        blockers.append(f"control plane unreadable: {type(exc).__name__}: {exc}")
        triggers.add(SafeModeTrigger.SAFETY_STATE_UNAVAILABLE)

    try:
        from app.services import (
            adaptive_edge_positions,
            gamma_move_positions,
            intraday_positions,
            snapback_live_positions,
        )
        from app.services.exposure_snapshot import _known_local_uids
        from app.services.kite_engine import order_journal, positions

        for uid in _known_local_uids():
            intents = list(order_journal.unresolved(uid))
            pending = (
                list(order_journal.pending_projection(uid))
                if hasattr(order_journal, "pending_projection") else []
            )
            if intents or pending:
                blockers.append(
                    f"{len(intents)} unresolved journal intent(s) and "
                    f"{len(pending)} pending projection(s) for {uid}"
                )
                triggers.add(SafeModeTrigger.UNRESOLVED_ORDER)

            reason = ""
            unreadable = getattr(positions, "unreadable_reason", None)
            if callable(unreadable):
                reason = str(unreadable(uid) or "")
            if reason:
                blockers.append(f"position store unreadable for {uid}: {reason}")
                triggers.add(SafeModeTrigger.SAFETY_STATE_UNAVAILABLE)

            unprotected: list[str] = []
            for registry in (
                positions, intraday_positions, adaptive_edge_positions, gamma_move_positions,
                snapback_live_positions,
            ):
                for position in registry.open_positions(uid):
                    if getattr(position, "protection_pending", False):
                        unprotected.append(
                            str(getattr(position, "symbol", "") or "position")
                        )
            if unprotected:
                blockers.append(
                    f"open position(s) with protection pending for {uid}: "
                    + ", ".join(sorted(set(unprotected))[:5])
                )
                triggers.add(SafeModeTrigger.PROTECTION_MISSING)
    except Exception as exc:  # noqa: BLE001
        blockers.append(f"Sterling state unreadable: {type(exc).__name__}: {exc}")
        triggers.add(SafeModeTrigger.SAFETY_STATE_UNAVAILABLE)

    return CleanCheck(
        clean=not blockers, blockers=tuple(blockers), triggers=tuple(sorted(triggers))
    )


@dataclass(frozen=True)
class SafeModeBootResult:
    #: initialised | already_normal | left_engaged | not_clean | error
    action: str
    state: str
    reason: str = ""
    triggers: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()

    @property
    def trading_blocked(self) -> bool:
        return self.state != "NORMAL"

    def as_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "state": self.state,
            "reason": self.reason,
            "triggers": list(self.triggers),
            "blockers": list(self.blockers),
        }


def _safe_mode_service():
    from app.services.safe_mode import SafeModeService
    from app.services.safety_supervisor import safe_mode_path

    return SafeModeService(safe_mode_path())


def initialise_safe_mode_if_clean(
    service: Any = None, *, check: Callable[[], CleanCheck] | None = None
) -> SafeModeBootResult:
    """Boot step: give a never-initialised machine a NORMAL safety state, if provable.

    Only a *missing* file is touched. An existing file, NORMAL or SAFE_MODE, was
    written by something that knew more than this boot step does (an operator, a
    reconciliation failure) and is left exactly as found. SAFE_MODE is never
    cleared here: leaving it is a person's decision.
    """
    try:
        service = service or _safe_mode_service()
        if service.path.exists():
            state = service.read()
            if state.active:
                return SafeModeBootResult(
                    "left_engaged", state.state, state.reason, tuple(state.triggers)
                )
            return SafeModeBootResult("already_normal", state.state, state.reason)

        verdict = (check or provably_clean)()
        if not verdict.clean:
            return SafeModeBootResult(
                "not_clean",
                "SAFE_MODE",
                "safety state has never been initialised and the system is not "
                "provably clean: " + "; ".join(verdict.blockers),
                verdict.triggers,
                verdict.blockers,
            )
        state = service.initialise(
            operator_ack=True,
            note="initialised at boot: control plane readable, no unresolved journal "
                 "intents, no open position with protection pending",
        )
        return SafeModeBootResult("initialised", state.state, state.reason)
    except Exception as exc:  # noqa: BLE001 - an unprovable boot stays SAFE_MODE
        return SafeModeBootResult(
            "error", "SAFE_MODE", f"safe-mode initialisation failed: {type(exc).__name__}: {exc}"
        )


def safe_mode_status(service: Any = None) -> dict[str, Any]:
    """The safe-mode state plus what lifting it would require. No secrets."""
    from app.services.safe_mode import SafeModeTrigger

    service = service or _safe_mode_service()
    state = service.read()
    blocking = sorted(set(state.triggers) & SafeModeTrigger.REQUIRES_INVESTIGATION)
    verdict = provably_clean()
    return {
        "state": state.state,
        "active": state.active,
        "triggers": list(state.triggers),
        "reason": state.reason,
        "entered_at": state.entered_at,
        "updated_at": state.updated_at,
        "file_present": service.path.exists(),
        "requires_acknowledgement": bool(state.active and blocking),
        "blocking_triggers": blocking,
        "clean_check": verdict.as_dict(),
        "confirmation_phrase": RESUME_CONFIRMATION,
    }


class SafeModeResumeRefused(Exception):
    """The request was well-formed but the state does not allow lifting it."""

    def __init__(self, message: str, *, status_code: int = 409) -> None:
        super().__init__(message)
        self.status_code = status_code


def _audit(service: Any, event: Mapping[str, Any]) -> None:
    """Append who/when to a file beside the safety state. Best effort."""
    try:
        path = Path(service.path).with_name("safe_mode_audit.jsonl")
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(dict(event), sort_keys=True) + "\n")
    except Exception as exc:  # noqa: BLE001 - the audit row must not block the action
        log.warning("safe-mode audit record failed: %s", exc)


def resume_safe_mode(
    *,
    actor: str,
    confirm: str,
    reason: str,
    acknowledge_investigation: bool = False,
    service: Any = None,
) -> dict[str, Any]:
    """Lift SAFE_MODE at an operator's explicit request.

    * ``confirm`` must be the confirmation phrase; ``reason`` is required.
    * An OPERATOR-set safe mode needs nothing more.
    * A safe mode caused by an investigation-required trigger (unresolved order,
      missing protection, reconciliation failure, a missing/unreadable safety
      state ...) additionally needs ``acknowledge_investigation=True``: the
      statement that a person looked at the condition and resolved it.
    """
    service = service or _safe_mode_service()
    actor = (actor or "").strip() or "unknown"
    reason = (reason or "").strip()
    if (confirm or "").strip().upper() != RESUME_CONFIRMATION:
        raise SafeModeResumeRefused(
            f'confirmation required: send confirm="{RESUME_CONFIRMATION}"', status_code=400
        )
    if len(reason) < 3:
        raise SafeModeResumeRefused("a reason is required", status_code=400)

    before = service.read()
    now = datetime.now(timezone.utc).isoformat()
    note = f"{reason} (resumed by {actor} via ops API at {now})"

    if not before.active:
        return {**before.as_dict(), "resumed": False, "already_normal": True,
                "resumed_by": None, "resumed_at": None}

    try:
        if not service.path.exists():
            # A missing file is "never initialised, or deleted". Only a person can
            # tell which, so the same acknowledgement as any investigated trigger.
            if not acknowledge_investigation:
                raise SafeModeResumeRefused(
                    "the safety state file is missing; confirm you have checked the "
                    "system and send acknowledge_investigation=true"
                )
            after = service.initialise(operator_ack=True, note=note)
        else:
            from app.services.safe_mode import SafeModeError

            try:
                after = service.release(operator_ack=bool(acknowledge_investigation), note=note)
            except SafeModeError as exc:
                raise SafeModeResumeRefused(str(exc)) from exc
    except SafeModeResumeRefused:
        _audit(service, {
            "event": "SAFE_MODE_RESUME_REFUSED", "actor": actor, "reason": reason,
            "at": now, "triggers": list(before.triggers),
        })
        raise

    _audit(service, {
        "event": "SAFE_MODE_RESUMED", "actor": actor, "reason": reason, "at": now,
        "triggers": list(before.triggers), "previous_reason": before.reason,
        "acknowledged_investigation": bool(acknowledge_investigation),
    })
    log.warning(
        "SAFE_MODE lifted by %s (triggers=%s, acknowledged=%s): %s",
        actor, list(before.triggers), bool(acknowledge_investigation), reason,
    )
    return {**after.as_dict(), "resumed": True, "already_normal": False,
            "resumed_by": actor, "resumed_at": now}


# ---------------------------------------------------------------------------
# O2: startup recovery
# ---------------------------------------------------------------------------


def _account_key(account: Any) -> tuple[str, str]:
    return (str(getattr(account, "user_id", "") or ""), str(getattr(account, "id", "") or ""))


def _control_recovery_state(uid: str, account_id: str) -> str | None:
    """The durable recovery state for one account scope; None if unreadable."""
    try:
        from app.services import db

        ctrl = db.get_execution_control(uid=uid, account_id=account_id)
        if not int(ctrl.get("initialized", 1) or 0):
            return RECOVERY_REQUIRED
        return str(ctrl.get("recovery_state") or RECOVERY_REQUIRED)
    except Exception:  # noqa: BLE001
        return None


def _journal_unsettled(uid: str, account_id: str) -> bool:
    """Is any order intent still mid-flight, whatever the control row says?

    A failed journal write can leave a sent order SUBMITTING under a CLEAN row;
    the periodic pass must still read the broker for it. Unreadable counts as
    unsettled.
    """
    try:
        from app.services.kite_engine import order_journal

        return bool(
            order_journal.unresolved(uid, account_id=account_id)
            or order_journal.pending_projection(uid, account_id)
        )
    except Exception:  # noqa: BLE001
        return True


async def _recover_account(
    account: Any, *, service: Any, kite_accounts: Any
) -> AccountRecovery:
    uid, account_id = _account_key(account)
    entry = _STATE.accounts.setdefault((uid, account_id), AccountRecovery(uid, account_id))
    entry.attempts += 1
    entry.last_attempt_ms = _now_ms()

    if not _has_session(account):
        entry.state = WAITING_FOR_SESSION
        entry.detail = "no live broker session yet; recovery runs as soon as one exists"
        return entry

    try:
        client = await kite_accounts.acquire_client(account)
        outcome = await service.startup_recovery(
            uid=uid, account_id=account_id, broker_client=client
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - a transient broker error is retried
        entry.state = ERROR
        entry.detail = f"{type(exc).__name__}: {exc}"
        log.warning("startup recovery for %s failed (will retry): %s", account_id, entry.detail)
        return entry

    outcome = outcome or {}
    entry.unresolved = int(outcome.get("unresolved_count") or 0)
    if outcome.get("status") == "success" and outcome.get("recovery_state") == CLEAN:
        entry.state = CLEAN
        entry.detail = "reconciled against the broker"
        entry.last_clean_ms = _now_ms()
    elif outcome.get("status") == "success":
        entry.state = RECOVERY_REQUIRED
        entry.detail = (
            f"{entry.unresolved} unresolved intent(s), "
            f"{int(outcome.get('pending_projections_count') or 0)} pending projection(s), "
            f"protection_pending={bool(outcome.get('protection_pending'))}"
        )
    else:
        entry.state = ERROR
        entry.detail = str(outcome.get("reason") or "recovery did not report success")
    return entry


#: The marker `sterlingctl start` engages at step 1 (service_lifecycle._step_engage_recovery).
CLI_ACTOR = "sterlingctl"
CLI_STARTUP_REASON = "STARTUP"
CLI_WAITING = "waiting for sterlingctl start to finish"


def _is_cli_startup_marker(ctrl: Mapping[str, Any]) -> bool:
    return (
        str(ctrl.get("actor") or "") == CLI_ACTOR
        or str(ctrl.get("reason_code") or "") == CLI_STARTUP_REASON
    )


def _settle_root_scope(entries: list[AccountRecovery]) -> str:
    """Clear the process-wide recovery marker once every live account is reconciled.

    Admission also reads the root (``default``/``default``) control scope, which
    startup recovery of a real account never writes. Without this it would read
    RECOVERY_REQUIRED for ever on a database that has never seen a CLI start.

    Only a marker that this runtime may legitimately retire is cleared: one that
    was never initialised, one this runtime wrote, or one written by something
    other than the CLI before this process booted. A RECOVERY_REQUIRED written
    *during* this process by anything else is a live uncertainty and stays until
    it is reconciled.

    A marker written by ``sterlingctl`` (the STARTUP marker its step 1 engages
    before the unit boots) is never retired here. Only the CLI's last step may
    clear it: that step runs after the bound account is checked against the
    authenticated one, the broker is reconciled, subscriptions and the feed are
    verified, the evidence writer accepts a write, and the broker is asked whether
    anything is open. Retiring it on the first recovery cycle would let entries
    open while ``sterlingctl start`` is still reporting STOPPED.
    """
    if not entries or any(e.state != CLEAN for e in entries):
        return "waiting for every live account to reconcile"
    try:
        from app.services import db

        ctrl = db.get_execution_control(uid="default", account_id="default")
        initialised = bool(int(ctrl.get("initialized", 0) or 0))
        if initialised and ctrl.get("recovery_state") == CLEAN:
            return "root scope already CLEAN"
        if initialised and _is_cli_startup_marker(ctrl):
            return CLI_WAITING
        written_ms = int(ctrl.get("updated_ms") or 0)
        stale_marker = written_ms and written_ms < _STATE.booted_ms
        ours = ctrl.get("actor") == ACTOR
        if initialised and not (stale_marker or ours):
            return (
                "root scope was set RECOVERY_REQUIRED during this run by "
                f"{ctrl.get('actor')!r} ({ctrl.get('reason_code')}); left for reconciliation"
            )
        db.set_recovery_state(
            recovery_state=CLEAN,
            reason_code="RECONCILIATION_CLEAN",
            reason="every live account reconciled against the broker at runtime start",
            actor=ACTOR,
            uid="default",
            account_id="default",
            expected_revision=int(ctrl.get("revision", 0) or 0),
            last_reconciled_ms=_now_ms(),
        )
        log.info("execution control root scope set CLEAN (all live accounts reconciled)")
        return "root scope set CLEAN"
    except Exception as exc:  # noqa: BLE001 - CAS conflicts retry on the next cycle
        log.warning("could not settle the root control scope: %s", exc)
        return f"root scope not settled: {type(exc).__name__}: {exc}"


def root_scope_status() -> str:
    """What the last recovery cycle concluded about the root control scope."""
    return _STATE.root_scope


async def run_recovery_cycle(
    *,
    service: Any = None,
    kite_accounts: Any = None,
    only_unclean: bool = False,
) -> list[AccountRecovery]:
    """Run observe-only recovery for every live account that needs it.

    ``only_unclean`` skips accounts that already reconciled and whose durable
    state still says CLEAN, which is what the periodic pass uses.
    """
    kite_accounts = kite_accounts or _kite_accounts()
    if service is None:
        from app.services.execution_service import execution_service as service

    live = _live_accounts(kite_accounts)
    results: list[AccountRecovery] = []
    for account in live:
        uid, account_id = _account_key(account)
        known = _STATE.accounts.get((uid, account_id))
        if (
            only_unclean
            and known is not None
            and known.state == CLEAN
            and _control_recovery_state(uid, account_id) == CLEAN
            and not _journal_unsettled(uid, account_id)
        ):
            results.append(known)
            continue
        results.append(await _recover_account(account, service=service, kite_accounts=kite_accounts))

    _STATE.recovery_cycles += 1
    _STATE.root_scope = _settle_root_scope(results)
    return results


def _recovery_delay(results: list[AccountRecovery], streak: int) -> float:
    if results and all(r.state == CLEAN for r in results):
        return RECOVERY_PERIODIC_S
    if not results:
        return RECOVERY_PERIODIC_S
    return min(RECOVERY_PERIODIC_S, RECOVERY_BACKOFF_START_S * (2 ** min(streak, 4)))


async def recovery_loop(
    *,
    initial_delay: float = RECOVERY_INITIAL_DELAY_S,
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    cycle: Callable[..., Awaitable[list[AccountRecovery]]] | None = None,
    max_cycles: int | None = None,
) -> None:
    """Retry recovery with backoff until it succeeds, then re-check periodically.

    Never raises into the event loop: every failure is logged and the loop goes
    on, because a recovery task that died once would leave trading halted for the
    rest of the process lifetime.
    """
    cycle = cycle or run_recovery_cycle
    await sleep(initial_delay)
    streak = 0
    first = True
    cycles = 0
    while max_cycles is None or cycles < max_cycles:
        cycles += 1
        results: list[AccountRecovery] = []
        try:
            results = await cycle(only_unclean=not first)
            first = False
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("live runtime: recovery cycle crashed; will retry")
        try:
            _retry_safe_mode_init()
            await _after_state_change()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("live runtime: post-recovery bookkeeping failed")

        if results and all(r.state == CLEAN for r in results):
            streak = 0
        else:
            streak += 1
        await sleep(_recovery_delay(results, streak))


def _retry_safe_mode_init() -> None:
    """A never-initialised safety state is re-proved each cycle until it can be."""
    boot = _STATE.safe_mode_boot
    if boot is None or boot.action not in ("not_clean", "error"):
        return
    result = initialise_safe_mode_if_clean()
    if result.action != boot.action or result.state != boot.state:
        _STATE.safe_mode_boot = result
        if result.action == "initialised":
            log.warning("SAFE_MODE initialised NORMAL: the system is now provably clean")


# ---------------------------------------------------------------------------
# O3: exposure observation refresher
# ---------------------------------------------------------------------------


async def refresh_exposure_once(
    *,
    kite_accounts: Any = None,
    exposure_fn: Callable[[], Awaitable[Any]] | None = None,
    recorder: Callable[[Any], None] | None = None,
    timeout: float = EXPOSURE_CALL_TIMEOUT_S,
) -> ExposureRefresh:
    """Ask the broker what it holds and record the answer for admission to read.

    Skips cleanly (recording nothing) when there is no live account or none has a
    session: there is nothing to observe, and a record written from nothing would
    claim an observation that never happened. Any other outcome, including a
    timeout or an exception, is recorded as an UNREADABLE observation so admission
    sees "could not ask" rather than a stale "flat".
    """
    state = _STATE.exposure
    state.last_attempt_ms = _now_ms()

    try:
        live = _live_accounts(kite_accounts)
    except Exception as exc:  # noqa: BLE001
        state.outcome = "error"
        state.detail = f"live-account registry unreadable: {type(exc).__name__}: {exc}"
        state.consecutive_failures += 1
        return state
    if not live:
        state.outcome, state.detail = "skipped", "no live broker account is configured"
        return state
    if not any(_has_session(a) for a in live):
        state.outcome, state.detail = "skipped", "no live broker session"
        return state

    from app.services import external_positions

    exposure_fn = exposure_fn or external_positions.external_exposure
    recorder = recorder or external_positions.record_observation
    try:
        exposure = await asyncio.wait_for(exposure_fn(), timeout=timeout)
    except asyncio.CancelledError:
        raise
    except asyncio.TimeoutError:
        exposure = external_positions.ExternalExposure(
            readable=False, detail=f"broker exposure read timed out after {timeout:.0f}s"
        )
    except Exception as exc:  # noqa: BLE001
        exposure = external_positions.ExternalExposure(
            readable=False,
            detail=f"broker exposure read failed: {type(exc).__name__}: {exc}",
        )

    await asyncio.to_thread(recorder, exposure)
    if exposure.readable:
        state.outcome, state.detail, state.consecutive_failures = "ok", exposure.detail or "", 0
    else:
        state.outcome, state.detail = "unreadable", exposure.detail
        state.consecutive_failures += 1
    return state


async def exposure_refresher_loop(
    *,
    interval: float = EXPOSURE_REFRESH_S,
    initial_delay: float = EXPOSURE_INITIAL_DELAY_S,
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    refresh: Callable[[], Awaitable[Any]] | None = None,
    max_cycles: int | None = None,
) -> None:
    """Refresh the recorded broker exposure every ``interval`` seconds, for ever."""
    refresh = refresh or refresh_exposure_once
    await sleep(initial_delay)
    cycles = 0
    while max_cycles is None or cycles < max_cycles:
        cycles += 1
        try:
            await refresh()
            await _after_state_change()
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("live runtime: exposure refresh failed; will retry")
        await sleep(interval)


# ---------------------------------------------------------------------------
# status (authenticated API, boot line, Telegram) - never any secret
# ---------------------------------------------------------------------------


def _exposure_summary() -> dict[str, Any]:
    from app.services import external_positions

    summary: dict[str, Any] = {
        "refresher": {
            "last_attempt_at": _iso(_STATE.exposure.last_attempt_ms),
            "outcome": _STATE.exposure.outcome,
            "detail": _STATE.exposure.detail,
            "consecutive_failures": _STATE.exposure.consecutive_failures,
        },
        "max_age_seconds": external_positions.MAX_OBSERVATION_AGE_SECONDS,
    }
    record = external_positions.last_observation()
    if record is None:
        summary.update(observed=False, fresh=False, readable=None, age_seconds=None)
        return summary
    age = None
    try:
        seen = datetime.fromisoformat(str(record.get("observed_at") or ""))
        if seen.tzinfo is None:
            seen = seen.replace(tzinfo=timezone.utc)
        age = round((datetime.now(timezone.utc) - seen).total_seconds(), 1)
    except ValueError:
        pass
    summary.update(
        observed=True,
        observed_at=record.get("observed_at"),
        age_seconds=age,
        fresh=bool(age is not None and 0 <= age <= external_positions.MAX_OBSERVATION_AGE_SECONDS),
        readable=bool(record.get("readable")),
        external_positions=record.get("count"),
        hazards=list(record.get("automation") or []),
        sterling_owned_orders_and_stops=len(record.get("owned_automation") or []),
        detail=str(record.get("detail") or ""),
    )
    return summary


def _admission_summary(live: list[Any]) -> dict[str, Any]:
    """What the single admission authority says right now, per live account."""
    from app.services.safety_supervisor import SafetySupervisor

    scopes = [_account_key(a) for a in live] or [("default", "default")]
    per_account = []
    for uid, account_id in scopes:
        try:
            verdict = SafetySupervisor().authorize(uid=uid, account_id=account_id)
            blockers = list(verdict.snapshot.blockers) if verdict.snapshot else (
                [verdict.code] if verdict.code else []
            )
            per_account.append({
                "account_id": account_id,
                "may_open_new_exposure": bool(verdict.allowed),
                "blockers": blockers,
                "reasons": list(verdict.snapshot.blocker_reasons) if verdict.snapshot else
                ([verdict.reason] if verdict.reason else []),
            })
        except Exception as exc:  # noqa: BLE001
            per_account.append({
                "account_id": account_id, "may_open_new_exposure": False,
                "blockers": ["admission_unreadable"],
                "reasons": [f"{type(exc).__name__}: {exc}"],
            })
    return {
        "may_open_new_exposure": bool(per_account) and all(
            a["may_open_new_exposure"] for a in per_account
        ),
        "accounts": per_account,
    }


def live_status() -> dict[str, Any]:
    """One snapshot of everything that decides whether Sterling may open a trade."""
    execution = live_execution_state()
    try:
        live = _live_accounts()
        registry_error = ""
    except Exception as exc:  # noqa: BLE001
        live, registry_error = [], f"{type(exc).__name__}: {exc}"

    try:
        service = _safe_mode_service()
        state = service.read()
        safe_mode: dict[str, Any] = {
            "state": state.state, "active": state.active,
            "triggers": list(state.triggers), "reason": state.reason,
            "entered_at": state.entered_at, "file_present": service.path.exists(),
        }
    except Exception as exc:  # noqa: BLE001
        safe_mode = {"state": "SAFE_MODE", "active": True,
                     "reason": f"unreadable: {type(exc).__name__}: {exc}"}

    accounts = [
        _STATE.accounts.get(_account_key(a)) or AccountRecovery(*_account_key(a))
        for a in live
    ]
    if not live:
        overall = "NO_LIVE_ACCOUNT"
    elif all(a.state == CLEAN for a in accounts):
        overall = CLEAN
    elif any(a.state in (RECOVERY_REQUIRED, ERROR) for a in accounts):
        overall = RECOVERY_REQUIRED
    else:
        overall = PENDING

    from app.services.snapback_family_mode import family_mode_enabled

    status = {
        "booted_at": _iso(_STATE.booted_ms),
        "boot_id": _STATE.boot_id,
        "live_execution": execution,
        "safe_mode": safe_mode,
        "safe_mode_boot": _STATE.safe_mode_boot.as_dict() if _STATE.safe_mode_boot else None,
        "recovery": {
            "overall": overall,
            "cycles": _STATE.recovery_cycles,
            "accounts": [a.as_dict() for a in accounts],
            "registry_error": registry_error,
            "root_scope": _STATE.root_scope,
        },
        "exposure": _exposure_summary(),
        "admission": _admission_summary(live),
        "live_accounts": len(live),
        "family_mode_setting_ignored": bool(family_mode_enabled()),
    }
    status["summary"] = summary_line(status)
    return status


def summary_line(status: Mapping[str, Any] | None = None) -> str:
    """The one startup line: live execution, safe mode, recovery, exposure freshness."""
    status = status if status is not None else live_status()
    execution = status["live_execution"]
    exposure = status["exposure"]
    if not exposure.get("observed"):
        freshness = "never observed"
    elif exposure.get("fresh"):
        freshness = f"fresh ({exposure.get('age_seconds')}s old)"
    else:
        freshness = f"STALE ({exposure.get('age_seconds')}s old)"
    if exposure.get("observed") and not exposure.get("readable"):
        freshness += ", broker unreadable"
    admission = status["admission"]
    blockers = sorted({b for a in admission["accounts"] for b in a["blockers"]})
    return (
        "LIVE STATUS: "
        f"execution={'KILLED' if execution['kill_switch'] else ('ENABLED' if execution['enabled'] else 'DISABLED')}"
        f" | safe_mode={status['safe_mode']['state']}"
        f" | recovery={status['recovery']['overall']}"
        f" | exposure_observation={freshness}"
        f" | new_entries={'OPEN' if admission['may_open_new_exposure'] else 'BLOCKED'}"
        + (f" ({', '.join(blockers)})" if blockers and not admission["may_open_new_exposure"] else "")
    )


# ---------------------------------------------------------------------------
# Telegram: owner's chat only, through the durable outbox (never blocks trading)
# ---------------------------------------------------------------------------


def _owner_uids() -> list[str]:
    try:
        uids = sorted({str(a.user_id) for a in _live_accounts() if getattr(a, "user_id", "")})
    except Exception:  # noqa: BLE001
        uids = []
    if uids:
        return uids
    from app.core.auth import DEFAULT_USER_ID

    return [DEFAULT_USER_ID]


def _enqueue(event_type: str, title: str, detail: str, severity: str, key: str,
             enqueue: Callable[..., Any] | None = None) -> int:
    if enqueue is None:
        from app.services.notifications import telegram_lifecycle

        enqueue = telegram_lifecycle.enqueue_best_effort
    sent = 0
    for uid in _owner_uids():
        if enqueue(
            uid,
            dedupe_key=f"{key}:{_STATE.boot_id}",
            strategy="Sterling",
            event_type=event_type,
            title=title,
            detail=detail,
            severity=severity,
        ):
            sent += 1
    return sent


def announce_boot(status: Mapping[str, Any] | None = None,
                  enqueue: Callable[..., Any] | None = None) -> int:
    """Queue the one boot alert for the owner. Non-blocking; failures are swallowed."""
    if _STATE.boot_announced:
        return 0
    status = status if status is not None else live_status()
    safe = status["safe_mode"]
    severity = "CRITICAL" if safe.get("active") else "INFO"
    lines = [status["summary"]]
    if safe.get("active"):
        lines.append(
            "SAFE_MODE is engaged, so no new entry will open. Exits, protection and "
            f"reconciliation continue. Reason: {safe.get('reason') or 'unknown'}. "
            "Once you have checked, use Resume trading in the app's trading controls."
        )
    _STATE.boot_announced = True
    try:
        return _enqueue("LIVE STATUS", "ALL STRATEGIES", "\n".join(lines), severity,
                        "live-boot", enqueue)
    except Exception as exc:  # noqa: BLE001
        log.warning("boot alert could not be queued: %s", exc)
        return 0


def _status_key(status: Mapping[str, Any]) -> str:
    """What makes two status lines the same event, ignoring the ticking age."""
    exposure = status.get("exposure") or {}
    if not exposure.get("observed"):
        bucket = "never"
    elif not exposure.get("readable"):
        bucket = "unreadable"
    else:
        bucket = "fresh" if exposure.get("fresh") else "stale"
    admission = status.get("admission") or {}
    blockers = sorted({
        b for a in (admission.get("accounts") or []) for b in (a.get("blockers") or [])
    })
    execution = status.get("live_execution") or {}
    return "|".join((
        str(execution.get("kill_switch")),
        str(execution.get("enabled")),
        str((status.get("safe_mode") or {}).get("state")),
        str((status.get("recovery") or {}).get("overall")),
        bucket,
        str(bool(admission.get("may_open_new_exposure"))),
        ",".join(blockers),
    ))


async def _after_state_change() -> None:
    """Log the status line when it changes; announce the first time entries open.

    Everything here is a handful of local file and SQLite reads, the same class of
    work admission already does on every order, so it runs inline rather than in a
    worker thread that would touch the in-memory position registries concurrently.
    """
    status = live_status()
    line = status["summary"]
    open_now = bool(status["admission"]["may_open_new_exposure"])
    key = _status_key(status)
    if key != _STATE.last_summary:
        _STATE.last_summary = key
        (log.info if open_now else log.warning)(line)
    if not _STATE.boot_announced and (
        _STATE.recovery_cycles > 0
        or time.time() * 1000 - _STATE.booted_ms > BOOT_ALERT_MAX_WAIT_S * 1000
    ):
        announce_boot(status)
    if open_now and not _STATE.ready_announced:
        _STATE.ready_announced = True
        try:
            _enqueue("LIVE READY", "ALL STRATEGIES", status["summary"], "INFO", "live-ready")
        except Exception as exc:  # noqa: BLE001
            log.warning("ready alert could not be queued: %s", exc)
    _STATE.was_open = open_now


# ---------------------------------------------------------------------------
# lifespan hooks
# ---------------------------------------------------------------------------


@dataclass
class LiveRuntimeHandles:
    tasks: list["asyncio.Task[Any]"] = field(default_factory=list)


def start_live_runtime() -> LiveRuntimeHandles:
    """Boot the live runtime. Never raises: a failure here leaves SAFE_MODE intact."""
    handles = LiveRuntimeHandles()
    try:
        boot = initialise_safe_mode_if_clean()
        _STATE.safe_mode_boot = boot
        if boot.action == "initialised":
            log.warning("SAFE_MODE initialised NORMAL at boot: %s", boot.reason)
        elif boot.trading_blocked:
            log.critical(
                "SAFE_MODE REMAINS ENGAGED AT BOOT (%s): %s. No new entry will open until "
                "the condition is resolved and an operator resumes trading (POST "
                "/api/v1/ops/safe-mode/resume).",
                boot.action, boot.reason,
            )
    except Exception:  # noqa: BLE001
        log.exception("live runtime: safe-mode boot step failed; SAFE_MODE stays engaged")

    try:
        from app.services.snapback_family_mode import family_mode_enabled

        if family_mode_enabled():
            log.warning(
                "STERLING_FAMILY_MODE is set but Family Mode is retired: it no longer "
                "suppresses any strategy loop. "
                "The variable now only tightens checks (a canonical capability for GTT and "
                "cancel writes, and the Snapback family-account binding); unset it unless "
                "you want that."
            )
        execution = live_execution_state()
        if execution["kill_switch"]:
            log.warning(
                "LIVE EXECUTION KILL SWITCH IS ON (%s=%s): no new entry will originate. "
                "Exits, protection and reconciliation continue.",
                LIVE_EXECUTION_ENV, execution["kill_switch_value"],
            )
        log.info(summary_line())
    except Exception:  # noqa: BLE001
        log.exception("live runtime: could not log the startup status line")

    handles.tasks.append(asyncio.create_task(recovery_loop(), name="live-recovery"))
    handles.tasks.append(asyncio.create_task(exposure_refresher_loop(), name="live-exposure"))
    log.info(
        "Live runtime started (startup recovery with backoff, exposure refresh every %ss)",
        int(EXPOSURE_REFRESH_S),
    )
    return handles


async def stop_live_runtime(handles: LiveRuntimeHandles | None) -> None:
    for task in (handles.tasks if handles else []):
        task.cancel()
        try:
            await task
        except (Exception, BaseException):  # noqa: BLE001 - shutdown must finish
            pass
