"""Alembic remains an offline/disposable staging tool, never live DB authority."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from app.persistence.alembic_guard import validated_alembic_url


@pytest.mark.parametrize("url", [
    "postgresql+asyncpg://synthetic:secret@localhost/sterling",
    "postgresql+asyncpg://synthetic:secret@localhost/postgres",
    "postgresql+asyncpg://synthetic:secret@localhost/sterling_test_proof",
])
def test_pg_migrations_refuse_unmarked_or_unapproved_database(url, monkeypatch):
    monkeypatch.delenv("STERLING_ALEMBIC_DISPOSABLE", raising=False)
    with pytest.raises(PermissionError, match="disposable"):
        validated_alembic_url(url)


@pytest.mark.parametrize("url", [
    "postgresql://synthetic:secret@localhost/sterling_test_review",
    "postgresql+psycopg2://synthetic:secret@localhost/sterling_test_review",
    "postgresql+psycopg://synthetic:secret@localhost/sterling_test_review",
    "mysql://synthetic:secret@localhost/sterling_test_review",
])
def test_unpinned_driver_or_unknown_backend_fails_closed(url, monkeypatch):
    monkeypatch.setenv("STERLING_ALEMBIC_DISPOSABLE", "1")
    with pytest.raises(ValueError):
        validated_alembic_url(url)


def test_disposable_pg_requires_nonproduction_environment(monkeypatch):
    url = "postgresql+asyncpg://synthetic:secret@localhost/sterling_test_review"
    monkeypatch.setenv("STERLING_ALEMBIC_DISPOSABLE", "1")
    monkeypatch.setenv("ENVIRONMENT", "production")
    with pytest.raises(PermissionError, match="production"):
        validated_alembic_url(url)
    monkeypatch.setenv("ENVIRONMENT", "test")
    same, is_async = validated_alembic_url(url)
    assert same == url and is_async is True


def test_default_sqlite_fixture_is_sync_and_has_no_pg_authority(tmp_path, monkeypatch):
    monkeypatch.delenv("STERLING_ALEMBIC_DISPOSABLE", raising=False)
    url = f"sqlite:///{tmp_path / 'disposable-orm.sqlite'}"
    same, is_async = validated_alembic_url(url)
    assert same == url and is_async is False


def test_alembic_upgrade_is_idempotent_on_isolated_sqlite_schema(tmp_path, monkeypatch):
    """Actually run the repository's ORM baseline on disposable SQLite twice."""
    from alembic import command
    from alembic.config import Config
    from app.core.config import settings

    db_path = tmp_path / "alembic-orm-test.sqlite"
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{db_path}")
    config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    command.upgrade(config, "head")
    with sqlite3.connect(db_path) as conn:
        first = conn.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        expected_tables = {"positions", "mirrored_records", "equity_snapshots", "alembic_version"}
        present = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        assert expected_tables <= present
        conn.execute(
            "INSERT INTO mirrored_records(store,key,payload,updated_ts)"
            " VALUES('synthetic','frozen','{\"safe\":true}',1)"
        )
    command.upgrade(config, "head")
    with sqlite3.connect(db_path) as conn:
        assert conn.execute("SELECT version_num FROM alembic_version").fetchone()[0] == first
        assert conn.execute(
            "SELECT payload FROM mirrored_records WHERE store='synthetic' AND key='frozen'"
        ).fetchone()[0] == '{"safe":true}'
    # Do not downgrade: the existing baseline downgrade drops all three tables.
