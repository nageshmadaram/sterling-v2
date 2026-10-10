"""Shared WebSocket fan-out for Zerodha/Kite account and tick channels.

Publishing never waits on a browser. ``ticker_manager`` awaits
``broadcast_to_channel`` from inside the Kite socket reader, so a client whose
TCP window is full (a throttled background tab, a half-open connection) used to
stall the reader, and with it every tick-driven exit monitor. Each connection now
owns a writer task and a bounded outbound buffer:

* ``kite_ticks`` frames are conflated per instrument token (latest wins), so a
  slow client's backlog is bounded by the number of tokens it subscribed to.
* Every other message (order updates, status events) is queued in order. If that
  queue overflows or a send exceeds ``SEND_TIMEOUT_S`` the client is closed; it
  reconnects and the UI refetches broker state. Dropping an order event silently
  would leave a stale screen that looks live, so we never drop one.
* An idle connection receives a ``heartbeat`` so the browser can tell a quiet
  market from a dead socket.
"""
import asyncio
import json
import logging
import os
import time
import uuid
from collections import deque
from typing import Deque, Dict, List, Optional

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core import tokens
from app.core.auth import (
    DEFAULT_USER_ID, USER_ID_HEADER, VIEW_QUERY, VIEW_SIMULATION, UserContext, scope_to_simulation,
)
from app.services.simulation import identity as _sim

log = logging.getLogger(__name__)
router = APIRouter()

# Per-connection bounds. Module-level so tests (and operators) can tune them.
OUTBOUND_EVENT_MAX = 512          # queued non-tick messages before the client is dropped
SEND_TIMEOUT_S = 5.0              # one send_json may not take longer than this
HEARTBEAT_INTERVAL_S = 10.0       # idle gap after which a heartbeat is sent
MAX_CHANNELS_PER_CONNECTION = 64  # a browser needs ~3; this stops runaway subscribes
CLOSE_CODE_TRY_AGAIN = 1013       # RFC 6455: "try again later" -- client reconnects

# Changes on every backend process start. Lets a browser notice a restart even
# when the reconnect itself happened too fast to observe.
BOOT_ID = uuid.uuid4().hex[:12]


class _Client:
    """Outbound state for one websocket. Touched only from the event loop."""

    __slots__ = ("ws", "events", "ticks", "wakeup", "task", "closed")

    def __init__(self, ws: WebSocket):
        self.ws = ws
        self.events: Deque[dict] = deque()
        # channel -> instrument_token -> latest tick
        self.ticks: Dict[str, Dict[int, dict]] = {}
        self.wakeup = asyncio.Event()
        self.task: Optional[asyncio.Task] = None
        self.closed = False


