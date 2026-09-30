import asyncio

import pytest

from app.services import yahoo_candles as y

# 2026-09-30 09:15 IST and 15:30 IST as epoch seconds.
T_0915 = 1790739900
T_1530 = T_0915 + (6 * 60 + 15) * 60


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _patch_http(monkeypatch, payload=None, exc=None):
    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None):
            if exc:
                raise exc
            return _Resp(payload)

    monkeypatch.setattr(y.httpx, "AsyncClient", _Client)


def _payload(stamps, o, h, l, c, v):
    return {"chart": {"result": [{"timestamp": stamps, "indicators": {"quote": [
        {"open": o, "high": h, "low": l, "close": c, "volume": v}]}}]}}


def test_symbol_mapping():
    assert y.yahoo_symbol("NIFTY 50") == "^NSEI"
    assert y.yahoo_symbol("sensex") == "^BSESN"
    assert y.yahoo_symbol("RELIANCE") == "RELIANCE.NS"
    assert y.yahoo_symbol("M&M") == "M&M.NS"
    assert y.yahoo_symbol("NIFTY26OCT22650PE") is None  # option contract
    assert y.yahoo_symbol("") is None


def test_unsupported_resolution_is_empty(monkeypatch):
    _patch_http(monkeypatch, exc=AssertionError("must not call the network"))
    assert asyncio.run(y.fetch_candles("NIFTY 50", "3m", 5)) == []


def test_parse_drops_null_bars_and_closing_auction_print(monkeypatch):
    stamps = [T_0915, T_0915 + 3600, T_0915 + 7200, T_1530]
    _patch_http(monkeypatch, _payload(
        stamps,
        o=[100.0, None, 102.0, 103.0], h=[101.0, None, 103.0, 103.0],
        l=[99.0, None, 101.0, 103.0], c=[100.5, None, 102.5, 103.0],
        v=[10, None, 30, 0]))
    rows = asyncio.run(y.fetch_candles("NIFTY 50", "1H", 5))
    assert [r["time"] for r in rows] == [T_0915, T_0915 + 7200]
    assert rows[1]["close"] == 102.5 and rows[0]["volume"] == 10.0


def test_http_failure_returns_empty(monkeypatch):
    _patch_http(monkeypatch, exc=RuntimeError("boom"))
    assert asyncio.run(y.fetch_candles("NIFTY 50", "1H", 5)) == []


def test_daily_bars_keep_the_1530_stamp(monkeypatch):
    _patch_http(monkeypatch, _payload([T_1530], [1.0], [2.0], [0.5], [1.5], [5]))
    assert len(asyncio.run(y.fetch_candles("NIFTY 50", "D", 5))) == 1


def test_client_serves_yahoo_when_selected_and_never_calls_kite(monkeypatch):
    from app.schemas.instruments import InstrumentMeta
    from app.services import db
    from app.services.exchanges.kite.client import KiteClient

    monkeypatch.setattr(db, "get_config", lambda key, default="": "yahoo")

    async def _fake_fetch(name, resolution, days):
        return [{"time": T_0915 + i * 3600, "open": 1.0, "high": 2.0, "low": 0.5,
                 "close": 1.5, "volume": 1.0} for i in range(6)]

    monkeypatch.setattr(y, "fetch_candles", _fake_fetch)

    client = KiteClient(api_key="ak", api_secret="sec", access_token="t", is_paper=False)

    async def _no_kite(*a, **k):
        raise AssertionError("Kite historical must not be called when source=yahoo")

    monkeypatch.setattr(client, "get_historical", _no_kite)
    inst = InstrumentMeta(underlying="NIFTY 50", tick_size=0.05, strike_step=50.0,
                          exchange_currency="INR", index_name="NIFTY 50",
                          has_options=True, exchange="zerodha", zerodha_token=256265)
    candles = asyncio.run(client.get_candles(inst, "1H", 4))
    assert len(candles) == 4
    assert candles[-1].timestamp_ms == (T_0915 + 5 * 3600) * 1000
