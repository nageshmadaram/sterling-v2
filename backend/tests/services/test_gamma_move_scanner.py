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
from app.services import gamma_move as service
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


def test_gamma_unwind_universe_uses_all_listed_stock_options_not_curated_subset():
    cfg = GammaMoveConfig(scan_all_stocks=True).validate()
    rows = [
        {"name": "RELIANCE", "segment": "NFO-OPT", "instrument_type": "CE"},
        {"name": "AARTIIND", "segment": "NFO-OPT", "instrument_type": "PE"},
        {"name": "NIFTY", "segment": "NFO-OPT", "instrument_type": "CE"},
        {"name": "011NSETEST", "segment": "NFO-OPT", "instrument_type": "CE"},
    ]

    assert service.stock_underlyings(rows, cfg) == ["AARTIIND", "RELIANCE"]


@pytest.mark.asyncio
async def test_recover_scheduled_watchlist_uses_historical_slot_context(monkeypatch):
    from types import SimpleNamespace
    from app.services import gamma_move_historical as historical

    cfg = GammaMoveConfig().validate()
    w = watch()
    build = datetime(2026, 10, 5, 13, 15, tzinfo=IST)

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

    context = SimpleNamespace(
        token=111,
        daily=[],
        five=[bar(0, close=102.0)],
    )

    async def underlyings(_uid, _cfg, _client, _day):
        return [], {"RELIANCE": context}, [], ["dated master"]

    async def watches_at(_uid, _cfg, _client, _day, _dump, _contexts, build_ms):
        assert build_ms == int(build.timestamp() * 1000)
        return [w], []

    async def prime(uid, _cfg, _client, watches, _day, _underlying, spots,
                    recorded_signals=None):
        assert watches == [w]
        assert spots["RELIANCE"] == pytest.approx(102.0)
        scanner._runtime[uid] = {
            222: scanner.RuntimeWatch(
                contract=w,
                engine=ContractEngine(w, cfg),
                closed_bars=[],
                underlying_bars=[],
                spot=102.0,
            )
        }

    monkeypatch.setattr(historical, "_underlyings", underlyings)
    monkeypatch.setattr(historical, "_watches_at", watches_at)
    monkeypatch.setattr(
        historical, "_last_closed",
        lambda bars, _at_ms, _minutes: bars[-1] if bars else None,
    )
    monkeypatch.setattr(scanner, "_prime_runtime", prime)
    monkeypatch.setattr(
        "app.services.gamma_move_history.record_scan",
        lambda *_args, **_kwargs: {},
    )

    result = await scanner.recover_scheduled_watchlist(
        "u1", cfg, build_time=build
    )

    assert result["watching"] == 1
    assert result["recovered_slot"] == "13:15"
    assert scanner.scan_stats("u1")["recovery_source"] == "scheduled_history"
    scanner.clear_runtime("u1")


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


def test_bar_input_recorder_round_trips_exact_live_engine_inputs(monkeypatch):
    from app.services import db

    values = {}
    monkeypatch.setattr(
        db, "get_config",
        lambda key, default="": values.get(key, default),
    )
    monkeypatch.setattr(
        db, "set_config",
        lambda key, value: values.__setitem__(key, value),
    )

    ts = bar(19).ts_ms
    scanner._save_bar_input("u1", 222, ts, 950.0, 1100.0)
    restored = scanner._load_bar_inputs("u1", BASE.date())

    assert restored[(222, ts)] == pytest.approx((950.0, 1100.0))


def test_level_cache_clear_is_tenant_scoped():
    day = BASE.date().isoformat()
    scanner._levels_cache.clear()
    scanner._levels_cache[("u1", day)] = ({}, {}, {}, {})
    scanner._levels_cache[("u2", day)] = ({}, {}, {}, {})

    scanner.clear_levels("u1")

    assert ("u1", day) not in scanner._levels_cache
    assert ("u2", day) in scanner._levels_cache
    scanner._levels_cache.clear()


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
