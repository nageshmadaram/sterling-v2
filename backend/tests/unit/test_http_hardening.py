"""Request-size limit, no-store API caching and the operator audit trail."""
from __future__ import annotations

import json

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.responses import PlainTextResponse
from fastapi.testclient import TestClient

from app.core.auth import UserContext, get_current_user
from app.core.http_hardening import install_http_hardening


@pytest.fixture()
def audit_path(tmp_path, monkeypatch):
    path = tmp_path / "audit" / "operator_audit.jsonl"
    monkeypatch.setenv("STERLING_AUDIT_LOG", str(path))
    monkeypatch.setenv("STERLING_MAX_BODY_BYTES", "2048")
    monkeypatch.delenv("ENVIRONMENT", raising=False)
    return path


def _app() -> FastAPI:
    app = FastAPI()

    @app.post("/api/v1/thing")
    async def thing(request: Request, user: UserContext = Depends(get_current_user)):
        body = await request.body()
        return {"n": len(body), "user": user.user_id}

    @app.get("/api/v1/thing")
    async def read(user: UserContext = Depends(get_current_user)):
        return {"ok": True}

    @app.get("/api/v1/kite/callback")
    async def callback():
        return PlainTextResponse("ok", headers={"Cache-Control": "max-age=5"})

    @app.get("/outside")
    async def outside():
        return {"ok": True}

    install_http_hardening(app)
    return app


def _lines(path):
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def test_oversized_declared_body_is_rejected_before_the_handler(audit_path):
    client = TestClient(_app())
    assert client.post("/api/v1/thing", content=b"x" * 2048).status_code == 200
    response = client.post("/api/v1/thing", content=b"x" * 2049)
    assert response.status_code == 413


def test_oversized_chunked_body_is_rejected(audit_path):
    def chunks():
        for _ in range(10):
            yield b"y" * 512

    response = TestClient(_app()).post("/api/v1/thing", content=chunks())
    assert response.status_code == 413


def test_api_responses_are_not_cacheable_and_explicit_headers_survive(audit_path):
    client = TestClient(_app())
    assert client.get("/api/v1/thing").headers["cache-control"] == "no-store"
    assert client.get("/api/v1/kite/callback").headers["cache-control"] == "max-age=5"
    assert "cache-control" not in client.get("/outside").headers


def test_mutations_are_audited_with_identity_and_without_secrets(audit_path):
    client = TestClient(_app())
    response = client.post(
        "/api/v1/thing?request_token=SECRETTOKEN&api_secret=SECRETVALUE",
        content=b"password=hunter2",
        headers={"X-User-Id": "default"},
    )
    assert response.status_code == 200
    rows = _lines(audit_path)
    assert len(rows) == 1
    row = rows[0]
    assert row["method"] == "POST"
    assert row["path"] == "/api/v1/thing"
    assert row["status"] == 200
    assert row["user"] == "default"
    assert row["peer"] == "testclient"
    assert "duration_ms" in row and "ts" in row
    raw = audit_path.read_text()
    for secret in ("SECRETTOKEN", "SECRETVALUE", "hunter2"):
        assert secret not in raw


def test_reads_are_not_audited_but_the_login_callback_is(audit_path):
    client = TestClient(_app())
    client.get("/api/v1/thing")
    client.get("/outside")
    assert _lines(audit_path) == []
    client.get("/api/v1/kite/callback?request_token=SECRETTOKEN&state=abc")
    rows = _lines(audit_path)
    assert [r["path"] for r in rows] == ["/api/v1/kite/callback"]
    assert "SECRETTOKEN" not in audit_path.read_text()


def test_rejected_requests_are_audited_too(audit_path):
    client = TestClient(_app())
    client.post("/api/v1/thing", content=b"x" * 5000)
    assert [r["status"] for r in _lines(audit_path)] == [413]


def test_unwritable_audit_log_never_breaks_a_request(tmp_path, monkeypatch):
    blocker = tmp_path / "file"
    blocker.write_text("not a directory")
    monkeypatch.setenv("STERLING_AUDIT_LOG", str(blocker / "sub" / "audit.jsonl"))
    monkeypatch.setenv("STERLING_MAX_BODY_BYTES", "2048")
    assert TestClient(_app()).post("/api/v1/thing", content=b"ok").status_code == 200


def test_audit_log_rotates_when_large(audit_path, monkeypatch):
    monkeypatch.setenv("STERLING_AUDIT_MAX_BYTES", "300")
    client = TestClient(_app())
    for _ in range(12):
        client.post("/api/v1/thing", content=b"ok")
    assert audit_path.with_name(audit_path.name + ".1").exists()
    assert audit_path.stat().st_size <= 600


def test_installed_on_the_real_app_with_identity_stamped(audit_path, monkeypatch):
    """main.create_app wires the middleware; the resolved user reaches the audit row."""
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("STERLING_MAX_BODY_BYTES", str(8 * 1024 * 1024))
    from main import create_app

    client = TestClient(create_app())
    response = client.post("/api/v1/auth/logout", headers={"X-User-Id": "default"})
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-frame-options"] == "DENY"
    rows = _lines(audit_path)
    assert rows and rows[-1]["path"] == "/api/v1/auth/logout"
    assert rows[-1]["user"] == "default"
    assert rows[-1]["cid"]  # correlation id from the app's own middleware
