"""Live posture for the operator: what a real entry still needs, and accepting a new outbound IP.

The read is authenticated like every broker-touching route. Accepting an IP is an
administrator action with an explicit confirmation phrase: it tells Sterling that the
Zerodha whitelist now holds the new address. Nothing here returns a secret.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core.auth import UserContext, get_current_user, require_admin

router = APIRouter(prefix="/ops", tags=["live-posture"])

ACCEPT_CONFIRMATION = "ACCEPT IP"


class AcceptIpRequest(BaseModel):
    ip: str = Field(..., min_length=3, max_length=45)
    #: Must equal :data:`ACCEPT_CONFIRMATION`.
    confirm: str = Field(..., max_length=32)


@router.get("/live-posture")
async def live_posture_status(_user: UserContext = Depends(get_current_user)) -> dict:
    """Secure keys, registered/verified static IP and account binding, with what is missing."""
    import asyncio

    from app.services import live_posture

    return await asyncio.to_thread(live_posture.posture_status, refresh=True)


@router.post("/live-posture/egress/accept")
async def accept_egress_ip(
    body: AcceptIpRequest, user: UserContext = Depends(require_admin)
) -> dict:
    """Register a new outbound IP once the Zerodha whitelist has been updated to it.

    The host is asked again where it answers from; an address it is not actually
    using is refused, so a typo cannot unlock entries.
    """
    from app.services import live_posture

    if body.confirm != ACCEPT_CONFIRMATION:
        raise HTTPException(
            status_code=400,
            detail=f"confirm must be exactly {ACCEPT_CONFIRMATION!r}: say that the Zerodha "
                   "whitelist already holds this address",
        )
    actor = (user.username or user.user_id or "").strip() or "operator"
    try:
        result = await live_posture.accept_egress_ip(body.ip, by=actor)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    live_posture.posture_status(refresh=True)
    return result


@router.post("/live-posture/binding/verify")
async def reverify_account_binding(_user: UserContext = Depends(require_admin)) -> dict:
    """Re-read the broker profile and inventory for the ACTIVE binding (read-only).

    The same check as ``sterlingctl bind verify``. Binding evidence expires after
    15 minutes and nothing refreshes it between logins except a live order, so
    the operator needs a way to refresh it from the screen that asks for it. It
    reads the broker; it places, modifies and cancels nothing.
    """
    from app.core.broker_account_binding import BindingError
    from app.services import live_posture
    from app.services.account_binding_service import AccountBindingStore
    from app.services.broker_binding_verification import verify_current_kite_binding

    active = AccountBindingStore().active()
    if active is None:
        raise HTTPException(409, "No ACTIVE broker account binding is declared")
    try:
        binding = await verify_current_kite_binding(active.binding_id)
    except BindingError as exc:
        raise HTTPException(409, f"Broker verification failed: {exc}") from exc
    posture = live_posture.posture_status(refresh=True)
    return {"binding_id": binding.binding_id, "verified_at": binding.verified_at,
            "ready": posture.get("ready"), "missing": posture.get("missing", [])}
