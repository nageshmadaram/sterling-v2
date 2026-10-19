"""What the deployed host actually observed about itself.

Section 11 is careful about one thing: a static IP is not a configuration flag.
A deployment can be configured to have a static address and still be answering
from a different one after a router reconnect, and the broker's allowlist cares
about the second fact. So this records observations — the host that saw them,
the address it saw, when, and which router generation it was on — and compares
them against what is registered.

Nothing here performs a network call. An application that asks the internet
what its own address is has made a safety check depend on a third party being
up, and has learned nothing about what the broker sees anyway. The deployment
takes the observation; this stores and judges it.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from ipaddress import ip_address
from pathlib import Path

from app.core.state_paths import state_root
from typing import Any, Final

__all__ = [
    "DeploymentObservation", "DeploymentIdentityStore", "IdentityVerdict",
    "RegistrationUnreadable", "resolve_registered", "verify_deployment_identity",
    "MAX_OBSERVATION_AGE",
    "IDENTITY_FILE", "REGISTRATION_FILE", "EXPECTED_ENV",
]

IDENTITY_FILE: Final[str] = "data/manifests/deployment_identity.json"
#: The registered static address when the operator did not put it in the
#: environment: a sibling of the identity file, written by the observer (first
#: sight, see live_posture) or by an operator accepting a new address.
REGISTRATION_FILE: Final[str] = "egress_registration.json"
EXPECTED_ENV: Final[str] = "STERLING_STATIC_EGRESS_IP"

#: An observation older than this is history, not a current fact. A lease can
#: renew, a router can reboot, an ISP can move a customer between pools.
MAX_OBSERVATION_AGE: Final[timedelta] = timedelta(days=7)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _root() -> Path:
    return state_root()


class RegistrationUnreadable(RuntimeError):
    """A registration file exists but cannot be trusted. Never read as "none"."""


def _atomic_write(path: Path, text: str) -> None:
    """Write whole or not at all: an order-time reader must never see half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def _literal_ip(value: str) -> str | None:
    """Return a canonical literal IP, never a hostname or arbitrary label."""
    try:
        return str(ip_address(str(value or "").strip()))
    except ValueError:
        return None


@dataclass(frozen=True)
class DeploymentObservation:
    """One reading of where this deployment answers from."""

    host_id: str
    outbound_ip: str
    observed_at: str
    router_generation: str = ""
    runtime_sha: str = ""
    release_tag: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "host_id": self.host_id,
            "outbound_ip": self.outbound_ip,
            "observed_at": self.observed_at,
            "router_generation": self.router_generation,
            "runtime_sha": self.runtime_sha,
            "release_tag": self.release_tag,
        }


