"""Authenticated owner configuration. Defaults never arm execution."""
from __future__ import annotations

from app.engines.ema_trend.config import EmaTrendConfig, PROFILES, descriptor
from . import repository as repo


def get(uid: str) -> dict:
    repo.owner(uid)
    try:
        stored = repo.get(uid, "settings", "config")
    except repo.NotFound:
        return {"id": "config", "revision": 0, "config": EmaTrendConfig().model_dump(mode="json"),
                "activation_ms": repo.now_ms(), "paused": False, "invalid": False}
    try:
        EmaTrendConfig.model_validate(stored["config"])
    except (ValueError, TypeError, KeyError):
        return {**stored, "config": EmaTrendConfig().model_dump(mode="json"), "invalid": True,
                "blockers": ["INVALID_PERSISTED_CONFIG"]}
    return {**stored, "invalid": False}


def config(uid: str) -> EmaTrendConfig:
    return EmaTrendConfig.model_validate(get(uid)["config"])


def update(uid: str, expected_revision: int, changes: dict) -> dict:
    if not changes:
        raise ValueError("no settings to change")
    unknown = set(changes) - set(EmaTrendConfig.model_fields)
    if unknown:
        raise ValueError("Unknown config fields: " + ", ".join(sorted(unknown)))
    with repo.transaction() as conn:
        current = repo._load(conn, repo.owner(uid), "settings", "config")
        rev = current["revision"] if current else 0
        if rev != expected_revision:
            raise repo.Conflict("STALE_REVISION")
        before = current["config"] if current else EmaTrendConfig().model_dump(mode="json")
        value = EmaTrendConfig.model_validate({**before, **changes})
        result = repo._put(conn, uid, "settings", "config", {
            "config": value.model_dump(mode="json"), "activation_ms": repo.now_ms(),
            "paused": bool(current and current.get("paused")), "status": "PAPER_ONLY"}, expected_revision)
        return result


def pause(uid: str, paused: bool) -> dict:
    current = get(uid)
    result = repo.put(uid, "settings", "config", {**current, "paused": paused,
         "activation_ms": repo.now_ms()}, current["revision"])
    return result


def response(uid: str) -> dict:
    current = get(uid)
    return {**current, "defaults": EmaTrendConfig().model_dump(mode="json"),
            "strategy": descriptor(), "profiles": {k: v.model_dump(mode="json") for k,v in PROFILES.items()},
            "capabilities": {"paper": True, "live": False, "broker_protection": False},
            "blockers": current.get("blockers", []) + descriptor()["blockers"]}
