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

import asyncio
import os
import time

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


# ─── control plane ───────────────────────────────────────────────────────────
#: Why the tenant's control row exists (shown in the control-plane audit).
INIT_REASON_CODE = "SIM_INIT"
ACTOR = "simulation"


def scope_ids(owner: str) -> tuple[str, str]:
    """``(sim uid, sim account id)``: the control-plane scope the virtual tenant lives in."""
    return identity.sim_uid(owner), identity.sim_account_id(owner)


def ensure_control_plane(owner: str) -> dict:
    """Give the virtual account its own execution-control row, RUNNING / CLEAN.

    Admission reads ``(global, <sim uid>, <sim account>)``. A missing row is "recovery has never
    run" and would refuse every virtual entry, yet nothing reconciles a virtual account with a
    broker at startup, so the row is created here. An existing row is left exactly as it is: a
    RECOVERY_REQUIRED one belongs to :func:`recover_tenant`, and only the tenant's own scope is
    ever written, never the process-wide root the live runtime settles.
    """
    from app.services import db

    uid, account_id = scope_ids(owner)
    control = db.get_execution_control(uid=uid, account_id=account_id)
    if int(control.get("initialized", 0) or 0):
        return control
    return db.set_execution_control(
        operator_state="RUNNING", recovery_state="CLEAN", reason_code=INIT_REASON_CODE,
        reason="virtual account created; nothing to reconcile with a broker", actor=ACTOR,
        uid=uid, account_id=account_id, last_reconciled_ms=int(time.time() * 1000),
    )


async def recover_tenant(owner: str, *, service=None, client=None) -> dict:
    """Reconcile the tenant's order journal with its OWN virtual book (observe-only).

    The same ``startup_recovery`` the live runtime runs per broker account, pointed at the
    virtual client: an order left SUBMITTING by a crash is matched against the virtual order
    book, and the tenant's control row returns to CLEAN once nothing is unsettled.
    """
    from app.services.exchanges.kite import accounts as kite_accounts

    account = kite_accounts.sim_account_for(owner)
    if account is None:
        return {"status": "no_account"}
    if service is None:
        from app.services.execution_service import execution_service as service
    client = client or await kite_accounts.acquire_client(account)
    uid, account_id = scope_ids(owner)
    return await service.startup_recovery(uid=uid, account_id=account_id, broker_client=client)


def _needs_recovery(owner: str) -> bool:
    from app.services import db
    from app.services.kite_engine import order_journal

    uid, account_id = scope_ids(owner)
    try:
        control = db.get_execution_control(uid=uid, account_id=account_id)
        if control.get("recovery_state") != "CLEAN" or not int(control.get("initialized", 0) or 0):
            return True
        return bool(order_journal.unresolved(uid, account_id=account_id)
                    or order_journal.pending_projection(uid, account_id))
    except Exception:  # noqa: BLE001 - unreadable counts as unsettled
        return True


async def recovery_loop(owner: str, *, interval_s: float = 30.0, max_cycles: int | None = None) -> None:
    """Keep the tenant reconciled for the life of the process.

    The live runtime recovers only broker accounts, so without this an order left unsettled in
    the virtual book would fence the tenant's entries for ever. Never raises: a failed cycle is
    logged and retried.
    """
    cycles = 0
    while max_cycles is None or cycles < max_cycles:
        cycles += 1
        try:
            if _needs_recovery(owner):
                await recover_tenant(owner)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("simulation recovery cycle failed: %s", exc)
        await asyncio.sleep(interval_s)


# ─── risk limits scaled to virtual capital ───────────────────────────────────
#: The account-level daily-loss limits ship in rupees sized for a real account (-1,500 hard).
#: On a virtual capital of lakhs that would halt the tenant on its first small loss, so the
#: tenant gets limits expressed as a share of ITS capital.
DAILY_LOSS_HARD_PCT = 2.0
DAILY_LOSS_SOFT_PCT = 1.0


def virtual_capital(owner: str) -> float:
    from app.services.simulation.ledger import get_ledger

    return float(get_ledger().ensure_account(owner)["capital"])


def apply_capital_limits(owner: str, capital: float | None = None):
    """Set the tenant's daily-loss limits to a share of its virtual capital. Returns the config."""
    from app.services import live_safety

    capital = float(capital if capital is not None else virtual_capital(owner))
    if not capital > 0:
        raise ValueError("virtual capital must be positive")
    uid, _ = scope_ids(owner)
    cfg = live_safety.DailyLossConfig(
        enabled=True,
        soft_warn_inr=-round(capital * DAILY_LOSS_SOFT_PCT / 100.0, 2),
        hard_halt_inr=-round(capital * DAILY_LOSS_HARD_PCT / 100.0, 2),
    )
    if live_safety.has_daily_loss_override(uid) and live_safety.daily_loss_config(uid) == cfg:
        return cfg
    return live_safety.configure_daily_loss(cfg, uid=uid)


def boot_tenant(owner: str):
    """Everything the tenant needs before an engine scans it. Idempotent; the boot step."""
    from app.services.exchanges.kite import accounts as kite_accounts

    account = kite_accounts.ensure_sim_account(owner)
    from app.services.simulation.ledger import get_ledger

    get_ledger().ensure_account(owner)
    ensure_control_plane(owner)
    apply_capital_limits(owner)
    return account
