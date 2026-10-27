import pytest
from starlette.testclient import TestClient

from app.core import tokens
from app.services.auth import user_store
from main import app


@pytest.fixture(autouse=True)
def _stream_users(monkeypatch, tmp_path):
    from app.services import db
    from app.services.auth import account_policy
    monkeypatch.setenv('ENVIRONMENT', 'test')
    monkeypatch.setattr(db, '_DB_PATH', str(tmp_path / 'stream-policy.db'))
    assert db.init()
    user_store.clear()
    user_store._init_table()
    user_store._loaded = True
    user_store.add('primary', 'Synthetic-password-123', role='admin', user_id='default')
    for uid in ['alice_uid']:
        user_store.add(uid, 'Synthetic-password-123', user_id=uid)
    for uid in ['default', 'alice_uid']:
        account_policy.set_features('default', uid, dict.fromkeys(account_policy.FEATURES, True), 0)
    yield
    user_store.clear()


def test_ws_default_user_channel_auth():
    client = TestClient(app)
    with client.websocket_connect("/api/v1/stream/ws") as ws:
        # Default user subscribing to kite_orders:default succeeds
        ws.send_json({"action": "subscribe", "channel": "kite_orders:default"})

        # Default user attempting to subscribe to kite_orders:alice_uid is rejected
        ws.send_json({"action": "subscribe", "channel": "kite_orders:alice_uid"})
        resp = ws.receive_json()
        assert resp.get("type") == "error"
        assert resp.get("error") == "unauthorized_channel"


def test_ws_authenticated_jwt_channel_auth():
    client = TestClient(app)
    token = tokens.mint_access("alice_uid", "stream_alice", "trader", 1)

    with client.websocket_connect(
        "/api/v1/stream/ws",
        headers={"Authorization": f"Bearer {token}"},
    ) as ws:
        # Authorized user subscribing to own orders succeeds.
        ws.send_json({"action": "subscribe", "channel": "kite_orders:alice_uid"})

        # Authorized user attempting to subscribe to another user's orders is rejected.
        ws.send_json({"action": "subscribe", "channel": "kite_orders:bob_uid"})
        resp = ws.receive_json()
        assert resp.get("type") == "error"
        assert resp.get("error") == "unauthorized_channel"


def test_ws_runtime_auth_action():
    client = TestClient(app)
    token = tokens.mint_access("alice_uid", "stream_alice", "trader", 1)

    with client.websocket_connect("/api/v1/stream/ws") as ws:
        # Send runtime auth action
        ws.send_json({"action": "auth", "token": token})
        resp = ws.receive_json()
        assert resp.get("type") == "auth_ok"
        assert resp.get("user_id") == "alice_uid"

        # Now subscribing to own channel works without error
        ws.send_json({"action": "subscribe", "channel": "kite_orders:alice_uid"})

        # But subscribing to another user fails
        ws.send_json({"action": "subscribe", "channel": "kite_orders:charlie_uid"})
        err = ws.receive_json()
        assert err.get("type") == "error"
        assert err.get("error") == "unauthorized_channel"


def test_ws_public_channel_allowed():
    client = TestClient(app)
    with client.websocket_connect("/api/v1/stream/ws") as ws:
        # Public non-user channels are allowed for everyone
        ws.send_json({"action": "subscribe", "channel": "market_depth:INFY"})



def test_ws_production_requires_auth_before_subscription(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    client = TestClient(app)
    token = tokens.mint_access("alice_uid", "stream_alice", "trader", 1)

    with client.websocket_connect("/api/v1/stream/ws") as ws:
        ws.send_json({"action": "subscribe", "channel": "kite_orders:alice_uid"})
        blocked = ws.receive_json()
        assert blocked["type"] == "error"
        assert blocked["error"] == "authentication_required"

        ws.send_json({"action": "auth", "token": token})
        authed = ws.receive_json()
        assert authed == {"type": "auth_ok", "user_id": "alice_uid"}

        # After first-frame authentication the verified user's own channel is allowed.
        ws.send_json({"action": "subscribe", "channel": "kite_orders:alice_uid"})
        ws.send_json({"action": "subscribe", "channel": "kite_orders:bob_uid"})
        denied = ws.receive_json()
        assert denied["error"] == "unauthorized_channel"


def test_ws_query_token_is_not_an_authentication_channel(monkeypatch):
    """Bearer credentials must not be accepted from a URL that access logs can retain."""
    monkeypatch.setenv("ENVIRONMENT", "production")
    client = TestClient(app)
    token = tokens.mint_access("alice_uid", "stream_alice", "trader", 1)

    with client.websocket_connect(f"/api/v1/stream/ws?token={token}") as ws:
        ws.send_json({"action": "subscribe", "channel": "kite_orders:alice_uid"})
        blocked = ws.receive_json()
        assert blocked["error"] == "authentication_required"
