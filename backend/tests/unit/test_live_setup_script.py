"""Offline key setup never rotates saved credentials or prints secrets."""
import importlib.util
from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "sterling_live_setup.py"
spec = importlib.util.spec_from_file_location("sterling_live_setup", SCRIPT)
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


@pytest.fixture(autouse=True)
def no_overrides(monkeypatch):
    for key in setup.KEYS:
        monkeypatch.delenv(key, raising=False)


def test_fresh_setup_preserves_settings_and_uses_protected_distinct_keys(tmp_path):
    path = tmp_path / ".env"
    path.write_text("# keep\nENVIRONMENT=production\n")
    assert setup.configure(path, tmp_path / "db.sqlite")
    values = setup.values(path.read_text())
    assert all(setup.strong(values[key]) for key in setup.KEYS)
    assert values[setup.KEYS[0]] != values[setup.KEYS[1]]
    assert values["ENVIRONMENT"] == "production"
    assert path.stat().st_mode & 0o777 == 0o600
    before = path.read_bytes()
    assert setup.configure(path, tmp_path / "db.sqlite")
    assert path.read_bytes() == before


def test_existing_credentials_prevent_unverified_encryption_key_rotation(tmp_path):
    path = tmp_path / ".env"
    path.write_text("STERLING_SECRET_KEY=weak-old-key\n")
    database = tmp_path / "db.sqlite"
    with sqlite3.connect(database) as conn:
        conn.execute("CREATE TABLE kite_accounts(api_secret_enc TEXT)")
        conn.execute("INSERT INTO kite_accounts VALUES ('fernet:stored-token')")
    with pytest.raises(ValueError, match="migration"):
        setup.configure(path, database)
    assert path.read_text() == "STERLING_SECRET_KEY=weak-old-key\n"


def test_check_never_creates_files(tmp_path):
    path = tmp_path / ".env"
    assert setup.configure(path, tmp_path / "db.sqlite", check=True) is False
    assert not path.exists()


def test_command_does_not_disclose_generated_secrets(tmp_path):
    path = tmp_path / ".env"
    result = subprocess.run([sys.executable, str(SCRIPT), "--env-file", str(path),
                             "--database", str(tmp_path / "db.sqlite")], capture_output=True, text=True)
    assert result.returncode == 0
    for key in setup.KEYS:
        assert setup.values(path.read_text())[key] not in result.stdout + result.stderr


def test_equal_keys_change_only_jwt_and_preserve_encryption(tmp_path):
    path = tmp_path / ".env"
    key = "existing-encryption-" + "x" * 48
    path.write_text(f"STERLING_SECRET_KEY={key}\nSTERLING_JWT_SECRET={key}\n")
    setup.configure(path, tmp_path / "db.sqlite")
    values = setup.values(path.read_text())
    assert values["STERLING_SECRET_KEY"] == key
    assert values["STERLING_JWT_SECRET"] != key


def test_nested_telegram_credentials_prevent_key_rotation(tmp_path):
    import json
    path = tmp_path / '.env'
    path.write_text('STERLING_SECRET_KEY=weak-old-key\n')
    database = tmp_path / 'db.sqlite'
    with sqlite3.connect(database) as conn:
        conn.execute('CREATE TABLE system_config(key TEXT, value TEXT)')
        conn.execute('INSERT INTO system_config VALUES (?, ?)', ('kite_tg_targets:default', json.dumps([{'id': 'one', 'bot_token_enc': 'fernet:saved'}])))
    with pytest.raises(ValueError, match='migration'):
        setup.configure(path, database)
    assert path.read_text() == 'STERLING_SECRET_KEY=weak-old-key\n'
