"""Source-verified, read-only tool metadata for future /tools navigation.

This registry does not dispatch endpoint functions, import Kite clients, or
represent a permission grant. Underlying canonical routes enforce user scope
and policy independently when clients actually request data.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

ToolCategory = Literal["analytics", "charting", "strategy", "feed"]


@dataclass(frozen=True)
class Tool:
    key: str
    label: str
    category: ToolCategory
    method: Literal["GET"]
    api_path: str
    backend_module: str
    requires_kite_feature: bool = True
    permits_order_write: bool = False
    launches_runtime: bool = False


# The mounted routes and HTTP methods are checked against source in CI.
# A templated {segment} is an informational path contract, not interpolated
# from untrusted input by the registry. Clients use the canonical API modules.
TOOLS: tuple[Tool, ...] = (
    Tool("pcr_session", "PCR session", "analytics", "GET",
         "/api/v1/pcr/session", "app.api.v1.endpoints.pcr"),
    Tool("pcr_instrument", "PCR instrument", "analytics", "GET",
         "/api/v1/pcr/instrument", "app.api.v1.endpoints.pcr"),
    Tool("candles", "Instrument candles", "charting", "GET",
         "/api/v1/candles/{underlying}", "app.api.v1.endpoints.candles"),
    Tool("strategy_modes", "Strategy modes", "strategy", "GET",
         "/api/v1/strategies/{strategy}/modes", "app.api.v1.endpoints.lanes"),
    Tool("ticker_health", "Kite ticker health", "feed", "GET",
         "/api/v1/kite/ticker/status", "app.api.v1.endpoints.kite"),
)


def tools_snapshot() -> list[dict]:
    """Return detached JSON-ready records; no service or network access."""
    return [asdict(tool) for tool in TOOLS]


def tool_by_key(key: str) -> Tool | None:
    """Allowlisted exact lookup, never a dynamic route or method dispatcher."""
    return next((tool for tool in TOOLS if tool.key == key), None)


def _validate() -> None:
    if len({t.key for t in TOOLS}) != len(TOOLS):
        raise ValueError("duplicate tool keys")
    if len({(t.method, t.api_path) for t in TOOLS}) != len(TOOLS):
        raise ValueError("duplicate tool API contract")
    if any(
        t.method != "GET" or t.permits_order_write or t.launches_runtime
        or not t.requires_kite_feature or not t.api_path.startswith("/api/v1/")
        or not t.backend_module.startswith("app.api.v1.endpoints.")
        for t in TOOLS
    ):
        raise ValueError("non-read-only/unscoped tool contract")


_validate()
