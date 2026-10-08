"""KiteTicker transport behaviour: reconnect pacing, status events, token rotation.

The Kite socket is faked at the ``websockets`` module boundary so these run with
no network and no broker credentials.
"""
from __future__ import annotations

import asyncio
import sys
import time
import types

import pytest

from app.services.exchanges.kite import ticker as ticker_mod
from app.services.exchanges.kite.ticker import KiteTicker


class FakeWs:
    """One scripted websocket connection."""

    def __init__(self, *, fail_send: bool = False):
        self.sent: list[str] = []
        self.fail_send = fail_send
        self._frames: asyncio.Queue = asyncio.Queue()

    def push(self, frame):
        self._frames.put_nowait(frame)

    def end(self):
        self._frames.put_nowait(None)

    async def send(self, frame):
        if self.fail_send:
            raise ConnectionError("socket is closing")
        self.sent.append(frame)

    async def close(self):
        self.end()

    def __aiter__(self):
        return self

    async def __anext__(self):
        item = await self._frames.get()
        if item is None:
            raise StopAsyncIteration
        if isinstance(item, Exception):
            raise item
        return item


class FakeWebsockets:
    """Stands in for the ``websockets`` module; each connect() pops one script."""

    def __init__(self, script):
        self.script = list(script)
        self.urls: list[str] = []

    def connect(self, url, **_kwargs):
        self.urls.append(url)
        step = self.script.pop(0) if self.script else FakeWs()
        outer = self

        class _Ctx:
            async def __aenter__(self_inner):
                if isinstance(step, Exception):
                    raise step
                outer.last = step
                return step

            async def __aexit__(self_inner, *exc):
                return False

        return _Ctx()


@pytest.fixture(autouse=True)
def _fast_reconnect(monkeypatch):
    monkeypatch.setattr(ticker_mod, "RECONNECT_BASE_S", 0.05)
    monkeypatch.setattr(ticker_mod, "RECONNECT_MAX_S", 0.2)
    monkeypatch.setattr(ticker_mod, "RECONNECT_JITTER", 0.0)


def _install(monkeypatch, script) -> FakeWebsockets:
    fake = FakeWebsockets(script)
    monkeypatch.setitem(sys.modules, "websockets", types.SimpleNamespace(connect=fake.connect))
    return fake


async def _until(predicate, timeout: float = 1.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.005)
    return predicate()


async def test_clean_server_close_backs_off_instead_of_hot_looping(monkeypatch):
    # Every connection ends immediately with a clean close. The old loop fell
    # straight back into connect() with no pause (thousands of attempts a second)
    # and left `_connected` True while talking to a closed socket.
    ws_list = [FakeWs() for _ in range(500)]
    for ws in ws_list:
        ws.end()
    fake = _install(monkeypatch, ws_list)

    t = KiteTicker("key", "tok")
    await t.start()
    try:
        await asyncio.sleep(0.3)
    finally:
        await t.stop()

    assert 2 <= len(fake.urls) <= 12, f"expected paced reconnects, got {len(fake.urls)}"
    assert t.last_error == "closed by server"


async def test_clean_close_marks_disconnected_while_waiting_to_retry(monkeypatch):
    monkeypatch.setattr(ticker_mod, "RECONNECT_BASE_S", 5.0)
    first = FakeWs()
    fake = _install(monkeypatch, [first])

    t = KiteTicker("key", "tok")
    await t.start()
    try:
        assert await _until(lambda: t.connected)
        first.end()
        assert await _until(lambda: not t.connected), "a closed socket must not read as connected"
        assert t.status()["connected"] is False
    finally:
        await t.stop()
    assert len(fake.urls) == 1


async def test_status_callback_fires_once_per_transition(monkeypatch):
    first, second = FakeWs(), FakeWs()
    _install(monkeypatch, [first, second])
    events: list[dict] = []

    async def on_status(status):
        events.append(status)

    t = KiteTicker("key", "tok", on_status=on_status)
    await t.start()
    try:
        assert await _until(lambda: [e["connected"] for e in events] == [True])
        first.end()
        assert await _until(lambda: [e["connected"] for e in events] == [True, False])
        assert await _until(lambda: [e["connected"] for e in events] == [True, False, True])
    finally:
        await t.stop()
    assert all("subscribed" in e and "last_tick_ms" in e for e in events)


