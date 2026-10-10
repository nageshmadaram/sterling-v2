"""Backpressure contract of the shared UI fan-out (``StreamManager``).

``ticker_manager`` awaits ``broadcast_to_channel`` from inside the Kite socket
reader. If delivery to one browser can block, the reader stops draining Kite and
every tick-driven exit monitor goes blind -- over a UI tab nobody is watching.
So publishing must never wait on a client socket, memory per client must be
bounded, and a client that cannot keep up must be dropped (it reconnects and
resyncs) rather than silently handed stale data.
"""
from __future__ import annotations

import asyncio
import time

import pytest

from app.api.v1.endpoints import stream as stream_mod
from app.api.v1.endpoints.stream import StreamManager


class FakeSocket:
    """Minimal Starlette WebSocket double."""

    def __init__(self, *, stall: bool = False):
        self.sent: list[dict] = []
        self.accepted = False
        self.closed_with: int | None = None
        self._stall = stall
        self.release = asyncio.Event()

    async def accept(self):
        self.accepted = True

    async def send_json(self, message):
        if self._stall:
            await self.release.wait()
        self.sent.append(message)

    async def close(self, code: int = 1000, reason: str | None = None):
        self.closed_with = code


async def _until(predicate, timeout: float = 1.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.005)
    return predicate()


def _tick(token: int, price: float) -> dict:
    return {"instrument_token": token, "last_price": price}


async def _connected(manager: StreamManager, ws: FakeSocket, channel: str = "kite_ticks:u1"):
    await manager.connect(ws)
    assert manager.subscribe(ws, channel) is True
    return ws


async def test_a_stalled_client_never_blocks_publishing():
    manager = StreamManager()
    slow = await _connected(manager, FakeSocket(stall=True))
    fast = await _connected(manager, FakeSocket())
    try:
        started = time.monotonic()
        for i in range(50):
            await manager.broadcast_to_channel(
                "kite_ticks:u1", {"type": "kite_ticks", "ticks": [_tick(1, 100.0 + i)]}
            )
        elapsed = time.monotonic() - started

        assert elapsed < 0.25, "publishing waited on a client socket"
        assert await _until(lambda: any(
            t["last_price"] == 149.0 for m in fast.sent for t in m.get("ticks", [])
        )), "the healthy client must still receive the latest tick"
    finally:
        slow.release.set()
        manager.disconnect(slow)
        manager.disconnect(fast)


async def test_tick_backlog_is_conflated_per_token_while_a_client_stalls():
    manager = StreamManager()
    slow = await _connected(manager, FakeSocket(stall=True))
    try:
        # First frame goes in flight and stalls inside send_json.
        await manager.broadcast_to_channel(
            "kite_ticks:u1", {"type": "kite_ticks", "ticks": [_tick(1, 1.0)]}
        )
        await asyncio.sleep(0.02)
        for i in range(5_000):
            await manager.broadcast_to_channel(
                "kite_ticks:u1",
                {"type": "kite_ticks", "ticks": [_tick(1, float(i)), _tick(2, float(i))]},
            )
        client = manager._clients[id(slow)]
        buffered = sum(len(by_token) for by_token in client.ticks.values())
        assert buffered == 2, f"tick backlog must be bounded by token count, got {buffered}"
        assert client.ticks["kite_ticks:u1"][1]["last_price"] == 4_999.0, "latest tick must win"
    finally:
        slow.release.set()
        manager.disconnect(slow)


