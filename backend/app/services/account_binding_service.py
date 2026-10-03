"""The store and the migration procedure for broker account bindings.

One file per binding under ``data/manifests/account_bindings``. Files rather
than a table because this is exactly the state an operator needs when the
service will not start: ``cat`` must be enough to answer "which account is this
deployment pointing at, and who owns it?".

Deactivated bindings are never deleted and never rewritten. A migration adds a
row; it does not edit history. The old account's fills keep the old segment
forever, which is the only way "was this our trade?" stays answerable after the
person who set the system up is no longer available to ask.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.core.state_paths import state_root
from typing import Any, Final, Iterable, Mapping

from app.core.broker_account_binding import (
    BINDING_MANIFEST_DIR,
    BindingError,
    BindingStatus,
    BrokerAccountBinding,
    assert_no_secrets,
    binding_from_row,
    new_binding_id,
    segment_id,
)

__all__ = [
    "AccountBindingStore",
    "MigrationStep",
    "MIGRATION_PROCEDURE",
    "active_binding",
    "binding_health",
    "attribute_fill",
    "FillAttributionError",
    "BindingVerificationEvidence",
    "MAX_BINDING_VERIFICATION_AGE",
    "VERIFICATION_DIR",
    "verification_is_current",
]


def _root() -> Path:
    return state_root()


MAX_BINDING_VERIFICATION_AGE: Final[timedelta] = timedelta(minutes=15)
VERIFICATION_DIR: Final[str] = "data/manifests/account_binding_verifications"


@dataclass(frozen=True)
class BindingVerificationEvidence:
    """Fresh read-only broker facts required before a binding may advance."""

    binding_id: str
    profile_client_id: str
    application_account_id: str
    deployment_id: str
    observed_at: str
    positions_read: bool
    orders_read: bool
    protective_orders_read: bool
    position_count: int = 0
    order_count: int = 0
    protective_order_count: int = 0
    evidence_ref: str = ""
    evidence_sha256: str = ""

    @property
    def inventory_read(self) -> bool:
        return (
            self.positions_read is True
            and self.orders_read is True
            and self.protective_orders_read is True
        )


def _parsed_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        stamp = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc)


def _verification_evidence_intact(binding: BrokerAccountBinding) -> bool:
    """The current binding must still point at the exact verifier-written bytes."""
    digest = str(binding.verification_evidence_sha256 or "").strip().lower()
    if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
        return False

    path = Path(str(binding.verification_evidence_ref or "")).expanduser()
    if not path.is_absolute():
        path = _root() / path
    try:
        path = path.resolve()
        canonical = (_root() / VERIFICATION_DIR / binding.binding_id).resolve()
    except OSError:
        return False
    if path.suffix.lower() != ".json" or canonical not in path.parents or not path.is_file():
        return False

    try:
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return False
    return actual == digest


def verification_is_current(
    binding: BrokerAccountBinding,
    *,
    now: datetime | None = None,
) -> bool:
    """Fresh, intact broker evidence — not metadata status — determines readiness."""
    stamp = _parsed_utc(binding.verified_at)
    if stamp is None:
        return False
    if not (
        binding.verification_inventory_read is True
        and binding.verification_profile_client_id == binding.client_id
        and binding.verification_deployment_id == binding.deployment_id
        and bool(binding.verification_account_id)
        and bool(binding.verification_evidence_ref)
        and _verification_evidence_intact(binding)
    ):
        return False
    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    age = moment - stamp
    return timedelta(0) <= age <= MAX_BINDING_VERIFICATION_AGE


class FillAttributionError(ValueError):
    """A broker fact was offered to the wrong account's evidence segment."""


@dataclass(frozen=True)
class MigrationStep:
    order: int
    name: str
    description: str
    #: Can this step be done by the software, or is it an operator/legal action?
    automated: bool


#: Section 11.2, written down where the code can show it to an operator.
MIGRATION_PROCEDURE: Final[tuple[MigrationStep, ...]] = (
    MigrationStep(1, "safe_mode", "Engage SAFE_MODE; block every new-exposure path.", True),
    MigrationStep(2, "resolve_exposure", "Close or reconcile Sterling-managed positions in the old account.", False),
    MigrationStep(3, "deactivate_old", "Deactivate the old binding. Its fills are never reassigned.", True),
    MigrationStep(4, "create_new", "Create the new authorised binding and its Kite app/client configuration.", True),
    MigrationStep(5, "verify_api", "Verify API access, static-IP registration, order permission and margin endpoints.", False),
    MigrationStep(6, "doctor_reconcile", "Run broker read-only doctor and reconciliation against the new account.", True),
    MigrationStep(7, "shadow_acceptance", "Run market-data and shadow-only acceptance on the new account.", True),
    MigrationStep(8, "new_segment", "Start a new execution-account evidence segment; keep the strategy identity.", True),
    MigrationStep(9, "hold_live", "Do not enable LIVE_MINIMUM until shadow, reconciliation and protection drills pass.", False),
)


