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
    mounts_route: bool = False
    permits_order_write: bool = False


# These paths are contract targets, not claims of full OpenAlgo compatibility.
# The existing read-only orderstatus adapter is mounted under /api/v1 in
# backend/main.py. The six planned UI surfaces have no route registration.
SURFACES: tuple[Surface, ...] = (
    Surface(
        "broker_api", "/api/v1/", "Sterling broker API", "existing_partial",
        "app.api.v1.endpoints.kite",
        mounts_route=True,
    ),
    Surface(
        "openalgo_orderstatus", "/api/v1/compat/openalgo/orderstatus",
        "Read-only canonical order status", "existing_partial",
        "app.api.v1.endpoints.openalgo_compat",
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
