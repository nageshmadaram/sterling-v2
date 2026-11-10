"""Simulation control API (``/api/v1/sim/*``): the virtual tenant's status, per-strategy mode, reset, capital.

This is NOT the market-replay router (``endpoints/simulation.py``). It is for the owner only (admin
whose id is the configured owner), and it works from either view: the owner is resolved from the
request context, never from the body. Nothing here places an order, touches the global kill switch,
SAFE_MODE or a broker session.

* ``GET  /sim/status``                          capital, cash, equity, today's P&L, per-strategy mode, runtime
* ``PUT  /sim/strategy/{name}/mode``            ``{mode: MANUAL|SIMULATION|AUTO}``; AUTO is live-view only
* ``POST /sim/reset``                           ``{confirm: true}``; wipes the virtual books, positions, journal, config
* ``PUT  /sim/capital``                         ``{capital: <INR>}``
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, StrictBool

from app.core.auth import VIEW_SIMULATION, UserContext, require_admin
from app.core.logging import get_logger
from app.services.exchanges.kite import accounts as kite_accounts
from app.services.simulation import identity, mode as sim_mode, tenant

log = get_logger(__name__)

router = APIRouter(prefix="/sim", tags=["simulation"])

MAX_CAPITAL = 10_000_000_000.0


class ModeBody(BaseModel):
    mode: str


class ResetBody(BaseModel):
    confirm: StrictBool = False                      # only a JSON true confirms ("yes" / 1 do not)
    capital: Optional[float] = Field(default=None, gt=0, le=MAX_CAPITAL)


class CapitalBody(BaseModel):
    capital: float = Field(gt=0, le=MAX_CAPITAL)


def _detail(code: str, message: str, **extra: Any) -> Dict[str, Any]:
    return {"code": code, "message": message, **extra}


def owner_of(user: UserContext) -> str:
    """The real owner behind the request; 403 for anyone who does not own the Simulation."""
    owner = user.owner_uid or user.user_id
    try:
        configured = tenant.configured_owner_uid()
    except ValueError as exc:                       # STERLING_OWNER_UID names a simulation tenant
        raise HTTPException(503, detail=_detail("owner_misconfigured", str(exc))) from exc
    if identity.is_sim_uid(owner) or owner != configured or user.role != "admin":
        raise HTTPException(403, detail=_detail("owner_only", "Simulation is available to the owner only."))
    return owner


def _refuse(exc: sim_mode.ModeError) -> HTTPException:
    if isinstance(exc, sim_mode.ModeBlocked):       # the shape the live AUTO gate answers with
        return HTTPException(exc.status, detail={"error": exc.code, "code": exc.code, "message": exc.message,
                                                 "reasons": exc.reasons})
    return HTTPException(exc.status, detail=_detail(exc.code, exc.message))


def _live_session(owner: str) -> Dict[str, Any]:
    """Whether virtual prices can flow: the simulation reads them through the owner's own broker session."""
    acct = kite_accounts.get_active(owner)
    if acct is None or kite_accounts.is_sim_account(acct):
        return {"available": False, "detail": "No broker account: simulation has no prices."}
    if not (acct.connected and acct.token_is_live):
        return {"available": False, "detail": "Broker session is not logged in: simulation is paused."}
    return {"available": True, "detail": ""}


def _require_unfenced_simulation(owner: str) -> None:
    """Gate mutation-capable status/bootstrap/capital routes before side effects."""
    from app.services.kite_engine import order_journal

    uid, _ = tenant.scope_ids(owner)
    try:
        state = order_journal.simulation_reset_fence_state(uid)
    except Exception as exc:
        raise HTTPException(503, detail=_detail(
            "simulation_reset_fence_unavailable",
            "Simulation reset recovery status could not be verified.",
        )) from exc
    if state is not None:
        raise HTTPException(409, detail=_detail(
            "simulation_reset_fenced",
            "Simulation reset recovery is required; virtual account changes are blocked.",
        ))


def _boot_or_refuse(owner: str) -> None:
    """Translate a reset racing between API preflight and virtual bootstrap."""
    try:
        tenant.boot_tenant(owner)
    except ValueError as exc:
        if str(exc) == "simulation_reset_fenced":
            raise HTTPException(409, detail=_detail(
                "simulation_reset_fenced", "Simulation reset recovery is required.",
            )) from exc
        raise HTTPException(503, detail=_detail(
            "simulation_tenant_unavailable", "Simulation tenant could not be prepared.",
        )) from exc
    except Exception as exc:
        raise HTTPException(503, detail=_detail(
            "simulation_tenant_unavailable", "Simulation tenant could not be prepared.",
        )) from exc


