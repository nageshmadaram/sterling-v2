from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from app.engines.gamma_move import GammaMoveConfig, InstrumentRef, WatchContract
from app.services import gamma_move_historical as replay

IST = timezone(timedelta(hours=5, minutes=30))
DAY = date(2026, 10, 5)


def _row(ts: datetime, *, close: float = 100.0, volume: float = 100.0,
         oi: float = 1000.0, high: float = 101.0, low: float = 99.0):
    return [
        ts.isoformat(), close, high, low, close, volume, oi,
    ]


def _watch() -> WatchContract:
    return WatchContract(
        underlying="RELIANCE",
        underlying_token=111,
        list_name="BREAKOUT-CE",
        instrument=InstrumentRef(
            instrument_id="222",
            tradingsymbol="RELIANCE26OCT3000CE",
            option_type="CE",
            strike=3000,
            expiry="2026-10-29",
            lot_size=10,
            tick_size=0.05,
            exchange="NFO",
        ),
        tag="HOI-1",
        oi=10_000,
        candidates_n=1,
        ref_price=102.0,
        zone_edge=103.0,
        atr_pct=3.0,
        build_oi=10_000,
        chain_ltp=100.0,
        dte=5,
    )


def _option_five():
    prev = datetime(2026, 10, 1, 13, 30, tzinfo=IST)
    rows = [
        _row(prev + timedelta(minutes=5 * i), volume=100, oi=1000)
        for i in range(18)
    ]
    rows += [
        _row(datetime(2026, 10, 5, 9, 15, tzinfo=IST),
             close=100.0, volume=200, oi=1000),
        _row(datetime(2026, 10, 5, 9, 20, tzinfo=IST),
             close=100.5, volume=200, oi=900),
    ]
    return rows


def _option_one():
    rows = []
    for minute in range(15, 25):
        rows.append(_row(
            datetime(2026, 10, 5, 9, minute, tzinfo=IST),
            close=100.5,
            volume=40,
            oi=1000 if minute < 20 else 900,
        ))
    # First minute of the *next* 5m slot. This is the first causal E1 chance.
    rows.append(_row(
        datetime(2026, 10, 5, 9, 25, tzinfo=IST),
        close=102.0,
        high=102.0,
        low=100.5,
        volume=200,
        oi=900,
    ))
    return rows


def _underlying_five():
    return [
        _row(datetime(2026, 10, 5, 9, 15, tzinfo=IST),
             close=101.0, high=101.2, low=100.8, volume=10_000, oi=0),
        _row(datetime(2026, 10, 5, 9, 20, tzinfo=IST),
             close=102.0, high=102.2, low=101.8, volume=10_000, oi=0),
    ]


def _underlying_one():
    return [
        _row(datetime(2026, 10, 5, 9, minute, tzinfo=IST),
             close=102.0, high=102.1, low=101.9, volume=2_000, oi=0)
        for minute in range(15, 26)
    ]


@pytest.mark.asyncio
async def test_replay_uses_minute_high_when_intraminute_e1_reverts_before_close(monkeypatch):
    cfg = GammaMoveConfig().validate()

    one = _option_one()
    # Reference E1 is tick-driven: this minute traded through the base at 102,
    # but closed back below it. Historical close-only replay used to miss it.
    one[-1] = _row(
        datetime(2026, 10, 5, 9, 25, tzinfo=IST),
        close=100.5,
        high=102.0,
        low=100.0,
        volume=200,
        oi=900,
    )

    async def historical(_client, _pacer, token, interval, _frm, _to, _oi):
        if token == 222 and interval == "5minute":
            return _option_five()
        if token == 222 and interval == "minute":
            return one
        if token == 111 and interval == "5minute":
            return _underlying_five()
        if token == 111 and interval == "minute":
            return _underlying_one()
        return []

    monkeypatch.setattr(replay, "_historical", historical)
    start = replay._clock_ms(DAY, "09:20")
    end = replay._clock_ms(DAY, "09:26")

    signals, _engine = await replay._replay_watch(
        object(), cfg, _watch(), DAY, start, end
    )

    assert len(signals) == 1
    assert signals[0].tier == "ENTRY"
    assert signals[0].ltp == 102.0
    assert signals[0].at_ms == end


@pytest.mark.asyncio
async def test_replay_does_not_fire_e1_from_same_bar_that_arms_e2(monkeypatch):
    cfg = GammaMoveConfig().validate()

    async def historical(_client, _pacer, token, interval, _frm, _to, _oi):
        if token == 222 and interval == "5minute":
            return _option_five()
        if token == 222 and interval == "minute":
            return _option_one()
        if token == 111 and interval == "5minute":
            return _underlying_five()
        if token == 111 and interval == "minute":
            return _underlying_one()
        return []

    monkeypatch.setattr(replay, "_historical", historical)
    start = replay._clock_ms(DAY, "09:20")

    same_bar_end = replay._clock_ms(DAY, "09:25")
    signals, engine = await replay._replay_watch(
        object(), cfg, _watch(), DAY, start, same_bar_end
    )
    assert signals == []
    assert engine is not None
    assert engine.state.value == "ARMED"

    next_minute_end = replay._clock_ms(DAY, "09:26")
    signals, _engine = await replay._replay_watch(
        object(), cfg, _watch(), DAY, start, next_minute_end
    )
    assert len(signals) == 1
    assert signals[0].tier == "ENTRY"
    assert signals[0].at_ms == next_minute_end

@pytest.mark.asyncio
async def test_replay_day_is_incomplete_when_selected_contract_has_no_usable_history(monkeypatch):
    from app.services.exchanges.kite import accounts

    cfg = GammaMoveConfig().validate()
    watch = _watch()

    monkeypatch.setattr(accounts, "get_active", lambda _uid: object())

    async def acquire_client(_acct):
        return object()

    async def underlyings(_uid, _cfg, _client, _day):
        return [], {"RELIANCE": object()}, [], []

    async def watches_at(_uid, _cfg, _client, _day, _dump, _contexts, _build_ms):
        return [watch], []

    async def no_usable_history(_client, _cfg, _watch, _day, _start_ms, _end_ms):
        return [], None

    monkeypatch.setattr(accounts, "acquire_client", acquire_client)
    monkeypatch.setattr(replay, "_underlyings", underlyings)
    monkeypatch.setattr(replay, "_watches_at", watches_at)
    monkeypatch.setattr(replay, "_replay_watch", no_usable_history)

    result = await replay.replay_day("default", DAY, cfg)

    assert result["complete"] is False
    assert result["signals"] == []
    assert result["watchlist"] == []
    assert "all selected Gamma-Unwind contracts failed historical replay" in result["issue"]
    assert any("insufficient historical warmup" in row for row in result["coverage"])

