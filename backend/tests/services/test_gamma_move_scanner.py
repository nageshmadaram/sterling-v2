from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.engines.gamma_move import (
    Candle,
    ContractEngine,
    ContractState,
    GammaMoveConfig,
    InstrumentRef,
    TIER_ENTRY,
    WatchContract,
)
from app.services import gamma_move_scanner as scanner

IST = timezone(timedelta(hours=5, minutes=30))
BASE = datetime(2026, 10, 5, 9, 15, tzinfo=IST)


def bar(i: int, *, close: float = 100.0, volume: float = 100.0,
        oi: float = 1000.0) -> Candle:
    return Candle(
        int((BASE + timedelta(minutes=5 * i)).timestamp() * 1000),
        close, 101.0, 99.0, close, volume, oi,
    )


def watch() -> WatchContract:
    return WatchContract(
        underlying="RELIANCE",
        underlying_token=111,
        list_name="BREAKOUT-CE",
        instrument=InstrumentRef(
            "222", "RELIANCE26OCT3000CE", "CE", 3000,
            "2026-10-29", 10, 0.05, "NFO",
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


def closed_option_bars() -> list[Candle]:
    rows = []
    for i in range(20):
        rows.append(bar(
            i,
            close=100.5 if i == 19 else 100.0,
            volume=200.0 if i >= 17 else 100.0,
            oi=900.0 if i == 19 else 1000.0,
        ))
    return rows


def underlying_bars() -> list[Candle]:
    return [
        Candle(
            bar(i).ts_ms,
            100 + i * 0.1,
            100.2 + i * 0.1,
            99.8 + i * 0.1,
            100 + i * 0.1,
            10_000,
            0,
        )
        for i in range(20)
    ]


def test_completed_drops_only_the_still_forming_bar():
    rows = [bar(0), bar(1)]
    inside_second = rows[-1].ts_ms + 60_000
    assert scanner._completed(rows, 5, inside_second) == [rows[0]]
    after_second = rows[-1].ts_ms + 5 * 60_000
    assert scanner._completed(rows, 5, after_second) == rows


def test_spread_pct_requires_two_sided_depth():
    assert scanner._spread_pct({"last_price": 100}) is None
    pct = scanner._spread_pct({
        "depth": {
            "buy": [{"price": 99.0}],
            "sell": [{"price": 101.0}],
        }
    })
    assert pct == pytest.approx(2.0)


def test_live_ticks_close_forming_bar_and_fire_reference_e1():
    cfg = GammaMoveConfig().validate()
    w = watch()
    bars = closed_option_bars()
    engine = ContractEngine(w, cfg)
    engine.on_bar_close(bars)
    assert engine.state is ContractState.ARMED

    scanner._runtime["u1"] = {
        222: scanner.RuntimeWatch(
            contract=w,
            engine=engine,
            closed_bars=list(bars),
            underlying_bars=underlying_bars(),
            spot=102.0,
        )
    }
    ts = bar(20).ts_ms + 60_000
    signals = scanner.process_ticks("u1", [
        {
            "instrument_token": 111,
            "last_price": 102.0,
            "volume_traded": 210_000,
            "exchange_timestamp": ts,
        },
        {
            "instrument_token": 222,
            "last_price": 102.0,
            "volume_traded": 2_500,
            "oi": 900,
            "exchange_timestamp": ts,
        },
    ])

    assert len(signals) == 1
    assert signals[0].tier == TIER_ENTRY
    assert signals[0].contract.instrument.tradingsymbol == "RELIANCE26OCT3000CE"
    assert engine.state is ContractState.DONE
    scanner.clear_runtime("u1")


@pytest.mark.asyncio
async def test_mark_levels_recomputes_after_clearing_cache(monkeypatch):
    cfg = GammaMoveConfig().validate()
    calls = []

    class Accounts:
        @staticmethod
        def get_active(_uid):
            return object()

        @staticmethod
        async def acquire_client(_acct):
            return object()

    monkeypatch.setattr(
        "app.services.exchanges.kite.accounts.get_active",
        Accounts.get_active,
    )
    monkeypatch.setattr(
        "app.services.exchanges.kite.accounts.acquire_client",
        Accounts.acquire_client,
    )
    monkeypatch.setattr(scanner, "clear_levels", lambda uid=None: calls.append(("clear", uid)))

    async def level_stage(uid, _cfg, _client, day):
        calls.append(("stage", uid, day.isoformat()))
        return ({"RELIANCE": object()}, {"RELIANCE": 100}, {"RELIANCE": 2.0}, {"RELIANCE": (111, [])})

    monkeypatch.setattr(scanner, "_level_stage", level_stage)
    result = await scanner.mark_levels(
        "u1", cfg, today=datetime(2026, 10, 5, tzinfo=IST).date()
    )
    assert calls[0] == ("clear", "u1")
    assert calls[1][0:2] == ("stage", "u1")
    assert result["symbols"] == 1
