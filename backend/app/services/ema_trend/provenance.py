"""Audit metadata only: never grants execution permission or rewrites old identity."""
from __future__ import annotations

from functools import lru_cache
import hashlib
import json
from pathlib import Path

from app.engines.ema_trend.config import EmaTrendConfig, PROFILES, descriptor, stable_id


@lru_cache(maxsize=1)
def source_identity() -> dict:
    app = Path(__file__).resolve().parents[2]
    paths = sorted((app / "engines" / "ema_trend").glob("*.py")) + sorted(Path(__file__).parent.glob("*.py"))
    digest = hashlib.sha256()
    for path in paths:
        digest.update(str(path.relative_to(app)).encode())
        digest.update(path.read_bytes())
    baseline = app.parents[1] / "docs/strategy/ema-trend/BASELINE_MANIFEST.json"
    return {"strategy_version":descriptor()["version"],"runtime_source_sha256": digest.hexdigest(),
            "workspace_baseline_sha256": hashlib.sha256(baseline.read_bytes()).hexdigest() if baseline.exists() else None,
            "rule_hash": descriptor()["rule_hash"]}


def bind(conn, uid: str, kind: str, ident: str, document: dict, current: dict | None, at_ms: int) -> dict:
    """Origin stays frozen across projection updates, even after a config change.

    Existing rows without metadata are explicitly legacy/unverified. Settings
    receive a fresh identity on each saved revision; a pause is also a revision.
    Parent identity comes from the campaign, plan or setup when available.
    """
    source = source_identity()
    if current and kind != "settings":
        prior = current.get("provenance")
        if prior:
            return {**prior, "projection_source": source, "projected_ms": at_ms}
        return {"schema_version": 1, "owner_uid": uid, "artifact_kind": kind, "artifact_id": ident,
                "origin": None, "origin_status": "LEGACY_ORIGIN_UNVERIFIED",
                "projection_source": source, "projected_ms": at_ms, "live_eligible": False}

    parent = None
    for parent_kind, key in (("campaign", "campaign_id"), ("plan", "plan_id"), ("signal", "signal_id")):
        parent_id = document.get(key)
        if parent_id:
            row = conn.execute("SELECT data_json FROM ema_trend_documents WHERE owner_uid=? AND kind=? AND id=?",
                               (uid, parent_kind, parent_id)).fetchone()
            if row:
                parent = {"kind": parent_kind, "id": parent_id, **json.loads(row[0])}
                break
    settings_row = conn.execute("SELECT data_json FROM ema_trend_documents WHERE owner_uid=? AND kind='settings' AND id='config'", (uid,)).fetchone()
    settings = json.loads(settings_row[0]) if settings_row else {}
    config_raw = document.get("config_snapshot") or document.get("config") or settings.get("config")
    try:
        config = EmaTrendConfig.model_validate(config_raw) if config_raw else EmaTrendConfig()
        config_hash, universe_hash = config.config_hash, stable_id(config.symbols)
        config_status = "VALIDATED" if config_raw else "DEFAULT_UNSAVED"
    except (ValueError, TypeError):
        config_hash = universe_hash = None
        config_status = "INVALID_UNVERIFIED"
    mode = document.get("mode") or (parent or {}).get("mode")
    profile = PROFILES.get(mode)
    revision = int(current.get("revision", 0))+1 if kind == "settings" and current else 1 if kind == "settings" else document.get("config_revision", settings.get("revision", 0))
    parent_provenance = (parent or {}).get("provenance", {})
    inherited = parent_provenance.get("origin")
    from app.services.simulation.identity import sim_uid,sim_account_id
    dataset=document.get("dataset_manifest") or document.get("manifest") or {}
    origin = inherited or {**source,"dataset_id":document.get("dataset_id"),
                          "dataset_sha256":document.get("dataset_sha256") or dataset.get("content_sha256"), "execution_uid": f"replay:{uid}" if kind=="run" else sim_uid(uid),
                          "account_id": f"replay:{uid}" if kind=="run" else sim_account_id(uid), "config_hash": config_hash, "config_revision": revision,
                          "universe_hash": universe_hash, "config_status": config_status,
                          "mode": mode, "profile": profile.model_dump(mode="json") if profile else None,
                          "tenant_kind": "REPLAY" if kind == "run" else "SIMULATION",
                          "execution_vehicle":(document.get("contract") or {}).get("execution_vehicle","SELECTED_INSTRUMENT"), "evidence_track": "PAPER_OR_MODELLED"}
    parent_unknown=bool(parent and not inherited)
    return {"schema_version": 1, "owner_uid": uid, "artifact_kind": kind, "artifact_id": ident,
            "origin": None if parent_unknown else origin, "origin_status": "PARENT_ORIGIN_UNVERIFIED" if parent_unknown else "PARENT_BOUND" if inherited else "CAPTURED_AT_CREATION",
            "parent": {"kind": parent["kind"], "id": parent["id"], "revision": parent["revision"]} if parent else None,
            "projection_source": source, "projected_ms": at_ms, "live_eligible": False}
