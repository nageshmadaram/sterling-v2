"""Stage B: the wall is the chain max, and a wide quote never becomes a candidate."""
from __future__ import annotations

from datetime import date, datetime, timezone, timedelta

import pytest

from app.engines.gamma_move import GammaMoveConfig, SpotLevel
from app.services import gamma_move_scanner as scanner

TODAY = date(2026, 9, 20)
IST = timezone(timedelta(hours=5, minutes=30))
LEVEL = SpotLevel(price=1300.0, kind="resistance", touches=3)


def _nfo(symbol: str, strike: float, token: int) -> dict:
    return {
        "instrument_token": token,
        "tradingsymbol": symbol,
        "name": "RELIANCE",
        "expiry": "2026-09-29",
        "strike": strike,
        "instrument_type": "CE",
        "segment": "NFO-OPT",
        "lot_size": 500,
        "tick_size": 0.05,
    }


def _opt_quote(oi: int, premium: float = 53.0, bid: float = 52.9, ask: float = 53.1) -> dict:
    return {
        "oi": oi,
        "last_price": premium,
        "volume": 5_000,
        "depth": {"buy": [{"price": bid}], "sell": [{"price": ask}]},
    }


def _quotes(nfo: dict) -> dict:
    out = {"NSE:RELIANCE": {"last_price": 1298.0}}
    out.update(nfo)
    return out


@pytest.fixture
def cfg() -> GammaMoveConfig:
    return GammaMoveConfig(enabled=True, max_premium_at_risk_inr=60_000)


async def _scan(monkeypatch, cfg: GammaMoveConfig, nfo_quotes: dict) -> list:
    rows = [
        _nfo("RELIANCE26SEP1300CE", 1300.0, 11),
        _nfo("RELIANCE26SEP1500CE", 1500.0, 22),
    ]
    quotes = _quotes(nfo_quotes)

    async def dump(_uid):
        return rows

    async def batched(_client, _pacer, keys):
        return {k: quotes.get(k) or quotes.get(k.split(":", 1)[-1]) or {} for k in keys}

    monkeypatch.setattr(scanner, "nfo_dump", dump)
    monkeypatch.setattr(scanner, "_quote_batched", batched)
    return await scanner.scan_strikes(
        "u1", cfg, client=object(),
        levels={"RELIANCE": [LEVEL]},
        spots={"RELIANCE": 1298.0},
        regimes={"RELIANCE": "up"},
        today=TODAY,
    )


@pytest.mark.asyncio
async def test_runner_up_oi_is_refused_when_the_chain_max_sits_elsewhere(monkeypatch, cfg):
    """The far 1500 is the wall. 1300 is near the level but is a runner-up.

    If the scanner only quoted the top-of-window sample, 1300 would look like
    the wall and leak through. chain_oi_max has to come from the full chain.
    """
    found = await _scan(monkeypatch, cfg, {
        "NFO:RELIANCE26SEP1300CE": _opt_quote(200_000),
        "NFO:RELIANCE26SEP1500CE": _opt_quote(5_000_000, premium=12.0),
    })
    assert found == []


@pytest.mark.asyncio
async def test_the_near_chain_max_is_kept(monkeypatch, cfg):
    found = await _scan(monkeypatch, cfg, {
        "NFO:RELIANCE26SEP1300CE": _opt_quote(5_000_000),
        "NFO:RELIANCE26SEP1500CE": _opt_quote(200_000, premium=12.0),
    })
    assert len(found) == 1
    assert found[0].instrument.tradingsymbol == "RELIANCE26SEP1300CE"
    assert found[0].chain_oi_max == 5_000_000


@pytest.mark.asyncio
async def test_spread_of_3_1_percent_is_refused(monkeypatch, cfg):
    # (103.15 - 100) / 101.575 * 100 = 3.101%
    found = await _scan(monkeypatch, cfg, {
        "NFO:RELIANCE26SEP1300CE": _opt_quote(5_000_000, bid=100.0, ask=103.15),
        "NFO:RELIANCE26SEP1500CE": _opt_quote(200_000, premium=12.0),
    })
    assert found == []


@pytest.mark.asyncio
async def test_spread_inside_3_percent_is_kept(monkeypatch, cfg):
    found = await _scan(monkeypatch, cfg, {
        "NFO:RELIANCE26SEP1300CE": _opt_quote(5_000_000, bid=100.0, ask=102.9),
        "NFO:RELIANCE26SEP1500CE": _opt_quote(200_000, premium=12.0),
    })
    assert len(found) == 1
    assert found[0].instrument.tradingsymbol == "RELIANCE26SEP1300CE"