@router.get("/reset-health")
async def reset_health(user: UserContext = Depends(require_admin)) -> Dict[str, Any]:
    """Owner-only, read-only recovery diagnosis; never shows tokens or clears fences."""
    from app.services import db
    from app.services.kite_engine import order_journal

    owner = owner_of(user)
    uid, account_id = tenant.scope_ids(owner)
    try:
        fence = order_journal.simulation_reset_fence_state(uid)
        control = db.get_execution_control(uid=uid, account_id=account_id)
    except Exception as exc:
        raise HTTPException(503, detail=_detail(
            "simulation_reset_fence_unavailable",
            "Simulation reset recovery status could not be verified.",
        )) from exc
    recovery_state = control["recovery_state"]
    return {
        "fence_state": fence or "CLEAR",
        "recovery_required": bool(fence) or recovery_state != "CLEAN",
        "automatic_release_allowed": False,
        "control_plane": {
            "initialized": bool(control.get("initialized")),
            "operator_state": control["operator_state"],
            "recovery_state": recovery_state,
        },
    }


def build_status(owner: str) -> Dict[str, Any]:
    from app.services.simulation import sim_runtime

    view = sim_runtime.status(owner)
    today_pnl = round(float(view["net_realised_today"]) + float(view["unrealised"]), 2)
    return {
        **view,
        "virtual": True,
        "today_pnl": today_pnl,
        "strategies": sim_mode.modes(owner),
        "runtime": {
            "running": bool(view["running"]), "last_pass_ms": view["last_pass_ms"], "passes": view["passes"],
            "last_error": view["last_error"], "session_open": sim_runtime.in_session(),
        },
        "price_feed": _live_session(owner),
        "message": "SIMULATION - virtual money, no real orders",
    }


@router.get("/status")
async def sim_status(user: UserContext = Depends(require_admin)) -> Dict[str, Any]:
    owner = owner_of(user)
    _require_unfenced_simulation(owner)
    if kite_accounts.sim_account_for(owner) is None:
        _boot_or_refuse(owner)  # only ordinary, non-fenced first look creates the tenant
    return build_status(owner)


@router.put("/strategy/{strategy}/mode")
async def put_strategy_mode(strategy: str, body: ModeBody,
                            user: UserContext = Depends(require_admin)) -> Dict[str, Any]:
    owner = owner_of(user)
    try:
        result = sim_mode.set_mode(owner, strategy, body.mode, allow_live=user.view != VIEW_SIMULATION)
    except sim_mode.ModeError as exc:
        raise _refuse(exc) from exc
    return {**result, "strategies": sim_mode.modes(owner)}


@router.post("/reset")
async def reset(body: ResetBody, user: UserContext = Depends(require_admin)) -> Dict[str, Any]:
    owner = owner_of(user)
    if body.confirm is not True:
        raise HTTPException(422, detail=_detail("confirm_required",
                                                "Resetting wipes every virtual order, position, trade and journal row; "
                                                "send {\"confirm\": true}."))
    log.warning("simulation reset requested by %s", user.username or user.user_id)
    try:
        result = sim_mode.reset_simulation(owner, capital=body.capital)
    except sim_mode.ModeError as exc:
        raise _refuse(exc) from exc
    return {**result, "status": build_status(owner)}


@router.put("/capital")
async def put_capital(body: CapitalBody, user: UserContext = Depends(require_admin)) -> Dict[str, Any]:
    from app.services.simulation import sim_runtime

    owner = owner_of(user)
    _require_unfenced_simulation(owner)
    _boot_or_refuse(owner)
    try:
        sim_runtime.set_capital(owner, body.capital)
        tenant.apply_capital_limits(owner, body.capital)
    except ValueError as exc:
        if str(exc) == "simulation_reset_fenced":
            raise HTTPException(409, detail=_detail(
                "simulation_reset_fenced", "Simulation reset recovery is required.",
            )) from exc
        raise HTTPException(422, detail=_detail("invalid_capital", str(exc))) from exc
    except Exception as exc:
        raise HTTPException(503, detail=_detail(
            "simulation_capital_unavailable", "Simulation capital update could not be verified.",
        )) from exc
    return build_status(owner)
