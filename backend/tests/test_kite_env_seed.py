"""Environment-seeded Kite credentials must preserve broker identity safety."""
from __future__ import annotations

from app.services.exchanges.kite import accounts, auth


def _env(monkeypatch, *, user_id: str = "") -> None:
    monkeypatch.setenv("KITE_API_KEY", "env-api-key")
    monkeypatch.setenv("KITE_API_SECRET", "env-api-secret")
    monkeypatch.setenv("KITE_ACCESS_TOKEN", "env-access-token")
    monkeypatch.setenv("KITE_PAPER", "false")
    monkeypatch.setenv("KITE_APP_USER_ID", "default")
    if user_id:
        monkeypatch.setenv("KITE_USER_ID", user_id)
    else:
        monkeypatch.delenv("KITE_USER_ID", raising=False)
        monkeypatch.delenv("ZERODHA_USER_ID", raising=False)


def test_live_env_token_without_broker_identity_is_not_adopted(monkeypatch):
    _env(monkeypatch)

    account_id = auth.seed_from_env()

    assert account_id
    acct = accounts.get("default", account_id)
    assert acct is not None
    assert acct.is_paper is False
    assert acct.connected is False
    assert acct.kite_user_id == ""


def test_live_env_token_with_broker_identity_is_adopted_unvalidated(monkeypatch):
    _env(monkeypatch, user_id="ZID-ENV")

    account_id = auth.seed_from_env()

    assert account_id
    acct = accounts.get("default", account_id)
    assert acct is not None
    assert acct.connected is True
    assert acct.access_token == "env-access-token"
    assert acct.kite_user_id == "ZID-ENV"
    # Environment provenance is not equivalent to Kite issuing the token in
    # this process; the first real request must prove it.
    assert accounts.validated_age_ms(account_id) is None


def test_production_environment_defaults_to_live_observation(monkeypatch):
    _env(monkeypatch)
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("KITE_PAPER")
    account_id = auth.seed_from_env()
    acct = accounts.get("default", account_id)
    assert acct.is_paper is False
    assert acct.connected is False


def test_production_environment_refuses_simulation_account(monkeypatch):
    _env(monkeypatch)
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("KITE_PAPER", "true")
    assert auth.seed_from_env() is None
    assert not accounts.list_accounts("default")