async def test_failed_attempts_do_not_spam_status_events(monkeypatch):
    _install(monkeypatch, [OSError("refused")] * 6)
    events: list[dict] = []

    async def on_status(status):
        events.append(status)

    t = KiteTicker("key", "tok", on_status=on_status)
    await t.start()
    try:
        await asyncio.sleep(0.25)
    finally:
        await t.stop()
    # Never connected, so there was nothing to announce going down.
    assert [e["connected"] for e in events if e["connected"] is False] == []
    assert t.last_error and t.last_error.startswith("OSError")


async def test_cached_ticks_are_dropped_when_the_socket_drops(monkeypatch):
    first = FakeWs()
    _install(monkeypatch, [first, FakeWs()])
    t = KiteTicker("key", "tok")
    await t.start()
    try:
        assert await _until(lambda: t.connected)
        t._ticks[1] = {"instrument_token": 1, "last_price": 99.0}
        assert t.snapshot()
        first.end()
        assert await _until(lambda: t.snapshot() == []), (
            "a price cached before the drop is not a current price"
        )
    finally:
        await t.stop()


async def test_subscriptions_replay_after_reconnect(monkeypatch):
    first, second = FakeWs(), FakeWs()
    fake = _install(monkeypatch, [first, second])
    t = KiteTicker("key", "tok")
    await t.start()
    try:
        assert await _until(lambda: t.connected)
        await t.subscribe([111, 222], mode="full")
        first.end()
        assert await _until(lambda: len(fake.urls) == 2 and t.connected)
        assert any('"subscribe"' in f and "111" in f for f in second.sent)
        assert any('"mode"' in f and "full" in f for f in second.sent)
    finally:
        await t.stop()


async def test_subscribe_survives_a_send_that_races_a_dying_socket(monkeypatch):
    broken = FakeWs(fail_send=True)
    _install(monkeypatch, [broken])
    t = KiteTicker("key", "tok")
    await t.start()
    try:
        assert await _until(lambda: t.connected)
        # Must not raise: the token is recorded and replayed on reconnect.
        await t.subscribe([333], mode="quote")
        await t.unsubscribe([999])
        assert 333 in t.status()["subscribed"]
    finally:
        await t.stop()


async def test_rotated_credentials_reconnect_immediately_with_the_new_token(monkeypatch):
    # Backoff far longer than the test: only the rotation path can reconnect fast.
    monkeypatch.setattr(ticker_mod, "RECONNECT_BASE_S", 30.0)
    first, second = FakeWs(), FakeWs()
    fake = _install(monkeypatch, [first, second])
    t = KiteTicker("key", "old-token")
    await t.start()
    try:
        assert await _until(lambda: t.connected)
        await t.subscribe([111], mode="quote")

        changed = await t.update_credentials("key", "new-token")

        assert changed is True
        assert await _until(lambda: len(fake.urls) == 2 and t.connected, timeout=1.0)
        assert "new-token" in fake.urls[1] and "old-token" not in fake.urls[1]
        assert any("111" in f for f in second.sent), "subscriptions must survive the swap"
    finally:
        await t.stop()


async def test_rotated_credentials_cut_a_backoff_sleep_short(monkeypatch):
    # Yesterday's token: every handshake is refused and the ticker sits in a long
    # backoff. Re-login must not leave it asleep until the sleep expires.
    monkeypatch.setattr(ticker_mod, "RECONNECT_BASE_S", 30.0)
    monkeypatch.setattr(ticker_mod, "RECONNECT_MAX_S", 30.0)
    good = FakeWs()
    fake = _install(monkeypatch, [PermissionError("403 Forbidden"), good])
    t = KiteTicker("key", "yesterday")
    await t.start()
    try:
        assert await _until(lambda: t.last_error is not None)
        assert not t.connected

        await t.update_credentials("key", "today")

        assert await _until(lambda: t.connected, timeout=1.0), "still asleep after re-login"
        assert "today" in fake.urls[-1]
    finally:
        await t.stop()


async def test_unchanged_credentials_are_a_noop(monkeypatch):
    first = FakeWs()
    fake = _install(monkeypatch, [first])
    t = KiteTicker("key", "tok")
    await t.start()
    try:
        assert await _until(lambda: t.connected)
        assert await t.update_credentials("key", "tok") is False
        await asyncio.sleep(0.05)
        assert len(fake.urls) == 1 and t.connected
    finally:
        await t.stop()