class DeploymentIdentityStore:
    """Append-only history of observations, newest last."""

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path else _root() / IDENTITY_FILE

    def read(self) -> list[DeploymentObservation]:
        if not self.path.exists():
            return []
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - a corrupt file is not an observation
            return []
        return [
            DeploymentObservation(
                host_id=str(row.get("host_id") or ""),
                outbound_ip=str(row.get("outbound_ip") or ""),
                observed_at=str(row.get("observed_at") or ""),
                router_generation=str(row.get("router_generation") or ""),
                runtime_sha=str(row.get("runtime_sha") or ""),
                release_tag=str(row.get("release_tag") or ""),
            )
            for row in payload.get("observations", [])
        ]

    def latest(self) -> DeploymentObservation | None:
        history = self.read()
        return history[-1] if history else None

    def record(
        self,
        *,
        host_id: str,
        outbound_ip: str,
        router_generation: str = "",
        observed_at: str | None = None,
        runtime_sha: str = "",
        release_tag: str = "",
    ) -> DeploymentObservation:
        """Append one observation. Nothing is ever overwritten.

        The history is the point: "the address changed on the 3rd" is a fact an
        operator needs, and it is invisible in a file that only holds the
        current value.
        """
        host = str(host_id).strip()
        raw_ip = str(outbound_ip).strip()
        if not host or not raw_ip:
            raise ValueError("an observation must name the host and the address it saw")
        normalized_ip = _literal_ip(raw_ip)
        if normalized_ip is None:
            raise ValueError("outbound_ip must be a valid IPv4 or IPv6 address")

        observation = DeploymentObservation(
            host_id=host,
            outbound_ip=normalized_ip,
            observed_at=observed_at or _now().isoformat(),
            router_generation=str(router_generation).strip(),
            runtime_sha=str(runtime_sha).strip(),
            release_tag=str(release_tag).strip(),
        )
        self._write([*self.read(), observation])
        return observation

    def _write(self, history: list[DeploymentObservation]) -> None:
        _atomic_write(
            self.path,
            json.dumps({"updated_at": _now().isoformat(),
                        "observations": [o.as_dict() for o in history]},
                       indent=2, sort_keys=True) + "\n",
        )

    def observe(
        self,
        *,
        host_id: str,
        outbound_ip: str,
        router_generation: str = "",
        observed_at: str | None = None,
        runtime_sha: str = "",
        release_tag: str = "",
    ) -> DeploymentObservation:
        """Record what an in-process observer just saw, without growing the file.

        An observer reads the address every few minutes. Appending a row each
        time would bury the one fact worth keeping, so an unchanged address only
        moves the latest row's ``observed_at`` forward ("last confirmed"). A
        different address (or host) is appended exactly as :meth:`record` does,
        so "the address changed on the 3rd" stays in the history.
        """
        history = self.read()
        latest = history[-1] if history else None
        ip = _literal_ip(outbound_ip)
        if (
            latest is not None and ip is not None
            and _literal_ip(latest.outbound_ip) == ip
            and latest.host_id == str(host_id).strip()
        ):
            confirmed = DeploymentObservation(
                host_id=latest.host_id,
                outbound_ip=ip,
                observed_at=observed_at or _now().isoformat(),
                router_generation=str(router_generation).strip() or latest.router_generation,
                runtime_sha=str(runtime_sha).strip() or latest.runtime_sha,
                release_tag=str(release_tag).strip() or latest.release_tag,
            )
            self._write([*history[:-1], confirmed])
            return confirmed
        return self.record(
            host_id=host_id, outbound_ip=outbound_ip, router_generation=router_generation,
            observed_at=observed_at, runtime_sha=runtime_sha, release_tag=release_tag,
        )

    # -- the registered static address -------------------------------------
    @property
    def registration_path(self) -> Path:
        return self.path.with_name(REGISTRATION_FILE)

    def registration(self) -> dict[str, Any] | None:
        """The registered address on file, or None when nothing was ever registered.

        A file that exists but is unreadable raises: "unreadable" must never
        look like "never registered", or the next observation would register
        itself over whatever was there.
        """
        path = self.registration_path
        if not path.exists():
            return None
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            raise RegistrationUnreadable(f"{path.name} is unreadable: {exc}") from exc
        ip = _literal_ip(str(row.get("registered_ip") or "")) if isinstance(row, dict) else None
        if ip is None:
            raise RegistrationUnreadable(f"{path.name} does not hold a valid IP address")
        return {**row, "registered_ip": ip}

    def register(self, ip: str, *, source: str, by: str = "", note: str = "") -> dict[str, Any]:
        """Make ``ip`` the registered static address. The previous one is kept."""
        literal = _literal_ip(ip)
        if literal is None:
            raise ValueError("the registered address must be a valid IPv4 or IPv6 address")
        try:
            previous = self.registration()
        except RegistrationUnreadable:
            previous = None
        earlier = list((previous or {}).get("previous") or [])
        if previous:
            earlier.append({k: previous.get(k) for k in
                            ("registered_ip", "registered_at", "source", "by")})
        row = {
            "registered_ip": literal,
            "registered_at": _now().isoformat(),
            "source": str(source).strip(),
            "by": str(by).strip(),
            "note": str(note).strip(),
            "previous": earlier[-20:],
        }
        _atomic_write(self.registration_path, json.dumps(row, indent=2, sort_keys=True) + "\n")
        return row


@dataclass(frozen=True)
class IdentityVerdict:
    """May this host be trusted to reach the broker under its registered address?"""

    #: True, False, or None when it could not be determined.
    verified: bool | None
    reason: str
    observation: DeploymentObservation | None = None
    expected_ip: str = ""
    changed_from: str = ""
    #: Where the registered address came from: "environment", "registration"
    #: (the file the observer or an operator wrote) or "" when there is none.
    source: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "verified": self.verified,
            "reason": self.reason,
            "expected_ip": self.expected_ip,
            "changed_from": self.changed_from,
            "source": self.source,
            "observation": self.observation.as_dict() if self.observation else None,
        }


