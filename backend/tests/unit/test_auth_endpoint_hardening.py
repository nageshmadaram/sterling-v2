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
