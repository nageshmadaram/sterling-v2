"""Tests can never open or wipe the operator's live database.

2026-10-22: a test run reached backend/sterling_paper.db and kite_accounts.clear()
deleted the real Kite account; the next boot seeded a placeholder key from .env.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.services import db
from app.services.exchanges.kite import accounts, auth

BACKEND = Path(__file__).resolve().parents[2]


def test_suite_runs_on_a_temporary_database():
    assert db.under_test()
    assert db.is_disposable_path(db._DB_PATH)


def test_db_refuses_a_non_test_path_under_test(tmp_path, monkeypatch):
    live = tmp_path.parent / "live_copy" / "sterling_paper.db"
    monkeypatch.setattr(db, "is_disposable_path", lambda p: False)
    monkeypatch.setattr(db, "_DB_PATH", str(live))
    with pytest.raises(RuntimeError, match="refusing to open"):
        with db._conn():
            pass
    assert not live.exists()


def test_clear_never_wipes_a_non_test_database(monkeypatch):
    calls = []
    monkeypatch.setattr(db, "is_disposable_path", lambda p: False)
    monkeypatch.setattr(db, "_conn", lambda: calls.append("opened"))
    accounts.clear()
    assert calls == []


def test_a_spawned_process_inherits_the_guard(tmp_path):
    # The live file name, in a directory that is neither temp nor named for tests.
    env = {**os.environ, "STERLING_DB_PATH": str(BACKEND / "sterling_paper.db")}
    out = subprocess.run(
        [sys.executable, "-c",
         "from app.services import db\n"
         "try:\n    db._conn().__enter__(); print('opened')\n"
         "except RuntimeError as e: print('refused')"],
        cwd=BACKEND, env=env, capture_output=True, text=True,
    )
    assert out.stdout.strip() == "refused", out.stderr


@pytest.mark.parametrize("key", ["paper_dev_key", "your-api-key", "EXAMPLE_KEY"])
def test_env_seed_ignores_placeholder_keys(monkeypatch, key):
    monkeypatch.setenv("KITE_API_KEY", key)
    monkeypatch.setenv("KITE_API_SECRET", "secret")
    assert auth.seed_from_env() is None


def test_checkout_database_is_not_disposable_even_inside_temp(tmp_path, monkeypatch):
    backend = tmp_path / 'checkout' / 'backend'
    live = backend / 'sterling_paper.db'
    monkeypatch.setattr(db, '_CHECKOUT_DB_PATH', str(live))
    assert not db.is_disposable_path(str(live))
    monkeypatch.setattr(db, '_DB_PATH', str(live))
    with pytest.raises(RuntimeError, match='refusing to open'):
        with db._conn():
            pass
    assert not live.exists()


def test_temporary_fixture_database_remains_disposable(tmp_path, monkeypatch):
    backend = tmp_path / 'checkout' / 'backend'
    monkeypatch.setattr(db, '_CHECKOUT_DB_PATH', str(backend / 'sterling_paper.db'))
    assert db.is_disposable_path(str(backend / 'fixture_test.db'))
