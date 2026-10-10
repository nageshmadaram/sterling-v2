"""Stop new entries for ONE tenant: the global kill switch for live, the tenant's own switch for Simulation.

The engines engage the kill switch when a real position is in a state they cannot vouch for (an exit
whose outcome is unknown, a stop that is not confirmed...). The switch is process-wide: it halts every
account. A Simulation hiccup (a virtual stop that did not confirm) must never halt the owner's REAL trading,
so every engine site that can run for the virtual tenant engages it through here:

* a live uid        ``live_safety.set_kill_switch(True, reason)``, exactly as before;
* a simulation uid  the virtual tenant's own control-plane row is set HALTED. Admission for that tenant reads
                    that row, so its entries stop; nothing global changes.
"""
from __future__ import annotations

from app.core.logging import get_logger
from app.services.simulation.identity import is_sim_uid, owner_uid, sim_account_id

log = get_logger(__name__)

SIM_HALT_CODE = "SIM_HALT"


def engage_kill_switch(uid: str, reason: str) -> None:
    """Halt new entries for ``uid``'s tenant. Raises what the underlying switch raises (callers wrap it)."""
    if is_sim_uid(uid):
        from app.services import db
        log.warning("simulation tenant %s halted its own new entries: %s", uid, reason)
        db.set_operator_state(operator_state="HALTED", reason_code=SIM_HALT_CODE, reason=str(reason or "halt"),
                              actor="simulation", uid=uid, account_id=sim_account_id(owner_uid(uid)))
        return
    from app.services import live_safety
    live_safety.set_kill_switch(True, reason)


def clear_halt(uid: str) -> None:
    """Lift a simulation tenant's own halt (a reset of its virtual books). Refuses a live uid."""
    if not is_sim_uid(uid):
        raise ValueError("only a simulation tenant's own halt can be cleared here")
    from app.services import db
    db.set_operator_state(operator_state="RUNNING", reason_code="SIM_RESUME", reason="virtual books reset",
                          actor="simulation", uid=uid, account_id=sim_account_id(owner_uid(uid)))


__all__ = ["SIM_HALT_CODE", "engage_kill_switch", "clear_halt"]
