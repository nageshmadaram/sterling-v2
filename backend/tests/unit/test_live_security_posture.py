"""Live broker security is tied to real authority, never ENVIRONMENT.

These tests exercise the real posture checks. The shared test harness bypasses
that posture for unrelated Kite fixtures, but explicitly excludes this module.
"""
from pathlib import Path

import pytest

from app.core import security
from app.services import db
from app.services.auth import user_store
from app.services.exchanges.kite import accounts
from app.services.exchanges.kite.models import KiteAccountCreate


SECRET_A = "enc-" + ("a" * 48)
SECRET_B = "enc-" + ("c" * 48)
JWT = "jwt-" + ("b" * 48)
ADMIN_PASSWORD = "a-long-unique-operator-passphrase"


@pytest.fixture()
def isolated_live_store(tmp_path, monkeypatch):
    path = str(tmp_path / "live-security.db")
    monkeypatch.setenv("STERLING_DB_PATH", path)
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setattr(db, "_DB_PATH", path)
    monkeypatch.setattr(db, "_available", True)
    db.init()

    security._fernet = None
    security._backend = "b64"
    security._active_secret_hash = ""
    accounts.clear()
    user_store.clear()
    yield

    accounts.clear()
    user_store.clear()
    security._fernet = None
    security._backend = "b64"
    security._active_secret_hash = ""


def _strong_live_env(monkeypatch):
    monkeypatch.setenv("STERLING_SECRET_KEY", SECRET_A)
    monkeypatch.setenv("STERLING_JWT_SECRET", JWT)
    monkeypatch.setenv("STERLING_ADMIN_USERNAME", "admin")
    monkeypatch.setenv("STERLING_ADMIN_PASSWORD", ADMIN_PASSWORD)


def test_live_security_is_strict_even_when_environment_is_development(
    isolated_live_store, monkeypatch,
):
    monkeypatch.delenv("STERLING_SECRET_KEY", raising=False)
    monkeypatch.delenv("STERLING_JWT_SECRET", raising=False)

    with pytest.raises(RuntimeError, match="Live security error: STERLING_SECRET_KEY"):
        security.validate_live_security()


def test_development_without_a_broker_does_not_seed_an_unknown_admin_password(
    isolated_live_store, monkeypatch,
):
    monkeypatch.delenv("STERLING_ADMIN_PASSWORD", raising=False)

    user_store.bootstrap()

    assert user_store.all_users() == []


@pytest.mark.parametrize("password", ["admin", "password", "short-pw-1", "Sterling123"])
def test_bootstrap_admin_is_never_seeded_with_a_weak_password(
    isolated_live_store, monkeypatch, password,
):
    monkeypatch.setenv("STERLING_ADMIN_PASSWORD", password)

    user_store.bootstrap()

    assert user_store.has_active_admin_credential() is False


def test_admin_crud_rejects_weak_passwords(isolated_live_store):
    with pytest.raises(ValueError, match="administrator password"):
        user_store.add("operator", "admin", role="admin", user_id="operator")

    admin = user_store.add(
        "operator",
        ADMIN_PASSWORD,
        role="admin",
        user_id="operator",
    )
    assert admin.role == "admin"

    with pytest.raises(ValueError, match="administrator password"):
        user_store.set_password(admin.id, "password")


def test_live_account_cannot_be_persisted_under_dev_crypto(
    isolated_live_store, monkeypatch,
):
    monkeypatch.delenv("STERLING_SECRET_KEY", raising=False)
    monkeypatch.delenv("STERLING_JWT_SECRET", raising=False)

    with pytest.raises(RuntimeError, match="Live security error"):
        accounts.add(
            "default",
            KiteAccountCreate(label="Live", api_key="key", api_secret="secret"),
        )

    assert accounts.all_accounts() == []


def test_test_double_account_stays_available_without_live_posture(
    isolated_live_store, monkeypatch,
):
    monkeypatch.delenv("STERLING_SECRET_KEY", raising=False)
    monkeypatch.delenv("STERLING_JWT_SECRET", raising=False)

    acct = accounts.add(
        "fixture",
        KiteAccountCreate(
            label="Fixture",
            api_key="key",
            api_secret="secret",
            is_paper=True,
        ),
    )

    assert acct.is_paper is True


def test_strong_live_posture_allows_fernet_broker_credentials(
    isolated_live_store, monkeypatch,
):
    _strong_live_env(monkeypatch)
    user_store.bootstrap()
    assert user_store.has_active_admin_credential()

    acct = accounts.add(
        "default",
        KiteAccountCreate(label="Live", api_key="key", api_secret="secret"),
    )

    assert acct.is_paper is False
    assert acct.api_secret_enc.startswith("fernet:")
    security.validate_live_security()


def test_key_change_cannot_leave_old_fernet_instance_authoritative(
    isolated_live_store, monkeypatch,
):
    _strong_live_env(monkeypatch)
    user_store.bootstrap()
    accounts.add(
        "default",
        KiteAccountCreate(label="Live", api_key="key", api_secret="secret"),
    )

    # Same process, different configured key. The validator must rebuild Fernet
    # and then fail to decrypt the bytes written under the prior key.
    monkeypatch.setenv("STERLING_SECRET_KEY", SECRET_B)

    with pytest.raises(RuntimeError, match="failed decryption under active key"):
        security.validate_live_security()


def test_env_kite_credentials_make_the_process_live_capable(monkeypatch):
    from main import _kite_env_live_credentials_present

    for key in ("KITE_API_KEY", "KITE_API_SECRET", "ZERODHA_API_KEY", "ZERODHA_API_SECRET"):
        monkeypatch.delenv(key, raising=False)
    assert _kite_env_live_credentials_present() is False

    monkeypatch.setenv("KITE_API_KEY", "key")
    monkeypatch.setenv("KITE_API_SECRET", "secret")
    assert _kite_env_live_credentials_present() is True


def test_startup_audits_live_security_before_identity_conversion_or_seed():
    root = Path(__file__).resolve().parents[2]
    source = (root / "main.py").read_text(encoding="utf-8")

    db_init = source.index("_db.init()")
    account_bootstrap = source.index("_kite_accounts.bootstrap()")
    live_security = source.index("validate_live_security()")
    user_bootstrap = source.index("_user_store.bootstrap()")
    migration = source.index("_kite_accounts.migrate_legacy_paper_accounts()")
    seed = source.index("_kite_auth.seed_from_env()")

    assert db_init < account_bootstrap < live_security < user_bootstrap < migration < seed
    assert "if _kite_env_live_credentials_present():" in source
    assert "raise RuntimeError(f\"Kite environment provisioning failed:" in source
