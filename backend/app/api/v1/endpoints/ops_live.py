"""Operator surface for the live runtime: status, and lifting SAFE_MODE.

Both reads and the one write are authenticated with the same dependency as every
broker-touching route. Lifting SAFE_MODE is a deployment-global decision (it
re-opens new-entry origination for every strategy and account), so the write also
requires administrator authority, an explicit confirmation phrase, a reason, and,
for a safe mode that came from an investigation-required trigger, an explicit
acknowledgement that a person looked at the condition.

Nothing here returns a secret: no token, API key or client secret. The Zerodha client id can appear inside a broker error detail.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core.auth import UserContext, get_current_user, require_admin
from app.services.production_settings import ProductionSettings

router = APIRouter(prefix="/ops", tags=["live-ops"])


@router.get('/production-settings')
def production_settings(_user: UserContext = Depends(require_admin)) -> dict:
    from app.services.production_settings import read_settings
    from app.services import live_runtime, live_posture
    from app.core.operator_report import doctor_from_preflight, lane_doctor_checks
    from app.core.waivers import is_advisory_doctor_check
    from app.services.snapback_preflight import run_preflight
    report = doctor_from_preflight(run_preflight().checks, extra=lane_doctor_checks())
    return {
        'settings': read_settings().model_dump(),
        'runtime': live_runtime.live_execution_state(),
        'safe_mode': live_runtime._safe_mode_service().read().as_dict(),
        'posture': live_posture.posture_status(refresh=True),
        'checks': [
            {'name': check.name, 'passed': check.passed, 'detail': check.detail,
             'advisory': is_advisory_doctor_check(check.name)}
            for check in report.checks
        ],
        'activation': 'Restart the backend after saving. Startup and order safety gates still apply.',
        'risk_scopes': _risk_scopes(),
    }


def _risk_scopes() -> dict:
    """Scopes the settings form fills, so the UI never hard-codes lane keys."""
    from app.core.risk_hierarchy import live_lane_scopes
    lanes = list(live_lane_scopes())
    strategies = sorted({lane.split(':', 1)[0] for lane in lanes} | {'gamma_move'})
    return {'strategies': strategies, 'lanes': lanes}


class ProductionSettingsRequest(BaseModel):
    settings: ProductionSettings
    confirm: str = Field(default='', max_length=32)


@router.put('/production-settings')
def update_production_settings(
    body: ProductionSettingsRequest, _user: UserContext = Depends(require_admin),
) -> dict:
    from app.services.production_settings import save_settings
    from app.services import live_runtime, live_posture
    cfg = body.settings
    if cfg.live_execution:
        if body.confirm != 'ENABLE LIVE ENTRIES':
            raise HTTPException(400, 'Type ENABLE LIVE ENTRIES to request live entries after restart')
        posture = live_posture.posture_status(refresh=True)
        if not posture['ready']:
            raise HTTPException(409, 'Resolve live setup first: ' + ', '.join(posture['missing']))
        if live_runtime._safe_mode_service().read().active:
            raise HTTPException(409, 'Resolve and resume safe mode before enabling entries')
        if not cfg.allocation_capital_inr or not cfg.risk_limits:
            raise HTTPException(409, 'Set approved allocation and risk limits before enabling entries')
        from app.core.risk_hierarchy import (
            ENV_VAR, configured_hierarchy, missing_live_lane_caps,
        )

        hierarchy = configured_hierarchy({ENV_VAR: cfg.risk_limits})
        missing = missing_live_lane_caps(hierarchy)
        if missing:
            raise HTTPException(
                409, 'Complete the strategy, mode and position risk caps before '
                'enabling entries: ' + ', '.join(missing),
            )
    try:
        save_settings(cfg)
    except (OSError, ValueError) as exc:
        raise HTTPException(409, 'Production settings could not be saved') from exc
    return {'settings': cfg.model_dump(), 'restart_required': True}


class SafeModeResumeRequest(BaseModel):
    #: Must equal the confirmation phrase reported by ``GET /ops/safe-mode``.
    confirm: str = Field(..., max_length=64)
    reason: str = Field(..., min_length=3, max_length=500)
    #: Required when the safe mode was caused by an investigation-required
    #: trigger. Saying so is the statement that a person looked and resolved it.
    acknowledge_investigation: bool = False


@router.get("/live-status")
def live_status(_user: UserContext = Depends(get_current_user)) -> dict:
    """Live execution, safe mode, recovery and exposure freshness in one place."""
    from app.services import live_runtime

    return live_runtime.live_status()


@router.get("/safe-mode")
def safe_mode_status(_user: UserContext = Depends(get_current_user)) -> dict:
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


@router.get("/runtime-identity")
def runtime_identity(frontend_sha: str = "", frontend_contract: str = "",
                     _user: UserContext = Depends(get_current_user)) -> dict:
    from app.core.runtime_identity import diagnostics
    return diagnostics(frontend_sha[:64], frontend_contract[:64])
