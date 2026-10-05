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


def test_env_seed_creates_a_live_account_by_default(monkeypatch):
    monkeypatch.setenv("KITE_API_KEY", "env-api-key")
    monkeypatch.setenv("KITE_API_SECRET", "env-api-secret")
    monkeypatch.delenv("KITE_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("KITE_PAPER", raising=False)
    monkeypatch.setenv("KITE_APP_USER_ID", "default")

    account_id = auth.seed_from_env()

    acct = accounts.get("default", account_id)
    assert acct is not None
    assert acct.is_paper is False
    assert accounts.build_client(acct)._is_paper is False


def test_kite_paper_env_is_no_longer_honoured_and_warns(monkeypatch, caplog):
    monkeypatch.setenv("KITE_API_KEY", "env-api-key")
    monkeypatch.setenv("KITE_API_SECRET", "env-api-secret")
    monkeypatch.delenv("KITE_ACCESS_TOKEN", raising=False)
    monkeypatch.setenv("KITE_PAPER", "true")
    monkeypatch.setenv("KITE_APP_USER_ID", "default")

    with caplog.at_level("WARNING"):
        account_id = auth.seed_from_env()

    acct = accounts.get("default", account_id)
    assert acct.is_paper is False                 # the flag changes nothing
    assert any("KITE_PAPER is no longer honoured" in r.getMessage() for r in caplog.records)


def test_kite_paper_false_is_silently_accepted(monkeypatch, caplog):
    _env(monkeypatch, user_id="ZID-ENV")           # sets KITE_PAPER=false

    with caplog.at_level("WARNING"):
        auth.seed_from_env()

    assert not any("KITE_PAPER" in r.getMessage() for r in caplog.records)
