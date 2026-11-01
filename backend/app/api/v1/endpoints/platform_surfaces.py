"""Read-only, JWT/account-policy gated platform discovery.

No live accounts, broker clients or workers are instantiated by this router.
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict

from app.core.auth import UserContext, get_current_user
from app.core.feature_access import require_feature
from app.services.platform_surface_registry import CANONICAL_SERVICES, catalog

router = APIRouter(tags=["platform-discovery"])


class SurfaceContract(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str
    path: str
    label: str
    state: Literal["existing_partial", "planned"]
    backing: str
    mounts_route: bool
    permits_order_write: Literal[False]


class PlatformCatalogContract(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal["0.1.0"]
    execution_authorized: Literal[False]
    planned_routes_mounted: Literal[False]
    canonical_services: dict[str, str]
    surfaces: list[SurfaceContract]


@router.get("/platform/surfaces", response_model=PlatformCatalogContract)
async def platform_surfaces(
    response: Response,
    user: UserContext = Depends(get_current_user),
) -> PlatformCatalogContract:
    # Do not rely solely on the app-wide middleware: an independently-mounted
    # router must still refuse identities without an active Kite feature grant.
    require_feature(user, "kite")
    response.headers["Cache-Control"] = "no-store, private"
    return PlatformCatalogContract(
        version="0.1.0",
        execution_authorized=False,
        planned_routes_mounted=False,
        canonical_services=dict(CANONICAL_SERVICES),
        surfaces=[SurfaceContract(**item) for item in catalog()],
    )
