import os
import pytest
from app.core import security
from app.core.security import validate_production_security, encrypt
from app.services import db

VALID_SECRET_1 = "test-" + ("a" * 40)
VALID_SECRET_2 = "test-" + ("b" * 40)

def test_security_gate_dev_mode():
    os.environ["ENVIRONMENT"] = "development"
    validate_production_security()

def test_security_gate_prod_missing_secret(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("STERLING_SECRET_KEY", raising=False)
    monkeypatch.delenv("STERLING_JWT_SECRET", raising=False)
    with pytest.raises(RuntimeError, match="STERLING_SECRET_KEY must be a distinct, randomly generated secret"):
        validate_production_security()

def test_security_gate_prod_short_secret(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("STERLING_SECRET_KEY", "short_secret_16char")
    monkeypatch.setenv("STERLING_JWT_SECRET", VALID_SECRET_2)
    with pytest.raises(RuntimeError, match="STERLING_SECRET_KEY must be a distinct, randomly generated secret of at least 32 characters"):
        validate_production_security()

def test_security_gate_prod_identical_secrets(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("STERLING_SECRET_KEY", VALID_SECRET_1)
    monkeypatch.setenv("STERLING_JWT_SECRET", VALID_SECRET_1)
    with pytest.raises(RuntimeError, match="STERLING_SECRET_KEY and STERLING_JWT_SECRET must be distinct"):
        validate_production_security()

def test_security_gate_prod_dev_fallback_jwt(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("STERLING_SECRET_KEY", VALID_SECRET_1)
    monkeypatch.setenv("STERLING_JWT_SECRET", "sterling-dev-jwt-secret-key")
    with pytest.raises(RuntimeError, match="STERLING_JWT_SECRET must be a distinct, randomly generated secret"):
        validate_production_security()



def test_security_gate_prod_rejects_wildcard_cors(monkeypatch):
    from app.core.config import settings

    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("STERLING_SECRET_KEY", VALID_SECRET_1)
    monkeypatch.setenv("STERLING_JWT_SECRET", VALID_SECRET_2)
    monkeypatch.setattr(settings, "cors_origins", ["*"])

    with pytest.raises(RuntimeError, match="CORS origins must be explicit trusted origins"):
        validate_production_security()


def test_security_gate_prod_rejects_cleartext_remote_cors(monkeypatch):
    from app.core.config import settings

    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("STERLING_SECRET_KEY", VALID_SECRET_1)
    monkeypatch.setenv("STERLING_JWT_SECRET", VALID_SECRET_2)
    monkeypatch.setattr(settings, "cors_origins", ["http://sterling.example.com"])

    with pytest.raises(RuntimeError, match="non-loopback CORS origins must use HTTPS"):
        validate_production_security()


def test_security_gate_prod_uninitialized_db(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("STERLING_SECRET_KEY", VALID_SECRET_1)
    monkeypatch.setenv("STERLING_JWT_SECRET", VALID_SECRET_2)
    monkeypatch.setattr(db, "_available", False)
    with pytest.raises(RuntimeError, match="Database store is uninitialized or unavailable for credential inspection"):
        validate_production_security()

def test_security_gate_prod_plaintext_system_config(monkeypatch, tmp_path):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("STERLING_SECRET_KEY", VALID_SECRET_1)
    monkeypatch.setenv("STERLING_JWT_SECRET", VALID_SECRET_2)
    db_file = tmp_path / "test.db"
    monkeypatch.setattr(db, "_DB_PATH", str(db_file))
    monkeypatch.setattr(db, "_available", True)
    db.init()
    db.set_config("telegram_bot_token", "fixture-plaintext-value")

    with pytest.raises(RuntimeError, match="Secret in system_config for key 'telegram_bot_token' is not Fernet-encrypted"):
        validate_production_security()

def test_security_gate_prod_b64_kite_account(monkeypatch, tmp_path):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("STERLING_SECRET_KEY", VALID_SECRET_1)
    monkeypatch.setenv("STERLING_JWT_SECRET", VALID_SECRET_2)
    db_file = tmp_path / "test.db"
    monkeypatch.setattr(db, "_DB_PATH", str(db_file))
    monkeypatch.setattr(db, "_available", True)
    db.init()
    with db._conn() as c:
        c.execute("INSERT INTO kite_accounts (id, user_id, api_secret_enc, created_at_ms, updated_at_ms) VALUES ('k1', 'u1', 'b64:c2VjcmV0', 100, 100)")

    with pytest.raises(RuntimeError, match="Credential column 'api_secret_enc' in table 'kite_accounts' contains unencrypted/legacy value"):
        validate_production_security()

def test_security_gate_prod_valid_fernet_credentials(monkeypatch, tmp_path):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("STERLING_SECRET_KEY", VALID_SECRET_1)
    monkeypatch.setenv("STERLING_JWT_SECRET", VALID_SECRET_2)
    # Ensure fernet initialized with VALID_SECRET_1
    security._fernet = None
    security._backend = "b64"
    security._init()

    db_file = tmp_path / "test.db"
    monkeypatch.setattr(db, "_DB_PATH", str(db_file))
    monkeypatch.setattr(db, "_available", True)
    db.init()

    encrypted_secret = encrypt("fixture-broker-credential")
    assert encrypted_secret.startswith("fernet:")

    with db._conn() as c:
        c.execute("INSERT INTO kite_accounts (id, user_id, api_secret_enc, created_at_ms, updated_at_ms) VALUES ('k1', 'u1', ?, 100, 100)", (encrypted_secret,))

    # Should pass without error
    validate_production_security()


def test_security_gate_rejects_legacy_exchange_plaintext(monkeypatch, tmp_path):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("STERLING_SECRET_KEY", VALID_SECRET_1)
    monkeypatch.setenv("STERLING_JWT_SECRET", VALID_SECRET_2)
    db_file = tmp_path / "legacy.db"
    monkeypatch.setattr(db, "_DB_PATH", str(db_file))
    monkeypatch.setattr(db, "_available", True)
    db.init()
    with db._conn() as conn:
        conn.execute(
            "INSERT INTO exchange_configs "
            "(id, name, display_name, api_key, api_secret, is_paper, is_active, extra) "
            "VALUES ('legacy-1', 'old', 'Old Broker', 'public-ish-key', 'plaintext-secret', 0, 0, '{}')"
        )

    with pytest.raises(RuntimeError, match="legacy exchange_configs contains plaintext broker credentials"):
        validate_production_security()


@pytest.mark.parametrize('token', ['plaintext-token', 'fernet:old-key-ciphertext'])
def test_security_gate_inspects_nested_telegram_credentials(monkeypatch, tmp_path, token):
    import json
    monkeypatch.setenv('ENVIRONMENT', 'production')
    monkeypatch.setenv('STERLING_SECRET_KEY', VALID_SECRET_1)
    monkeypatch.setenv('STERLING_JWT_SECRET', VALID_SECRET_2)
    monkeypatch.setattr(db, '_DB_PATH', str(tmp_path / 'nested.db'))
    monkeypatch.setattr(db, '_available', True)
    db.init()
    db.set_config('kite_tg_targets:default', json.dumps([{'id': 'target', 'bot_token_enc': token}]))
    with pytest.raises(RuntimeError, match='Telegram target credentials require verified migration'):
        validate_production_security()
