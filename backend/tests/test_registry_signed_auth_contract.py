"""Signed Sterling access-token gate for registry writes (no live services)."""
from types import SimpleNamespace

import pytest

from app.core import tokens
from app.persistence import strategy_registry_service as registry
from app.services.auth import user_store


def test_registry_requires_signed_current_privileged_access_token(monkeypatch):
    # Synthetic accounts and signing key. Real JWT signing + decoding path is
    # exercised, but no production secret, user DB or Kite session is accessed.
    monkeypatch.setattr(tokens, "get_jwt_key", lambda: "synthetic-registry-test-signing-key")
    accounts = {
        "administrator": SimpleNamespace(
            id="administrator", username="a", role="admin", is_active=True, token_version=1),
        "viewer": SimpleNamespace(
            id="viewer", username="v", role="user", is_active=True, token_version=1),
        "operator": SimpleNamespace(
            id="operator", username="o", role="operator", is_active=True, token_version=1),
    }
    monkeypatch.setattr(user_store, "bootstrap", lambda: None)
    monkeypatch.setattr(user_store, "get_by_id", lambda user_id: accounts.get(user_id))
    access = tokens.mint_access("administrator", "a", "admin", 1)
    assert registry._identity(access).user_id == "administrator"
    assert registry._identity(tokens.mint_access("operator", "o", "operator", 1)).role == "operator"

    for invalid in (
        "",
        access + "invalid-signature",
        tokens.mint_refresh("administrator", "a", "admin", 1),
        tokens.mint_access("viewer", "v", "admin", 1),  # signed role claim is not authority
    ):
        with pytest.raises(PermissionError):
            registry._identity(invalid)

    # Revocation and account deactivation are checked against current user state.
    accounts["administrator"].token_version = 2
    with pytest.raises(PermissionError):
        registry._identity(access)
    accounts["administrator"].token_version = 1
    accounts["administrator"].is_active = False
    with pytest.raises(PermissionError):
        registry._identity(access)