async def test_event_queue_overflow_drops_the_client_instead_of_losing_events(monkeypatch):
    monkeypatch.setattr(stream_mod, "OUTBOUND_EVENT_MAX", 8)
    manager = StreamManager()
    slow = await _connected(manager, FakeSocket(stall=True), "kite_orders:u1")
    other = await _connected(manager, FakeSocket(), "kite_orders:u1")
    try:
        await manager.broadcast_to_channel(
            "kite_orders:u1", {"type": "kite_order_update", "order": {"order_id": "0"}}
        )
        await asyncio.sleep(0.02)  # frame 0 is now stuck in the stalled send
        for i in range(1, 40):
            await manager.broadcast_to_channel(
                "kite_orders:u1", {"type": "kite_order_update", "order": {"order_id": str(i)}}
            )
            await asyncio.sleep(0)  # the Kite reader yields between frames; so do we

        assert await _until(lambda: slow.closed_with is not None), (
            "a client that cannot drain order events must be closed so it reconnects and resyncs"
        )
        assert slow not in manager.all_connections
        assert id(slow) not in manager._clients
        # The healthy client lost nothing, in order.
        assert await _until(lambda: len(other.sent) == 40)
        assert [m["order"]["order_id"] for m in other.sent] == [str(i) for i in range(40)]
    finally:
        slow.release.set()
        manager.disconnect(other)


async def test_a_send_that_hangs_past_the_timeout_evicts_the_client(monkeypatch):
    monkeypatch.setattr(stream_mod, "SEND_TIMEOUT_S", 0.05)
    manager = StreamManager()
    hung = await _connected(manager, FakeSocket(stall=True))
    try:
        await manager.broadcast_to_channel(
            "kite_ticks:u1", {"type": "kite_ticks", "ticks": [_tick(1, 1.0)]}
        )
        assert await _until(lambda: hung.closed_with is not None, timeout=1.0)
        assert hung not in manager.all_connections
        assert manager.active_connections == {}
    finally:
        hung.release.set()


async def test_idle_connection_gets_a_heartbeat(monkeypatch):
    monkeypatch.setattr(stream_mod, "HEARTBEAT_INTERVAL_S", 0.03)
    manager = StreamManager()
    ws = await _connected(manager, FakeSocket())
    try:
        assert await _until(lambda: any(m.get("type") == "heartbeat" for m in ws.sent))
        beat = next(m for m in ws.sent if m.get("type") == "heartbeat")
        assert isinstance(beat["ts_ms"], int)
        assert beat["boot_id"] == stream_mod.BOOT_ID
    finally:
        manager.disconnect(ws)


async def test_disconnect_stops_the_writer_and_forgets_the_client():
    manager = StreamManager()
    ws = await _connected(manager, FakeSocket())
    client = manager._clients[id(ws)]
    task = client.task
    assert task is not None and not task.done()

    manager.disconnect(ws)

    assert await _until(task.done)
    assert id(ws) not in manager._clients
    assert manager.active_connections == {}
    assert manager.all_connections == []
    # Publishing to a channel nobody listens to is a quiet no-op.
    await manager.broadcast_to_channel("kite_ticks:u1", {"type": "kite_ticks", "ticks": []})


async def test_channel_count_per_connection_is_capped(monkeypatch):
    monkeypatch.setattr(stream_mod, "MAX_CHANNELS_PER_CONNECTION", 3)
    manager = StreamManager()
    ws = FakeSocket()
    await manager.connect(ws)
    try:
        assert manager.subscribe(ws, "c1") is True
        assert manager.subscribe(ws, "c2") is True
        assert manager.subscribe(ws, "c3") is True
        assert manager.subscribe(ws, "c3") is True, "re-subscribing an existing channel is free"
        assert manager.subscribe(ws, "c4") is False
        assert "c4" not in manager.active_connections
    finally:
        manager.disconnect(ws)


async def test_non_tick_messages_keep_their_order_and_are_not_conflated():
    manager = StreamManager()
    ws = await _connected(manager, FakeSocket(), "kite_orders:u1")
    try:
        for i in range(5):
            await manager.broadcast_to_channel(
                "kite_orders:u1", {"type": "kite_order_update", "order": {"order_id": str(i)}}
            )
        assert await _until(lambda: len(ws.sent) == 5)
        assert [m["order"]["order_id"] for m in ws.sent] == ["0", "1", "2", "3", "4"]
    finally:
        manager.disconnect(ws)
