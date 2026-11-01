"""Read-only exposure-gate contract over Sterling's existing lane declarations.

This is NOT an executable strategy adapter. It provides the product layer with
source-of-truth lane identities and explicitly denies platform-side execution.
Trading continues exclusively through Sterling's canonical entry/execution
services and their existing authorization, inventory, risk and kill switches.

Never invoke a strategy, construct an ExecutionRequest, build a broker client,
or return an "allowed to submit" signal from platform metadata.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from app.core.lane_registry import all_lanes


CANONICAL_ENTRY = "app.services.canonical_strategy_entry.submit_entry"
CANONICAL_EXECUTION = "app.services.execution_service.CanonicalExecutionService"
CANONICAL_LANE_AUTHORITY = "app.core.lane_registry.may_originate"


@dataclass(frozen=True)
class StrategyLaneContract:
    strategy_id: str
    mode: str
    lane_key: str
    state: str
    rules_defined: bool
    platform_execute_authorized: bool = False


def strategy_contract_snapshot() -> list[dict]:
    """Snapshot the original lane registry; do not compute trading permission.

    Lane state is descriptive. Even LIVE_MINIMUM and rules_defined=true cannot
    grant entry authority: only the existing canonical runtime can decide that.
    """
    result: list[dict] = []
    for lane in all_lanes():
        row = StrategyLaneContract(
            strategy_id=lane.strategy_id,
            mode=lane.mode.value,
            lane_key=lane.lane_key,
            state=lane.state.value,
            rules_defined=lane.rules_defined,
        )
        result.append(asdict(row))
    return sorted(result, key=lambda row: (row["strategy_id"], row["mode"]))
