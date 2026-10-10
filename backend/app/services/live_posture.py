"""Live posture, automated: the protections stay, the ceremonies run themselves.

Three facts gate every REAL broker entry, whatever ENVIRONMENT says: a verified
static egress address, an ACTIVE live-ready broker-account binding for this
deployment, and strong distinct secrets. Each used to need a command-line
ceremony nobody at home could run. This module performs the ceremonies, and keeps
refusing whenever a fact is genuinely wrong:

* **Static egress.** An in-process observer asks 2+ independent HTTPS echo
  services where this host's traffic comes from and writes the answer into the
  deployment identity store (the one ``sterlingctl egress record`` writes).
  Trust on first use: with no address registered yet, the first consensus
  address becomes the registered one and the owner is told to confirm it is the
  address whitelisted at Zerodha. Once one is registered a different address is
  NEVER adopted automatically: entries are refused and a critical alert says
  Kite will refuse orders until the whitelist and the registration agree. An
  unreadable observation (every echo service down) keeps the last good one valid
  for a grace period, then refuses with the reason.
* **Account binding.** With no ACTIVE binding and exactly one live account that
  has a bound Kite user and a current session, the binding is declared, verified
  read-only against Kite and activated. A changed account or Kite user under an
  ACTIVE binding is refused and alerted, never silently rebound.
* **Security posture.** Reported here (``posture_status``) and repaired by
  ``scripts/sterling_live_setup.py``.

The Simulation tenant is exempt from all of it: it never holds broker authority,
so it is never a candidate for a binding and its client never reaches these gates.
"""
from __future__ import annotations

import asyncio
import logging
import os
import socket
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from ipaddress import ip_address
from typing import Any, Awaitable, Callable, Mapping, Sequence

from app.core.deployment_identity import (
    EXPECTED_ENV,
    DeploymentIdentityStore,
    RegistrationUnreadable,
    resolve_registered,
    verify_deployment_identity,
)
from app.core.state_paths import state_root

log = logging.getLogger(__name__)

__all__ = [
    "OBSERVER_ENV", "OBSERVER_INTERVAL_S", "OBSERVER_RETRY_S", "OBSERVATION_GRACE",
    "ECHO_SERVICES", "Discovery", "ObservationOutcome",
    "discover_outbound_ip", "observe_once", "observer_loop", "start_observer",
    "observer_active", "observer_status", "observer_hint", "accept_egress_ip",
    "ensure_deployment_id", "boot_deployment_id", "ensure_account_binding", "BindOutcome", "binding_loop",
    "start_posture_tasks", "posture_status", "auto_bind_enabled", "SECURE_KEYS_LINE",
    "announce_secure_keys_needed", "reset_for_tests",
]

#: ``0`` turns the in-process observer off (an address is then only ever the one
#: an operator recorded by hand, and the 7-day hand-recorded window applies).
OBSERVER_ENV = "STERLING_EGRESS_OBSERVER"

OBSERVER_INTERVAL_S = 600.0
#: While the last attempt failed, try again quickly: a blip should heal in a minute.
OBSERVER_RETRY_S = 60.0
#: How long the last good observation stays valid when the address cannot be
#: re-read. It is three observer cycles, so a healthy machine (confirmed every
#: ~10 minutes) is never refused for staleness, and an outage has to outlast
#: several attempts before entries stop.
OBSERVATION_GRACE = timedelta(minutes=30)
ECHO_TIMEOUT_S = 6.0

#: Independent operators, plain HTTPS, answer = the caller's address as text.
#: Nothing is sent but the request itself: no key, token, account or body.
ECHO_SERVICES: tuple[str, ...] = (
    "https://api.ipify.org",
    "https://checkip.amazonaws.com",
    "https://icanhazip.com",
    "https://ifconfig.me/ip",
    "https://ipinfo.io/ip",
)

Fetch = Callable[[str], Awaitable[str]]
Alert = Callable[..., Any]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _env_on(name: str) -> bool:
    """Default on; only an explicit off value disables."""
    return (os.environ.get(name) or "").strip().lower() not in {"0", "false", "off", "no", "disabled"}


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Discovery:
    """What the echo services said, reduced to one address or an honest "unknown"."""

    ip: str | None
    reason: str = ""
    answers: Mapping[str, str | None] = field(default_factory=dict)


async def _http_fetch(url: str) -> str:
    import httpx

    # Same transport defaults as the Kite client (environment proxies honoured),
    # so the address read here is the address Kite sees.
    async with httpx.AsyncClient(timeout=ECHO_TIMEOUT_S, follow_redirects=False) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.text[:128]


