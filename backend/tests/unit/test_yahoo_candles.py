import asyncio

import pytest

_REAL_SLEEP = asyncio.sleep

pytestmark = pytest.mark.yahoo_unit

from app.services import yahoo_candles as y

# 2026-09-30 09:15 IST and 15:30 IST as epoch seconds.
T_0915 = 1790739900
T_1530 = T_0915 + (6 * 60 + 15) * 60


class _Resp:
    def __init__(self, payload, status=200, headers=None):
        self._payload = payload
        self.status_code = status
        self.headers = headers or {}
        self.request = None

    def raise_for_status(self):
        if self.status_code >= 400:
            raise y.httpx.HTTPStatusError(
                f"HTTP {self.status_code}", request=None, response=self)

    def json(self):
        return self._payload


@pytest.fixture(autouse=True)
def _fast_throttle(monkeypatch):
    monkeypatch.setattr(y, "_MIN_SPACING_S", 0.0)
    monkeypatch.setattr(y, "_BACKOFF_BASE_S", 0.0)
    y._unknown_until.clear()

    async def _instant(_s):
        return None

    monkeypatch.setattr(y.asyncio, "sleep", _instant)
    yield
    y._unknown_until.clear()


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


def test_client_reads_owner_scoped_market_data_source(monkeypatch):
    from app.schemas.instruments import InstrumentMeta
    from app.services import db
    from app.services.exchanges.kite.client import KiteClient

    values = {
        "market_data_source:tenant-a": "yahoo",
        "market_data_source:tenant-b": "kite",
    }
    monkeypatch.setattr(db, "get_config", lambda key, default="": values.get(key, default))

    async def _fake_fetch(name, resolution, days):
        return [{"time": T_0915, "open": 1.0, "high": 2.0, "low": 0.5,
                 "close": 1.5, "volume": 1.0}]

    monkeypatch.setattr(y, "fetch_candles", _fake_fetch)

    inst = InstrumentMeta(
        underlying="NIFTY 50",
        tick_size=0.05,
        strike_step=50.0,
        exchange_currency="INR",
        index_name="NIFTY 50",
        has_options=True,
        exchange="zerodha",
        zerodha_token=256265,
    )

    a = KiteClient(api_key="ak", api_secret="sec", access_token="t", is_paper=False)
    a._owner_user_id = "tenant-a"
    async def _a_no_kite(*_args, **_kwargs):
        raise AssertionError("tenant-a selected Yahoo; Kite must not be called")
    monkeypatch.setattr(a, "get_historical", _a_no_kite)
    assert len(asyncio.run(a.get_candles(inst, "1H", 1))) == 1

    b = KiteClient(api_key="ak", api_secret="sec", access_token="t", is_paper=False)
    b._owner_user_id = "tenant-b"
    calls = {"kite": 0}
    async def _b_kite(*_args, **_kwargs):
        calls["kite"] += 1
        return {"candles": [["2026-09-30T09:15:00+05:30", 1, 2, 0.5, 1.5, 1]]}
    monkeypatch.setattr(b, "get_historical", _b_kite)
    asyncio.run(b.get_candles(inst, "1H", 1))
    assert calls["kite"] == 1


