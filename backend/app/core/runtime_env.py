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


_INTEGRATION_ENV = frozenset({
    "KITE_API_KEY", "KITE_API_SECRET", "KITE_APP_USER_ID", "KITE_LABEL",
    "KITE_ACCESS_TOKEN", "KITE_USER_ID", "ZERODHA_API_KEY", "ZERODHA_API_SECRET",
    "ZERODHA_USER_ID", "TELEGRAM_TOKEN", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
})

def load_sterling_env(path=None, environ=None) -> list[str]:
    """Export non-empty Sterling settings and explicitly supported integration keys from ``backend/.env``.

    ``run_backend.sh`` exports the same namespace before uvicorn imports the app.
    Direct ``python3 main.py`` must therefore do the same *before* importing any
    module that snapshots a safety setting at import time. Otherwise a kill switch
    such as ``STERLING_LIVE_EXECUTION=0``, or production secret configuration,
    can mean different things depending only on how the process was launched.

    A real process environment variable always wins. Broker and Telegram clients
    read the explicit integration allowlist directly; other settings remain
    the responsibility of pydantic-settings. Returns the names populated from the
    file, in file order.
    """
    from pathlib import Path

    env = os.environ if environ is None else environ
    file = Path(path) if path else Path(__file__).resolve().parents[2] / ".env"
    loaded: list[str] = []
    try:
        lines = file.read_text(encoding="utf-8").splitlines()
    except OSError:
        return loaded
    for line in lines:
        line = line.strip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not key.startswith("STERLING_") and key not in _INTEGRATION_ENV:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key not in env and value != "":
            env[key] = value
            loaded.append(key)
    return loaded


__all__ = ["environment", "is_production", "load_sterling_env"]
