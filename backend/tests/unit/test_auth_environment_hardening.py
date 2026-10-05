"""Authentication posture that must hold even when the process is mis-launched.

Two real failure modes this file pins:

* The dev ``X-User-Id`` / default-admin fallback used to be honoured for ANY
  network peer. A backend started with ``--host 0.0.0.0`` and no production
  environment therefore handed unauthenticated admin (orders, kill switch,
  config writes) to everything on the LAN, to DNS-rebinding pages and to
  cross-site requests from the operator's own browser.
* ``ENVIRONMENT=production`` placed only in ``backend/.env`` (which the launch
  script deliberately does not export) was invisible to the auth/security code,
  which read ``os.environ`` only. The deployment looked production-configured
  and ran with the development identity seam.
"""
from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.core import runtime_env, tokens
from app.core.auth import UserContext, get_current_user
from app.services import db
from app.services.auth import user_store

LAN_PEER = ("192.168.29.77", 40000)


def _app() -> FastAPI:
    app = FastAPI()

    @app.get("/who")
    async def who(current: UserContext = Depends(get_current_user)):
        return {"id": current.user_id, "role": current.role}

    @app.post("/mutate")
    async def mutate(current: UserContext = Depends(get_current_user)):
        return {"ok": True, "id": current.user_id}

    return app


@pytest.fixture()
def dev_env(tmp_path, monkeypatch):
    db_path = str(tmp_path / "dev.db")
    monkeypatch.setenv("STERLING_DB_PATH", db_path)
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.delenv("STERLING_DEV_AUTH_ALLOW_REMOTE", raising=False)
    monkeypatch.setattr(db, "_DB_PATH", db_path)
    monkeypatch.setattr(db, "_available", True)
    db.init()
    user_store.clear()
    yield
    user_store.clear()