class AccountBindingStore:
    """Metadata-only, append-mostly store of broker account bindings."""

    def __init__(self, directory: Path | str | None = None) -> None:
        self.directory = Path(directory) if directory else _root() / BINDING_MANIFEST_DIR

    # -- reading -----------------------------------------------------------
    def _paths(self) -> list[Path]:
        if not self.directory.exists():
            return []
        return sorted(p for p in self.directory.glob("*.json") if p.is_file())

    def all(self) -> list[BrokerAccountBinding]:
        out: list[BrokerAccountBinding] = []
        for path in self._paths():
            try:
                row = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise BindingError(f"account binding {path.name} is unreadable: {exc}") from exc
            out.append(binding_from_row(row))
        return out

    def get(self, binding_id: str) -> BrokerAccountBinding | None:
        for binding in self.all():
            if binding.binding_id == binding_id:
                return binding
        return None

    def active(self) -> BrokerAccountBinding | None:
        """The one ACTIVE binding. Two is a configuration error, not a choice."""
        actives = [b for b in self.all() if b.status is BindingStatus.ACTIVE]
        if len(actives) > 1:
            raise BindingError(
                "more than one ACTIVE broker account binding: "
                + ", ".join(b.binding_id for b in actives)
                + ". Deactivate all but one before trading."
            )
        return actives[0] if actives else None

    # -- writing -----------------------------------------------------------
    def _path_for(self, binding_id: str) -> Path:
        return self.directory / f"{binding_id}.json"

    def save(self, binding: BrokerAccountBinding) -> Path:
        row = binding.as_row()
        assert_no_secrets(row)
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self._path_for(binding.binding_id)
        path.write_text(json.dumps(row, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return path

    def declare(
        self,
        *,
        broker: str,
        legal_account_holder: str,
        client_id: str,
        account_scope: str,
        deployment_id: str,
        static_egress_profile: str = "",
        notes: str = "",
        **extra: Any,
    ) -> BrokerAccountBinding:
        """Create a binding in DECLARED state. Never activates it."""
        assert_no_secrets(extra)
        if extra:
            raise BindingError(f"unknown binding field(s): {', '.join(sorted(extra))}")
        binding = BrokerAccountBinding(
            binding_id=new_binding_id(broker, client_id),
            broker=broker,
            legal_account_holder=legal_account_holder,
            client_id=client_id,
            account_scope=account_scope,
            deployment_id=deployment_id,
            static_egress_profile=static_egress_profile,
            notes=notes,
        )
        if self.get(binding.binding_id) is not None:
            raise BindingError(
                f"binding {binding.binding_id} already exists; deactivate it before "
                "declaring another for the same account and day"
            )
        self.save(binding)
        return binding

    def activate(self, binding_id: str) -> BrokerAccountBinding:
        """Make one binding ACTIVE, refusing while another still is.

        Silently deactivating the incumbent would be the one action that can
        move money to the wrong account. The operator deactivates it explicitly.
        """
        binding = self.get(binding_id)
        if binding is None:
            raise BindingError(f"no such binding: {binding_id}")
        if binding.status not in (BindingStatus.VERIFIED, BindingStatus.ACTIVE):
            raise BindingError(
                f"{binding_id} is {binding.status.value}; run fresh broker verification first"
            )
        if not verification_is_current(binding):
            raise BindingError(
                f"{binding_id} broker verification is absent or stale; verify again"
            )
        current = self.active()
        if current is not None and current.binding_id != binding_id:
            raise BindingError(
                f"{current.binding_id} is still ACTIVE; deactivate it before "
                f"activating {binding_id}"
            )
        updated = binding.activated()
        self.save(updated)
        return updated

    def deactivate(self, binding_id: str, *, reason: str = "") -> BrokerAccountBinding:
        binding = self.get(binding_id)
        if binding is None:
            raise BindingError(f"no such binding: {binding_id}")
        updated = binding.deactivated()
        if reason:
            updated = binding_from_row({**updated.as_row(), "notes": reason})
        self.save(updated)
        return updated

    def verify(
        self,
        binding_id: str,
        evidence: BindingVerificationEvidence,
    ) -> BrokerAccountBinding:
        """Persist VERIFIED only from current read-only broker evidence."""
        binding = self.get(binding_id)
        if binding is None:
            raise BindingError(f"no such binding: {binding_id}")
        if evidence.binding_id != binding_id:
            raise BindingError("broker verification evidence names a different binding")
        observed = _parsed_utc(evidence.observed_at)
        if observed is None:
            raise BindingError("broker verification timestamp is missing or invalid")
        age = datetime.now(timezone.utc) - observed
        if age < timedelta(0) or age > MAX_BINDING_VERIFICATION_AGE:
            raise BindingError("broker verification evidence is stale")
        if not evidence.inventory_read:
            raise BindingError("broker inventory verification is incomplete")
        digest = str(evidence.evidence_sha256 or "").strip().lower()
        if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
            raise BindingError("broker verification evidence SHA-256 is missing or invalid")
        updated = binding.verified(
            profile_client_id=evidence.profile_client_id,
            application_account_id=evidence.application_account_id,
            deployment_id=evidence.deployment_id,
            inventory_read=True,
            evidence_ref=evidence.evidence_ref,
            evidence_sha256=digest,
            when=evidence.observed_at,
        )
        self.save(updated)
        return updated


def active_binding(directory: Path | str | None = None) -> BrokerAccountBinding | None:
    return AccountBindingStore(directory).active()


def attribute_fill(
    fill: Mapping[str, Any],
    binding: BrokerAccountBinding,
) -> dict[str, Any]:
    """Stamp a broker fact with the segment that produced it.

    Refuses when the fill already names a different account. A fill that
    travelled from one account's book to another's reconciliation is not a
    bookkeeping wrinkle: it is a false record of where money went.
    """
    stated = str(fill.get("account_segment") or "").strip()
    expected = segment_id(binding)
    if stated and stated != expected:
        raise FillAttributionError(
            f"fill belongs to segment {stated!r}, not {expected!r}; broker facts "
            "are never reassigned between accounts"
        )
    client = str(fill.get("client_id") or "").strip()
    if client and client != binding.client_id:
        raise FillAttributionError(
            f"fill was reported for client {client!r} but the binding is "
            f"{binding.client_id!r}"
        )
    out = dict(fill)
    out["account_segment"] = expected
    out["account_binding_id"] = binding.binding_id
    return out


def segments_in(rows: Iterable[Mapping[str, Any]]) -> set[str]:
    return {str(r.get("account_segment") or "") for r in rows}


def binding_health(directory: Path | str | None = None) -> dict[str, Any]:
    """What the dashboard and doctor show. Unknown never reads as ready."""
    try:
        store = AccountBindingStore(directory)
        binding = store.active()
    except BindingError as exc:
        return {
            "account_binding_readable": False,
            "account_binding_ready": False,
            "account_binding_id": None,
            "error": str(exc),
        }

    if binding is None:
        return {
            "account_binding_readable": True,
            "account_binding_ready": False,
            "account_binding_id": None,
            "detail": "no ACTIVE broker account binding is declared",
        }

    verification_current = verification_is_current(binding)
    return {
        "account_binding_readable": True,
        "account_binding_ready": binding.usable and verification_current,
        "account_binding_live_ready": binding.live_ready and verification_current,
        "account_binding_verification_current": verification_current,
        "account_binding_verified_at": binding.verified_at,
        "account_binding_id": binding.binding_id,
        "account_binding_status": binding.status.value,
        "broker": binding.broker,
        "legal_account_holder": binding.legal_account_holder,
        "account_scope": binding.account_scope,
        "deployment_id": binding.deployment_id,
        "static_egress_profile": binding.static_egress_profile or None,
        "segment": binding.segment,
    }


def render_migration_procedure() -> str:
    lines = ["BROKER ACCOUNT MIGRATION", ""]
    for step in MIGRATION_PROCEDURE:
        who = "sterlingctl" if step.automated else "operator"
        lines.append(f"{step.order}. [{who}] {step.description}")
    lines.extend(
        [
            "",
            "The strategy identity does not change. The execution-evidence",
            "segment does: old fills stay with the old account forever.",
        ]
    )
    return "\n".join(lines)
