"""Allow Alembic to touch only explicitly designated disposable PostgreSQL DBs.

This is a development migration guard, NOT a production migration permit.
An environment label or a host name alone is not proof of disposability; an
independent operator must still review the target and its backups.
"""
from __future__ import annotations

import os

from sqlalchemy.engine import make_url


def validated_alembic_url(url: str) -> tuple[str, bool]:
    """Return (original URL, async driver?) or refuse unsafe PG migration."""
    if not isinstance(url, str) or not url.strip():
        raise ValueError("Alembic database URL is required")
    parsed = make_url(url)
    backend = parsed.get_backend_name()
    driver = parsed.get_driver_name()
    if backend not in {"sqlite", "postgresql"}:
        raise ValueError("Unsupported Alembic database backend")
    if backend == "sqlite":
        if driver not in {"pysqlite"}:
            raise ValueError("Unsupported SQLite Alembic driver")
        return url, False
    if driver not in {"asyncpg"}:
        # The lockfile provides asyncpg, not a synchronous psycopg driver.
        raise ValueError("PostgreSQL Alembic requires the pinned asyncpg driver")
    name = (parsed.database or "").lower()
    if (not (name.startswith("sterling_test_")
             or name.startswith("sterling_disposable_"))
            or os.environ.get("STERLING_ALEMBIC_DISPOSABLE") != "1"):
        raise PermissionError(
            "PostgreSQL migrations require an explicitly marked disposable "
            "sterling_test_*/sterling_disposable_* DB and "
            "STERLING_ALEMBIC_DISPOSABLE=1; production migration prohibited"
        )
    # Runtime environment is sourced by pydantic-settings as well as process
    # variables. Checking only os.environ would silently bypass production
    # posture loaded from backend/.env.
    from app.core.runtime_env import is_production
    if is_production():
        raise PermissionError("Alembic PostgreSQL review migrations forbidden in production")
    return url, True
