"""Single answer to "is this process running in production?".

Security code used to read ``os.environ["ENVIRONMENT"]`` while the rest of the
application read ``settings.environment`` (pydantic-settings, which also loads
``backend/.env``). The launch script exports only ``STERLING_*`` from ``.env``
and says the rest is "read by pydantic-settings" - so ``ENVIRONMENT=production``
written to ``.env`` was honoured by the rate limiter and silently ignored by
authentication, the secret-strength gate and the stream socket. A deployment that
looked production-configured therefore ran with the development identity seam.

Precedence matches pydantic-settings: a real environment variable wins, and an
unset or blank one falls back to the value loaded from ``.env``.
"""
from __future__ import annotations

import os

_PRODUCTION = "production"


def environment() -> str:
    """The normalised environment name (``development`` when nothing says otherwise)."""
    raw = os.environ.get("ENVIRONMENT")
    if raw is None or not raw.strip():
        try:
            from app.core.config import settings

            raw = settings.environment
        except Exception:  # noqa: BLE001 - never let config import break auth
            raw = ""
    return (raw or "").strip().lower() or "development"


def is_production() -> bool:
    return environment() == _PRODUCTION


__all__ = ["environment", "is_production"]