def resolve_registered(
    expected_ip: str | None = None,
    store: "DeploymentIdentityStore | None" = None,
) -> tuple[str, str]:
    """The registered static address and where it came from.

    An address the caller passes, or the operator's environment, is declared
    intent and wins. Only when neither says anything does the registration file
    (first sight by the observer, or an operator's accept) answer. Raises
    :class:`RegistrationUnreadable` when that file exists and cannot be trusted.
    """
    if expected_ip is not None:
        return str(expected_ip).strip(), "argument"
    declared = os.environ.get(EXPECTED_ENV, "").strip()
    if declared:
        return declared, "environment"
    row = (store or DeploymentIdentityStore()).registration()
    return (row["registered_ip"], "registration") if row else ("", "")


def _describe_age(age: timedelta, limit: timedelta) -> str:
    if limit >= timedelta(days=1):
        return f"the address was last verified {age.days} days ago, which is not a current fact"
    minutes = int(age.total_seconds() // 60)
    return (
        f"the address was last confirmed {minutes} min ago (limit {int(limit.total_seconds() // 60)} "
        "min) and cannot be re-checked now, so it is not a current fact"
    )


def verify_deployment_identity(
    *,
    expected_ip: str | None = None,
    store: DeploymentIdentityStore | None = None,
    now: datetime | None = None,
    max_age: timedelta | None = None,
) -> IdentityVerdict:
    """Compare the latest observation with the registered static address.

    ``max_age`` is how old the latest observation may be. It defaults to
    :data:`MAX_OBSERVATION_AGE`, which suits a person recording an address by
    hand. A process that observes its own address every few minutes passes a
    window matched to that cadence instead (see live_posture).
    """
    identity_store = store or DeploymentIdentityStore()
    history = identity_store.read()
    latest = history[-1] if history else None
    moment = now or _now()
    limit = max_age if max_age is not None else MAX_OBSERVATION_AGE

    try:
        registered, source = resolve_registered(expected_ip, identity_store)
    except RegistrationUnreadable as exc:
        return IdentityVerdict(
            False, f"the registered static address cannot be read ({exc}); "
                   "fix or remove it deliberately, it is never replaced automatically",
            latest, "", "", "registration")

    if not registered:
        return IdentityVerdict(
            None, "no static address is registered for this deployment", latest)

    registered_ip = _literal_ip(registered)
    if registered_ip is None:
        return IdentityVerdict(
            False,
            f"registered static egress {registered!r} is not a valid IP address",
            latest,
            registered,
            source=source,
        )
    if latest is None:
        return IdentityVerdict(
            None, "no deployment observation has been recorded", None, registered_ip,
            source=source)

    observed_ip = _literal_ip(latest.outbound_ip)
    if observed_ip is None:
        return IdentityVerdict(
            False,
            f"latest deployment observation {latest.outbound_ip!r} is not a valid IP address",
            latest,
            registered_ip,
            source=source,
        )

    try:
        seen_at = datetime.fromisoformat(latest.observed_at)
    except ValueError:
        return IdentityVerdict(
            None, f"observation timestamp is unreadable: {latest.observed_at!r}",
            latest, registered, source=source)
    if seen_at.tzinfo is None:
        return IdentityVerdict(
            None,
            "deployment observation timestamp has no timezone; cannot establish "
            "when the egress address was checked",
            latest, registered_ip, source=source,
        )
    age = moment - seen_at
    if age < timedelta(0):
        return IdentityVerdict(
            None,
            "deployment observation timestamp is in the future; clock state is not trustworthy",
            latest,
            registered_ip,
            source=source,
        )

    previous = history[-2] if len(history) > 1 else None
    previous_ip = _literal_ip(previous.outbound_ip) if previous else None
    changed_from = (
        previous.outbound_ip
        if previous and previous_ip is not None and previous_ip != observed_ip
        else ""
    )

    if observed_ip != registered_ip:
        return IdentityVerdict(
            False,
            f"this host answers from {observed_ip}, not the registered "
            f"{registered_ip}; broker automation must stay blocked until it is re-approved "
            f"(after the Zerodha whitelist is updated: sterlingctl egress accept {observed_ip})",
            latest, registered_ip, changed_from, source)

    if age > limit:
        return IdentityVerdict(
            None, _describe_age(age, limit), latest, registered_ip, changed_from, source)

    if changed_from:
        # It matches now, but it moved. That is worth saying out loud: the
        # broker's allowlist may have been wrong in between.
        return IdentityVerdict(
            True,
            f"{latest.outbound_ip} matches, having changed from {changed_from}",
            latest, registered_ip, changed_from, source)

    return IdentityVerdict(True, f"{observed_ip} matches the registered address",
                           latest, registered_ip, source=source)
