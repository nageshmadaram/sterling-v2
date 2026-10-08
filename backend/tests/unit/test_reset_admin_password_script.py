from __future__ import annotations

import importlib.util
from pathlib import Path

from app.core.passwords import verify_password
from app.services import db
from app.services.auth import user_store

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "reset_admin_password.py"


def _load():
    spec = importlib.util.spec_from_file_location("reset_admin_password", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _prepare(tmp_path, monkeypatch):
    path = str(tmp_path / "pw.db")
    monkeypatch.setenv("STERLING_DB_PATH", path)
    monkeypatch.setattr(db, "_DB_PATH", path)
    monkeypatch.setattr(db, "_available", True)
    db.init()
    user_store.clear()
    user_store.bootstrap()
    user_store._users.clear()
    user_store.add("admin", "old password value", role="admin", user_id="default")


def test_resets_password_and_signs_everyone_out(tmp_path, monkeypatch):
    _prepare(tmp_path, monkeypatch)
    module = _load()
    answers = iter(["a brand new passphrase", "a brand new passphrase"])
    monkeypatch.setattr(module.getpass, "getpass", lambda prompt="": next(answers))
    monkeypatch.setattr("sys.argv", ["reset_admin_password.py"])
    before = user_store.get_by_id("default").token_version
    assert module.main() == 0
    user = user_store.get_by_id("default")
    assert verify_password("a brand new passphrase", user.password_hash)
    assert not verify_password("old password value", user.password_hash)
    assert user.token_version == before + 1
    user_store.clear()


def test_refuses_short_or_mismatched_passwords(tmp_path, monkeypatch, capsys):
    _prepare(tmp_path, monkeypatch)
    module = _load()
    monkeypatch.setattr("sys.argv", ["reset_admin_password.py"])
    monkeypatch.setattr(module.getpass, "getpass", lambda prompt="": "short")
    assert module.main() == 2
    answers = iter(["long enough password", "different password!!"])
    monkeypatch.setattr(module.getpass, "getpass", lambda prompt="": next(answers))
    assert module.main() == 2
    assert verify_password("old password value", user_store.get_by_id("default").password_hash)
    assert "long enough password" not in capsys.readouterr().out
    user_store.clear()
