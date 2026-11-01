"""Read-only projection over Sterling's actual canonical strategy/lane registry.

The lane configuration is descriptive, not a pre-trade eligibility decision.
No calls are made to risk gates, account/broker services or the order router.
"""
from __future__ import annotations

from app.core.lane_registry import all_lanes


def strategy_catalog() -> list[dict]:
    rows = [
        {
            "strategy_id": lane.strategy_id,
            "mode": lane.mode.value,
            "lane_key": lane.lane_key,
            "configured_state": lane.state.value,
            "rules_defined": lane.rules_defined,
            "order_submission_enabled": False,
        }
        for lane in all_lanes()
    ]
    if len({row["lane_key"] for row in rows}) != len(rows):
        raise ValueError("duplicate_canonical_strategy_lane")
    return sorted(rows, key=lambda row: (row["strategy_id"], row["mode"]))
