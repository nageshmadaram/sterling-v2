import pytest
from app.core.runtime_lease import engine_lease


def test_second_engine_is_refused_and_released_after_failure(tmp_path):
    database = str(tmp_path / 'state.db')
    with pytest.raises(ValueError):
        with engine_lease(database):
            with pytest.raises(RuntimeError, match='Another Sterling'):
                with engine_lease(database):
                    pytest.fail('duplicate engine admitted')
            raise ValueError('startup failed')
    with engine_lease(database):
        pass


def test_different_databases_do_not_block_each_other(tmp_path):
    with engine_lease(str(tmp_path / 'first.db')):
        with engine_lease(str(tmp_path / 'second.db')):
            pass


@pytest.mark.asyncio
async def test_unavailable_database_refuses_startup_and_releases_lease(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from app.services import db
    from main import lifespan
    database = str(tmp_path / 'unavailable.db')
    monkeypatch.setattr(db, '_DB_PATH', database)
    monkeypatch.setattr(db, 'init', lambda: False)
    with pytest.raises(RuntimeError, match='Durable trading database unavailable'):
        async with lifespan(FastAPI()):
            pytest.fail('execution workers must not start')
    with engine_lease(database):
        pass
