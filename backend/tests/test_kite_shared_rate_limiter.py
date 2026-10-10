"""One process-wide limiter in front of every Kite REST call.

Kite allows ~3 historical requests/s and ~10 other requests/s per app. Each scanner
used to bound only its own concurrency, so several scanners running together could
exceed the limit and earn 429s (and the retry storms that follow).
"""
from __future__ import annotations

import asyncio

import pytest

from app.services.exchanges.kite import client as kite_client
from app.services.exchanges.kite.client import KiteClient, ReservationRateLimiter


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_burst_then_steady_rate(monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(kite_client.time, "monotonic", clock)
    limiter = ReservationRateLimiter(rate=3.0, burst=3)
    waits = [limiter.reserve() for _ in range(6)]
    assert waits[:3] == pytest.approx([0.0, 0.0, 0.0], abs=1e-6)  # the burst
    assert waits[3] == pytest.approx(1 / 3, abs=1e-6)
    assert waits[4] == pytest.approx(2 / 3, abs=1e-6)
    assert waits[5] == pytest.approx(3 / 3, abs=1e-6)


def test_credit_does_not_accumulate_beyond_the_burst(monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(kite_client.time, "monotonic", clock)
    limiter = ReservationRateLimiter(rate=2.0, burst=2)
    limiter.reserve()
    clock.now += 3600  # an hour idle must not buy an hour of credit
    waits = [limiter.reserve() for _ in range(4)]
    assert waits[:2] == pytest.approx([0.0, 0.0], abs=1e-6)
    assert waits[2] > 0 and waits[3] > waits[2]


def test_disabled_limiter_never_waits():
    limiter = ReservationRateLimiter(rate=0, burst=1)
    assert [limiter.reserve() for _ in range(100)] == [0.0] * 100


def test_limiter_is_usable_from_several_event_loops():
    limiter = ReservationRateLimiter(rate=1000.0, burst=1)

    async def use():
        for _ in range(3):
            await limiter.acquire()

    asyncio.run(use())
    asyncio.run(use())  # a second loop: no "bound to a different event loop"


class _Recorder:
    def __init__(self, name, log):
        self.name, self.log = name, log

    async def acquire(self):
        self.log.append(self.name)


@pytest.fixture()
def recorded(monkeypatch):
    log: list[str] = []
    monkeypatch.setattr(kite_client, "_GLOBAL_KITE_HISTORICAL_LIMITER", _Recorder("historical", log))
    monkeypatch.setattr(kite_client, "_GLOBAL_KITE_API_LIMITER", _Recorder("general", log))
    return log


def _client(monkeypatch):
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "success", "data": {"candles": []}})

    client = KiteClient(api_key="k", api_secret="s", access_token="t", is_paper=True)

    async def _get_client():
        return httpx.AsyncClient(
            base_url="https://api.kite.trade", transport=httpx.MockTransport(handler)
        )

    monkeypatch.setattr(client, "_get_client", _get_client)
    return client


def test_historical_calls_use_the_historical_bucket(monkeypatch, recorded):
    client = _client(monkeypatch)
    asyncio.run(client.get_historical(256265, "minute", "2026-10-01 09:15:00", "2026-10-01 15:30:00"))
    assert recorded == ["historical"]


def test_other_reads_use_the_general_bucket(monkeypatch, recorded):
    client = _client(monkeypatch)
    asyncio.run(client.get_ltp(["NSE:INFY"]))
    asyncio.run(client.get_margins())
    assert recorded == ["general", "general"]


def test_order_writes_are_not_double_throttled(monkeypatch, recorded):
    """Placement already has its own limiter and is on the time-critical path."""
    client = _client(monkeypatch)
    asyncio.run(client._auth_post("/orders/regular", {"x": 1}))
    asyncio.run(client._auth_delete("/orders/regular/123"))
    assert recorded == []


def test_rate_env_parsing(monkeypatch):
    monkeypatch.delenv("STERLING_KITE_X", raising=False)
    assert kite_client._rate_from_env("STERLING_KITE_X", 2.8) == 2.8
    monkeypatch.setenv("STERLING_KITE_X", "0")
    assert kite_client._rate_from_env("STERLING_KITE_X", 2.8) == 0.0  # disabled
    monkeypatch.setenv("STERLING_KITE_X", "junk")
    assert kite_client._rate_from_env("STERLING_KITE_X", 2.8) == 2.8
    monkeypatch.setenv("STERLING_KITE_X", "-4")
    assert kite_client._rate_from_env("STERLING_KITE_X", 2.8) == 2.8


def test_shipped_defaults_sit_just_under_kites_documented_limits():
    """The suite pins the limiter off, so read the defaults from a clean interpreter."""
    import json
    import os
    import subprocess
    import sys

    env = {k: v for k, v in os.environ.items() if not k.startswith("STERLING_KITE_")}
    out = subprocess.run(
        [sys.executable, "-c",
         "import json;from app.services.exchanges.kite import client as c;"
         "print(json.dumps([c._HISTORICAL_RPS, c._API_RPS]))"],
        capture_output=True, text=True, env=env, timeout=60,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    )
    hist, api = json.loads(out.stdout.strip().splitlines()[-1])
    assert 2.0 <= hist < 3.0  # Kite: ~3 historical requests/s
    assert 5.0 <= api < 10.0  # Kite: ~10 other requests/s