def _parse_ip(text: str) -> str | None:
    try:
        ip = ip_address(str(text or "").strip())
    except ValueError:
        return None
    if ip.is_loopback or ip.is_unspecified or ip.is_multicast or ip.is_link_local:
        return None
    return str(ip)


async def discover_outbound_ip(
    fetch: Fetch | None = None,
    services: Sequence[str] | None = None,
) -> Discovery:
    """Ask every echo service at once; trust only an address two of them agree on.

    One answer is a rumour, and a service that is wrong or hostile must not be
    able to move the registered address on its own, so a single answer, or a
    tie between two addresses, is "unknown" rather than a guess.
    """
    urls = tuple(services if services is not None else ECHO_SERVICES)
    fetcher = fetch or _http_fetch

    async def one(url: str) -> str | None:
        try:
            return _parse_ip(await asyncio.wait_for(fetcher(url), timeout=ECHO_TIMEOUT_S + 2))
        except Exception:  # noqa: BLE001 - a dead or lying service is just no answer
            return None

    results = await asyncio.gather(*(one(u) for u in urls))
    answers = dict(zip(urls, results))
    votes = Counter(ip for ip in results if ip)
    ranked = votes.most_common(2)
    if ranked and ranked[0][1] >= 2 and (len(ranked) == 1 or ranked[0][1] > ranked[1][1]):
        return Discovery(ranked[0][0], "", answers)
    answered = sum(votes.values())
    if len(votes) > 1:
        reason = "the echo services disagree about this host's address"
    elif answered == 1:
        reason = f"only 1 of {len(urls)} echo services answered; two must agree"
    else:
        reason = f"none of the {len(urls)} echo services could be reached"
    return Discovery(None, reason, answers)


# ---------------------------------------------------------------------------
# Observer
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ObservationOutcome:
    """The result of one observation, for the loop, the status and the tests."""

    outcome: str          # registered | confirmed | mismatch | unreadable | registration_unreadable
    ip: str | None = None
    registered_ip: str = ""
    detail: str = ""


@dataclass
class _ObserverState:
    running: bool = False
    last_attempt_at: str | None = None
    last_ok_at: str | None = None
    last_ip: str | None = None
    last_outcome: str = "never"
    last_detail: str = ""
    consecutive_failures: int = 0
    mismatch_alerted: bool = False
    outage_alerted: bool = False
    tags: tuple[str, str] | None = None


_STATE = _ObserverState()


def reset_for_tests() -> None:
    global _STATE, _BIND, _POSTURE_CACHE
    _STATE = _ObserverState()
    _BIND = _BindState()
    _POSTURE_CACHE = (0.0, None)


def observer_active() -> bool:
    """Is this process re-reading its own address every few minutes right now?"""
    return _STATE.running