class StreamManager:
    def __init__(self):
        # Map channel names (e.g., symbols) to a list of active websocket connections
        self.active_connections: Dict[str, List[WebSocket]] = {}
        # Keep track of which connections are open to avoid exceptions on disconnected sockets
        self.all_connections: List[WebSocket] = []
        self._clients: Dict[int, _Client] = {}
        self._closing: set = set()

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.all_connections.append(websocket)
        client = _Client(websocket)
        self._clients[id(websocket)] = client
        client.task = asyncio.get_running_loop().create_task(self._writer(client))

    def disconnect(self, websocket: WebSocket):
        client = self._clients.pop(id(websocket), None)
        if client is not None:
            client.closed = True
            client.events.clear()
            client.ticks.clear()
            task = client.task
            if task is not None and not task.done() and task is not _current_task():
                task.cancel()
        if websocket in self.all_connections:
            self.all_connections.remove(websocket)
        for channel in list(self.active_connections.keys()):
            if websocket in self.active_connections[channel]:
                self.active_connections[channel].remove(websocket)
                if not self.active_connections[channel]:
                    del self.active_connections[channel]

    def subscribe(self, websocket: WebSocket, channel: str) -> bool:
        """Register ``websocket`` on ``channel``. False when the per-connection cap is hit."""
        if channel not in self.active_connections:
            self.active_connections[channel] = []
        if websocket in self.active_connections[channel]:
            return True
        held = sum(1 for conns in self.active_connections.values() if websocket in conns)
        if held >= MAX_CHANNELS_PER_CONNECTION:
            if not self.active_connections[channel]:
                del self.active_connections[channel]
            return False
        self.active_connections[channel].append(websocket)
        return True

    def unsubscribe(self, websocket: WebSocket, channel: str):
        if channel in self.active_connections and websocket in self.active_connections[channel]:
            self.active_connections[channel].remove(websocket)
            if not self.active_connections[channel]:
                del self.active_connections[channel]

    async def broadcast_to_channel(self, channel: str, message: dict):
        """Queue ``message`` for every subscriber. Never waits on a client socket."""
        self.publish(channel, message)

    def publish(self, channel: str, message: dict) -> int:
        """Synchronous enqueue; returns how many clients accepted the message."""
        conns = self.active_connections.get(channel)
        if not conns:
            return 0
        accepted = 0
        for connection in list(conns):
            client = self._clients.get(id(connection))
            if client is not None and self._enqueue(client, channel, message):
                accepted += 1
        return accepted

    def send_to(self, websocket: WebSocket, message: dict) -> bool:
        """Queue one message for a single connection (ordered with the rest)."""
        client = self._clients.get(id(websocket))
        return bool(client is not None and self._enqueue(client, None, message))

    # ── internals ────────────────────────────────────────────────────────────
    def _enqueue(self, client: _Client, channel: Optional[str], message: dict) -> bool:
        if client.closed:
            return False
        ticks = message.get("ticks") if message.get("type") == "kite_ticks" else None
        if channel is not None and isinstance(ticks, list):
            bucket = client.ticks.setdefault(channel, {})
            for tick in ticks:
                token = tick.get("instrument_token") if isinstance(tick, dict) else None
                if token is not None:
                    bucket[token] = tick  # latest tick per token wins
        else:
            if len(client.events) >= OUTBOUND_EVENT_MAX:
                self._evict(client, "outbound event queue full")
                return False
            client.events.append(message)
        client.wakeup.set()
        return True

    def _evict(self, client: _Client, reason: str) -> None:
        if client.closed:
            return
        log.warning("Dropping websocket client (%s); it will reconnect and resync", reason)
        ws = client.ws
        self.disconnect(ws)
        task = asyncio.get_running_loop().create_task(_close_quietly(ws, CLOSE_CODE_TRY_AGAIN, reason))
        self._closing.add(task)
        task.add_done_callback(self._closing.discard)

    async def _send(self, client: _Client, message: dict) -> None:
        await asyncio.wait_for(client.ws.send_json(message), timeout=SEND_TIMEOUT_S)

    async def _writer(self, client: _Client) -> None:
        try:
            while not client.closed:
                try:
                    await asyncio.wait_for(client.wakeup.wait(), timeout=HEARTBEAT_INTERVAL_S)
                except asyncio.TimeoutError:
                    await self._send(client, {
                        "type": "heartbeat",
                        "ts_ms": int(time.time() * 1000),
                        "boot_id": BOOT_ID,
                    })
                    continue
                client.wakeup.clear()
                while client.events and not client.closed:
                    await self._send(client, client.events.popleft())
                if client.ticks and not client.closed:
                    pending, client.ticks = client.ticks, {}
                    for by_token in pending.values():
                        await self._send(
                            client, {"type": "kite_ticks", "ticks": list(by_token.values())}
                        )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # slow, hung or already-closed peer
            self._evict(client, f"send failed: {type(exc).__name__}")


def _current_task() -> Optional[asyncio.Task]:
    try:
        return asyncio.current_task()
    except RuntimeError:
        return None


async def _close_quietly(websocket: WebSocket, code: int, reason: str) -> None:
    try:
        await asyncio.wait_for(websocket.close(code=code, reason=reason[:100]), timeout=2.0)
    except Exception:  # noqa: BLE001 - peer may already be gone
        pass


stream_manager = StreamManager()

def _production() -> bool:
    # Same answer as the HTTP auth dependency: backend/.env counts, not just the
    # process environment (app.core.runtime_env).
    try:
        from app.core import runtime_env

        return bool(runtime_env.is_production())
    except Exception:  # noqa: BLE001 - fall back to the process environment
        return (os.environ.get("ENVIRONMENT", "development") or "development").lower() == "production"