# ── runtime_env ──────────────────────────────────────────────────────────────
def test_environment_variable_wins_over_dotenv_setting(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setenv("ENVIRONMENT", "development")
    assert runtime_env.is_production() is False
    monkeypatch.setenv("ENVIRONMENT", "PRODUCTION ")
    assert runtime_env.is_production() is True


def test_dotenv_only_production_is_honoured(monkeypatch):
    """settings.environment is what pydantic read from backend/.env."""
    from app.core.config import settings

    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.setattr(settings, "environment", "production")
    assert runtime_env.is_production() is True
    monkeypatch.setattr(settings, "environment", "development")
    assert runtime_env.is_production() is False


def test_blank_environment_variable_falls_back_to_setting(monkeypatch):
    from app.core.config import settings

    monkeypatch.setenv("ENVIRONMENT", "   ")
    monkeypatch.setattr(settings, "environment", "production")
    assert runtime_env.is_production() is True


def test_dotenv_only_production_rejects_header_identity(tmp_path, monkeypatch):
    from app.core.config import settings

    db_path = str(tmp_path / "prod.db")
    monkeypatch.setenv("STERLING_DB_PATH", db_path)
    monkeypatch.setattr(db, "_DB_PATH", db_path)
    monkeypatch.setattr(db, "_available", True)
    db.init()
    user_store.clear()
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    monkeypatch.setattr(settings, "environment", "production")

    response = TestClient(_app()).get("/who", headers={"X-User-Id": "default"})
    assert response.status_code == 401
    user_store.clear()


# ── development identity seam is loopback-only ───────────────────────────────
def test_dev_header_identity_refused_for_lan_peer(dev_env):
    client = TestClient(_app(), client=LAN_PEER)
    assert client.get("/who").status_code == 401
    assert client.get("/who", headers={"X-User-Id": "default"}).status_code == 401
    assert client.post("/mutate").status_code == 401


@pytest.mark.parametrize("peer", ["127.0.0.1", "::1", "::ffff:127.0.0.1", "testclient"])
def test_dev_header_identity_allowed_for_loopback_peer(dev_env, peer):
    response = TestClient(_app(), client=(peer, 1234)).get("/who")
    assert response.status_code == 200
    assert response.json() == {"id": "default", "role": "admin"}


def test_dev_header_identity_refused_for_rebinding_host(dev_env):
    """A page on evil.example resolving to 127.0.0.1 still sends Host: evil.example."""
    client = TestClient(_app(), client=("127.0.0.1", 1234))
    response = client.get("/who", headers={"Host": "evil.example:8000"})
    assert response.status_code == 401


@pytest.mark.parametrize("host", ["localhost:8000", "127.0.0.1:8000", "[::1]:8000", "localhost"])
def test_dev_header_identity_allows_loopback_host_names(dev_env, host):
    client = TestClient(_app(), client=("127.0.0.1", 1234))
    assert client.get("/who", headers={"Host": host}).status_code == 200


def test_dev_mutation_with_foreign_origin_is_refused(dev_env):
    client = TestClient(_app(), client=("127.0.0.1", 1234))
    refused = client.post("/mutate", headers={"Origin": "https://evil.example"})
    assert refused.status_code == 403
    null_origin = client.post("/mutate", headers={"Origin": "null"})
    assert null_origin.status_code == 403


def test_dev_mutation_with_local_origin_is_allowed(dev_env):
    client = TestClient(_app(), client=("127.0.0.1", 1234))
    for origin in ("http://localhost:5173", "http://127.0.0.1:3000", "http://[::1]:5173"):
        assert client.post("/mutate", headers={"Origin": origin}).status_code == 200


def test_dev_mutation_without_origin_is_allowed(dev_env):
    """curl, the e2e harness and pytest send no Origin."""
    client = TestClient(_app(), client=("127.0.0.1", 1234))
    assert client.post("/mutate", headers={"X-User-Id": "default"}).status_code == 200


def test_dev_read_with_foreign_origin_is_not_blocked_by_identity_layer(dev_env):
    """CORS already stops the page reading the response; reads stay compatible."""
    client = TestClient(_app(), client=("127.0.0.1", 1234))
    assert client.get("/who", headers={"Origin": "https://evil.example"}).status_code == 200


def test_explicit_opt_out_allows_remote_dev_identity(dev_env, monkeypatch):
    monkeypatch.setenv("STERLING_DEV_AUTH_ALLOW_REMOTE", "1")
    client = TestClient(_app(), client=LAN_PEER)
    assert client.get("/who", headers={"Host": "192.168.29.252:8000"}).status_code == 200


def test_valid_bearer_token_works_from_lan_in_development(dev_env):
    user = user_store.add("alice", "correct horse battery staple", role="admin", user_id="alice-id")
    access = tokens.mint_access(user.id, user.username, user.role, user.token_version)
    client = TestClient(_app(), client=LAN_PEER)
    response = client.get("/who", headers={"Authorization": f"Bearer {access}"})
    assert response.status_code == 200
    assert response.json()["id"] == "alice-id"


# ── WebSocket identity seam ──────────────────────────────────────────────────
def test_ws_dev_identity_refused_for_lan_peer(dev_env):
    from main import app

    client = TestClient(app, client=LAN_PEER)
    with client.websocket_connect("/api/v1/stream/ws") as ws:
        ws.send_json({"action": "subscribe", "channel": "kite_orders:default"})
        reply = ws.receive_json()
        assert reply.get("type") == "error"


def test_ws_dev_identity_refused_for_foreign_origin(dev_env):
    from main import app

    from starlette.websockets import WebSocketDisconnect

    client = TestClient(app, client=("127.0.0.1", 1234))
    with pytest.raises(WebSocketDisconnect) as exc_info:
        with client.websocket_connect(
            "/api/v1/stream/ws", headers={"Origin": "https://evil.example"}
        ):
            pass
    assert exc_info.value.code == 4403


def test_ws_dev_identity_allowed_for_local_origin(dev_env):
    from main import app

    client = TestClient(app, client=("127.0.0.1", 1234))
    with client.websocket_connect(
        "/api/v1/stream/ws", headers={"Origin": "http://localhost:5173"}
    ) as ws:
        ws.send_json({"action": "subscribe", "channel": "kite_orders:default"})
        ws.send_json({"action": "subscribe", "channel": "kite_orders:someone-else"})
        reply = ws.receive_json()
        assert reply.get("error") == "unauthorized_channel"


# ── a LIVE broker account ends the anonymous development identity ────────────
@pytest.fixture()
def live_account(monkeypatch):
    """A stored LIVE Kite account, with the suite-wide anonymous-live opt-out removed."""
    from app.services.exchanges.kite import accounts as kite_accounts

    monkeypatch.delenv("STERLING_DEV_AUTH_ALLOW_ANONYMOUS_LIVE", raising=False)

    class _Acct:
        is_paper = False

    monkeypatch.setattr(kite_accounts, "all_accounts", lambda: [_Acct()])
    return _Acct


def test_anonymous_dev_identity_refused_while_a_live_account_exists(dev_env, live_account):
    client = TestClient(_app(), client=("127.0.0.1", 1234))
    for response in (
        client.get("/who"),
        client.get("/who", headers={"X-User-Id": "default"}),
        client.post("/mutate"),
    ):
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"
        assert "live broker account" in response.json()["detail"]


def test_anonymous_dev_identity_stays_available_with_only_paper_accounts(dev_env, monkeypatch):
    from app.services.exchanges.kite import accounts as kite_accounts

    monkeypatch.delenv("STERLING_DEV_AUTH_ALLOW_ANONYMOUS_LIVE", raising=False)

    class _Paper:
        is_paper = True

    monkeypatch.setattr(kite_accounts, "all_accounts", lambda: [_Paper()])
    assert TestClient(_app(), client=("127.0.0.1", 1234)).get("/who").status_code == 200


def test_bearer_token_still_works_with_a_live_account(dev_env, live_account):
    user = user_store.add("alice", "correct horse battery staple", role="admin", user_id="alice-id")
    access = tokens.mint_access(user.id, user.username, user.role, user.token_version)
    client = TestClient(_app(), client=("127.0.0.1", 1234))
    response = client.post("/mutate", headers={"Authorization": f"Bearer {access}"})
    assert response.status_code == 200
    assert response.json()["id"] == "alice-id"


def test_anonymous_live_opt_out_is_explicit(dev_env, live_account, monkeypatch):
    monkeypatch.setenv("STERLING_DEV_AUTH_ALLOW_ANONYMOUS_LIVE", "1")
    assert TestClient(_app(), client=("127.0.0.1", 1234)).get("/who").status_code == 200


def test_live_account_check_failure_fails_closed(dev_env, monkeypatch):
    from app.services.exchanges.kite import accounts as kite_accounts

    monkeypatch.delenv("STERLING_DEV_AUTH_ALLOW_ANONYMOUS_LIVE", raising=False)

    def _boom():
        raise RuntimeError("store unreadable")

    monkeypatch.setattr(kite_accounts, "all_accounts", _boom)
    assert TestClient(_app(), client=("127.0.0.1", 1234)).get("/who").status_code == 401


def test_ws_anonymous_identity_refused_while_a_live_account_exists(dev_env, live_account):
    from main import app

    client = TestClient(app, client=("127.0.0.1", 1234))
    with client.websocket_connect("/api/v1/stream/ws") as ws:
        ws.send_json({"action": "subscribe", "channel": "kite_orders:default"})
        assert ws.receive_json().get("error") == "unauthorized_channel"