def observer_status() -> dict[str, Any]:
    return {
        "enabled": _env_on(OBSERVER_ENV),
        "running": _STATE.running,
        "last_attempt_at": _STATE.last_attempt_at,
        "last_ok_at": _STATE.last_ok_at,
        "last_ip": _STATE.last_ip,
        "last_outcome": _STATE.last_outcome,
        "detail": _STATE.last_detail,
        "consecutive_failures": _STATE.consecutive_failures,
        "interval_seconds": int(OBSERVER_INTERVAL_S),
        "grace_minutes": int(OBSERVATION_GRACE.total_seconds() // 60),
    }


def observer_hint() -> str:
    """Why the last read failed, for the refusal text; empty while reads succeed."""
    if _STATE.last_outcome == "unreadable" and _STATE.last_detail:
        return f" (observer: {_STATE.last_detail})"
    return ""


def _suppressed_for_tests() -> bool:
    """Unit tests must neither reach the network nor enqueue into a real outbox."""
    return bool(os.environ.get("PYTEST_CURRENT_TEST")) and not os.environ.get(
        "STERLING_POSTURE_IN_TESTS"
    )


def _default_alert(*, key: str, event_type: str, title: str, detail: str,
                   severity: str = "INFO") -> None:
    """Owner-tenant Telegram/system alert through the durable outbox. Never raises."""
    if _suppressed_for_tests():
        return
    try:
        from app.services.notifications import system_alerts

        system_alerts._emit(
            system_alerts._tenants(), key=key, event_type=event_type, title=title,
            detail=detail, severity=severity,
        )
    except Exception as exc:  # noqa: BLE001 - an alert problem never changes a decision
        log.warning("live posture alert %s could not be queued: %s", event_type, type(exc).__name__)


def _ist_day(moment: datetime) -> str:
    return (moment.astimezone(timezone(timedelta(hours=5, minutes=30)))).strftime("%Y%m%d")


async def _identity_tags() -> tuple[str, str]:
    """Runtime SHA and release tag, read once (they shell out to git)."""
    if _STATE.tags is None:
        try:
            from app.core.release_manifest import release_tag, runtime_sha

            _STATE.tags = await asyncio.to_thread(lambda: (runtime_sha(), release_tag()))
        except Exception:  # noqa: BLE001
            _STATE.tags = ("", "")
    return _STATE.tags


async def observe_once(
    *,
    store: DeploymentIdentityStore | None = None,
    fetch: Fetch | None = None,
    services: Sequence[str] | None = None,
    now: Callable[[], datetime] = _now,
    alert: Alert | None = None,
    host_id: str | None = None,
) -> ObservationOutcome:
    """One look at this host's outbound address, and what to do about it.

    * nothing registered yet  -> register this address (trust on first use) and say so;
    * registered, same        -> confirm it (moves "last confirmed" forward);
    * registered, different   -> record what was seen, change NOTHING registered,
      alert critically; entries are refused until an operator accepts the new
      address (after updating the Zerodha whitelist);
    * unreadable              -> change nothing; once the last good observation is
      older than the grace period, alert (the refusal itself comes from the
      freshness window, with its own explicit reason).
    """
    identity = store or DeploymentIdentityStore()
    emit = alert or _default_alert
    moment = now()
    _STATE.last_attempt_at = moment.isoformat()

    discovery = await discover_outbound_ip(fetch, services)
    if discovery.ip is None:
        return _unreadable(identity, discovery.reason, moment, emit)

    ip = discovery.ip
    try:
        registered, source = resolve_registered(None, identity)
    except RegistrationUnreadable as exc:
        # Never register over a file that may hold the real address.
        _STATE.last_outcome, _STATE.last_detail = "registration_unreadable", str(exc)
        emit(key=f"egress-registration-unreadable:{_ist_day(moment)}",
             event_type="STATIC IP", title="ALL STRATEGIES", severity="CRITICAL",
             detail=f"The registered outbound IP file cannot be read ({exc}). New entries are "
                    "refused until it is repaired or removed on purpose; Sterling will not "
                    "register a new address over it.")
        return ObservationOutcome("registration_unreadable", ip, "", str(exc))

    sha, tag = await _identity_tags()
    host = host_id or socket.gethostname()

    def record() -> None:
        identity.observe(host_id=host, outbound_ip=ip, observed_at=moment.isoformat(),
                         runtime_sha=sha, release_tag=tag)

    if not registered:
        await asyncio.to_thread(identity.register, ip, source="first-observation",
                                note="trust on first use by the in-process observer")
        await asyncio.to_thread(record)
        _ok(ip, moment, "registered")
        emit(key=f"egress-registered:{ip}", event_type="STATIC IP", title="ALL STRATEGIES",
             severity="WARNING",
             detail=f"Registered outbound IP {ip} — it must be the static IP whitelisted at "
                    "Zerodha. If it is not, orders will be refused by Kite; whitelist this "
                    "address, or put the right one in place and run: sterlingctl egress accept "
                    "<ip>. It is registered once and never changed automatically.")
        log.warning("Registered outbound IP %s on first observation (trust on first use)", ip)
        return ObservationOutcome("registered", ip, ip, "first observation")

    await asyncio.to_thread(record)
    if ip == registered:
        was_wrong = _STATE.mismatch_alerted
        _ok(ip, moment, "confirmed")
        if was_wrong:
            _STATE.mismatch_alerted = False
            emit(key=f"egress-restored:{ip}:{int(moment.timestamp())}", event_type="STATIC IP",
                 title="ALL STRATEGIES", severity="INFO",
                 detail=f"Outbound IP is {ip} again, matching the registered address. "
                        "New entries are no longer blocked by the IP check.")
        return ObservationOutcome("confirmed", ip, registered, "matches")

    detail = (f"this host answers from {ip}, not the registered {registered} "
              f"(registered via {source})")
    _STATE.last_outcome, _STATE.last_detail = "mismatch", detail
    _STATE.last_ip, _STATE.last_ok_at = ip, moment.isoformat()
    _STATE.consecutive_failures = 0
    _STATE.mismatch_alerted = True
    how = ("Update STERLING_STATIC_EGRESS_IP and restart"
           if source == "environment" else f"Run: sterlingctl egress accept {ip}")
    emit(key=f"egress-changed:{registered}:{ip}:{_ist_day(moment)}", event_type="STATIC IP",
         title="ALL STRATEGIES", severity="CRITICAL",
         detail=f"Outbound IP changed: {registered} -> {ip}. Kite will refuse orders until the "
                "Zerodha whitelist and the registered IP are updated. New entries are refused "
                f"meanwhile; exits and protection continue. After the whitelist is updated to "
                f"{ip}: {how}.")
    log.critical("OUTBOUND IP CHANGED %s -> %s: entries refused until it is re-approved",
                 registered, ip)
    return ObservationOutcome("mismatch", ip, registered, detail)


def _ok(ip: str, moment: datetime, outcome: str) -> None:
    _STATE.last_ok_at, _STATE.last_ip = moment.isoformat(), ip
    _STATE.last_outcome, _STATE.last_detail = outcome, ""
    _STATE.consecutive_failures = 0
    _STATE.outage_alerted = False


def _unreadable(identity: DeploymentIdentityStore, reason: str, moment: datetime,
                emit: Alert) -> ObservationOutcome:
    _STATE.consecutive_failures += 1
    _STATE.last_outcome, _STATE.last_detail = "unreadable", reason
    latest = identity.latest()
    age = None
    if latest is not None:
        try:
            seen = datetime.fromisoformat(latest.observed_at)
            age = moment - (seen if seen.tzinfo else seen.replace(tzinfo=timezone.utc))
        except ValueError:
            age = None
    if age is not None and age >= OBSERVATION_GRACE and not _STATE.outage_alerted:
        _STATE.outage_alerted = True
        minutes = int(age.total_seconds() // 60)
        emit(key=f"egress-unreadable:{latest.observed_at}", event_type="STATIC IP",
             title="ALL STRATEGIES", severity="CRITICAL",
             detail=f"Sterling cannot confirm its outbound IP ({reason}); the last confirmed "
                    f"reading is {minutes} min old, so new entries are refused until it can. "
                    "Check this machine's internet connection. Exits and protection continue.")
        log.critical("Outbound IP unconfirmed for %d min (%s): entries refused", minutes, reason)
    else:
        log.warning("Outbound IP could not be read (%s); the last good reading stays valid for "
                    "up to %d min", reason, int(OBSERVATION_GRACE.total_seconds() // 60))
    return ObservationOutcome("unreadable", None, "", reason)


async def observer_loop(
    *,
    interval: float | None = None,
    retry: float | None = None,
    after_cycle: Callable[[], Awaitable[Any]] | None = None,
    **kwargs: Any,
) -> None:
    """Observe at boot and then every ~10 minutes (a minute after a failed read)."""
    _STATE.running = True
    try:
        await asyncio.sleep(1.0)
        while True:
            try:
                outcome = await observe_once(**kwargs)
                delay = (retry if retry is not None else OBSERVER_RETRY_S) \
                    if outcome.outcome == "unreadable" else \
                    (interval if interval is not None else OBSERVER_INTERVAL_S)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - one bad cycle never ends the observer
                log.exception("egress observer cycle failed")
                delay = retry if retry is not None else OBSERVER_RETRY_S
            if after_cycle is not None:
                try:
                    await after_cycle()
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001
                    log.exception("posture follow-up cycle failed")
            await asyncio.sleep(delay)
    finally:
        _STATE.running = False


def start_observer(after_cycle: Callable[[], Awaitable[Any]] | None = None
                   ) -> "asyncio.Task[Any] | None":
    """Start the observer task; None when disabled (or under the test suite)."""
    if not _env_on(OBSERVER_ENV) or _suppressed_for_tests():
        return None
    return asyncio.create_task(observer_loop(after_cycle=after_cycle), name="live-posture")


# ---------------------------------------------------------------------------
# Operator: accept a new address
# ---------------------------------------------------------------------------


async def accept_egress_ip(
    ip: str,
    *,
    by: str = "operator",
    store: DeploymentIdentityStore | None = None,
    fetch: Fetch | None = None,
    services: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Register ``ip`` as the static address, after proving this host answers from it.

    The operator says "I updated the Zerodha whitelist to X". The host is asked
    again, so a typo (or a VPN that is down) cannot register an address the host
    is not actually using. Refuses while the operator's environment pins another
    address: that line, not this file, is then the authority.
    """
    identity = store or DeploymentIdentityStore()
    try:
        literal = str(ip_address(str(ip).strip()))
    except ValueError as exc:
        raise ValueError("that is not a valid IP address") from exc
    declared = os.environ.get(EXPECTED_ENV, "").strip()
    if declared and declared != literal:
        raise ValueError(
            f"{EXPECTED_ENV} is set to {declared} in the environment, which overrides the "
            f"registered address. Change that line to {literal} and restart instead."
        )
    discovery = await discover_outbound_ip(fetch, services)
    if discovery.ip is None:
        raise ValueError(f"cannot confirm this host's address right now ({discovery.reason})")
    if discovery.ip != literal:
        raise ValueError(
            f"this host answers from {discovery.ip}, not {literal}; nothing was changed"
        )
    sha, tag = await _identity_tags()
    host = socket.gethostname()
    row = await asyncio.to_thread(
        identity.register, literal, source="operator", by=by, note="accepted after whitelist update")
    await asyncio.to_thread(
        lambda: identity.observe(host_id=host, outbound_ip=literal, runtime_sha=sha,
                                 release_tag=tag))
    _STATE.mismatch_alerted = False
    verdict = verify_deployment_identity(store=identity)
    return {"registered_ip": literal, "verified": verdict.verified, "reason": verdict.reason,
            "registered_at": row["registered_at"]}


# ---------------------------------------------------------------------------
# Deployment identity
# ---------------------------------------------------------------------------

DEPLOYMENT_ID_FILE = "data/manifests/deployment_id"


def ensure_deployment_id() -> str:
    """The deployment id the account binding is tied to, made stable without a ceremony.

    ``STERLING_DEPLOYMENT_ID`` wins when the operator set it. Otherwise a generated
    id is persisted once under the state root and exported into this process, so
    every check that reads the variable agrees, and a restart (or a new process on
    the same state root) gets the same one back. An unreadable file is never
    silently replaced: a new id would orphan the ACTIVE binding.
    """
    declared = (os.environ.get("STERLING_DEPLOYMENT_ID") or "").strip()
    if declared:
        return declared
    path = state_root() / DEPLOYMENT_ID_FILE
    if path.exists():
        value = path.read_text(encoding="utf-8").strip()
        if not value or len(value) > 120 or any(c.isspace() for c in value):
            raise RuntimeError(f"{path} does not hold a usable deployment id; fix or remove it")
    else:
        host = "".join(c if c.isalnum() else "-" for c in socket.gethostname().lower()).strip("-")
        value = f"sterling-{host or 'host'}-{uuid.uuid4().hex[:8]}"
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}")
        try:
            tmp.write_text(value + "\n", encoding="utf-8")
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)
        log.warning("Generated a stable deployment id %s (stored at %s)", value, path)
    os.environ["STERLING_DEPLOYMENT_ID"] = value
    return value


def boot_deployment_id() -> str:
    """The boot-time call: establish the id, except under the test suite.

    A test that boots the whole application must not write a deployment id into the
    repository's own state root or leak one into the environment of every later test.
    """
    if _suppressed_for_tests():
        return (os.environ.get("STERLING_DEPLOYMENT_ID") or "").strip()
    return ensure_deployment_id()


# ---------------------------------------------------------------------------
# Account binding
# ---------------------------------------------------------------------------

AUTO_BIND_ENV = "STERLING_AUTO_BIND_ACCOUNT"
BIND_INTERVAL_S = 60.0
#: After a failed broker read, wait before asking Kite again.
BIND_RETRY_S = 300.0
#: The NAME of the egress profile on an auto-declared binding (never the address).
EGRESS_PROFILE_NAME = "auto-registered-static-ip"
_REFUSAL_WORDS = ("does not match", "different", "no unambiguous")


@dataclass(frozen=True)
class BindOutcome:
    action: str   # disabled | bound | reverified | current | waiting | refused | error
    detail: str = ""
    binding_id: str = ""


@dataclass
class _BindState:
    last_action: str = "never"
    last_detail: str = ""
    last_at: str | None = None
    failures: int = 0
    next_attempt: float = 0.0
    lock: asyncio.Lock | None = None


_BIND = _BindState()


def auto_bind_enabled() -> bool:
    return _env_on(AUTO_BIND_ENV)


def _ms(value: str | None) -> float | None:
    if not value:
        return None
    try:
        stamp = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return (stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)).timestamp() * 1000


def _live_candidates(accounts: Any) -> list[Any]:
    """Real broker accounts only: the virtual tenant is never a binding candidate."""
    accounts.bootstrap()
    return [a for a in accounts.live_accounts()
            if not getattr(a, "is_sim", False) and not getattr(a, "is_paper", False)]


def _has_session(account: Any) -> bool:
    return bool(getattr(account, "connected", False)) and bool(getattr(account, "token_is_live", False))


async def ensure_account_binding(
    *,
    store: Any = None,
    verify: Callable[..., Awaitable[Any]] | None = None,
    accounts: Any = None,
    alert: Alert | None = None,
    now: Callable[[], datetime] = _now,
) -> BindOutcome:
    """Declare, verify and activate the broker-account binding when it is missing; keep it fresh.

    * No ACTIVE binding, exactly one live account with a bound Kite user and a
      current session: declare (scope ``live``, this deployment), verify read-only
      against Kite, activate.
    * An ACTIVE binding: re-verify after each new Kite login (the order-time
      refresh covers staleness between logins). If the live account's Kite user is
      not the bound client, REFUSE and alert; a binding is never moved silently.
    * Anything ambiguous (several live accounts, a binding an operator deactivated,
      a verification that fails) is refused and reported, never guessed.
    """
    from app.core.broker_account_binding import BindingError, BindingStatus
    from app.services.account_binding_service import AccountBindingStore

    if not auto_bind_enabled():
        return _bind_note(BindOutcome("disabled", f"{AUTO_BIND_ENV}=0"), now)
    if _BIND.lock is None:
        _BIND.lock = asyncio.Lock()
    async with _BIND.lock:
        emit = alert or _default_alert
        if accounts is None:
            from app.services.exchanges.kite import accounts as accounts  # noqa: PLC0415
        if verify is None:
            from app.services.broker_binding_verification import verify_current_kite_binding as verify
        try:
            live = _live_candidates(accounts)
            bindings = store or AccountBindingStore()
            active = bindings.active()
            deployment = ensure_deployment_id()
        except Exception as exc:  # noqa: BLE001 - unreadable state is a refusal, not a guess
            return _bind_refused(f"cannot read the binding state: {type(exc).__name__}: {exc}",
                                 emit, now, key=f"binding-unreadable:{_ist_day(now())}")
        if time.monotonic() < _BIND.next_attempt:
            return BindOutcome("waiting", "retrying after a failed broker read")

        async def verified_by_kite(binding_id: str, account: Any) -> Any:
            return await verify(binding_id, store=bindings, user_id=str(account.user_id),
                                application_account_id=str(account.id), deployment_id=deployment)

        if active is not None:
            return await _maintain(active, live, verified_by_kite, emit, now)
        return await _create(live, bindings, deployment, verified_by_kite, emit, now,
                             BindingError, BindingStatus)


def _bind_note(outcome: BindOutcome, now: Callable[[], datetime]) -> BindOutcome:
    _BIND.last_action, _BIND.last_detail = outcome.action, outcome.detail
    _BIND.last_at = now().isoformat()
    if outcome.action in {"bound", "reverified", "current"}:
        _BIND.failures, _BIND.next_attempt = 0, 0.0
    return outcome


def _bind_refused(detail: str, emit: Alert, now: Callable[[], datetime], *, key: str,
                  severity: str = "CRITICAL", binding_id: str = "") -> BindOutcome:
    emit(key=key, event_type="ACCOUNT BINDING", title="ALL STRATEGIES", severity=severity,
         detail=f"{detail} New entries stay refused until this is resolved; exits and protection "
                "continue.")
    if not (_BIND.last_action == "refused" and _BIND.last_detail == detail):
        log.log(logging.CRITICAL if severity == "CRITICAL" else logging.WARNING,
                "Account binding: %s", detail)
    return _bind_note(BindOutcome("refused", detail, binding_id), now)


def _bind_failed(exc: Exception, emit: Alert, now: Callable[[], datetime],
                 binding_id: str) -> BindOutcome:
    """A verification that did not complete. An identity conflict is critical; the rest retries."""
    detail = f"{type(exc).__name__}: {exc}"
    conflict = any(w in str(exc).lower() for w in _REFUSAL_WORDS)
    _BIND.failures += 1
    _BIND.next_attempt = time.monotonic() + BIND_RETRY_S
    if conflict or _BIND.failures == 3:
        return _bind_refused(
            f"The broker account binding could not be verified: {detail}.", emit, now,
            key=f"binding-verify:{binding_id}:{_ist_day(now())}",
            severity="CRITICAL" if conflict else "WARNING", binding_id=binding_id)
    log.warning("Account binding verification failed (will retry): %s", detail)
    return _bind_note(BindOutcome("error", detail, binding_id), now)


async def _maintain(active: Any, live: list[Any], verified_by_kite: Callable[..., Awaitable[Any]],
                    emit: Alert, now: Callable[[], datetime]) -> BindOutcome:
    mine = [a for a in live if str(getattr(a, "kite_user_id", "") or "").strip() == active.client_id]
    others = [a for a in live if str(getattr(a, "kite_user_id", "") or "").strip()
              and str(a.kite_user_id).strip() != active.client_id]
    if not mine:
        if others:
            return _bind_refused(
                f"The ACTIVE binding is for Kite user {active.client_id}, but the live account is "
                f"now logged in as {others[0].kite_user_id}. Sterling will not move a binding on "
                "its own: if the account really changed, deactivate the old binding and declare "
                "the new one deliberately.", emit, now,
                key=f"binding-changed:{active.binding_id}:{others[0].kite_user_id}",
                binding_id=active.binding_id)
        return _bind_note(BindOutcome(
            "waiting", f"waiting for a Kite login as {active.client_id}", active.binding_id), now)
    account = mine[0]
    verified_ms = _ms(active.verified_at)
    login_ms = getattr(account, "last_login_at_ms", None)
    stale_for_login = verified_ms is None or (login_ms is not None and login_ms > verified_ms)
    if not stale_for_login:
        # Verified since the last login. Between logins the order-time refresh keeps the
        # evidence inside its 15-minute window, so nothing is asked of the broker here.
        return _bind_note(BindOutcome("current", "verified since the last login",
                                      active.binding_id), now)
    if not _has_session(account):
        return _bind_note(BindOutcome("waiting", "waiting for the Kite session", active.binding_id), now)
    try:
        refreshed = await verified_by_kite(active.binding_id, account)
    except Exception as exc:  # noqa: BLE001
        return _bind_failed(exc, emit, now, active.binding_id)
    log.info("Account binding %s re-verified after the Kite login", refreshed.binding_id)
    return _bind_note(BindOutcome("reverified", "re-verified after the Kite login",
                                  refreshed.binding_id), now)


async def _create(live: list[Any], store: Any, deployment: str,
                  verified_by_kite: Callable[..., Awaitable[Any]], emit: Alert,
                  now: Callable[[], datetime], binding_error: type,
                  status: Any) -> BindOutcome:
    if not live:
        return _bind_note(BindOutcome("waiting", "no live Kite account yet"), now)
    if len(live) > 1:
        return _bind_refused(
            f"{len(live)} live Kite accounts exist and none is bound. Automatic binding needs "
            "exactly one; declare the binding for the right account with sterlingctl bind.",
            emit, now, key=f"binding-ambiguous:{_ist_day(now())}", severity="WARNING")
    account = live[0]
    kite_user = str(getattr(account, "kite_user_id", "") or "").strip()
    if not kite_user:
        return _bind_note(BindOutcome("waiting", "the account has not logged in to Kite yet; it "
                                      "is bound right after the first login"), now)
    if not _has_session(account):
        return _bind_note(BindOutcome("waiting", "waiting for the Kite session"), now)

    try:
        existing = [b for b in store.all()
                    if b.broker.strip().lower() == "zerodha" and b.client_id == kite_user]
    except Exception as exc:  # noqa: BLE001
        return _bind_refused(f"cannot read the binding store: {exc}", emit, now,
                             key=f"binding-unreadable:{_ist_day(now())}")
    if any(b.status is status.DEACTIVATED for b in existing):
        return _bind_refused(
            f"The binding for Kite user {kite_user} was deactivated on purpose, so Sterling will "
            "not bind it again by itself. Declare and activate a new binding with sterlingctl "
            "bind.", emit, now, key=f"binding-deactivated:{kite_user}", severity="WARNING")

    reusable = [b for b in existing
                if b.status in (status.DECLARED, status.VERIFIED)
                and b.deployment_id == deployment and "live" in b.account_scope.lower()
                and b.static_egress_profile.strip()]
    try:
        binding = reusable[-1] if reusable else store.declare(
            broker="zerodha",
            legal_account_holder=str(getattr(account, "user_name", "") or kite_user),
            client_id=kite_user,
            account_scope="live",
            deployment_id=deployment,
            static_egress_profile=EGRESS_PROFILE_NAME,
            notes="declared automatically after the Kite login (STERLING_AUTO_BIND_ACCOUNT)",
        )
    except binding_error as exc:
        return _bind_refused(f"The binding could not be declared: {exc}", emit, now,
                             key=f"binding-declare:{kite_user}:{_ist_day(now())}",
                             severity="WARNING")
    try:
        await verified_by_kite(binding.binding_id, account)
        activated = store.activate(binding.binding_id)
    except Exception as exc:  # noqa: BLE001
        return _bind_failed(exc, emit, now, binding.binding_id)
    emit(key=f"binding-bound:{activated.binding_id}", event_type="ACCOUNT BINDING",
         title="ALL STRATEGIES", severity="INFO",
         detail=f"Bound this deployment to Kite account {kite_user} (live), verified read-only "
                "against the broker. It is never moved to another account automatically.")
    log.warning("Account binding %s declared, verified and activated for Kite user %s",
                activated.binding_id, kite_user)
    return _bind_note(BindOutcome("bound", f"bound to Kite user {kite_user}",
                                  activated.binding_id), now)


async def binding_loop(*, interval: float | None = None, **kwargs: Any) -> None:
    """Keep the binding in step with the Kite login, a minute at a time (local checks only)."""
    await asyncio.sleep(3.0)
    while True:
        try:
            await ensure_account_binding(**kwargs)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - one bad cycle never ends the loop
            log.exception("account binding cycle failed")
        await asyncio.sleep(interval if interval is not None else BIND_INTERVAL_S)


def start_posture_tasks() -> list["asyncio.Task[Any]"]:
    """Boot the observer and the binding keeper. Empty under the test suite."""
    if _suppressed_for_tests():
        return []
    tasks: list[asyncio.Task[Any]] = []
    observer = start_observer()
    if observer is not None:
        tasks.append(observer)
    if auto_bind_enabled():
        tasks.append(asyncio.create_task(binding_loop(), name="live-binding"))
    return tasks


# ---------------------------------------------------------------------------
# Posture facts for the live status
# ---------------------------------------------------------------------------

SECURE_KEYS_LINE = "Live trading needs secure keys: run python scripts/sterling_live_setup.py"


def announce_secure_keys_needed(exc: BaseException | None = None) -> str:
    """The ONE plain line printed at boot when live security validation fails.

    Without it a machine that never ran the setup script boots with a broker that
    can neither decrypt nor trade, and nothing says why. The technical reason goes
    to the log, not the line.
    """
    print(SECURE_KEYS_LINE, flush=True)
    log.critical("%s (%s)", SECURE_KEYS_LINE, exc)
    return SECURE_KEYS_LINE

_POSTURE_CACHE: tuple[float, dict[str, Any] | None] = (0.0, None)
_POSTURE_TTL_S = 30.0


def _secrets_fact() -> dict[str, Any]:
    from app.core.security import validate_live_security

    try:
        validate_live_security()
        return {"ok": True, "problem": ""}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "problem": str(exc)[:300]}


def _egress_fact() -> dict[str, Any]:
    from app.services.continuity_doctor import egress_status

    store = DeploymentIdentityStore()
    try:
        registered, source = resolve_registered(None, store)
    except RegistrationUnreadable:
        registered, source = "", "unreadable"
    try:
        status = egress_status()
        verified, reason = bool(status.verified), status.reason
        observed, observed_at = status.observed, status.observed_at
    except Exception as exc:  # noqa: BLE001
        verified, reason, observed, observed_at = False, f"{type(exc).__name__}: {exc}", None, None
    return {
        "registered": bool(registered),
        "registered_ip": registered or None,
        "registration_source": source or None,
        "observed_ip": observed,
        "observed_at": observed_at,
        "verified": verified,
        "reason": reason,
        "observer": observer_status(),
    }


def _binding_fact() -> dict[str, Any]:
    from app.services.account_binding_service import binding_health

    health = binding_health()
    return {
        "active": bool(health.get("account_binding_id")),
        "binding_id": health.get("account_binding_id"),
        "live_ready": bool(health.get("account_binding_live_ready")),
        "verification_current": bool(health.get("account_binding_verification_current")),
        "detail": health.get("detail") or health.get("error") or "",
        "deployment_id_set": bool((os.environ.get("STERLING_DEPLOYMENT_ID") or "").strip()),
        "auto_bind": {
            "enabled": auto_bind_enabled(),
            "last_action": _BIND.last_action,
            "detail": _BIND.last_detail,
            "last_at": _BIND.last_at,
        },
    }


def posture_status(*, refresh: bool = False) -> dict[str, Any]:
    """What a real broker entry needs, and which of it is missing. Never any secret.

    Cached briefly: the live status is read on every recovery cycle and the
    secrets check inspects the credential store.
    """
    global _POSTURE_CACHE
    stamp, cached = _POSTURE_CACHE
    if not refresh and cached is not None and time.monotonic() - stamp < _POSTURE_TTL_S:
        return cached
    secrets = _secrets_fact()
    egress = _egress_fact()
    binding = _binding_fact()
    missing: list[str] = []
    if not secrets["ok"]:
        missing.append("secure keys")
    if not egress["registered"]:
        missing.append("static IP not registered yet")
    elif not egress["verified"]:
        missing.append("static IP not verified")
    if not binding["live_ready"]:
        missing.append("account binding")
    result = {
        "ready": not missing,
        "missing": missing,
        "secrets": secrets,
        "egress": egress,
        "binding": binding,
        "setup_hint": SECURE_KEYS_LINE if not secrets["ok"] else "",
    }
    _POSTURE_CACHE = (time.monotonic(), result)
    return result
