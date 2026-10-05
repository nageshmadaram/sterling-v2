"""Operator surface for the live runtime: status, and lifting SAFE_MODE.

Both reads and the one write are authenticated with the same dependency as every
broker-touching route. Lifting SAFE_MODE is a deployment-global decision (it
re-opens new-entry origination for every strategy and account), so the write also
requires administrator authority, an explicit confirmation phrase, a reason, and,
for a safe mode that came from an investigation-required trigger, an explicit
acknowledgement that a person looked at the condition.

Nothing here returns a secret: no token, API key, client secret or broker user id.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core.auth import UserContext, get_current_user, require_admin

router = APIRouter(prefix="/ops", tags=["live-ops"])


class SafeModeResumeRequest(BaseModel):
    #: Must equal the confirmation phrase reported by ``GET /ops/safe-mode``.
    confirm: str = Field(..., max_length=64)
    reason: str = Field(..., min_length=3, max_length=500)
    #: Required when the safe mode was caused by an investigation-required
    #: trigger. Saying so is the statement that a person looked and resolved it.
    acknowledge_investigation: bool = False


@router.get("/live-status")
async def live_status(_user: UserContext = Depends(get_current_user)) -> dict:
    """Live execution, safe mode, recovery and exposure freshness in one place."""
    from app.services import live_runtime

    return live_runtime.live_status()


@router.get("/safe-mode")
async def safe_mode_status(_user: UserContext = Depends(get_current_user)) -> dict:
    """Current safe-mode state and what lifting it would require."""
    from app.services import live_runtime

    return live_runtime.safe_mode_status()


@router.post("/safe-mode/resume")
async def resume_safe_mode(
    body: SafeModeResumeRequest, user: UserContext = Depends(require_admin)
) -> dict:
    """Lift SAFE_MODE. Records who and when. Refuses without the confirmation."""
    from app.services import live_runtime

    actor = (user.username or user.user_id or "").strip()
    try:
        return live_runtime.resume_safe_mode(
            actor=actor,
            confirm=body.confirm,
            reason=body.reason,
            acknowledge_investigation=body.acknowledge_investigation,
        )
    except live_runtime.SafeModeResumeRefused as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
