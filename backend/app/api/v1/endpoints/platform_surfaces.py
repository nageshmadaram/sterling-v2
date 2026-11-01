"""Read-only, JWT/account-policy gated platform discovery.

No live accounts, broker clients or workers are instantiated by this router.
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict

from app.core.auth import UserContext, get_current_user
from app.core.feature_access import require_feature
from app.services.platform_surface_registry import CANONICAL_SERVICES, catalog
from app.services.platform_session_read import session_feed_snapshot
from app.services.platform_tool_registry import tools_snapshot
from app.services.platform_api_normalization import normalization_snapshot
from app.services.platform_strategy_contract import (
    CANONICAL_ENTRY, CANONICAL_EXECUTION, CANONICAL_LANE_AUTHORITY,
    strategy_contract_snapshot,
)

router = APIRouter(tags=["platform-discovery"])


class SurfaceContract(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str
    path: str
    label: str
    state: Literal["existing_partial", "planned"]
    backing: str
    http_method: Literal["GET", "POST"]
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


class PlatformRuntimeContract(BaseModel):
    """A passive session/feed projection; no source IDs, tokens or credentials."""
    model_config = ConfigDict(extra="forbid")
    version: Literal["0.1.0"]
    execution_authorized: Literal[False]
    view: Literal["live", "simulation"]
    account_configured: bool
    stored_session_present: bool
    shared_owner_feed: bool
    active_account_feed_bound: bool
    feed_connected: bool
    subscribed_count: int
    last_tick_ms: int


@router.get("/platform/runtime", response_model=PlatformRuntimeContract)
async def platform_runtime(
    response: Response,
    user: UserContext = Depends(get_current_user),
) -> PlatformRuntimeContract:
    """Read only the authenticated tenant's active account/shared ticker status.

    Token presence is not a validated broker login, and no new Kite session or
    feed is established. The real account and subscription-owner lists stay
    entirely in the existing services.
    """
    require_feature(user, "kite")
    response.headers["Cache-Control"] = "no-store, private"
    try:
        snapshot = session_feed_snapshot(user)
    except (ValueError, TypeError):
        # A broken tenant/account binding is a hard refusal, not an excuse to
        # fall back to another user's account or provision a new connection.
        raise HTTPException(status_code=503, detail="Scoped platform runtime unavailable") from None
    return PlatformRuntimeContract(
        version="0.1.0",
        execution_authorized=False,
        **snapshot,
    )


class ToolContract(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str
    label: str
    category: Literal["analytics", "charting", "strategy", "feed"]
    method: Literal["GET"]
    api_path: str
    backend_module: str
    requires_kite_feature: Literal[True]
    permits_order_write: Literal[False]
    launches_runtime: Literal[False]


class ToolCatalogContract(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal["0.1.0"]
    execution_authorized: Literal[False]
    tools_ui_mounted: Literal[False]
    tools: list[ToolContract]


@router.get("/platform/tools", response_model=ToolCatalogContract)
async def platform_tools(
    response: Response,
    user: UserContext = Depends(get_current_user),
) -> ToolCatalogContract:
    """Expose existing read-route metadata, not a new tool runtime or proxy."""
    require_feature(user, "kite")
    response.headers["Cache-Control"] = "no-store, private"
    return ToolCatalogContract(
        version="0.1.0",
        execution_authorized=False,
        tools_ui_mounted=False,
        tools=[ToolContract(**entry) for entry in tools_snapshot()],
    )


class ApiNormalizationEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str
    target_method: Literal["GET", "POST", "DELETE"]
    target_path: str
    canonical_method: Literal["GET", "POST", "DELETE"]
    canonical_path: str
    canonical_module: str
    compatibility: Literal["read_only_adapter", "no_wrapper"]
    executable_through_platform: Literal[False]


class ApiNormalizationContract(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal["0.1.0"]
    execution_authorized: Literal[False]
    proxy_mounted: Literal[False]
    mappings: list[ApiNormalizationEntry]


@router.get("/platform/api-normalization", response_model=ApiNormalizationContract)
async def platform_api_normalization(
    response: Response,
    user: UserContext = Depends(get_current_user),
) -> ApiNormalizationContract:
    """Inventory only; does not dispatch to any mapped broker or order route."""
    require_feature(user, "kite")
    response.headers["Cache-Control"] = "no-store, private"
    return ApiNormalizationContract(
        version="0.1.0", execution_authorized=False, proxy_mounted=False,
        mappings=[ApiNormalizationEntry(**entry) for entry in normalization_snapshot()],
    )


class StrategyLaneReadContract(BaseModel):
    model_config = ConfigDict(extra="forbid")
    strategy_id: str
    mode: str
    lane_key: str
    state: str
    rules_defined: bool
    platform_execute_authorized: Literal[False]


class StrategyAdapterReadContract(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal["0.1.0"]
    execution_authorized: Literal[False]
    strategy_dispatch_mounted: Literal[False]
    canonical_entry: str
    canonical_execution: str
    canonical_lane_authority: str
    lanes: list[StrategyLaneReadContract]


@router.get("/platform/strategies", response_model=StrategyAdapterReadContract)
async def platform_strategies(
    response: Response,
    user: UserContext = Depends(get_current_user),
) -> StrategyAdapterReadContract:
    """Describe real registered strategy lanes, never submit or approve orders."""
    require_feature(user, "kite")
    response.headers["Cache-Control"] = "no-store, private"
    return StrategyAdapterReadContract(
        version="0.1.0",
        execution_authorized=False,
        strategy_dispatch_mounted=False,
        canonical_entry=CANONICAL_ENTRY,
        canonical_execution=CANONICAL_EXECUTION,
        canonical_lane_authority=CANONICAL_LANE_AUTHORITY,
        lanes=[StrategyLaneReadContract(**r) for r in strategy_contract_snapshot()],
    )
