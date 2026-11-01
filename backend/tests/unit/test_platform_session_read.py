"""No broker/session duplication or cross-account data during read-only discovery."""
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from app.api.v1.endpoints import platform_surfaces as endpoint
from app.core.auth import UserContext, get_current_user
from app.services.exchanges.kite import accounts, ticker_manager
from app.services.platform_session_read import session_feed_snapshot


def account(owner="alice", ident="KITE-A", connected=True, *, paper=False):
    return SimpleNamespace(user_id=owner, id=ident, connected=connected,
                           is_paper=paper, is_sim=False)


def fail_new_state(*args, **kwargs):
    raise AssertionError("Platform read contract must not create broker/feed state")


def test_reads_owner_account_and_existing_ticker_only(monkeypatch):
    seen = []
    monkeypatch.setattr(accounts, "get_active", lambda uid: seen.append(("account", uid)) or account())
    monkeypatch.setattr(ticker_manager, "status", lambda uid: seen.append(("ticker", uid)) or {
        "account_id": "KITE-A", "connected": True, "subscribed": [11, 12],
        "last_tick_ms": 123456,
    })
    monkeypatch.setattr(accounts, "acquire_client", fail_new_state)
    monkeypatch.setattr(accounts, "build_client", fail_new_state)
    monkeypatch.setattr(ticker_manager, "ensure", fail_new_state)
    monkeypatch.setattr(ticker_manager, "subscribe", fail_new_state)
    result = session_feed_snapshot(UserContext(user_id="alice", role="viewer"))
    assert result == {
        "view": "live", "account_configured": True,
        "stored_session_present": True, "shared_owner_feed": False,
        "active_account_feed_bound": True, "feed_connected": True,
        "subscribed_count": 2, "last_tick_ms": 123456,
    }
    assert seen == [("account", "alice"), ("ticker", "alice")]
    assert "KITE-A" not in str(result)
    assert "11" not in str(result)


def test_no_account_does_not_probe_other_users_feed(monkeypatch):
    monkeypatch.setattr(accounts, "get_active", lambda uid: None)
    monkeypatch.setattr(ticker_manager, "status", fail_new_state)
    result = session_feed_snapshot(UserContext(user_id="other", role="viewer"))
    assert not result["account_configured"]
    assert not result["feed_connected"]
    assert not result["active_account_feed_bound"]
    assert result["subscribed_count"] == 0


def test_account_switch_never_rebrands_stale_socket(monkeypatch):
    monkeypatch.setattr(accounts, "get_active", lambda uid: account(ident="KITE-NEW"))
    monkeypatch.setattr(ticker_manager, "status", lambda uid: {
        "account_id": "KITE-OLD", "connected": True, "subscribed": [1, 2, 3],
        "last_tick_ms": 98765,
    })
    result = session_feed_snapshot(UserContext(user_id="alice"))
    assert result["stored_session_present"] is True
    assert not result["active_account_feed_bound"]
    assert not result["feed_connected"]
    assert result["subscribed_count"] == result["last_tick_ms"] == 0


def test_simulation_reads_exact_owner_feed_without_new_ticker(monkeypatch):
    read = []
    monkeypatch.setattr(accounts, "get_active", lambda uid: read.append(("account", uid)) or account())
    monkeypatch.setattr(ticker_manager, "status", lambda uid: read.append(("ticker", uid)) or {
        "account_id": "KITE-A", "connected": True, "subscribed": [7],
        "last_tick_ms": 50,
    })
    monkeypatch.setattr(ticker_manager, "ensure", fail_new_state)
    result = session_feed_snapshot(UserContext(
        user_id="sim:alice", owner_uid="alice", view="simulation", role="admin",
    ))
    assert result["view"] == "simulation"
    assert result["shared_owner_feed"] is True
    assert result["feed_connected"] is True
    assert read == [("account", "alice"), ("ticker", "sim:alice")]


@pytest.mark.parametrize("principal", [
    UserContext(user_id="sim:alice"),
    UserContext(user_id="sim:alice", owner_uid="bob", view="simulation", role="admin"),
    UserContext(user_id="sim:alice", owner_uid="alice", view="simulation", role="viewer"),
    UserContext(user_id="alice", view="simulation", owner_uid="alice", role="admin"),
])
def test_reserved_and_inconsistent_simulation_scope_is_refused(principal, monkeypatch):
    monkeypatch.setattr(accounts, "get_active", fail_new_state)
    with pytest.raises(ValueError, match="scope"):
        session_feed_snapshot(principal)


@pytest.mark.parametrize("bad_account", [
    account(owner="other"),
    account(paper=True),
    SimpleNamespace(user_id="alice", id="SIM-OTHER", connected=True,
                    is_paper=False, is_sim=True),
])
def test_foreign_paper_or_virtual_active_account_is_refused(bad_account, monkeypatch):
    monkeypatch.setattr(accounts, "get_active", lambda uid: bad_account)
    monkeypatch.setattr(ticker_manager, "status", fail_new_state)
    with pytest.raises(ValueError, match="account_scope"):
        session_feed_snapshot(UserContext(user_id="alice"))


