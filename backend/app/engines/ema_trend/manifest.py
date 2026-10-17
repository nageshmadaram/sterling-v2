"""Strategy-owned identity and condition-based Swing horizon."""
from __future__ import annotations
from pydantic import BaseModel, ConfigDict
from .config import EmaTrendConfig, Mode, PROFILES, descriptor, stable_id


class EmaTrendHorizonContract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    mode: Mode
    signal_minutes: int
    context_minutes: int
    policy: str
    max_hold_bars: int | None
    force_close: str | None
    exit_policy: str = "STOP_THEN_STRUCTURAL_THEN_QUARTERS"
    version: str = "ema_trend_horizon_v1"


def horizon(mode: Mode) -> EmaTrendHorizonContract:
    p = PROFILES[mode]
    return EmaTrendHorizonContract(mode=mode, signal_minutes=p.signal_minutes,
                                   context_minutes=p.context_minutes, policy=p.horizon_policy,
                                   max_hold_bars=p.max_hold_bars, force_close=p.force_close)


def identity(config: EmaTrendConfig, mode: Mode, *, uid: str, account_id: str,
             tenant_kind: str, config_revision: int, runtime_sha: str,
             workspace_manifest_hash: str, run_id: str | None = None, instrument_meta: dict | None = None) -> dict:
    if not uid or not account_id or tenant_kind not in {"SIMULATION", "REPLAY"}:
        raise ValueError("EMA_TREND_REQUIRES_EXPLICIT_PAPER_TENANT")
    profile = PROFILES[mode]
    from .instruments import contract
    resolved=contract(instrument_meta,mode) if instrument_meta else None
    vehicle = {"execution_vehicle": resolved["execution_vehicle"] if resolved else "EQUITY" if all(s.startswith(("NSE:","BSE:")) for s in config.symbols) else "UNRESOLVED",
               "contract":resolved,"product": resolved["product"] if resolved else profile.product,
               "allows_short": False, "research_only": True}
    return {"schema_version": 1, "strategy_id": "ema_trend", "strategy_version": "1.1.0",
            "uid": uid, "account_id": account_id, "tenant_kind": tenant_kind,
            "canonical_mode": mode, "mode_version": f"ema_trend_{mode}_v1",
            "config_revision": config_revision, "config_hash": config.config_hash,
            "rule_hash": descriptor()["rule_hash"], "universe_hash": stable_id(config.symbols),
            **vehicle, "vehicle_policy_hash": stable_id(vehicle),
            "horizon": horizon(mode).model_dump(), "runtime_sha": runtime_sha,
            "workspace_manifest_hash": workspace_manifest_hash, "replay_run_id": run_id,
            "evidence_track": "RESEARCH_MODELLED", "live_eligible": False}
