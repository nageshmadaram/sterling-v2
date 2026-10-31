"""Restricted, read-only OpenAlgo-style order-status compatibility slice.

Not a drop-in OpenAlgo API: Sterling JWT, feature grant, and account-scoped
canonical journal evidence remain mandatory. No API key passthrough, broker
connection, or order submission is possible through this router.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.core.auth import UserContext, get_current_user
from app.core.feature_access import require_feature
from app.services.kite_engine import order_journal
from app.services.openalgo_compat import canonical_order_status

router = APIRouter(prefix="/compat/openalgo", tags=["compatibility"])


class OrderStatusQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account_id: str = Field(min_length=1, max_length=128)
    orderid: str = Field(min_length=1, max_length=128)


@router.post("/orderstatus")
async def order_status(
    body: OrderStatusQuery,
    user: UserContext = Depends(get_current_user),
):
    """Read only this principal's canonical order evidence, never broker state."""
    require_feature(user, "kite")
    try:
        intent = order_journal.find(
            uid=user.user_id, account_id=body.account_id, order_id=body.orderid,
        )
    except ValueError:
        return JSONResponse(status_code=409, content={
            "status": "error", "code": "CANONICAL_IDENTITY_CONFLICT",
            "message": "Order identity requires reconciliation",
        })
    except Exception:
        return JSONResponse(status_code=503, content={
            "status": "error", "code": "CANONICAL_JOURNAL_UNAVAILABLE",
            "message": "Durable order evidence is unavailable",
        })
    response = canonical_order_status(intent)
    if response["status"] == "success":
        return response
    http_code = 404 if response["code"] == "ORDER_NOT_FOUND" else 409
    return JSONResponse(status_code=http_code, content=response)
