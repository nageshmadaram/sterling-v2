"""Safe OpenAlgo-to-Sterling API normalization inventory, not a dispatcher.

Metadata maps OpenAlgo operations to Sterling's *existing* canonical routes.
No order routes or proxy are mounted. A write target can be documented without
allowing it through a compatibility endpoint.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal


@dataclass(frozen=True)
class Mapping:
    key: str
    target_method: Literal["GET", "POST", "DELETE"]
    target_path: str
    canonical_method: Literal["GET", "POST", "DELETE"]
    canonical_path: str
    canonical_module: str
    compatibility: Literal["read_only_adapter", "no_wrapper"]
    executable_through_platform: Literal[False] = False


MAPPINGS: tuple[Mapping, ...] = (
    Mapping("order_status", "POST", "/api/v1/orderstatus", "POST",
            "/api/v1/compat/openalgo/orderstatus",
            "app.api.v1.endpoints.openalgo_compat", "read_only_adapter"),
    Mapping("order_book", "POST", "/api/v1/orderbook", "GET",
            "/api/v1/kite/orders", "app.api.v1.endpoints.kite", "no_wrapper"),
    Mapping("place_order", "POST", "/api/v1/placeorder", "POST",
            "/api/v1/kite/orders", "app.api.v1.endpoints.kite", "no_wrapper"),
    Mapping("cancel_order", "POST", "/api/v1/cancelorder", "DELETE",
            "/api/v1/kite/orders/{order_id}", "app.api.v1.endpoints.kite",
            "no_wrapper"),
)


def normalization_snapshot() -> list[dict]:
    return [asdict(entry) for entry in MAPPINGS]


def _validate() -> None:
    if len({e.key for e in MAPPINGS}) != len(MAPPINGS):
        raise ValueError("duplicate API normalization key")
    if len({(e.target_method, e.target_path) for e in MAPPINGS}) != len(MAPPINGS):
        raise ValueError("duplicate target API mapping")
    for item in MAPPINGS:
        if not item.target_path.startswith("/api/v1/"):
            raise ValueError("unknown API version")
        if not item.canonical_path.startswith("/api/v1/"):
            raise ValueError("noncanonical broker API target")
        if item.executable_through_platform:
            raise ValueError("API normalization is non-authorizing")
        if item.compatibility == "read_only_adapter" and item.key != "order_status":
            raise ValueError("unreviewed compatibility adapter")


_validate()