def _daily(y, m, d, close, *, hh=0, mm=0):
    from app.engines.gamma_move import Candle
    ts = int(datetime(y, m, d, hh, mm, tzinfo=IST).timestamp() * 1000)
    return Candle(ts_ms=ts, open=close, high=close, low=close, close=close)


def test_closed_daily_drops_today_only_before_the_session_ends():
    bars = [_daily(2026, 9, 18, 1290.0), _daily(2026, 9, 19, 1295.0),
            _daily(2026, 9, 20, 1310.0)]
    morning = datetime(2026, 9, 20, 10, 0, tzinfo=IST)
    eod = datetime(2026, 9, 20, 15, 30, tzinfo=IST)
    am = scanner._closed_daily(bars, TODAY, now=morning)
    assert len(am) == 2
    assert am[-1].close == 1295.0
    pm = scanner._closed_daily(bars, TODAY, now=eod)
    assert len(pm) == 3
    assert pm[-1].close == 1310.0


# ── a scan that cannot look must say so, not read as a quiet market ────────────

class _Client:
    def __init__(self, token: str = "", fail: Exception | None = None):
        self._access_token = token
        self._fail = fail
        self.history_calls = 0

    async def search_instruments(self, q, exchange, limit=0):
        return [{"tradingsymbol": "RELIANCE", "segment": "NSE", "instrument_token": 1}]

    async def get_historical(self, *a, **k):
        self.history_calls += 1
        raise self._fail or RuntimeError("unexpected")


def _patch_scan_inputs(monkeypatch, client):
    from types import SimpleNamespace
    from app.services.exchanges.kite import accounts

    async def acquire(_acct):
        return client

    async def dump(_uid):
        return []

    monkeypatch.setattr(accounts, "get_active", lambda uid: SimpleNamespace(id="a", connected=bool(client._access_token)))
    monkeypatch.setattr(accounts, "acquire_client", acquire)
    monkeypatch.setattr(scanner, "nfo_dump", dump)
    scanner._scan_stats.clear(); scanner._scan_issue.clear(); scanner._last_no_session_warn.clear()


@pytest.mark.asyncio
async def test_no_session_skips_the_scan_and_says_why(monkeypatch, cfg, caplog):
    from app.engines.gamma_move import GammaMoveStrategy
    client = _Client(token="")
    _patch_scan_inputs(monkeypatch, client)

    with caplog.at_level("WARNING"):
        out = await scanner.scan_once("u1", cfg, GammaMoveStrategy(cfg), today=TODAY)

    assert out == []
    assert client.history_calls == 0, "no point asking the broker without a session"
    assert "no Kite session" in scanner.scan_issue("u1")
    assert "no Kite session" in scanner.scan_stats("u1")["issue"]
    assert any("scan skipped" in r.message for r in caplog.records if r.levelname == "WARNING")


@pytest.mark.asyncio
async def test_the_no_session_warning_is_not_repeated_every_scan(monkeypatch, cfg, caplog):
    from app.engines.gamma_move import GammaMoveStrategy
    client = _Client(token="")
    _patch_scan_inputs(monkeypatch, client)

    with caplog.at_level("WARNING"):
        for _ in range(3):
            await scanner.scan_once("u1", cfg, GammaMoveStrategy(cfg), today=TODAY)

    assert sum("scan skipped" in r.message for r in caplog.records) == 1
    assert scanner.scan_issue("u1"), "the issue itself stays visible on every scan"


@pytest.mark.asyncio
async def test_a_history_outage_is_reported_and_never_cached_as_no_levels(monkeypatch, cfg, caplog):
    client = _Client(token="T", fail=RuntimeError("Kite is down"))
    _patch_scan_inputs(monkeypatch, client)
    monkeypatch.setattr(scanner, "stock_underlyings", lambda rows, c: ["RELIANCE"])
    scanner._levels_cache.clear()

    with caplog.at_level("WARNING"):
        levels, _, _ = await scanner.scan_levels("u1", cfg, client, today=TODAY)

    assert levels == {}
    assert "Kite is down" in scanner.scan_issue("u1")
    assert any("daily history unavailable" in r.message for r in caplog.records)
    assert not scanner._levels_cache, "an outage must not be remembered as 'no levels today'"


@pytest.mark.asyncio
async def test_snapshot_blocker_when_the_account_is_logged_out(monkeypatch):
    from types import SimpleNamespace
    from app.services import gamma_move
    from app.services.exchanges.kite import accounts

    async def dump(_uid):
        return []

    monkeypatch.setattr(gamma_move, "nfo_dump", dump)
    monkeypatch.setattr(accounts, "get_active", lambda uid: SimpleNamespace(id="a", connected=False))
    scanner._scan_issue.clear()

    snap = await gamma_move.snapshot("u1")

    assert any("no Kite session" in b for b in snap["blockers"])
