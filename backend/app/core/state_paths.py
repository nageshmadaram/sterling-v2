"""Resolve immutable source and mutable persistent state independently.

STERLING_ROOT remains the source/repository root for imports and Git identity.
STERLING_STATE_ROOT is the root of durable runtime state. A production image can
therefore keep immutable code and a mounted data volume under different roots.
Existing checkouts remain backward-compatible until an operator explicitly sets
STERLING_STATE_ROOT.
"""
from __future__ import annotations

import os
from pathlib import Path

__all__ = ["source_root", "state_root", "state_path"]


def _inferred_source_root() -> Path:
    backend_or_image = Path(__file__).resolve().parents[2]
    if backend_or_image.name == "backend":
        return backend_or_image.parent
    return backend_or_image


def source_root() -> Path:
    configured = (os.environ.get("STERLING_ROOT") or "").strip()
    return Path(configured).expanduser() if configured else _inferred_source_root()


def state_root() -> Path:
    configured = (os.environ.get("STERLING_STATE_ROOT") or "").strip()
    if configured:
        return Path(configured).expanduser()
    source = (os.environ.get("STERLING_ROOT") or "").strip()
    if source:
        return Path(source).expanduser()
    return _inferred_source_root()


def state_path(relative: str | Path) -> Path:
    path = Path(relative)
    return path if path.is_absolute() else state_root() / path
