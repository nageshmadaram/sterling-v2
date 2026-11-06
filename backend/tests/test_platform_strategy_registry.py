"""Stage-1 contract and migration tests (never connect to a live DB)."""
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from pydantic import ValidationError
from sqlalchemy import inspect

from app.domain.strategy_registry import (
    RunState, StrategyRunIntent, StrategyState, StrategyVersionSnapshot,
    require_transition,
)


def test_version_snapshots_deterministic_and_no_secrets():
    a = StrategyVersionSnapshot.from_parameters(strategy_id="snapback", version="1", parameters={"b": 2, "a": 1})
    b = StrategyVersionSnapshot.from_parameters(strategy_id="snapback", version="1", parameters={"a": 1, "b": 2})
    assert a.sha256() == b.sha256()
    with pytest.raises(ValidationError):
        StrategyVersionSnapshot.from_parameters(strategy_id="snapback", version="1", parameters={"auth": [{"access_token": "bad"}]})
    with pytest.raises(ValidationError):
        StrategyVersionSnapshot(strategy_id="snapback", version="1", parameters_json="{\"x\":NaN}")


def test_new_runs_cannot_be_live():
    assert StrategyRunIntent(version_id="v1", requested_by="operator").mode == "validation"
    with pytest.raises(ValidationError):
        StrategyRunIntent(version_id="v1", requested_by="operator", mode="live")


def test_transitions_are_explicit():
    require_transition(StrategyState.DRAFT, StrategyState.VALIDATED)
    require_transition(RunState.QUEUED, RunState.ACTIVE)
    with pytest.raises(ValueError):
        require_transition(StrategyState.RETIRED, StrategyState.ACTIVE)
    with pytest.raises(ValueError):
        require_transition(RunState.STOPPED, RunState.ACTIVE)
    with pytest.raises(ValueError):
        require_transition(StrategyState.DRAFT, RunState.ACTIVE)


def test_migration_roundtrip_on_disposable_database(tmp_path, monkeypatch):
    from app.core.config import settings
    from app.persistence.base import make_engine
    backend = Path(__file__).resolve().parents[1]
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp_path / 'registry.db'}")
    cfg = Config(str(backend / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend / "alembic"))
    command.upgrade(cfg, "head")
    engine = make_engine(settings.database_url)
    try:
        tables = set(inspect(engine).get_table_names())
        assert {"positions", "platform_strategies", "platform_strategy_versions", "platform_strategy_runs"} <= tables
        from sqlalchemy import text
        from sqlalchemy.exc import IntegrityError
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO platform_strategies(id,owner_id,slug,display_name,created_at_ms,updated_at_ms) VALUES ('s1','owner','snapback','Snapback',1,1)"))
            conn.execute(text("INSERT INTO platform_strategy_versions(id,strategy_id,version,parameters_json,content_sha256,created_by,created_at_ms) VALUES ('v1','s1','1','{}','hash','operator',1)"))
            conn.execute(text("INSERT INTO platform_strategy_runs(id,version_id,requested_by,created_at_ms,updated_at_ms) VALUES ('r1','v1','operator',1,1)"))
        with pytest.raises(IntegrityError):
            with engine.begin() as conn:
                conn.execute(text("INSERT INTO platform_strategy_runs(id,version_id,requested_by,mode,created_at_ms,updated_at_ms) VALUES ('r2','v1','operator','live',1,1)"))
        command.downgrade(cfg, "efe2cc0f2c21")
        tables = set(inspect(engine).get_table_names())
        assert "platform_strategy_runs" not in tables
        assert "positions" in tables
        command.upgrade(cfg, "head")
        assert "platform_strategy_runs" in inspect(engine).get_table_names()
    finally:
        engine.dispose()
