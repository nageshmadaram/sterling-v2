"""Snapback signal -> row -> snapshot audit: signs, provenance, staleness, closed bars.

Each test pins a way a board row could say something the engine did not.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import numpy as np
import pytest

from app.engines.snapback import SnapbackConfig, intraday
from app.engines.snapback.models import Bars, IST, SnapbackSignal
from app.services import snapback

SCALP = replace(SnapbackConfig(), trading_mode="scalp", enabled=True)
START = datetime(2026, 9, 14, 9, 15, tzinfo=IST).timestamp()


def _tape(prices, opens):
    c = np.asarray(prices, dtype=float)
    o = np.asarray(opens, dtype=float)
    return Bars(START + np.arange(len(c)) * 60, o, np.maximum(o, c) + .1,
                np.minimum(o, c) - .1, c, np.full(len(c), 1000.))


def _push_then_fade():
    c = list(100 + np.sin(np.arange(60) * 2) * .5) + [104, 100.4]
    o = c.copy()
    o[-1] = c[-2]
    return _tape(c, o)


def _flush_then_fade():
    b = _push_then_fade()
    return Bars(b.time, 200 - b.open, 200 - b.low, 200 - b.high, 200 - b.close, b.volume)


# ----------------------------------------------------------------- stretch sign

def test_intraday_stretch_is_signed_like_the_daily_lane():
    """A flush below the mean must read negative, exactly as it does on the daily lane."""
    up = intraday.evaluate_intraday(_push_then_fade(), SCALP, "NIFTY",
                                    asof=_push_then_fade().time[-1] + 60)[0]
    flush = _flush_then_fade()
    down = intraday.evaluate_intraday(flush, replace(SCALP, allow_fade_down=True), "NIFTY",
                                      asof=flush.time[-1] + 60)[0]
    assert up.side == "fade_up" and up.stretch > 0
    assert down.side == "fade_down" and down.option_type == "CE"
    assert down.stretch < 0
    assert abs(down.stretch) == pytest.approx(abs(up.stretch), rel=1e-6)


# ------------------------------------------------------------- scalp row levels

def _scalp_signal():
    return SnapbackSignal(
        symbol="NIFTY", side="fade_up", direction="BEARISH", option_type="PE",
        timestamp_ms=1_788_928_200_000, entry=25000, mean_target=24990, stretch=2,
        atr=10, realized_vol=0.2, assumed_iv=0.2, level=25010, strength="MODERATE",
        reasons=("confirmed reversal",),
        metrics={"signal_close_timestamp_ms": 1_788_928_260_000})


def test_scalp_row_does_not_put_the_initial_stop_in_the_trail_column():
    row = snapback._row(_scalp_signal(), SCALP, contract={"lot_size": 50, "symbol": "OPT"},
                        quote={"premium": 100, "bid": 99, "ask": 100}, blocked=None,
                        underlying_token=1)
    assert row["stop_premium"] is not None and row["stop_premium"] < 100
    # No ratchet is running until the lock level is reached; the stop is not a trail.
    assert row["trail_premium"] is None
    assert row["profit_lock_premium"] > row["breakeven_premium"] > 100
    assert row["trail_points"] == SCALP.scalp_trail_points


# ------------------------------------------------------------- daily candle cache

def _daily_rows(day: datetime, close: float):
    return [{"time": int(day.replace(hour=0, minute=0).timestamp()), "open": 100.0,
             "high": 110.0, "low": 95.0, "close": close, "volume": 1000.0}]


@pytest.mark.asyncio
async def test_cached_partial_daily_bar_is_not_reused_after_the_close(monkeypatch):
    """A bar cached at 15:20 is still forming; at 15:31 it must be re-fetched."""
    day = datetime(2026, 9, 16, tzinfo=IST)  # Wednesday, a session
    clock = {"now": day.replace(hour=15, minute=20)}
    monkeypatch.setattr(snapback, "_wall_now", lambda: clock["now"])
    partial, final = _daily_rows(day, 101.0), _daily_rows(day, 107.0)
    client = SimpleNamespace(get_candles=AsyncMock(side_effect=[partial, final]))
    st = snapback.ScanState()

    assert await snapback._candles(client, st, 7, "NIFTY") == partial
    clock["now"] = day.replace(hour=15, minute=21)
    assert await snapback._candles(client, st, 7, "NIFTY") == partial      # genuine hit
    assert client.get_candles.await_count == 1
    clock["now"] = day.replace(hour=15, minute=31)
    assert await snapback._candles(client, st, 7, "NIFTY") == final        # close crossed
    assert client.get_candles.await_count == 2


def test_drop_forming_uses_the_same_clock(monkeypatch):
    day = datetime(2026, 9, 16, tzinfo=IST)
    rows = _daily_rows(day, 101.0)
    monkeypatch.setattr(snapback, "_wall_now", lambda: day.replace(hour=15, minute=20))
    assert snapback._drop_forming(rows) == []
    monkeypatch.setattr(snapback, "_wall_now", lambda: day.replace(hour=15, minute=31))
    assert len(snapback._drop_forming(rows)) == 1


# ------------------------------------------------------------------- staleness

@pytest.mark.asyncio
async def test_quote_carries_the_time_it_describes():
    stamp = datetime.now(IST) - timedelta(seconds=3)
    client = SimpleNamespace(get_quote=AsyncMock(return_value={"NFO:OPT": {
        "depth": {"buy": [{"price": 99}], "sell": [{"price": 100}]},
        "last_price": 99.5, "oi": 100_000, "timestamp": stamp}}))
    quote = await snapback._quote_for(client, {"exchange": "NFO", "symbol": "OPT"}, SCALP)
    assert quote["quote_ts_ms"] == pytest.approx(stamp.timestamp() * 1000, abs=1)
    assert quote["observed_ms"] >= quote["quote_ts_ms"]


def _cached_row(**quote):
    row = snapback._row(_scalp_signal(), SCALP, contract={"lot_size": 50, "symbol": "OPT"},
                        quote={"premium": 100, "bid": 99, "ask": 100, "ltp": 99.5, **quote},
                        blocked=None, underlying_token=1)
    return row


def test_snapshot_ages_the_cached_quote_instead_of_presenting_it_as_current(monkeypatch):
    uid = "row-audit-age"
    snapback._state.pop(uid, None)
    now_ms = int(datetime.now(IST).timestamp() * 1000)
    st = snapback.status(uid)
    st.rows = [_cached_row(quote_ts_ms=now_ms - 600_000, observed_ms=now_ms - 590_000)]
    monkeypatch.setattr(snapback, "get_config", lambda uid=None: SCALP)
    row = snapback.snapshot(uid)["rows"][0]
    assert 590 <= row["quote_age_s"] <= 620
    assert row["execution_eligible"] is False


def test_snapshot_demotes_an_armed_swing_row_whose_quote_aged_out(monkeypatch):
    uid = "row-audit-demote"
    snapback._state.pop(uid, None)
    swing = replace(SnapbackConfig(), enabled=True)
    now_ms = int(datetime.now(IST).timestamp() * 1000)
    st = snapback.status(uid)
    row = _cached_row(quote_ts_ms=now_ms - 4_000_000, observed_ms=now_ms - 3_990_000)
    row.update(state="armed", reason=None, trading_mode="swing")
    st.rows = [row]
    monkeypatch.setattr(snapback, "get_config", lambda uid=None: swing)
    snap = snapback.snapshot(uid)
    assert snap["rows"][0]["state"] == "watching"
    assert snap["rows"][0]["stale"] is True
    assert "quote" in snap["rows"][0]["reason"].lower()
    assert snap["armed"] == 0


# ------------------------------------------------------- hedged swing row quote

def _swing_signal():
    return SnapbackSignal(
        symbol="RELIANCE", side="fade_up", direction="BEARISH", option_type="PE",
        timestamp_ms=1_788_928_200_000, entry=1500.0, mean_target=1450.0, stretch=2.2,
        atr=20.0, realized_vol=0.25, assumed_iv=0.30, level=1490.0, strength="MODERATE",
        reasons=("closed through its high",), metrics={"lookback_days": 20})


@pytest.mark.asyncio
async def test_hedged_swing_row_shows_the_real_ask_while_declining_to_arm(monkeypatch):
    """The default config hedges, which blocks arming. It must not also hide the quote."""
    from app.services.exchanges.kite import accounts

    cfg = SnapbackConfig(enabled=True, market_filter="off")
    assert cfg.hedge_mode != "none"
    item = SimpleNamespace(name="RELIANCE", token=1, tradingsymbol="RELIANCE",
                           option_exchange="NFO")
    contract = {"symbol": "RELIANCE26NOV1600PE", "strike": 1600.0, "option_type": "PE",
                "expiry": "2026-11-26", "dte": 45, "lot_size": 500, "token": 5,
                "exchange": "NFO", "delta": -0.7, "moneyness": "ITM"}
    quote = {"premium": 112.0, "bid": 111.5, "ask": 112.0, "ltp": 111.8, "oi": 90_000,
             "spread_pct": 0.4, "blockers": []}
    monkeypatch.setattr(snapback, "get_config", lambda uid=None: cfg)
    monkeypatch.setattr(accounts, "get_active", lambda uid: object())
    monkeypatch.setattr(accounts, "acquire_client", AsyncMock(
        return_value=SimpleNamespace(search_instruments=AsyncMock(return_value=[]))))
    monkeypatch.setattr(snapback, "resolve_universe", lambda *a, **k: [item])
    monkeypatch.setattr(snapback, "_candles", AsyncMock(return_value=[{"x": 1}]))
    monkeypatch.setattr(snapback, "_drop_forming", lambda c: c)
    monkeypatch.setattr(snapback, "evaluate_symbol", lambda *a, **k: [_swing_signal()])
    monkeypatch.setattr(snapback, "_contract_for", AsyncMock(return_value=contract))
    fetch = AsyncMock(return_value=quote)
    monkeypatch.setattr(snapback, "_quote_for", fetch)
    uid = "hedged-quote-test"
    snapback._state.pop(uid, None)

    body = await snapback.scan_once(uid)

    row = body["rows"][0]
    assert fetch.await_count == 1
    assert row["state"] == "watching" and row["execution_eligible"] is False
    assert "hedge" in row["reason"].lower()
    assert row["premium"] == 112.0 and row["premium_is_modelled"] is False
    assert row["stop_premium"] == pytest.approx(112.0 * (1 - cfg.premium_stop_pct / 100), abs=0.01)
