"""Phone login completes through the Mini App's authenticated action boundary."""
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.endpoints import tma
from app.core.auth import UserContext
from app.services.exchanges.kite import accounts, session
from app.services.exchanges.kite.models import KiteAccountCreate
from app.services.notifications.telegram_webapp_auth import tma_user


@pytest.fixture
def broker_desk(monkeypatch):
    accounts.clear()
    acct = accounts.add("mobile-user", KiteAccountCreate(
        label="Mobile", api_key="synthetic-key", api_secret="synthetic-secret", is_paper=True,
    ))
    monkeypatch.setattr("app.services.exchanges.kite.login_preflight.check_login_key", AsyncMock())
    monkeypatch.setattr("app.services.public_access.status", lambda: {
        "callback_url": "https://sterling.example.test/api/v1/kite/callback",
    })
    client = MagicMock()
    client.generate_session = AsyncMock(return_value={
        "access_token": "synthetic-access", "user_id": "MOBILE123", "user_name": "Mobile User",
    })
    client.close = AsyncMock()
    monkeypatch.setattr(accounts, "build_client", lambda _acct: client)
    release = AsyncMock()
    monkeypatch.setattr(accounts, "release_client", release)
    monkeypatch.setattr("app.services.notifications.telegram_lifecycle.enqueue_best_effort", lambda *args, **kwargs: None)
    app = FastAPI()
    app.include_router(tma.router)
    app.dependency_overrides[tma_user] = lambda: UserContext(user_id="mobile-user", role="admin")
    with TestClient(app) as http:
        yield http, acct, client, release
    accounts.clear()


def _callback(state):
    return "http://localhost:5173/api/v1/kite/callback?" + urlencode({
        "request_token": "fresh-mobile-token", "state": state, "status": "success", "action": "login",
    })


@pytest.mark.kite_features('default', 'mobile-user')
def test_phone_can_finish_localhost_redirect_without_leaving_mini_app(broker_desk):
    http, acct, client, release = broker_desk
    login = http.post("/api/v1/tma/action", json={"action": "kite_login"})
    assert login.status_code == 200, login.text
    body = login.json()
    assert body["redirect_uri"] == "https://sterling.example.test/api/v1/kite/callback"
    assert body["account_id"] == acct.id
    state = parse_qs(parse_qs(urlsplit(body["login_url"]).query)["redirect_params"][0])["state"][0]
    completed = http.post("/api/v1/tma/action", json={
        "action": "kite_login_complete", "callback_url": _callback(state), "account_id": body["account_id"],
    })
    assert completed.status_code == 200, completed.text
    assert completed.json()["success"] is True
    assert completed.json()["account_id"] == acct.id
    assert completed.json()["connected"] is True
    assert completed.json()["kite_user_id"] == "MOBILE123"
    assert completed.json()["user_name"] == "Mobile User"
    assert completed.json()["last_login_at_ms"] is not None
    assert completed.json()["login_confirmation_id"] == body["confirmation_id"]
    assert completed.json()["token_expires_at_ms"] > completed.json()["last_login_at_ms"]
    client.generate_session.assert_awaited_once_with("fresh-mobile-token")
    client.close.assert_awaited_once()
    release.assert_awaited_once_with(acct.id)
    assert accounts.get("mobile-user", acct.id).access_token == "synthetic-access"
    assert "synthetic-access" not in completed.text
    assert "synthetic-secret" not in login.text


def test_login_status_confirms_durable_session_without_broker_or_renewal_calls(broker_desk, monkeypatch):
    http, acct, client, _release = broker_desk
    renew = AsyncMock()
    monkeypatch.setattr("app.services.exchanges.kite.auth.renew", renew)
    login = http.post("/api/v1/tma/action", json={"action": "kite_login"}).json()
    assert login["last_login_at_ms"] is None
    assert login["connected"] is False
    pending = http.post("/api/v1/tma/action", json={"action": "kite_login_status", "account_id": acct.id})
    assert pending.json()["connected"] is False
    saved = accounts.save_session("mobile-user", acct.id, access_token="synthetic-access", kite_user_id="MOBILE123", user_name="Mobile User")
    confirmed = http.post("/api/v1/tma/action", json={"action": "kite_login_status", "account_id": acct.id})
    assert confirmed.status_code == 200
    assert confirmed.json() == {
        "success": True, "connected": True, "account_id": acct.id, "label": "Mobile",
        "kite_user_id": "MOBILE123", "user_name": "Mobile User",
        "login_confirmation_id": "",
        "last_login_at_ms": saved.last_login_at_ms, "token_expires_at_ms": saved.token_expires_at,
    }
    assert "synthetic-access" not in confirmed.text
    client.generate_session.assert_not_awaited()
    renew.assert_not_awaited()


