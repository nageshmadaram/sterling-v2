"""WebSocket sync contract the browser relies on (initial feed status, caps, identity)."""
from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from app.api.v1.endpoints import stream as stream_mod
from app.core import tokens
from app.services.auth import user_store
from main import app


@pytest.fixture(autouse=True)
def _users(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "test")
    user_store.clear()
    user_store._loaded = True
    for uid in ("alice_uid", "bob_uid"):
        user_store._users[uid] = user_store._User(
            id=uid, username=uid, role="trader", token_version=1,
        )
    yield
    user_store.clear()


def test_subscribing_to_the_tick_channel_reports_current_broker_feed_state():
    # Without this a browser that connects AFTER the Kite socket dropped never
    # learns about it and keeps showing the last prices as live.
    client = TestClient(app)
    with client.websocket_connect("/api/v1/stream/ws") as ws:
        ws.send_json({"action": "subscribe", "channel": "kite_ticks:default"})
        frame = ws.receive_json()
        assert frame["type"] == "kite_stream_status"
        assert frame["connected"] is False and frame["started"] is False


def test_order_channel_subscribe_has_no_unsolicited_ack():
    # Existing clients read the next frame expecting an error; do not add acks.
    client = TestClient(app)
    with client.websocket_connect("/api/v1/stream/ws") as ws:
        ws.send_json({"action": "subscribe", "channel": "kite_orders:default"})
        ws.send_json({"action": "subscribe", "channel": "kite_orders:alice_uid"})
        assert ws.receive_json()["error"] == "unauthorized_channel"


def test_channel_cap_is_reported_to_the_client(monkeypatch):
    monkeypatch.setattr(stream_mod, "MAX_CHANNELS_PER_CONNECTION", 2)
    client = TestClient(app)
    with client.websocket_connect("/api/v1/stream/ws") as ws:
        ws.send_json({"action": "subscribe", "channel": "market_depth:A"})
        ws.send_json({"action": "subscribe", "channel": "market_depth:B"})
        ws.send_json({"action": "subscribe", "channel": "market_depth:C"})
        frame = ws.receive_json()
        assert frame["error"] == "too_many_channels"
        assert frame["channel"] == "market_depth:C"


def test_reauthenticating_as_another_user_drops_the_previous_users_channels():
    client = TestClient(app)
    alice = tokens.mint_access("alice_uid", "alice_uid", "trader", 1)
    bob = tokens.mint_access("bob_uid", "bob_uid", "trader", 1)
    with client.websocket_connect("/api/v1/stream/ws") as ws:
        ws.send_json({"action": "auth", "token": alice})
        assert ws.receive_json()["user_id"] == "alice_uid"
        ws.send_json({"action": "subscribe", "channel": "kite_orders:alice_uid"})
        ws.send_json({"action": "subscribe", "channel": "market_depth:INFY"})
        # Round-trip so both subscribes are processed before inspecting state.
        ws.send_json({"action": "subscribe", "channel": "kite_orders:bob_uid"})
        assert ws.receive_json()["error"] == "unauthorized_channel"
        assert "kite_orders:alice_uid" in stream_mod.stream_manager.active_connections

        ws.send_json({"action": "auth", "token": bob})
        assert ws.receive_json()["user_id"] == "bob_uid"
        ws.send_json({"action": "subscribe", "channel": "kite_orders:alice_uid"})
        assert ws.receive_json()["error"] == "unauthorized_channel"

        assert "kite_orders:alice_uid" not in stream_mod.stream_manager.active_connections
        assert "market_depth:INFY" in stream_mod.stream_manager.active_connections, (
            "public channels are not identity-scoped and stay"
        )


def test_non_json_and_non_object_frames_do_not_kill_the_socket():
    client = TestClient(app)
    with client.websocket_connect("/api/v1/stream/ws") as ws:
        ws.send_text("not json")
        ws.send_text("[1, 2, 3]")
        ws.send_bytes(b"\x00\x01")
        ws.send_json({"action": "subscribe", "channel": "kite_orders:alice_uid"})
        assert ws.receive_json()["error"] == "unauthorized_channel"


@pytest.mark.asyncio
async def test_revoked_token_never_sends_another_private_frame():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    token = tokens.mint_access('alice_uid','alice_uid','trader',1)
    ws = SimpleNamespace(state=SimpleNamespace(sterling_access_token=token),send_json=AsyncMock())
    user_store._users['alice_uid'].token_version = 2
    with pytest.raises(tokens.TokenError):
        await stream_mod.stream_manager._send(SimpleNamespace(ws=ws), {'type':'private_order'})
    ws.send_json.assert_not_awaited()
