"""Item 4a: the plan and arm endpoints.

Arming is the moment a proposal becomes an authorization, so it is the one place
that must refuse everything the live system has not earned right now. A plan is
published from a board signal by the server (``POST /snapback/plans``): the browser
supplies a signal id and nothing else, so it can never influence a quantity, a
price, a contract or an account. The operator then arms that exact revision, and
the same live executor the dispatcher uses takes it from there.

Every response code says something different to a client: 404 the plan or signal is
not here, 409 you are working from a stale revision or the plan is no longer armable,
410 the plan's window has passed, 403 the system will not authorize this right now
(with the reason codes), 422 the request itself is wrong. A retry with the same
idempotency key replays rather than repeats.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core.auth import UserContext, get_current_user

log = logging.getLogger(__name__)

router = APIRouter(tags=["snapback-plans"])

#: Strong references to in-flight background executions, so they are not collected.
_executions: set = set()


class ArmRequest(BaseModel):
    expected_revision: int = Field(..., ge=1)
    idempotency_key: str = Field(..., min_length=1, max_length=128)
    mode: str = Field(default="LIVE")


class PublishRequest(BaseModel):
    signal_id: str = Field(..., min_length=1, max_length=200)


def _store():
    from app.services.snapback_plan_store import shared_store

    return shared_store()


def _owned_plan(store, plan_id: str, user: UserContext) -> Dict[str, Any]:
    """Return this caller's plan or 404 without disclosing foreign plan ids."""
    plan = store.get(plan_id)
    owner_uid = str((plan or {}).get("owner_uid") or "").strip()
    if plan is None or not owner_uid or owner_uid != str(user.user_id):
        raise HTTPException(status_code=404, detail=f"plan {plan_id} not found")
    return plan


def _admission(*, mode: str, account_id: str, uid: str = "default") -> Dict[str, Any]:
    """Whether the system can arm a Snapback plan right now, from real conditions.

    Live is the only mode. The reasons are short codes (``kill_switch``,
    ``safe_mode``, ``recovery_required``, ``daily_loss_halt``, ``system_not_healthy``,
    ``broker_not_reconciled``, ``session_closed`` ...) so a client can act on them.
    """
    if str(mode).upper() != "LIVE":
        return {"allowed": False, "mode": "LIVE", "reasons": ["unsupported_mode"]}

    from app.services.snapback import get_config
    from app.services.snapback_plan_producer import unsupported_config_reasons
    from app.services.snapback_readiness import live_conditions

    cfg = get_config(uid)
    reasons = []
    if not _dispatcher_running():
        # Arming places the real orders; only the dispatcher loop manages the
        # position afterwards (exits, protection retry, hedge). Never arm without it.
        reasons.append("dispatcher_not_running")
    if not bool(getattr(cfg, "enabled", False)):
        reasons.append("engine_disabled")
    reasons.extend(unsupported_config_reasons(cfg))
    try:
        cond = live_conditions(uid, cfg, account_id=account_id)
        reasons.extend(b for b in cond.blockers if b not in reasons)
        reasons.extend(b for b in cond.session if b not in reasons)
    except Exception as exc:  # noqa: BLE001 - unknown is not "fine"
        log.warning("snapback arm admission: conditions unreadable: %s", exc)
        reasons.append("conditions_unreadable")
    return {"allowed": not reasons, "mode": "LIVE", "reasons": reasons}


def _dispatcher_running() -> bool:
    try:
        from app.services.snapback_dispatcher import is_running

        return bool(is_running())
    except Exception as exc:  # noqa: BLE001 - unknown is not "running"
        log.warning("snapback arm admission: dispatcher state unreadable: %s", exc)
        return False


def _reason_text(codes) -> list:
    from app.services.snapback_readiness import reason_text

    return [reason_text(c) for c in codes]


