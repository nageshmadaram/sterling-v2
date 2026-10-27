"""Production release strategy scope.

This is the canonical allowlist for NEW exposure in the production build.
It is deliberately separate from the lane/evidence registry: the latter still
models the Snapback/SuperTrend research lanes, while Gamma-Unwind has its own
runtime contract. Legacy strategies remain readable and may manage existing
risk. Archive opt-in does not restore live origination permission.
"""
from __future__ import annotations

from typing import Final

RELEASE_ORIGINATORS: Final[dict[str, str]] = {
    "sterling-kite": "SuperTrend",
    "gamma_move": "Gamma-Unwind",
    "snapback": "Snapback",
    "price_rules": "Price Rules",
}


def normalize_strategy_id(strategy_id: str) -> str:
    return str(strategy_id or "").strip().lower()


def may_originate(strategy_id: str, uid: str = "") -> bool:
    strategy = normalize_strategy_id(strategy_id)
    # EMA Trend is a virtual-money research strategy. Its dedicated service
    # never constructs a live write client; production's scope check still
    # permits the canonical journal to execute for a verified simulated tenant.
    if strategy == "ema_trend":
        from app.services.simulation.identity import is_sim_uid
        return is_sim_uid(uid)
    if strategy in RELEASE_ORIGINATORS:
        return True
    # Navigator remains advisory: it has no standalone owned execution lifecycle.
    if uid and strategy in {"adaptive_edge", "intraday", "opening_volume_leaders"}:
        from app.services.simulation.identity import is_sim_uid
        if not is_sim_uid(uid):
            return False
        from app.services.archived_strategies import enabled
        return enabled(uid, strategy)
    return False


def release_originator_ids() -> tuple[str, ...]:
    return tuple(sorted(RELEASE_ORIGINATORS))


__all__ = [
    "RELEASE_ORIGINATORS",
    "may_originate",
    "normalize_strategy_id",
    "release_originator_ids",
]
