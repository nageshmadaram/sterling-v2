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
    assert "entry_controls" in data
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


def test_tma_has_browser_login_and_saved_session_recovery(client):
    page = client.get('/tma').text
    assert 'id="sterling-login-form"' in page
    assert 'autocomplete="current-password"' in page
    assert "localStorage.getItem(key)" in page
    assert "'/api/v1/auth/login'" in page
    assert "'/api/v1/auth/refresh'" in page
    assert "useSterlingLogin=true" in page
    assert 'Open full Sterling app' in page


def test_tma_global_entry_switch_is_independent_of_strategy_auto(monkeypatch):
    from app.api.v1.endpoints.tma import _entry_switch_status
    monkeypatch.setenv('STERLING_LIVE_EXECUTION', 'false')
    state = _entry_switch_status()
    assert state['enabled'] is False
    assert state['label'] == 'New entries OFF'


def test_tma_safe_mode_still_blocks_when_live_switch_is_on(monkeypatch, tmp_path):
    from app.api.v1.endpoints.tma import _entry_switch_status
    from app.services.safe_mode import SafeModeService, SafeModeTrigger
    path = tmp_path / 'safe.json'
    monkeypatch.setenv('STERLING_SAFE_MODE_FILE', str(path))
    monkeypatch.setenv('STERLING_LIVE_EXECUTION', 'true')
    SafeModeService(path).engage(trigger=SafeModeTrigger.OPERATOR, reason='readiness')
    state = _entry_switch_status()
    assert state['enabled'] is False
    assert 'readiness' in state['reason']


def test_tma_missing_entry_state_never_looks_enabled(monkeypatch):
    from app.api.v1.endpoints.tma import _entry_switch_status
    from app.services import live_runtime
    def unavailable():
        raise OSError('unreadable')
    monkeypatch.setattr(live_runtime, 'live_execution_state', unavailable)
    assert _entry_switch_status()['label'] == 'Entry status UNKNOWN'