def _verified_ws_identity(auth_token: str) -> tuple:
    payload = tokens.decode(auth_token, expected_typ="access")
    from app.services.auth import user_store

    user_store.bootstrap()
    user = user_store.get_by_id(str(payload.get("sub") or ""))
    if user is None or not user.is_active:
        raise tokens.TokenError("user is inactive or missing")
    if int(payload.get("ver", -1)) != int(user.token_version):
        raise tokens.TokenError("token has been revoked")
    return user.id, user.role


def _verified_ws_uid(auth_token: str) -> str:
    return _verified_ws_identity(auth_token)[0]


def _wants_simulation(websocket: WebSocket) -> bool:
    """``?view=simulation`` on the socket URL: a WebSocket cannot carry a view header."""
    return (websocket.query_params.get(VIEW_QUERY) or "").strip().lower() == VIEW_SIMULATION


def _scoped_ws_uid(uid: str, role: str, simulation: bool) -> str:
    """The tenant this socket speaks for. Only the owner may take the virtual one."""
    if _sim.is_sim_uid(uid):
        raise PermissionError("reserved identity")
    if not simulation:
        return uid
    try:
        return scope_to_simulation(UserContext(user_id=uid, username=uid, role=role)).user_id
    except Exception as exc:  # noqa: BLE001 - HTTPException 403 from the owner check
        raise PermissionError("simulation view is for the owner only") from exc


_USER_SCOPED_PREFIXES = ("kite_orders:", "orders:", "positions:", "kite_ticks:")


def _drop_user_channels(websocket: WebSocket) -> None:
    for channel in list(stream_manager.active_connections.keys()):
        if channel.startswith(_USER_SCOPED_PREFIXES):
            stream_manager.unsubscribe(websocket, channel)


