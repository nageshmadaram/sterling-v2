"""Pure, non-authorizing inventory of Sterling V2 OpenAlgo-style surfaces.

The registry is not a router, a feature switch or an execution service. It
creates no client, session, subscription, subprocess or write capability.
Existing source modules stay canonical; planned URLs are NOT mounted.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal


SurfaceState = Literal["existing_partial", "planned"]


@dataclass(frozen=True)
class Surface:
    key: str
    path: str
    label: str
    state: SurfaceState
    backing: str
    http_method: Literal["GET", "POST"] = "GET"
    mounts_route: bool = False
    permits_order_write: bool = False


# These paths are contract targets, not claims of full OpenAlgo compatibility.
# The existing read-only orderstatus adapter is mounted under /api/v1 in
# backend/main.py. The six planned UI surfaces have no route registration.
SURFACES: tuple[Surface, ...] = (
    Surface(
        "platform_surfaces", "/api/v1/platform/surfaces",
        "Authenticated platform surface discovery", "existing_partial",
        "app.api.v1.endpoints.platform_surfaces", mounts_route=True,
    ),
    Surface(
        "broker_api", "/api/v1/kite/accounts", "Sterling scoped account read", "existing_partial",
        "app.api.v1.endpoints.kite",
        mounts_route=True,
    ),
    Surface(
        "openalgo_orderstatus", "/api/v1/compat/openalgo/orderstatus",
        "Read-only canonical order status", "existing_partial",
        "app.api.v1.endpoints.openalgo_compat",
        http_method="POST", mounts_route=True,
    ),
    Surface(
        "platform_runtime", "/api/v1/platform/runtime",
        "Existing account and feed read contract", "existing_partial",
        "app.api.v1.endpoints.platform_surfaces", mounts_route=True,
    ),
    Surface(
        "platform_tools", "/api/v1/platform/tools",
        "Existing read-only tools catalog", "existing_partial",
        "app.api.v1.endpoints.platform_surfaces", mounts_route=True,
    ),
    Surface(
        "api_normalization", "/api/v1/platform/api-normalization",
        "Canonical broker API normalization inventory", "existing_partial",
        "app.api.v1.endpoints.platform_surfaces", mounts_route=True,
    ),
    Surface(
        "strategy_contract", "/api/v1/platform/strategies",
        "Existing lane and canonical execution source map", "existing_partial",
        "app.api.v1.endpoints.platform_surfaces", mounts_route=True,
    ),
    Surface(
        "view_contracts", "/api/v1/platform/view-contracts",
        "Existing Kite charting and scalping read surface contracts",
        "existing_partial", "app.api.v1.endpoints.platform_surfaces",
        mounts_route=True,
    ),
    Surface(
        "python", "/python", "Python strategy host", "planned", "",
    ),
    Surface(
        "flow", "/flow", "Flow/no-code builder", "planned", "",
    ),
    Surface(
        "strategy", "/strategy", "Strategy and RMS", "planned", "",
    ),
    Surface(
        "tools", "/tools", "Options and portfolio tools", "planned", "",
    ),
    Surface(
        "trading", "/trading", "Charting terminal", "planned", "",
    ),
    Surface(
        "scalping", "/scalping", "Scalping terminal", "planned", "",
    ),
)


CANONICAL_SERVICES = {
    "broker_sessions": "app.services.exchanges.kite.accounts",
    "market_data": "app.services.exchanges.kite.ticker_manager",
    "execution": "app.services.execution_service",
    "journal": "app.services.kite_engine.order_journal",
}


def catalog() -> list[dict]:
    """Return freshly copied JSON-ready metadata without inspecting broker data."""
    return [asdict(item) for item in SURFACES]


def surface_for_path(path: str) -> Surface | None:
    """Exact metadata lookup only. It never mounts or executes a matched route."""
    return next((item for item in SURFACES if item.path == path), None)


def _validate_contract() -> None:
    keys = [surface.key for surface in SURFACES]
    paths = [surface.path for surface in SURFACES]
    if len(keys) != len(set(keys)) or len(paths) != len(set(paths)):
        raise ValueError("duplicate platform surface key or path")
    if any(
        surface.permits_order_write or (surface.state == "planned" and surface.mounts_route)
        for surface in SURFACES
    ):
        raise ValueError("platform discovery must not grant execution or mount planned routes")


_validate_contract()
