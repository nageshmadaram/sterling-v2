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


#: Settings that decide WHICH database and state files a process uses. ``run_backend.sh``
#: exports every STERLING_* line; ``python3 main.py`` exported none, so one ``.env`` selected
#: different databases depending on the launch method. Only these location keys are aligned
#: here, so ``python3 main.py`` keeps every other behaviour it always had.
LOCATION_KEYS = frozenset({
    "STERLING_DB_PATH", "STERLING_STATE_ROOT", "STERLING_SAFE_MODE_FILE", "STERLING_INTENT_DB",
})


def load_sterling_env(path=None, environ=None) -> list[str]:
    """Export the location settings of ``backend/.env`` (see ``LOCATION_KEYS``).

    A real environment variable always wins. Returns the names that were set.
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
        if "=" not in line or line.partition("=")[0].strip() not in LOCATION_KEYS:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key and key not in env and value != "":
            env[key] = value
            loaded.append(key)
    return loaded


__all__ = ["environment", "is_production", "load_sterling_env"]
