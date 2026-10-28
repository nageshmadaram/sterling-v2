"""A failed phone localhost redirect can complete the same signed server login."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from urllib.parse import urlencode

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.endpoints import kite
from app.core.auth import UserContext, get_current_user
from app.services import db, public_access
from app.services.exchanges.kite import accounts, login_preflight, login_recovery, session
from app.services.exchanges.kite.errors import KiteTokenError
from app.services.exchanges.kite.models import KiteAccountCreate
from app.services.notifications import telegram_kite, telegram_lifecycle


def callback_url(user_id="phone-user", account_id="KITE-A", **overrides):
    params = {"request_token": "phone-token", "status": "success", "action": "login",
              "state": session.make_state(user_id, account_id)}
    params.update(overrides)
    return "http://localhost:5173/api/v1/kite/callback?" + urlencode(params)


@pytest.mark.parametrize("origin", ["http://localhost:5173", "https://public.example.test"])
def test_redirect_parses_without_fetching_its_host(origin):
    url = callback_url().replace("http://localhost:5173", origin)
    assert login_recovery.parse_callback_url(
        url, expected_user_id="phone-user", expected_account_id="KITE-A",
    ) == "phone-token"


@pytest.mark.parametrize("change", [
    lambda url: url + "&state=extra",
    lambda url: url + "&request_token=extra",
    lambda url: url + "&status=success",
    lambda url: url + "&action=login",
    lambda url: url.replace("status=success", "status=cancelled"),
    lambda url: url.replace("request_token=phone-token", "request_token="),
    lambda url: url.replace("request_token=phone-token", "request_token=bad%0Atoken"),
    lambda url: url.replace("http://localhost", "file://localhost"),
    lambda url: url.replace("http://localhost", "http://user:password@localhost"),
    lambda url: url + "#fragment",
])
def test_malformed_redirect_is_rejected(change):
    with pytest.raises(ValueError):
        login_recovery.parse_callback_url(
            change(callback_url()), expected_user_id="phone-user", expected_account_id="KITE-A",
        )


@pytest.mark.parametrize("state", [
    "", "tampered", session.make_state("other-user", "KITE-A"),
    session.make_state("phone-user", "OTHER-ACCOUNT"),
    session.make_state("phone-user", "KITE-A", issued_at_ms=1),
])
def test_signed_redirect_must_match_user_and_account(state):
    with pytest.raises(ValueError):
        login_recovery.parse_callback_url(
            callback_url(state=state), expected_user_id="phone-user", expected_account_id="KITE-A",
        )


@pytest.fixture(autouse=True)
def interactive_feature_policy(tmp_path, monkeypatch):
    from app.services import db
    from app.services.auth import user_store, account_policy
    monkeypatch.setattr(db, '_DB_PATH', str(tmp_path / 'interactive-policy.db'))
    assert db.init()
    monkeypatch.setattr(user_store, '_users', {})
    monkeypatch.setattr(user_store, '_loaded', True)
    user_store._init_table()
    user_store.add('primary', 'Synthetic-password-123', role='admin', user_id='default')
    user_store.add('member', 'Synthetic-password-123', user_id='phone-user')
    for ident in ('default', 'phone-user'):
        account_policy.set_features('default', ident, dict.fromkeys(account_policy.FEATURES, True), 0)


@pytest.fixture
def mobile_client(monkeypatch):
    app = FastAPI()
    app.include_router(kite.router, prefix="/api/v1")
    app.dependency_overrides[get_current_user] = lambda: UserContext(
        user_id="phone-user", username="operator", role="admin",
    )
    monkeypatch.setattr(login_preflight, "check_login_key", AsyncMock())
    monkeypatch.setattr(public_access, "status", lambda: {})
    monkeypatch.setattr(telegram_kite, "schedule_broker_login_confirmation", Mock())
    with TestClient(app) as client:
        yield client


def test_login_guidance_uses_public_callback_instead_of_desktop_localhost(mobile_client, monkeypatch):
    acct = accounts.add("phone-user", KiteAccountCreate(label="Phone", api_key="key", api_secret="secret"))
    monkeypatch.setattr(public_access, "status", lambda: {
        "callback_url": "https://public.example.test/api/v1/kite/callback",
    })
    result = mobile_client.get("/api/v1/kite/login-url").json()
    assert result["redirect_uri"] == "https://public.example.test/api/v1/kite/callback"
    assert session.parse_state(result["state"]) == ("phone-user", acct.id)


def test_phone_redirect_paste_exchanges_and_persists_session(mobile_client, monkeypatch):
    acct = accounts.add("phone-user", KiteAccountCreate(label="Phone", api_key="key", api_secret="secret"))
    broker = SimpleNamespace(generate_session=AsyncMock(return_value={
        "access_token": "saved-access", "user_id": "PHONE123", "user_name": "Phone Trader",
    }), close=AsyncMock())
    monkeypatch.setattr(accounts, "build_client", lambda _: broker)
    release = AsyncMock()
    monkeypatch.setattr(accounts, "release_client", release)
    notify = Mock(return_value="login-confirmation-event")
    monkeypatch.setattr(telegram_lifecycle, "enqueue_best_effort", notify)
    schedule = Mock()
    monkeypatch.setattr(telegram_kite, "schedule_broker_login_confirmation", schedule)

    result = mobile_client.post("/api/v1/kite/session", json={
        "account_id": acct.id, "request_token": callback_url(account_id=acct.id),
    })
    assert result.status_code == 200, result.text
    assert result.json()["connected"] is True
    saved = accounts.get("phone-user", acct.id)
    assert saved.access_token == "saved-access" and saved.kite_user_id == "PHONE123"
    broker.generate_session.assert_awaited_once_with("phone-token")
    broker.close.assert_awaited_once()
    release.assert_awaited_once_with(acct.id)
    assert notify.call_args.kwargs["dedupe_key"] == f"kite_login:{acct.id}:{saved.last_login_at_ms}"
    detail = notify.call_args.kwargs["detail"]
    assert "Phone Trader" in detail and "PHONE123" in detail and "Valid until:" in detail
    assert "saved-access" not in detail and "secret" not in detail
    schedule.assert_called_once_with("phone-user", "login-confirmation-event")
    assert "saved-access" not in result.text


def test_foreign_signed_redirect_cannot_reach_broker(mobile_client, monkeypatch):
    acct = accounts.add("phone-user", KiteAccountCreate(label="Phone", api_key="key", api_secret="secret"))
    build = Mock()
    monkeypatch.setattr(accounts, "build_client", build)
    result = mobile_client.post("/api/v1/kite/session", json={
        "account_id": acct.id, "request_token": callback_url(user_id="other-user", account_id=acct.id),
    })
    assert result.status_code == 400
    build.assert_not_called()
    assert not accounts.get("phone-user", acct.id).connected


@pytest.mark.parametrize("already_connected", [False, True])
def test_consumed_token_preserves_existing_login_recovery(mobile_client, monkeypatch, already_connected):
    acct = accounts.add("phone-user", KiteAccountCreate(label="Phone", api_key="key", api_secret="secret"))
    if already_connected:
        accounts.save_session("phone-user", acct.id, access_token="saved-access", kite_user_id="PHONE123")
    broker = SimpleNamespace(generate_session=AsyncMock(side_effect=KiteTokenError("Token already used")), close=AsyncMock())
    monkeypatch.setattr(accounts, "build_client", lambda _: broker)
    result = mobile_client.post("/api/v1/kite/session", json={
        "account_id": acct.id, "request_token": callback_url(account_id=acct.id),
    })
    assert result.status_code == (200 if already_connected else 401), result.text
    if already_connected:
        assert result.json()["connected"] is True
    else:
        assert "only be used once" in result.json()["detail"]
    broker.close.assert_awaited_once()


def test_login_activates_assigned_profile_when_no_account_is_active(mobile_client, monkeypatch):
    acct = accounts.add("phone-user", KiteAccountCreate(label="Assigned", api_key="key", api_secret="secret"))
    acct.is_active = False
    accounts._persist(acct)
    broker = SimpleNamespace(generate_session=AsyncMock(return_value={
        "access_token": "saved-access", "user_id": "PHONE123", "user_name": "Phone Trader",
    }), close=AsyncMock())
    monkeypatch.setattr(accounts, "build_client", lambda _: broker)
    response = mobile_client.post("/api/v1/kite/session", json={
        "account_id": acct.id, "request_token": callback_url(account_id=acct.id),
    })
    assert response.status_code == 200, response.text
    status = mobile_client.get("/api/v1/kite/status").json()
    assert status["connected"] is True
    assert status["account_id"] == acct.id
    assert status["kite_user_id"] == "PHONE123"


def test_login_does_not_replace_another_active_account(mobile_client, monkeypatch):
    current = accounts.add("phone-user", KiteAccountCreate(label="Current", api_key="one", api_secret="secret"))
    target = accounts.add("phone-user", KiteAccountCreate(label="Other", api_key="two", api_secret="secret"))
    broker = SimpleNamespace(generate_session=AsyncMock(return_value={
        "access_token": "saved-access", "user_id": "PHONE123",
    }), close=AsyncMock())
    monkeypatch.setattr(accounts, "build_client", lambda _: broker)
    response = mobile_client.post("/api/v1/kite/session", json={
        "account_id": target.id, "request_token": callback_url(account_id=target.id),
    })
    assert response.status_code == 200, response.text
    assert accounts.get_active("phone-user").id == current.id
    assert accounts.get("phone-user", target.id).connected


async def test_failed_exchange_closes_client_without_publishing_session(monkeypatch):
    acct = SimpleNamespace(id="KITE-A", api_key="key", api_secret="secret")
    broker = SimpleNamespace(generate_session=AsyncMock(side_effect=RuntimeError("offline")), close=AsyncMock())
    monkeypatch.setattr(accounts, "build_client", lambda _: broker)
    save = Mock()
    monkeypatch.setattr(accounts, "save_session", save)
    with pytest.raises(RuntimeError, match="offline"):
        await login_recovery.complete_login("phone-user", acct, "phone-token")
    broker.close.assert_awaited_once()
    save.assert_not_called()


def test_failed_persistence_never_confirms_login(mobile_client, monkeypatch):
    acct = accounts.add("phone-user", KiteAccountCreate(label="Phone", api_key="key", api_secret="secret"))
    broker = SimpleNamespace(generate_session=AsyncMock(return_value={
        "access_token": "saved-access", "user_id": "PHONE123",
    }), close=AsyncMock())
    monkeypatch.setattr(accounts, "build_client", lambda _: broker)
    monkeypatch.setattr(accounts, "save_session", Mock(side_effect=ValueError("identity conflict")))
    notify, schedule = Mock(), Mock()
    monkeypatch.setattr(telegram_lifecycle, "enqueue_best_effort", notify)
    monkeypatch.setattr(telegram_kite, "schedule_broker_login_confirmation", schedule)
    response = mobile_client.post("/api/v1/kite/session", json={
        "account_id": acct.id, "request_token": callback_url(account_id=acct.id),
    })
    assert response.status_code == 409
    notify.assert_not_called()
    schedule.assert_not_called()
    assert not accounts.get("phone-user", acct.id).connected


def test_notification_failure_does_not_change_successful_login(mobile_client, monkeypatch):
    acct = accounts.add("phone-user", KiteAccountCreate(label="Phone", api_key="key", api_secret="secret"))
    broker = SimpleNamespace(generate_session=AsyncMock(return_value={
        "access_token": "saved-access", "user_id": "PHONE123",
    }), close=AsyncMock())
    monkeypatch.setattr(accounts, "build_client", lambda _: broker)
    monkeypatch.setattr(telegram_lifecycle, "enqueue_best_effort", Mock(return_value="queued-event"))
    monkeypatch.setattr(telegram_kite, "schedule_broker_login_confirmation", Mock(side_effect=RuntimeError("notification offline")))
    response = mobile_client.post("/api/v1/kite/session", json={
        "account_id": acct.id, "request_token": callback_url(account_id=acct.id),
    })
    assert response.status_code == 200 and response.json()["connected"] is True
    assert accounts.get("phone-user", acct.id).connected


def test_callback_gives_persistent_login_receipt_after_save(mobile_client, monkeypatch):
    acct = accounts.add("phone-user", KiteAccountCreate(label="Phone", api_key="key", api_secret="secret"))
    broker = SimpleNamespace(generate_session=AsyncMock(return_value={
        "access_token": "saved-access", "user_id": "PHONE123", "user_name": "Phone Trader",
    }), close=AsyncMock())
    monkeypatch.setattr(accounts, "build_client", lambda _: broker)
    response = mobile_client.get("/api/v1/kite/callback", params={
        "state": session.make_state("phone-user", acct.id), "request_token": "phone-token", "status": "success",
    })
    assert response.status_code == 200
    for text in ("Kite login successful", "Phone Trader", "PHONE123", "Expires", "IST", "session is saved", "Open Sterling"):
        assert text in response.text
    assert "setInterval" not in response.text and "setTimeout" not in response.text
    assert "saved-access" not in response.text


def test_interactive_receipt_survives_restart_and_silent_renewal(mobile_client, monkeypatch):
    acct = accounts.add("phone-user", KiteAccountCreate(label="Phone", api_key="key", api_secret="secret"))
    broker = SimpleNamespace(generate_session=AsyncMock(return_value={
        "access_token": "saved-access", "user_id": "PHONE123",
    }), close=AsyncMock())
    monkeypatch.setattr(accounts, "build_client", lambda _: broker)
    login = mobile_client.get("/api/v1/kite/login-url").json()
    assert login["account_id"] == acct.id
    assert login["confirmation_id"] == session.login_confirmation_id(login["state"])
    assert accounts.get_login_confirmation_id("phone-user", acct.id) == ""
    response = mobile_client.post("/api/v1/kite/session", json={
        "account_id": acct.id, "request_token": callback_url(account_id=acct.id, state=login["state"]),
    })
    assert response.status_code == 200
    assert accounts.to_response(accounts.get("phone-user", acct.id)).login_confirmation_id == login["confirmation_id"]

    accounts._accounts.clear()
    accounts._loaded = False
    accounts.bootstrap()
    assert accounts.get_login_confirmation_id("phone-user", acct.id) == login["confirmation_id"]
    assert accounts.get_login_confirmation_id("other-user", acct.id) == ""
    accounts.save_session("phone-user", acct.id, access_token="renewed-access", kite_user_id="PHONE123")
    assert accounts.get_login_confirmation_id("phone-user", acct.id) == login["confirmation_id"]


def test_receipt_persistence_failure_rolls_back_session(mobile_client, monkeypatch):
    acct = accounts.add("phone-user", KiteAccountCreate(label="Phone", api_key="key", api_secret="secret"))
    broker = SimpleNamespace(generate_session=AsyncMock(return_value={
        "access_token": "must-not-publish", "user_id": "PHONE123",
    }), close=AsyncMock())
    monkeypatch.setattr(accounts, "build_client", lambda _: broker)
    notify = Mock()
    monkeypatch.setattr(telegram_lifecycle, "enqueue_best_effort", notify)
    with db._conn() as connection:
        connection.execute("""CREATE TRIGGER fail_kite_login_receipt BEFORE INSERT ON system_config
            WHEN NEW.key LIKE 'kite_login_receipt:%'
            BEGIN SELECT RAISE(ABORT, 'receipt unavailable'); END""")
    try:
        response = mobile_client.post("/api/v1/kite/session", json={
            "account_id": acct.id, "request_token": callback_url(account_id=acct.id),
        })
        assert response.status_code == 503
        assert not accounts.get("phone-user", acct.id).connected
        assert accounts.get_login_confirmation_id("phone-user", acct.id) == ""
        notify.assert_not_called()
    finally:
        with db._conn() as connection:
            connection.execute("DROP TRIGGER fail_kite_login_receipt")


async def test_selected_broker_login_is_bound_to_owner_without_switching_active(monkeypatch):
    user = SimpleNamespace(user_id='owner')
    selected = SimpleNamespace(id='second', api_key='second-key')
    get = Mock(side_effect=lambda uid, aid: selected if (uid, aid) == ('owner', 'second') else None)
    monkeypatch.setattr(accounts, 'get', get)
    monkeypatch.setattr(kite, '_require_active', Mock(side_effect=AssertionError('must not switch or use active')))
    monkeypatch.setattr(login_preflight, 'check_login_key', AsyncMock())
    result = await kite.login_url(user=user, account_id='second')
    assert result.account_id == 'second'
    assert session.parse_state(result.state) == ('owner', 'second')
    with pytest.raises(kite.HTTPException) as rejected:
        await kite.login_url(user=user, account_id='another-users-account')
    assert rejected.value.status_code == 404