def _send_initial_kite_status(websocket: WebSocket, channel: str) -> None:
    """Tell a fresh subscriber whether the broker feed is up *right now*.

    Status transitions are published when they happen, so a browser that
    connects after the Kite socket dropped would otherwise never learn about it
    and would keep showing the last prices as if they were live.
    """
    try:
        from app.services.exchanges.kite import ticker_manager

        user_id = channel.split(":", 1)[1] if ":" in channel else ""
        if user_id:
            # The virtual tenant has no broker feed of its own: it reads the owner's.
            stream_manager.send_to(
                websocket, ticker_manager.stream_status_event(_sim.owner_uid(user_id)))
    except Exception as exc:  # noqa: BLE001 - status is advisory, never fatal
        log.debug("initial kite stream status unavailable: %s", exc)


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    # Native browser WebSocket cannot attach an Authorization header. Do not put
    # the access JWT in the URL query string: URLs are routinely captured by
    # reverse-proxy/access logs. Browser clients authenticate in the first frame;
    # non-browser clients may still use an Authorization: Bearer header.
    auth_token = ""
    auth_hdr = websocket.headers.get("authorization") or ""
    if auth_hdr.lower().startswith("bearer "):
        auth_token = auth_hdr[7:].strip()

    simulation = _wants_simulation(websocket)
    conn_uid: Optional[str] = None
    if auth_token:
        try:
            verified_uid, verified_role = _verified_ws_identity(auth_token)
            conn_uid = _scoped_ws_uid(verified_uid, verified_role, simulation)
        except PermissionError as exc:
            log.warning("WebSocket identity refused: %s", exc)
            await websocket.close(code=4403, reason=str(exc))
            return
        except Exception as exc:
            log.warning("WebSocket token verification failed: %s", exc)
            if _production():
                await websocket.close(code=4401, reason="invalid access token")
                return
    elif not _production():
        # Local/test compatibility only. Production never trusts this header, and
        # outside production it is honoured only for loopback peers/origins: a
        # LAN client or a cross-site page must not inherit the operator's order
        # stream (app.core.auth.dev_identity_refusal).
        try:
            from app.core.auth import dev_identity_refusal
        except ImportError:  # older auth module: keep the previous behaviour
            dev_identity_refusal = None

        refusal = (
            dev_identity_refusal(
                peer=websocket.client.host if websocket.client else None,
                host_header=websocket.headers.get("host", ""),
                origin=websocket.headers.get("origin", ""),
                method="WEBSOCKET",
            )
            if dev_identity_refusal is not None
            else None
        )
        if refusal is None:
            dev_uid = websocket.headers.get(USER_ID_HEADER) or DEFAULT_USER_ID
            try:
                conn_uid = _scoped_ws_uid(
                    dev_uid, "admin" if dev_uid == DEFAULT_USER_ID else "user", simulation)
            except PermissionError as exc:
                await websocket.close(code=4403, reason=str(exc))
                return
        elif refusal[0] == 403:
            await websocket.close(code=4403, reason="cross-origin websocket refused")
            return

    await stream_manager.connect(websocket)
    auth_deadline = (
        asyncio.get_running_loop().time() + 10.0
        if _production() and conn_uid is None
        else None
    )

    try:
        while True:
            if auth_deadline is not None and conn_uid is None:
                remaining = auth_deadline - asyncio.get_running_loop().time()
                if remaining <= 0:
                    await websocket.close(code=4401, reason="authentication timeout")
                    return
                try:
                    data = await asyncio.wait_for(websocket.receive_text(), timeout=remaining)
                except asyncio.TimeoutError:
                    await websocket.close(code=4401, reason="authentication timeout")
                    return
                except KeyError:
                    continue  # binary frame: not part of this protocol
            else:
                try:
                    data = await websocket.receive_text()
                except KeyError:
                    continue  # binary frame: not part of this protocol

            try:
                payload = json.loads(data)
                if not isinstance(payload, dict):
                    continue
                action = payload.get("action")

                if action == "auth":
                    msg_token = payload.get("token")
                    if msg_token:
                        try:
                            verified_uid, verified_role = _verified_ws_identity(str(msg_token))
                            verified_uid = _scoped_ws_uid(verified_uid, verified_role, simulation)
                            if conn_uid and verified_uid != conn_uid:
                                # Identity changed on a live socket: whatever the
                                # previous user was subscribed to is no longer theirs.
                                _drop_user_channels(websocket)
                            conn_uid = verified_uid
                            auth_deadline = None
                            await websocket.send_json({"type": "auth_ok", "user_id": conn_uid})
                        except PermissionError as exc:
                            await websocket.send_json({"type": "auth_error", "detail": str(exc)})
                            if _production():
                                await websocket.close(code=4403, reason=str(exc))
                                return
                        except Exception:
                            await websocket.send_json({
                                "type": "auth_error",
                                "detail": "invalid or expired access token",
                            })
                            if _production():
                                await websocket.close(code=4401, reason="invalid access token")
                                return
                    elif _production():
                        await websocket.send_json({
                            "type": "auth_error",
                            "detail": "access token required",
                        })
                    continue

                if _production() and conn_uid is None:
                    await websocket.send_json({
                        "type": "error",
                        "error": "authentication_required",
                        "detail": "Authenticate before subscribing",
                    })
                    continue

                channel = payload.get("channel")
                if isinstance(channel, str):
                    channel = channel.strip()
                    # Sanitize: allow only safe channel identifier tokens.
                    if channel and all(c.isalnum() or c in ":_-" for c in channel):
                        if action == "subscribe":
                            # Enforce user authorization on user-scoped channels.
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
                            if not stream_manager.subscribe(websocket, channel):
                                await websocket.send_json({
                                    "type": "error",
                                    "error": "too_many_channels",
                                    "channel": channel,
                                    "detail": "Per-connection channel limit reached",
                                })
                                continue
                            if channel.startswith("kite_ticks:"):
                                if _sim.is_sim_uid(channel.split(":", 1)[1]):
                                    # Prices are the owner's real market data; the virtual tenant
                                    # has no broker socket, so its view listens to the owner's.
                                    stream_manager.subscribe(
                                        websocket, "kite_ticks:" + _sim.owner_uid(channel.split(":", 1)[1]))
                                _send_initial_kite_status(websocket, channel)
                        elif action == "unsubscribe":
                            stream_manager.unsubscribe(websocket, channel)
            except json.JSONDecodeError:
                pass
    except WebSocketDisconnect:
        pass
    finally:
        stream_manager.disconnect(websocket)