@router.post("/snapback/plans")
async def publish_plan(
    body: PublishRequest,
    user: UserContext = Depends(get_current_user),
) -> dict:
    """Build and publish the plan for one board signal (it is not armed yet).

    The server sizes, prices, hedges and funds the plan from fresh broker evidence;
    this is where a stale signal, an unquotable contract or an unfundable hedge is
    refused, before anything is authorized.
    """
    from app.services.snapback_dispatcher import get_dispatcher

    result = await get_dispatcher().publish_from_signal(str(user.user_id), body.signal_id)
    if result.get("ok"):
        return result
    reasons = list(result.get("reasons") or ["refused"])
    if reasons == ["signal_not_found"]:
        raise HTTPException(status_code=404, detail=f"signal {body.signal_id} not found")
    raise HTTPException(
        status_code=422,
        detail={"error": "plan_refused", "reasons": reasons,
                "reason_text": _reason_text(reasons), "detail": result.get("detail", "")},
    )


@router.post("/snapback/plans/{plan_id}/arm")
async def arm_plan(
    plan_id: str,
    body: ArmRequest,
    user: UserContext = Depends(get_current_user),
) -> dict:
    from app.services.snapback_plan_store import PlanConflictError, PlanExpiredError

    store = _store()
    plan = _owned_plan(store, plan_id, user)

    if str(body.mode).upper() != "LIVE":
        raise HTTPException(status_code=422, detail="Live is the only mode")

    verdict = _admission(
        mode=body.mode, account_id=str(plan.get("account_id") or ""),
        uid=str(user.user_id),
    )
    if not verdict.get("allowed"):
        # Refused before any state changes: a plan that could not be armed must
        # not end up recorded as having been armed.
        raise HTTPException(
            status_code=403,
            detail={
                "error": "arming_not_authorized",
                "mode": verdict.get("mode"),
                "reasons": verdict.get("reasons") or [],
                "reason_text": _reason_text(verdict.get("reasons") or []),
            },
        )

    try:
        armed = store.arm(
            plan_id,
            expected_revision=body.expected_revision,
            idempotency_key=body.idempotency_key,
            armed_by=str(getattr(user, "user_id", "") or "unknown"),
            mode="LIVE",
        )
    except PlanExpiredError as exc:
        raise HTTPException(status_code=410, detail=str(exc)) from exc
    except PlanConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    execution = "already_replayed"
    if not armed.get("idempotent_replay"):
        # Arming authorises; execution is the dispatcher's, and it runs in the
        # background so a slow broker never holds the request open. The plan
        # status (CONSUMED / RECONCILING ...) is read from the plan route.
        execution = _schedule_execution(str(user.user_id), plan_id)

    return {
        "plan_id": plan_id,
        "status": armed.get("status"),
        "revision": armed.get("revision"),
        "mode": armed.get("mode"),
        "armed_at": armed.get("armed_at"),
        "armed_by": armed.get("armed_by"),
        "idempotent_replay": bool(armed.get("idempotent_replay")),
        "execution": execution,
    }


def _schedule_execution(uid: str, plan_id: str) -> str:
    from app.services.snapback_dispatcher import get_dispatcher

    async def run() -> None:
        try:
            result = await get_dispatcher().execute_plan(uid, plan_id)
            log.info("snapback manual plan %s executed: %s %s", plan_id,
                     result.status, result.reasons)
        except Exception as exc:  # noqa: BLE001 - the dispatcher loop retries it
            log.error("snapback manual plan %s execution failed: %s", plan_id, exc)

    try:
        task = asyncio.get_running_loop().create_task(run())
    except RuntimeError:
        return "deferred_to_dispatcher"
    _executions.add(task)
    task.add_done_callback(_executions.discard)
    return "scheduled"


@router.get("/snapback/plans/{plan_id}")
async def get_plan(
    plan_id: str, user: UserContext = Depends(get_current_user),
) -> dict:
    store = _store()
    return _owned_plan(store, plan_id, user)


@router.get("/snapback/plans/{plan_id}/events")
async def get_plan_events(
    plan_id: str, user: UserContext = Depends(get_current_user),
) -> dict:
    store = _store()
    _owned_plan(store, plan_id, user)
    return {"plan_id": plan_id, "events": store.events(plan_id)}
