from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.endpoints import health
from app.core.auth import UserContext, require_admin


def test_deep_health_uses_scoped_broker_and_real_telegram_status(monkeypatch):
    from types import SimpleNamespace
    from app.services import db
    from app.services.exchanges.kite import ticker_manager
    from app.services.notifications import telegram_bot
    import shutil
    app = FastAPI()
    app.include_router(health.router)
    app.state.critical_workers = {"test_worker": SimpleNamespace(done=lambda: False)}
    app.dependency_overrides[require_admin] = lambda: UserContext('alice', role='admin')
    monkeypatch.setattr(db, 'is_available', lambda: True)
    monkeypatch.setattr(shutil, 'disk_usage', lambda _: SimpleNamespace(total=100, used=10, free=90))
    seen = []
    def broker(uid):
        seen.append(uid)
        return {'connected': True}
    monkeypatch.setattr(ticker_manager, 'status', broker)
    monkeypatch.setattr(telegram_bot, 'poll_status', lambda: {'state': 'polling'})
    response = TestClient(app).get('/health/deep')
    assert response.status_code == 200
    assert seen == ['alice']
    assert 'path' not in response.json()['checks']['database']
    monkeypatch.setattr(telegram_bot, 'poll_status', lambda: {'state': 'stalled'})
    response = TestClient(app).get('/health/deep')
    assert response.status_code == 503
    assert response.json()['checks']['telegram']['status'] == 'stalled'
