import logging

from app.core import logging as core_logging
from app.core.observability import correlation_scope


def test_ring_preserves_redacted_exception_and_correlation():
    handler = core_logging.RingBufferLogHandler()
    handler.addFilter(core_logging.RedactSecretsFilter())
    try:
        raise RuntimeError('password="p a s s" api_key=x')
    except RuntimeError:
        import sys
        record = logging.LogRecord('fixture', logging.ERROR, __file__, 1, 'request_token=private-token', (), sys.exc_info())
    with correlation_scope('fixture-correlation'):
        handler.handle(record)
    entry = core_logging.recent_logs(1)[0]
    assert entry['correlation_id'] == 'fixture-correlation'
    assert entry['id']
    assert 'RuntimeError' in entry['message']
    assert 'p a s s' not in entry['message']
    assert 'private-token' not in entry['message']
    assert 'api_key=x' not in entry['message']


def test_global_log_route_requires_administrator():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api.v1.endpoints.kite_engine import router
    from app.core.auth import get_current_user, UserContext
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    user = UserContext("ordinary-user", role="user")
    app.dependency_overrides[get_current_user] = lambda: user
    with TestClient(app) as client:
        path = '/api/v1' + next(route.path for route in router.routes if route.path.endswith('/server-logs'))
        assert client.get(path).status_code == 403
        user = UserContext("operator", role="admin")
        response = client.get(path)
        assert response.status_code == 200
        assert isinstance(response.json()["logs"], list)


def test_http_capture_retains_response_correlation_without_query_credentials():
    from fastapi import FastAPI, Response
    from fastapi.testclient import TestClient
    from app.core.http_hardening import HttpHardeningMiddleware
    app = FastAPI()
    app.add_middleware(HttpHardeningMiddleware)
    @app.get('/api/v1/fixture')
    async def endpoint(response: Response):
        response.headers['X-Correlation-ID'] = 'response-cid'
        return {"ok": True}
    with TestClient(app) as client:
        assert client.get('/api/v1/fixture?access_token=do-not-log').status_code == 200
    entry = next(entry for entry in reversed(core_logging.recent_logs()) if entry.get('path') == '/api/v1/fixture')
    assert entry['correlation_id'] == 'response-cid'
    assert entry['status'] == 200 and entry['method'] == 'GET'
    assert entry['duration_ms'] >= 0
    assert 'do-not-log' not in entry['message']


def test_log_limits_remain_bounded_for_hostile_query_values():
    assert len(core_logging.recent_logs(999999)) <= 1000
    assert len(core_logging.recent_logs(-1)) <= 1


def test_authorization_redacts_short_bearer_before_field_match():
    for text in ('Authorization: Bearer x', 'authorization="Bearer abc"', 'Bearer short'):
        redacted = core_logging.redact_secrets(text)
        assert ' x' not in redacted and 'abc' not in redacted and 'short' not in redacted
        assert '[redacted]' in redacted
