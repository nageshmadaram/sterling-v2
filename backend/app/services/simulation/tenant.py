"""The virtual Simulation tenant: who owns it, how it boots, how it is kept clean.

The account itself (``ensure_sim_account``) and its client live with the rest of the account
store (``app.services.exchanges.kite.accounts``); this module is the tenant's operations:

* :func:`configured_owner_uid` - the one owner a simulation belongs to;
* :func:`boot_tenant` - account + control plane + risk limits, idempotent and safe to repeat;
* :func:`recover_tenant` - reconcile the tenant's order journal against its OWN virtual book;
* :func:`apply_capital_limits` - the virtual daily-loss limits, scaled to virtual capital.

Nothing here touches a real broker, the global kill switch, SAFE_MODE or a live account.
"""
from __future__ import annotations

import os

from app.core.logging import get_logger
from app.services.simulation import identity

log = get_logger(__name__)

OWNER_UID_ENV = "STERLING_OWNER_UID"
DEFAULT_OWNER_UID = "default"


def configured_owner_uid() -> str:
    """The real uid whose Simulation this process runs (``default`` unless configured)."""
    value = (os.environ.get(OWNER_UID_ENV) or "").strip()
    if value and identity.is_sim_uid(value):
        raise ValueError(f"{OWNER_UID_ENV} must be a real user id, not a simulation tenant")
    return value or DEFAULT_OWNER_UID