def test_client_serves_truedata_without_requiring_zerodha_token(monkeypatch):
    from types import SimpleNamespace

    from app.schemas.instruments import InstrumentMeta
    from app.services import db
    from app.services.exchanges.kite.client import KiteClient
    from app.services.providers import truedata as td_service

    monkeypatch.setattr(
        db,
        "get_config",
        lambda key, default="": "truedata" if key == "market_data_source:tenant-td" else default,
    )
    monkeypatch.setattr(
        td_service,
        "get_active",
        lambda uid: SimpleNamespace(has_credentials=True) if uid == "tenant-td" else None,
    )

    class TDClient:
        async def get_bars(self, symbol, start, end, *, interval):
            assert symbol == "NIFTY 50"
            assert interval == "60min"
            return [{
                "timestamp": "2026-09-30 09:15:00",
                "open": "24000",
                "high": "24100",
                "low": "23950",
                "close": "24080",
                "volume": "100",
            }]

        async def aclose(self):
            return None

    monkeypatch.setattr(td_service, "build_client", lambda acct: TDClient())
    monkeypatch.setattr(
        "app.services.ohlcv_store.upsert_candles",
        lambda *_args, **_kwargs: 1,
    )

    client = KiteClient(api_key="", api_secret="", access_token="", is_paper=True)
    client._owner_user_id = "tenant-td"

    async def _no_kite(*_args, **_kwargs):
        raise AssertionError("Kite historical must not be called for TrueData source")

    monkeypatch.setattr(client, "get_historical", _no_kite)

    inst = InstrumentMeta(
        underlying="NIFTY 50",
        tick_size=0.05,
        strike_step=50.0,
        exchange_currency="INR",
        index_name="NIFTY 50",
        has_options=True,
        exchange="zerodha",
        zerodha_token=None,
    )

    candles = asyncio.run(client.get_candles(inst, "1H", 1))
    assert len(candles) == 1
    assert candles[0].close == 24080.0


def _scripted(monkeypatch, steps):
    """Each call pops the next step: an exception to raise or a ``_Resp``."""
    calls = {"n": 0}

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None):
            step = steps[min(calls["n"], len(steps) - 1)]
            calls["n"] += 1
            if isinstance(step, Exception):
                raise step
            return step

    monkeypatch.setattr(y.httpx, "AsyncClient", _Client)
    return calls


_OK = _payload([T_0915], [1.0], [2.0], [0.5], [1.5], [5])


def test_retries_429_then_succeeds(monkeypatch):
    calls = _scripted(monkeypatch, [_Resp(None, 429), _Resp(None, 503), _Resp(_OK)])
    assert len(asyncio.run(y.fetch_candles("RELIANCE", "1H", 5))) == 1
    assert calls["n"] == 3


def test_gives_up_after_max_attempts(monkeypatch):
    calls = _scripted(monkeypatch, [_Resp(None, 429)])
    assert asyncio.run(y.fetch_candles("RELIANCE", "1H", 5)) == []
    assert calls["n"] == y._MAX_ATTEMPTS


def test_retries_network_errors(monkeypatch):
    calls = _scripted(monkeypatch, [y.httpx.ConnectError("down"), _Resp(_OK)])
    assert len(asyncio.run(y.fetch_candles("RELIANCE", "1H", 5))) == 1
    assert calls["n"] == 2


def test_non_transient_4xx_is_not_retried(monkeypatch):
    calls = _scripted(monkeypatch, [_Resp(None, 403)])
    assert asyncio.run(y.fetch_candles("RELIANCE", "1H", 5)) == []
    assert calls["n"] == 1


def test_404_is_not_retried_and_is_remembered(monkeypatch):
    calls = _scripted(monkeypatch, [_Resp(None, 404)])
    assert asyncio.run(y.fetch_candles("BANKEX", "1H", 5)) == []
    assert asyncio.run(y.fetch_candles("BANKEX", "1H", 5)) == []
    assert calls["n"] == 1  # second scan did not ask Yahoo again


def test_retry_after_header_is_honoured(monkeypatch):
    assert y._retry_delay(0, "3") == 3.0
    assert y._retry_delay(0, "999") == y._BACKOFF_MAX_S
    assert y._retry_delay(2, None) == min(y._BACKOFF_BASE_S * 4, y._BACKOFF_MAX_S)


def test_concurrency_is_capped(monkeypatch):
    state = {"now": 0, "peak": 0}

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None):
            state["now"] += 1
            state["peak"] = max(state["peak"], state["now"])
            await _REAL_SLEEP(0.02)  # hold the slot so in-flight requests overlap
            state["now"] -= 1
            return _Resp(_OK)

    monkeypatch.setattr(y.httpx, "AsyncClient", _Client)

    async def _run():
        return await asyncio.gather(*[
            y.fetch_candles(f"SYM{chr(65 + i)}", "1H", 5) for i in range(12)])

    out = asyncio.run(_run())
    assert all(len(r) == 1 for r in out)
    assert state["peak"] == 4  # cap reached, never exceeded
