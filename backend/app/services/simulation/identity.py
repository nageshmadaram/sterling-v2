"""Who is simulated. The single source of truth for sim identities.

A simulation runs as a second *tenant*: ``sim:<owner>`` with one virtual account
``SIM-<owner-hash>``. Nothing here talks to a broker; it only names things so every
other module can ask the same question the same way ("is this uid / account virtual?").
"""
from __future__ import annotations

import hashlib

SIM_PREFIX = "sim:"
SIM_ACCOUNT_PREFIX = "SIM-"
MODE_LIVE = "LIVE"
MODE_SIMULATION = "SIMULATION"


def is_sim_uid(uid: object) -> bool:
    return isinstance(uid, str) and uid.startswith(SIM_PREFIX) and len(uid) > len(SIM_PREFIX)


def sim_uid(owner_uid: str) -> str:
    """The virtual tenant of ``owner_uid`` (idempotent: a sim uid maps to itself)."""
    owner = str(owner_uid or "").strip()
    if not owner:
        raise ValueError("owner uid is required")
    return owner if is_sim_uid(owner) else SIM_PREFIX + owner


def owner_uid(uid: str) -> str:
    """The real owner behind a uid (a real uid maps to itself)."""
    value = str(uid or "").strip()
    return value[len(SIM_PREFIX):] if is_sim_uid(value) else value


def sim_account_id(owner: str) -> str:
    """Stable id of the owner's virtual account."""
    digest = hashlib.sha1(owner_uid(owner).encode("utf-8")).hexdigest()[:10].upper()
    return f"{SIM_ACCOUNT_PREFIX}{digest}"


def is_sim_account_id(account_id: object) -> bool:
    return isinstance(account_id, str) and account_id.startswith(SIM_ACCOUNT_PREFIX)


def mode_for_uid(uid: object) -> str:
    return MODE_SIMULATION if is_sim_uid(uid) else MODE_LIVE


__all__ = [
    "SIM_PREFIX", "SIM_ACCOUNT_PREFIX", "MODE_LIVE", "MODE_SIMULATION",
    "is_sim_uid", "sim_uid", "owner_uid", "sim_account_id", "is_sim_account_id", "mode_for_uid",
]
