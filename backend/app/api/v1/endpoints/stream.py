"""Shared WebSocket fan-out for Zerodha/Kite account and tick channels."""
import json
import logging
import os
from typing import Dict, List, Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core import tokens
from app.core.auth import DEFAULT_USER_ID, USER_ID_HEADER

log = logging.getLogger(__name__)
router = APIRouter()

class StreamManager:
    def __init__(self):
        # Map channel names (e.g., symbols) to a list of active websocket connections
        self.active_connections: Dict[str, List[WebSocket]] = {}
        # Keep track of which connections are open to avoid exceptions on disconnected sockets
        self.all_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.all_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.all_connections:
            self.all_connections.remove(websocket)
        for channel in list(self.active_connections.keys()):
            if websocket in self.active_connections[channel]:
                self.active_connections[channel].remove(websocket)
                if not self.active_connections[channel]:
                    del self.active_connections[channel]

    def subscribe(self, websocket: WebSocket, channel: str):
        if channel not in self.active_connections:
            self.active_connections[channel] = []
        if websocket not in self.active_connections[channel]:
            self.active_connections[channel].append(websocket)

    def unsubscribe(self, websocket: WebSocket, channel: str):
        if channel in self.active_connections and websocket in self.active_connections[channel]:
            self.active_connections[channel].remove(websocket)
            if not self.active_connections[channel]:
                del self.active_connections[channel]

    async def broadcast_to_channel(self, channel: str, message: dict):
        if channel in self.active_connections:
            dead_connections = []
            for connection in self.active_connections[channel]:
                try:
                    await connection.send_json(message)
                except Exception as e:
                    log.warning(f"Failed to send to websocket, removing: {e}")
                    dead_connections.append(connection)
            
            for dead in dead_connections:
                self.disconnect(dead)

stream_manager = StreamManager()

def _production() -> bool:
    return (os.environ.get("ENVIRONMENT", "development") or "development").lower() == "production"


def _verified_ws_uid(auth_token: str) -> str:
    payload = tokens.decode(auth_token, expected_typ="access")
    from app.services.auth import user_store

    user_store.bootstrap()
    user = user_store.get_by_id(str(payload.get("sub") or ""))
    if user is None or not user.is_active:
        raise tokens.TokenError("user is inactive or missing")
    if int(payload.get("ver", -1)) != int(user.token_version):
        raise tokens.TokenError("token has been revoked")
    return user.id


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, token: Optional[str] = None):
    auth_token = token or websocket.query_params.get("token")
    if not auth_token:
        auth_hdr = websocket.headers.get("authorization") or ""
        if auth_hdr.lower().startswith("bearer "):
            auth_token = auth_hdr[7:].strip()

    conn_uid: Optional[str] = None
    if auth_token:
        try:
            conn_uid = _verified_ws_uid(str(auth_token))
        except Exception as exc:
            log.warning("WebSocket token verification failed: %s", exc)
            if _production():
                await websocket.close(code=4401, reason="invalid access token")
                return
    elif _production():
        await websocket.close(code=4401, reason="authentication required")
        return
    else:
        # Local/test compatibility only. Production never trusts this header.
        conn_uid = websocket.headers.get(USER_ID_HEADER) or DEFAULT_USER_ID

    await stream_manager.connect(websocket)

    try:
        while True:
            data = await websocket.receive_text()
            try:
                payload = json.loads(data)
                action = payload.get("action")

                if action == "auth":
                    msg_token = payload.get("token")
                    if msg_token:
                        try:
                            conn_uid = _verified_ws_uid(str(msg_token))
                            await websocket.send_json({"type": "auth_ok", "user_id": conn_uid})
                        except Exception:
                            await websocket.send_json({
                                "type": "auth_error",
                                "detail": "invalid or expired access token",
                            })
                    continue

                channel = payload.get("channel")
                if isinstance(channel, str):
                    channel = channel.strip()
                    # Sanitize: allow only safe channel identifier tokens
                    if channel and all(c.isalnum() or c in ":_-" for c in channel):
                        if action == "subscribe":
                            # Enforce user authorization on user-scoped channels
                            if channel.startswith(("kite_orders:", "orders:", "positions:", "kite_ticks:")):
                                parts = channel.split(":", 1)
                                target_uid = parts[1] if len(parts) > 1 else ""
                                if not conn_uid or (target_uid and target_uid != conn_uid):
                                    log.warning(
                                        "Blocked unauthorized subscription: connection user '%s' tried subscribing to '%s'",
                                        conn_uid, channel,
                                    )
                                    await websocket.send_json({
                                        "type": "error",
                                        "error": "unauthorized_channel",
                                        "channel": channel,
                                        "detail": f"Access denied to channel for user '{target_uid}'",
                                    })
                                    continue
                            stream_manager.subscribe(websocket, channel)
                        elif action == "unsubscribe":
                            stream_manager.unsubscribe(websocket, channel)
            except json.JSONDecodeError:
                pass
    except WebSocketDisconnect:
        stream_manager.disconnect(websocket)