def test_status_rejects_other_users_account_and_missing_account_id(broker_desk):
    http, _acct, client, _release = broker_desk
    other = accounts.add("other-user", KiteAccountCreate(
        label="Private", api_key="other-key", api_secret="other-secret", is_paper=True,
    ))
    response = http.post("/api/v1/tma/action", json={"action": "kite_login_status", "account_id": other.id})
    assert response.status_code == 404
    assert "Private" not in response.text
    for account_id in [None, "", 1]:
        invalid = http.post("/api/v1/tma/action", json={"action": "kite_login_status", "account_id": account_id})
        assert invalid.status_code == 422
    client.generate_session.assert_not_awaited()


def test_existing_session_is_exposed_as_login_baseline_and_expired_tokens_are_not_connected(broker_desk):
    http, acct, _client, _release = broker_desk
    saved = accounts.save_session("mobile-user", acct.id, access_token="old-access", kite_user_id="MOBILE123")
    login = http.post("/api/v1/tma/action", json={"action": "kite_login"}).json()
    assert login["connected"] is True
    assert login["last_login_at_ms"] == saved.last_login_at_ms
    accounts.save_session("mobile-user", acct.id, access_token="expired-access", kite_user_id="MOBILE123", expires_at_ms=1)
    status = http.post("/api/v1/tma/action", json={"action": "kite_login_status", "account_id": acct.id})
    assert status.json()["connected"] is False


def test_pending_mobile_login_is_not_confirmed_by_silent_renewal(broker_desk, monkeypatch):
    http, acct, _client, _release = broker_desk
    old_receipt = "previous-interactive-receipt"
    saved = accounts.save_session(
        "mobile-user", acct.id, access_token="old-access", kite_user_id="MOBILE123",
        login_confirmation_id=old_receipt,
    )
    original_login_at = saved.last_login_at_ms
    login = http.post("/api/v1/tma/action", json={"action": "kite_login"}).json()
    monkeypatch.setattr(accounts, "_now_ms", lambda: original_login_at + 1000)
    renewed = accounts.save_session("mobile-user", acct.id, access_token="renewed-access", kite_user_id="MOBILE123")
    status = http.post("/api/v1/tma/action", json={"action": "kite_login_status", "account_id": acct.id}).json()
    assert status["connected"] is True
    assert renewed.last_login_at_ms != original_login_at
    assert status["login_confirmation_id"] == old_receipt
    assert status["login_confirmation_id"] != login["confirmation_id"]


@pytest.mark.parametrize("state", ["forged", session.make_state("another-user", "another-account")])
def test_recovery_rejects_forged_or_other_tenant_handoff_before_exchange(broker_desk, state):
    http, acct, client, _release = broker_desk
    response = http.post("/tma/action", json={
        "action": "kite_login_complete", "callback_url": _callback(state), "account_id": acct.id,
    })
    assert response.status_code == 400
    client.generate_session.assert_not_awaited()
    assert not accounts.get("mobile-user", acct.id).connected


@pytest.mark.parametrize("value", ["fresh-mobile-token", "", None, {"request_token": "token"}])
def test_mini_app_recovery_requires_full_signed_browser_address(broker_desk, value):
    http, _acct, client, _release = broker_desk
    response = http.post("/tma/action", json={"action": "kite_login_complete", "callback_url": value})
    assert response.status_code == 422
    client.generate_session.assert_not_awaited()


def test_recovery_requires_sterling_or_telegram_authentication():
    app = FastAPI()
    app.include_router(tma.router)
    with TestClient(app) as http:
        response = http.post("/api/v1/tma/action", json={
            "action": "kite_login_complete", "callback_url": _callback("forged"),
        })
    assert response.status_code == 401


def test_login_status_requires_sterling_or_telegram_authentication():
    app = FastAPI()
    app.include_router(tma.router)
    with TestClient(app) as http:
        response = http.post("/api/v1/tma/action", json={"action": "kite_login_status", "account_id": "private-account"})
    assert response.status_code == 401
