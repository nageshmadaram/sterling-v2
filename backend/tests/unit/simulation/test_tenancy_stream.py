"""The WebSocket speaks for the virtual tenant only when asked (?view=simulation) and only for the owner."""
from __future__ import annotations

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from tenancy_support import OWNER, SIM_UID

import main
from app.api.v1.endpoints import stream as stream_mod
from app.core import tokens
from app.services.auth import user_store

URL = "/api/v1/stream/ws"
SIM_URL = URL + "?view=simulation"


@pytest.fixture(autouse=True)
def _users(monkeypatch, tmp_path):
    from app.services import db
    from app.services.auth import account_policy
    monkeypatch.setenv('ENVIRONMENT', 'test')
    monkeypatch.setattr(db, '_DB_PATH', str(tmp_path / 'sim-stream-policy.db'))
    assert db.init()
    user_store.clear()
    user_store._init_table()
    user_store._loaded = True
    user_store.add('owner', 'Synthetic-password-123', role='admin', user_id='default')
    user_store.add('trader', 'Synthetic-password-123', user_id='trader_uid')
    for uid in ('default', 'trader_uid'):
        account_policy.set_features('default', uid, dict.fromkeys(account_policy.FEATURES, True), 0)
    yield
    user_store.clear()


@pytest.fixture
def client():
    return TestClient(main.app)


def _round_trip(ws):
    """Force a processed frame: an unauthorised subscribe always answers with an error."""
    ws.send_json({"action": "subscribe", "channel": "kite_orders:nobody"})
    return ws.receive_json()


def test_the_simulation_socket_may_subscribe_to_the_sim_tenants_order_channel(client):
    with client.websocket_connect(SIM_URL) as ws:
        ws.send_json({"action": "subscribe", "channel": f"kite_orders:{SIM_UID}"})
        assert _round_trip(ws)["error"] == "unauthorized_channel"      # the probe, not the sim channel
        assert f"kite_orders:{SIM_UID}" in stream_mod.stream_manager.active_connections


def test_the_simulation_socket_cannot_read_the_live_tenants_channels(client):
    with client.websocket_connect(SIM_URL) as ws:
        ws.send_json({"action": "subscribe", "channel": f"kite_orders:{OWNER}"})
        frame = ws.receive_json()
        assert frame["error"] == "unauthorized_channel" and frame["channel"] == f"kite_orders:{OWNER}"


def test_the_live_socket_cannot_read_the_sim_tenants_channels(client):
    with client.websocket_connect(URL) as ws:
        ws.send_json({"action": "subscribe", "channel": f"kite_orders:{SIM_UID}"})
        frame = ws.receive_json()
        assert frame["error"] == "unauthorized_channel" and frame["channel"] == f"kite_orders:{SIM_UID}"


def test_the_sim_tick_channel_rides_the_owners_real_feed(client):
    with client.websocket_connect(SIM_URL) as ws:
        ws.send_json({"action": "subscribe", "channel": f"kite_ticks:{SIM_UID}"})
        frame = ws.receive_json()
        assert frame["type"] == "kite_stream_status"                    # the OWNER's feed health
        active = stream_mod.stream_manager.active_connections
        assert f"kite_ticks:{SIM_UID}" in active and f"kite_ticks:{OWNER}" in active


def test_a_non_owner_cannot_open_a_simulation_socket(client):
    with pytest.raises(WebSocketDisconnect) as err:
        with client.websocket_connect(SIM_URL, headers={"X-User-Id": "someone-else"}) as ws:
            ws.receive_json()
    assert err.value.code == 4403


def test_a_header_cannot_claim_to_be_the_sim_tenant(client):
    with pytest.raises(WebSocketDisconnect) as err:
        with client.websocket_connect(URL, headers={"X-User-Id": SIM_UID}) as ws:
            ws.receive_json()
    assert err.value.code == 4403


def test_first_frame_auth_with_the_owner_token_scopes_to_the_sim_tenant(client):
    owner = tokens.mint_access("default", "owner", "admin", 1)
    with client.websocket_connect(SIM_URL) as ws:
        ws.send_json({"action": "auth", "token": owner})
        assert ws.receive_json() == {"type": "auth_ok", "user_id": SIM_UID}


def test_first_frame_auth_without_the_view_stays_live(client):
    owner = tokens.mint_access("default", "owner", "admin", 1)
    with client.websocket_connect(URL) as ws:
        ws.send_json({"action": "auth", "token": owner})
        assert ws.receive_json()["user_id"] == OWNER


def test_first_frame_auth_of_a_non_owner_is_refused_the_simulation_view(client):
    trader = tokens.mint_access("trader_uid", "t", "trader", 1)
    with client.websocket_connect(SIM_URL) as ws:
        ws.send_json({"action": "auth", "token": trader})
        frame = ws.receive_json()
        assert frame["type"] == "auth_error" and "owner" in frame["detail"]


def test_a_bearer_header_token_of_a_non_owner_is_refused_the_simulation_view(client):
    trader = tokens.mint_access("trader_uid", "t", "trader", 1)
    with pytest.raises(WebSocketDisconnect) as err:
        with client.websocket_connect(SIM_URL, headers={"Authorization": f"Bearer {trader}"}) as ws:
            ws.receive_json()
    assert err.value.code == 4403
