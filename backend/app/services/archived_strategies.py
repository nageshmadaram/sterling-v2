"""Per-user opt-in for archived engines. Missing/unreadable state never grants entry."""
from __future__ import annotations
import json
from app.services import db

CATALOG = {
    "adaptive_edge": ("Adaptive Edge", "Directional options; live execution requires promotion evidence."),
    "intraday": ("Intraday Pack", "Pivot Break, MA Ribbon and VWAP SuperTrend; unattended entries require validation."),
    "opening_volume_leaders": ("Opening Volume Leaders", "Opening-volume scanner; live entries require approved economic evidence and shared AUTO."),
    "navigator": ("Value-Flow Navigator", "Advisory value/flow scanner; enabling does not authorize standalone live orders."),
}

def _key(uid: str, strategy: str) -> str:
    if strategy not in CATALOG or not str(uid).strip():
        raise ValueError("Unknown archived strategy or missing user")
    return f"archived_strategy_enabled:{uid}:{strategy}"

def enabled(uid: str, strategy: str) -> bool:
    try:
        return db.get_config_strict(_key(uid, strategy)) == "true"
    except Exception:
        return False

def list_for(uid: str) -> list[dict]:
    # Settings must report unavailable storage, rather than display a false save.
    return [{"id": sid, "name": name, "description": description,
             "enabled": db.get_config_strict(_key(uid, sid)) == "true"}
            for sid, (name, description) in CATALOG.items()]

def set_enabled(uid: str, strategy: str, value: bool) -> list[dict]:
    if type(value) is not bool:
        raise ValueError("enabled must be a boolean")
    key = _key(uid, strategy)
    if value and db.get_config_strict(key) == "true":
        return list_for(uid)
    # Revoke first. A failed native-config update leaves origination closed.
    db.set_config_strict(key, "false")
    if strategy in {"adaptive_edge", "intraday"}:
        module = __import__(f"app.services.{strategy}", fromlist=["get_config"])
        cfg = module.get_config(uid).as_dict()
        cfg["enabled"] = value
        if value:
            cfg["auto_execute"] = False
        db.set_config_strict(f"{strategy}_config:{uid}", json.dumps(cfg))
    elif strategy == "opening_volume_leaders":
        from dataclasses import asdict
        from app.services.opening_volume_execution import get_config
        cfg = asdict(get_config(uid)); cfg["enabled"] = value
        db.set_config_strict(f"opening_volume_execution_config:{uid}", json.dumps(cfg))
    else:
        from app.services.navigator import config_store
        current = config_store.get(uid, default_underlyings=["NIFTY", "BANKNIFTY"])
        if value and current.config.operating_mode == "gate" and current.calibration_readiness != "ready":
            raise ValueError("Navigator gate mode requires a promoted calibration report")
        cfg = current.config.model_copy(update={"enabled": value})
        config_store.save(uid, cfg, expected_revision=current.revision,
                          default_underlyings=list(current.config.underlyings))
    if value:
        db.set_config_strict(key, "true")
    return list_for(uid)
