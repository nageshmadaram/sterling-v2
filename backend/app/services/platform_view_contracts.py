"""Read-only charting/scalping surface contracts for existing Kite components.

No new global routes, broker clients, market feeds or strategy runners. These
records describe where existing user-visible chart and scalping-lane evidence
already lives; they do not navigate, dispatch, subscribe or authorize orders.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

from app.core.lane_registry import all_lanes


@dataclass(frozen=True)
class ViewContract:
    key: Literal["charting", "scalping"]
    proposed_global_route: str
    existing_kite_surface: str
    existing_component: str
    canonical_read_api: str
    route_mounted: Literal[False] = False
    execution_authorized: Literal[False] = False
    independent_feed: Literal[False] = False


VIEW_CONTRACTS: tuple[ViewContract, ...] = (
    ViewContract(
        "charting", "/trading", "Kite main dock · InstrumentPane chart",
        "frontend/src/components/kite/InstrumentPane.tsx",
        "/api/v1/candles/{underlying}",
    ),
    ViewContract(
        "scalping", "/scalping", "Existing Kite strategy-mode lanes (not a new UI)",
        "",
        "/api/v1/strategies/{strategy}/{mode}/status",
    ),
)


def view_contract_snapshot() -> list[dict]:
    """Project known UI/read surfaces and existing scalping-lane identifiers.

    Lane states/permission are intentionally NOT reported as approval to
    originate. Only the canonical execution and risk services may decide.
    """
    keys = sorted(lane.lane_key for lane in all_lanes() if lane.mode.value == "scalping")
    records = []
    for contract in VIEW_CONTRACTS:
        record = asdict(contract)
        record["existing_lane_keys"] = keys.copy() if contract.key == "scalping" else []
        records.append(record)
    return records


def _validate() -> None:
    if len({item.key for item in VIEW_CONTRACTS}) != len(VIEW_CONTRACTS):
        raise ValueError("duplicate view contract")
    if len({item.proposed_global_route for item in VIEW_CONTRACTS}) != len(VIEW_CONTRACTS):
        raise ValueError("duplicate proposed global route")
    if any(
        item.route_mounted or item.execution_authorized or item.independent_feed
        or not item.canonical_read_api.startswith("/api/v1/")
        for item in VIEW_CONTRACTS
    ):
        raise ValueError("view contract cannot mount a route, feed or execution")


_validate()
