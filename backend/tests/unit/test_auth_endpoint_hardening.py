"""Login/refresh endpoint abuse controls."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core import rate_limit, tokens
from app.services import db
from app.services.auth import user_store


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = str(tmp_path / "auth-ep.db")
    monkeypatch.setenv("STERLING_DB_PATH", db_path)
    monkeypatch.setattr(db, "_DB_PATH", db_path)
    monkeypatch.setattr(db, "_available", True)
    db.init()
    user_store.clear()
    user_store.bootstrap()
    user_store._users.clear()
    monkeypatch.setattr(
        rate_limit, "_refresh_limiter", rate_limit.SlidingWindowRateLimiter(30, 60)
    )
    monkeypatch.setattr(
        rate_limit, "_login_limiter", rate_limit.SlidingWindowRateLimiter(10, 60)
    )
    from main import create_app

    yield TestClient(create_app())
    user_store.clear()


def test_refresh_is_rate_limited(client):
    for _ in range(30):
        assert client.post("/api/v1/auth/refresh", json={"refresh_token": "x"}).status_code == 401
    assert client.post("/api/v1/auth/refresh", json={"refresh_token": "x"}).status_code == 429


def test_valid_refresh_still_works_under_the_limit(client):
    user = user_store.add("op", "correct horse battery staple", role="admin", user_id="op-id")
    refresh = tokens.mint_refresh(user.id, user.username, user.role, user.token_version)
    response = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
    assert response.status_code == 200
    assert response.json()["token_type"] == "bearer"


def test_failed_login_is_logged_without_the_password(client, caplog):
    import logging

    user_store.add("op", "correct horse battery staple", role="admin", user_id="op-id")
    with caplog.at_level(logging.WARNING):
        response = client.post(
            "/api/v1/auth/login",
            data={"username": "op", "password": "hunter2-very-secret"},
        )
    assert response.status_code == 401
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "login failed" in text
    assert "hunter2-very-secret" not in text


def test_login_is_rate_limited_per_peer(client):
    for _ in range(10):
        assert client.post("/api/v1/auth/login", data={"username": "nobody", "password": "x"}).status_code == 401
    assert client.post("/api/v1/auth/login", data={"username": "nobody", "password": "x"}).status_code == 429


def _req(peer, xff=None):
    from starlette.requests import Request

    headers = [(b"x-forwarded-for", xff.encode())] if xff else []
    return Request({"type": "http", "method": "POST", "path": "/", "headers": headers, "client": (peer, 1)})


def test_limiter_key_uses_the_address_our_own_proxy_appended():
    # Peer is a private (proxy) address: the LAST hop is the one nginx appended; earlier
    # entries are attacker-controlled and ignored.
    assert rate_limit.limiter_client_key(_req("172.18.0.2", "6.6.6.6, 203.0.113.9")) == "203.0.113.9"
    assert rate_limit.limiter_client_key(_req("127.0.0.1", "203.0.113.9")) == "203.0.113.9"


def test_limiter_key_ignores_forwarded_for_from_public_peers_and_garbage():
    assert rate_limit.limiter_client_key(_req("8.8.8.8", "203.0.113.9")) == "8.8.8.8"
    assert rate_limit.limiter_client_key(_req("172.18.0.2", "not-an-ip")) == "172.18.0.2"
    assert rate_limit.limiter_client_key(_req("172.18.0.2")) == "172.18.0.2"


def test_two_clients_behind_one_proxy_do_not_share_a_login_bucket(client):
    headers_a = {"X-Forwarded-For": "203.0.113.1"}
    for _ in range(10):
        client.post("/api/v1/auth/login", data={"username": "n", "password": "x"}, headers=headers_a)
    # client A is now limited; client B (same proxy peer) is not.
    from fastapi.testclient import TestClient
    from main import create_app

    proxied = TestClient(create_app(), client=("172.18.0.2", 4000))
    # fresh peer key space for the proxied client
    for _ in range(10):
        proxied.post("/api/v1/auth/login", data={"username": "n", "password": "x"},
                     headers={"X-Forwarded-For": "203.0.113.1"})
    assert proxied.post("/api/v1/auth/login", data={"username": "n", "password": "x"},
                        headers={"X-Forwarded-For": "203.0.113.1"}).status_code == 429
    assert proxied.post("/api/v1/auth/login", data={"username": "n", "password": "x"},
                        headers={"X-Forwarded-For": "203.0.113.77"}).status_code == 401