def test_bearer_scope_and_kite_feature_guard_are_required_for_runtime(monkeypatch):
    app = FastAPI()
    app.include_router(endpoint.router, prefix="/api/v1")
    app.dependency_overrides[get_current_user] = lambda: UserContext(user_id="alice", role="viewer")
    calls = []
    monkeypatch.setattr(endpoint, "require_feature", lambda user, feature: calls.append((user.user_id, feature)))
    monkeypatch.setattr(endpoint, "session_feed_snapshot", lambda user: {
        "view": "live", "account_configured": False, "stored_session_present": False,
        "shared_owner_feed": False, "active_account_feed_bound": False,
        "feed_connected": False, "subscribed_count": 0, "last_tick_ms": 0,
    })
    with TestClient(app) as c:
        response = c.get("/api/v1/platform/runtime")
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store, private"
        assert calls == [("alice", "kite")]
        assert response.json()["execution_authorized"] is False
        assert c.post("/api/v1/platform/runtime").status_code == 405
        monkeypatch.setattr(endpoint, "require_feature",
                            lambda *args: (_ for _ in ()).throw(HTTPException(403)))
        assert c.get("/api/v1/platform/runtime").status_code == 403


def test_runtime_inconsistent_account_fails_closed(monkeypatch):
    app = FastAPI()
    app.include_router(endpoint.router, prefix="/api/v1")
    app.dependency_overrides[get_current_user] = lambda: UserContext(user_id="alice")
    monkeypatch.setattr(endpoint, "require_feature", lambda *args: None)
    monkeypatch.setattr(endpoint, "session_feed_snapshot",
                        lambda *args: (_ for _ in ()).throw(ValueError("account_scope_mismatch")))
    with TestClient(app) as c:
        assert c.get("/api/v1/platform/runtime").status_code == 503


@pytest.mark.parametrize("identifier", [None, "", " ", "\t"])
def test_matching_missing_account_ids_never_prove_ticker_ownership(monkeypatch, identifier):
    """Two empty IDs matching must not turn a disconnected/unknown feed green."""
    monkeypatch.setattr(accounts, "get_active", lambda uid: account(ident=identifier))
    monkeypatch.setattr(ticker_manager, "status", lambda uid: {
        "account_id": identifier, "connected": True, "subscribed": [1, 2],
        "last_tick_ms": 123,
    })
    result = session_feed_snapshot(UserContext(user_id="alice"))
    assert result["account_configured"] is True
    assert result["stored_session_present"] is True
    assert result["active_account_feed_bound"] is False
    assert result["feed_connected"] is False
    assert result["subscribed_count"] == result["last_tick_ms"] == 0


@pytest.mark.parametrize("ticker_id", [None, "", " ", "OTHER-ACCOUNT"])
def test_unverified_ticker_account_id_is_not_trusted(monkeypatch, ticker_id):
    monkeypatch.setattr(accounts, "get_active", lambda uid: account(ident="KITE-A"))
    monkeypatch.setattr(ticker_manager, "status", lambda uid: {
        "account_id": ticker_id, "connected": True, "subscribed": [1],
        "last_tick_ms": 101,
    })
    result = session_feed_snapshot(UserContext(user_id="alice"))
    assert result["active_account_feed_bound"] is False
    assert result["feed_connected"] is False
    assert result["subscribed_count"] == result["last_tick_ms"] == 0


@pytest.mark.parametrize("ticker_state", [None, [], "connected", 1])
def test_malformed_existing_ticker_status_fails_closed(monkeypatch, ticker_state):
    monkeypatch.setattr(accounts, "get_active", lambda uid: account())
    monkeypatch.setattr(ticker_manager, "status", lambda uid: ticker_state)
    monkeypatch.setattr(ticker_manager, "ensure", fail_new_state)
    with pytest.raises(ValueError, match="scoped_ticker_status_invalid"):
        session_feed_snapshot(UserContext(user_id="alice"))


def test_malformed_ticker_status_is_not_returned_as_a_healthy_api_response(monkeypatch):
    app = FastAPI()
    app.include_router(endpoint.router, prefix="/api/v1")
    app.dependency_overrides[get_current_user] = lambda: UserContext(user_id="alice")
    monkeypatch.setattr(endpoint, "require_feature", lambda *_args: None)
    monkeypatch.setattr(accounts, "get_active", lambda uid: account())
    monkeypatch.setattr(ticker_manager, "status", lambda uid: None)
    monkeypatch.setattr(ticker_manager, "ensure", fail_new_state)
    with TestClient(app) as client:
        response = client.get("/api/v1/platform/runtime")
    assert response.status_code == 503
    assert response.json() == {"detail": "Scoped platform runtime unavailable"}
