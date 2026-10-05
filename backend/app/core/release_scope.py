"""Production release strategy scope.

This is the canonical allowlist for NEW exposure in the production build.
It is deliberately separate from the lane/evidence registry: the latter still
models the Snapback/SuperTrend research lanes, while Gamma-Unwind has its own
runtime contract. Legacy strategies may remain readable and may manage existing
risk, but they are not release originators.
"""
from __future__ import annotations

from typing import Final

RELEASE_ORIGINATORS: Final[dict[str, str]] = {
    "sterling-kite": "SuperTrend",
    "gamma_move": "Gamma-Unwind",
    "snapback": "Snapback",
}


def normalize_strategy_id(strategy_id: str) -> str:
    return str(strategy_id or "").strip().lower()


def may_originate(strategy_id: str) -> bool:
    return normalize_strategy_id(strategy_id) in RELEASE_ORIGINATORS


def release_originator_ids() -> tuple[str, ...]:
    return tuple(sorted(RELEASE_ORIGINATORS))


__all__ = [
    "RELEASE_ORIGINATORS",
    "may_originate",
    "normalize_strategy_id",
    "release_originator_ids",
]
