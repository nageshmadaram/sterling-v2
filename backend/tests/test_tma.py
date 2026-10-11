"""Unit tests for Telegram Mini App (TMA) endpoints."""
import pytest
from fastapi.testclient import TestClient

from main import create_app


@pytest.fixture
def client():
    app = create_app()
    from app.services.notifications.telegram_webapp_auth import tma_user
    from app.core.auth import UserContext
    app.dependency_overrides[tma_user] = lambda: UserContext(user_id='default', username='test', role='admin')
    return TestClient(app)


def test_tma_html_endpoint(client):
    """Verify /tma HTML page renders successfully."""
    res = client.get("/tma")
    assert res.status_code == 200
    assert "text/html" in res.headers["content-type"]
    assert "Sterling" in res.text
    assert "Telegram Mini App" in res.text
    assert "telegram-web-app.js" in res.text


def test_tma_data_endpoint(client):
    """Verify /tma/data returns consolidated system state JSON."""
    res = client.get("/tma/data")
    assert res.status_code == 200
    data = res.json()
    assert "account" in data
    assert "algo_enabled" in data
    assert "signals" in data
    assert "positions" in data
    assert "orders" in data
    assert "pnl" in data
    assert "funds" in data
    assert "pcr" in data
    assert "strategies" in data


def test_tma_search_endpoint(client):
    """Verify /tma/search handles symbol queries."""
    res = client.get("/tma/search?q=NIFTY")
    assert res.status_code == 200
    data = res.json()
    assert data["query"] == "NIFTY"
    assert "html" in data
    assert len(data["html"]) > 0


def test_tma_action_toggle_algo(client):
    """Verify /tma/action toggles ALGO mode."""
    res = client.post("/tma/action", json={"action": "toggle_algo", "target": "TOGGLE"})
    assert res.status_code == 200
    data = res.json()
    assert "success" in data
    assert "algo_enabled" in data
