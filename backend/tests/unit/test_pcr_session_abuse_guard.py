"""/pcr/session scrapes five third-party pages: it must be authenticated, single-flight
and must not re-scrape on every poll when the source is down."""
from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from app.api.v1.endpoints import pcr


@pytest.fixture(autouse=True)
def _reset_cache(monkeypatch):
    monkeypatch.setitem(pcr._CACHE, "at", 0.0)
    monkeypatch.setitem(pcr._CACHE, "payload", None)
    monkeypatch.setattr(pcr, "_scrape_lock", None)


def test_pcr_session_requires_authentication_in_production(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    from main import create_app

    assert TestClient(create_app()).get("/api/v1/pcr/session").status_code == 401


@pytest.mark.asyncio
async def test_concurrent_callers_share_one_scrape_and_a_miss_is_cached(monkeypatch):
    calls = {"n": 0}

    async def fake_scrape(client, sym, url):
        calls["n"] += 1
        await asyncio.sleep(0.01)
        return {}  # nothing live

    monkeypatch.setattr(pcr, "_scrape", fake_scrape)
    results = await asyncio.gather(*[pcr.pcr_session(None) for _ in range(8)])
    assert calls["n"] == len(pcr.PAGES)  # one scrape of every page, not eight
    assert all(r["source"] == "snapshot" for r in results)

    await pcr.pcr_session(None)  # a later poll inside the miss window is a cache hit
    assert calls["n"] == len(pcr.PAGES)
