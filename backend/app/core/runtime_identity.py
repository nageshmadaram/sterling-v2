"""Startup-frozen source provenance. A mutable checkout is never loaded-code proof."""
from __future__ import annotations
import hashlib
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
API_CONTRACT = "sterling-api-v1"
_BOOT = None


def source_fingerprint():
    digest = hashlib.sha256()
    for path in sorted([ROOT / "main.py", *(ROOT / "app").rglob("*.py")]):
        digest.update(str(path.relative_to(ROOT)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def checkout_sha():
    try:
        result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                                capture_output=True, text=True, timeout=3, check=True)
        return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        from app.core.build_artifact_identity import artifact_identity
        artifact = artifact_identity()
        return artifact.build_sha if artifact.valid else "UNKNOWN"


def source_dirty():
    try:
        result = subprocess.run(["git", "status", "--porcelain", "--", "app", "main.py"],
                                cwd=ROOT, capture_output=True, text=True, timeout=3, check=True)
        return bool(result.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        from app.core.build_artifact_identity import artifact_identity
        artifact = artifact_identity()
        return not artifact.valid if artifact.present else None


def capture_startup():
    global _BOOT
    if _BOOT is None:
        _BOOT = {"captured_at": datetime.now(timezone.utc).isoformat(),
                 "checkout_sha_at_start": checkout_sha(),
                 "source_sha256_at_start": source_fingerprint(),
                 "declared_runtime_sha": os.getenv("STERLING_RUNTIME_SHA") or None,
                 "api_contract": API_CONTRACT, "source_dirty_at_start": source_dirty()}
    return dict(_BOOT)


def diagnostics(frontend_sha: str = "", frontend_contract: str = ""):
    boot = dict(_BOOT or {})
    current = checkout_sha()
    reasons = []
    if not boot:
        reasons.append("startup_identity_not_captured")
    if boot.get("source_sha256_at_start") != source_fingerprint():
        reasons.append("backend_source_changed_since_start")
    if boot.get("source_dirty_at_start") is not False:
        reasons.append("backend_startup_source_not_clean_release")
    if boot and current != boot.get("checkout_sha_at_start"):
        reasons.append("backend_checkout_changed_since_start")
    declared = boot.get("declared_runtime_sha")
    if declared and declared != boot.get("checkout_sha_at_start"):
        reasons.append("declared_backend_release_mismatch")
    if not frontend_sha or frontend_sha == "UNKNOWN":
        reasons.append("frontend_build_unknown")
    elif frontend_sha != boot.get("checkout_sha_at_start"):
        reasons.append("frontend_backend_release_mismatch")
    if frontend_contract != API_CONTRACT:
        reasons.append("frontend_backend_contract_mismatch")
    return {**boot, "current_checkout_sha": current, "frontend_build_sha": frontend_sha or None,
            "compatible": not reasons, "blockers": reasons,
            "provenance_scope": "startup source snapshot; immutable release artifacts still required"}
