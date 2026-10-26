from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.endpoints import health


def test_shallow_health_never_reads_or_invents_account_inventory(monkeypatch):
    from app.services.exchanges.kite import accounts

    # Missing, stale or unavailable broker state must not become a flat account.
    account_read = Mock(side_effect=AssertionError('liveness queried account state'))
    monkeypatch.setattr(accounts, 'get_active', account_read)
    app = FastAPI()
    app.include_router(health.router)
    response = TestClient(app, raise_server_exceptions=False).get('/health')
    assert response.status_code == 200
    body = response.json()
    account_read.assert_not_called()
    assert body['scope'] == 'liveness'
    assert body['exchange_reachable'] is None
    assert body['positions'] == {
        'status': 'not_checked', 'open': None, 'partially_closed': None, 'closed': None,
    }
    assert body['alerts'] == {
        'status': 'not_checked', 'active': None, 'triggered': None,
    }


@pytest.mark.parametrize('enabled', [False, True])
def test_shallow_health_preserves_authoritative_execution_control(monkeypatch, enabled):
    from app.core import lane_registry

    monkeypatch.setattr(lane_registry, 'LIVE_EXECUTION_ENABLED', enabled)
    app = FastAPI()
    app.include_router(health.router)
    response = TestClient(app).get('/health')
    assert response.status_code == 200
    assert response.json()['live_execution'] is enabled
