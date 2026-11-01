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
from app.services.platform_broker_read import normalize_broker_read
from app.services.platform_strategy_read import strategy_catalog
from app.services.platform_authoring_contracts import authoring_capabilities

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


class BrokerSummaryContract(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal["0.1.0"]
    source: Literal["sterling_existing_account_and_ticker_managers"]
    view: Literal["live", "simulation"]
    session_state: Literal[
        "not_configured", "no_stored_token", "stored_token_unverified",
        "feed_disconnected", "feed_connected",
    ]
    broker_login_verified: Literal[False]
    order_execution_authorized: Literal[False]
    feed_bound_to_active_account: bool
    feed_connected: bool
    shared_owner_feed: bool
    subscriptions_count: int
    last_tick_ms: int


@router.get("/platform/broker/summary", response_model=BrokerSummaryContract)
async def platform_broker_summary(
    response: Response, user: UserContext = Depends(get_current_user),
) -> BrokerSummaryContract:
    """Normalize read-only session/feed evidence; no broker validation or writes."""
    require_feature(user, "kite")
    response.headers["Cache-Control"] = "no-store, private"
    try:
        result = normalize_broker_read(session_feed_snapshot(user))
    except (ValueError, TypeError, KeyError):
        raise HTTPException(status_code=503, detail="Scoped broker read unavailable") from None
    return BrokerSummaryContract(**result)


class StrategyLaneContract(BaseModel):
    model_config = ConfigDict(extra="forbid")
    strategy_id: str
    mode: str
    lane_key: str
    configured_state: str
    rules_defined: bool
    order_submission_enabled: Literal[False]


class StrategyCatalogContract(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal["0.1.0"]
    execution_authorized: Literal[False]
    risk_preflight_performed: Literal[False]
    source: Literal["sterling_canonical_lane_registry"]
    lanes: list[StrategyLaneContract]


@router.get("/platform/strategies", response_model=StrategyCatalogContract)
async def platform_strategy_catalog(
    response: Response, user: UserContext = Depends(get_current_user),
) -> StrategyCatalogContract:
    """Describe existing lanes; never dispatch or claim risk/kill-switch approval."""
    require_feature(user, "kite")
    response.headers["Cache-Control"] = "no-store, private"
    return StrategyCatalogContract(
        version="0.1.0", execution_authorized=False,
        risk_preflight_performed=False, source="sterling_canonical_lane_registry",
        lanes=[StrategyLaneContract(**row) for row in strategy_catalog()],
    )


class PythonAuthoringContract(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_validation_only: Literal[True]
    execute_enabled: Literal[False]
    sandbox_certified: Literal[False]
    max_source_bytes: int


class FlowAuthoringContract(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_validation_only: Literal[True]
    execute_enabled: Literal[False]
    node_kinds: list[Literal["source", "compare", "note"]]
    max_nodes: int
    max_edges: int


class ChartReadContract(BaseModel):
    model_config = ConfigDict(extra="forbid")
    existing_data_route: Literal["/api/v1/candles/{underlying}"]
    global_workspace_mounted: Literal[False]


class ScalpReadContract(BaseModel):
    model_config = ConfigDict(extra="forbid")
    existing_strategy_modes_route: Literal["/api/v1/strategies/{strategy}/modes"]
    global_workspace_mounted: Literal[False]
    new_order_controls_enabled: Literal[False]


class AuthoringCapabilitiesContract(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal["0.1.0"]
    python: PythonAuthoringContract
    flow: FlowAuthoringContract
    charting: ChartReadContract
    scalping: ScalpReadContract


@router.get("/platform/authoring/capabilities", response_model=AuthoringCapabilitiesContract)
async def platform_authoring_capabilities(
    response: Response, user: UserContext = Depends(get_current_user),
) -> AuthoringCapabilitiesContract:
    """Read-only authoring constraints; no submitted source or graph execution."""
    require_feature(user, "kite")
    response.headers["Cache-Control"] = "no-store, private"
    return AuthoringCapabilitiesContract(**authoring_capabilities())
