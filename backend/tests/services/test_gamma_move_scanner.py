from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

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
async def test_cached_daily_zones_are_repriced_with_current_spot(monkeypatch):
    cfg = GammaMoveConfig().validate()
    daily = [bar(0)]
    cached = (
        {"RELIANCE": SimpleNamespace(ref_price=100.0)},
        {"RELIANCE": 100.0},
        {"RELIANCE": 2.0},
        {"RELIANCE": (111, daily)},
    )
    seen = {}

    async def quotes(_client, keys):
        assert keys == ["NSE:RELIANCE"]
        return {"NSE:RELIANCE": {"last_price": 112.5}}

    monkeypatch.setattr(scanner, "_quote_batched", quotes)
    monkeypatch.setattr(
        scanner, "compute_zones",
        lambda bars, _cfg: seen.setdefault("bars", list(bars)) or object(),
    )

    def pick(_state, spot):
        seen["spot"] = spot
        return SimpleNamespace(
            ref_price=spot,
            support=None,
            resistance=None,
            dist_support_pct=None,
            dist_resistance_pct=None,
        )

    monkeypatch.setattr(scanner, "pick_levels", pick)

    levels, spots, atrs, underlying = await scanner._refresh_cached_levels(
        object(), cfg, cached
    )

    assert spots == {"RELIANCE": 112.5}
    assert levels["RELIANCE"].ref_price == pytest.approx(112.5)
    assert seen["spot"] == pytest.approx(112.5)
    assert atrs == {"RELIANCE": 2.0}
    assert underlying["RELIANCE"][0] == 111


@pytest.mark.asyncio
async def test_total_daily_history_outage_is_not_cached(monkeypatch):
    cfg = GammaMoveConfig().validate()
    day = BASE.date()
    scanner.clear_levels("u1")

    class Client:
        async def search_instruments(self, *_args, **_kwargs):
            return [{
                "tradingsymbol": "RELIANCE",
                "instrument_token": 111,
                "segment": "NSE",
            }]

    async def dump(_uid):
        return [{"name": "RELIANCE"}]

    async def quotes(_client, _keys):
        return {"NSE:RELIANCE": {"last_price": 2500.0}}

    async def failed_history(*_args, **_kwargs):
        raise TimeoutError("temporary broker history outage")

    monkeypatch.setattr(scanner, "nfo_dump", dump)
    monkeypatch.setattr(
        scanner, "stock_underlyings", lambda _rows, _cfg: ["RELIANCE"]
    )
    monkeypatch.setattr(scanner, "_quote_batched", quotes)
    monkeypatch.setattr(scanner, "_historical", failed_history)

    with pytest.raises(RuntimeError, match="broker daily history unavailable"):
        await scanner._level_stage("u1", cfg, Client(), day)

    assert ("u1", day.isoformat()) not in scanner._levels_cache


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


def test_completed_zero_watch_scan_counts_as_completed_for_day():
    day = BASE.date()
    scanner._stats["u-zero"] = {
        "last_run_ms": int(
            datetime(2026, 10, 5, 13, 15, tzinfo=IST).timestamp() * 1000
        ),
        "stage_b": {"candidates": 0},
        "issue": "",
    }
    try:
        assert scanner.completed_scan_for_day("u-zero", day) is True
    finally:
        scanner._stats.pop("u-zero", None)


def test_failed_scan_does_not_count_as_completed_for_day():
    day = BASE.date()
    scanner._stats["u-failed"] = {
        "last_run_ms": int(
            datetime(2026, 10, 5, 13, 15, tzinfo=IST).timestamp() * 1000
        ),
        "issue": "no Kite session",
    }
    try:
        assert scanner.completed_scan_for_day("u-failed", day) is False
    finally:
        scanner._stats.pop("u-failed", None)


@pytest.mark.asyncio
async def test_missing_live_kite_session_keeps_scan_retryable(monkeypatch):
    cfg = GammaMoveConfig().validate()
    recorded = []

    class Account:
        pass

    class Client:
        _is_paper = False
        _access_token = ""

    monkeypatch.setattr(
        "app.services.exchanges.kite.accounts.get_active",
        lambda _uid: Account(),
    )

    async def acquire(_acct):
        return Client()

    monkeypatch.setattr(
        "app.services.exchanges.kite.accounts.acquire_client",
        acquire,
    )
    monkeypatch.setattr(
        "app.services.gamma_move_history.record_scan",
        lambda *args, **kwargs: recorded.append((args, kwargs)) or {},
    )

    with pytest.raises(RuntimeError, match="no Kite session"):
        await scanner.scan_once("u1", cfg, today=BASE.date())

    assert recorded
    assert recorded[-1][1]["watchlist"] is None
    assert scanner.completed_scan_for_day("u1", BASE.date()) is False
    scanner._stats.pop("u1", None)
    scanner._issue.pop("u1", None)

@pytest.mark.asyncio
async def test_selected_contract_history_outage_keeps_live_scan_retryable(monkeypatch):
    from types import SimpleNamespace

    uid = 'u-prime-fail'
    cfg = GammaMoveConfig().validate()
    day = BASE.date()
    recorded = []

    class Client:
        _is_paper = False
        _access_token = 'token'

    monkeypatch.setattr(
        'app.services.exchanges.kite.accounts.get_active', lambda _uid: object()
    )
    async def acquire(_acct):
        return Client()
    monkeypatch.setattr(
        'app.services.exchanges.kite.accounts.acquire_client', acquire
    )

    level = SimpleNamespace(
        support=None, resistance=None,
        dist_support_pct=None, dist_resistance_pct=None,
    )
    async def level_stage(*_args, **_kwargs):
        return ({'RELIANCE': level}, {'RELIANCE': 100.0}, {'RELIANCE': 2.0}, {'RELIANCE': (111, [])})
    async def watchlist_stage(*_args, **_kwargs):
        return [watch()]
    async def prime(*_args, **_kwargs):
        # Simulates the selected option contract history call timing out.
        scanner._runtime[uid] = {222: object()}
        return ['RELIANCE26OCT2500CE: option 5-minute history unavailable (timeout)']

    monkeypatch.setattr(scanner, '_level_stage', level_stage)
    monkeypatch.setattr(scanner, '_watchlist_stage', watchlist_stage)
    monkeypatch.setattr(scanner, '_prime_runtime', prime)
    monkeypatch.setattr(
        'app.services.gamma_move_history.record_scan',
        lambda *args, **kwargs: recorded.append((args, kwargs)) or {},
    )

    with pytest.raises(RuntimeError, match='watcher history incomplete'):
        await scanner.scan_once(uid, cfg, today=day)

    assert scanner.has_watchers(uid) is False
    assert scanner.completed_scan_for_day(uid, day) is False
    assert recorded and recorded[-1][1]['watchlist'] is None
    assert 'option 5-minute history unavailable' in recorded[-1][1]['issue']
    scanner._stats.pop(uid, None)
    scanner._issue.pop(uid, None)

def test_invalidate_runtime_clears_completed_scan_state():
    uid = "u-config"
    day = BASE.date()
    scanner._runtime[uid] = {222: object()}
    scanner._stats[uid] = {
        "last_run_ms": int(BASE.timestamp() * 1000),
        "stage_b": {"candidates": 1},
        "issue": "",
    }
    scanner._issue[uid] = "stale diagnostic"
    try:
        assert scanner.completed_scan_for_day(uid, day) is True

        scanner.invalidate_runtime(uid)

        assert scanner.has_watchers(uid) is False
        assert scanner.completed_scan_for_day(uid, day) is False
        assert scanner.scan_issue(uid) == ""
    finally:
        scanner._runtime.pop(uid, None)
        scanner._stats.pop(uid, None)
        scanner._issue.pop(uid, None)


def test_signal_universe_config_change_invalidates_levels_and_runtime(monkeypatch):
    cfg = GammaMoveConfig().validate()
    calls = []

    monkeypatch.setattr(service, "get_config", lambda _uid=None: cfg)
    monkeypatch.setattr(
        "app.services.db.set_config",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        scanner, "clear_levels",
        lambda uid=None: calls.append(("levels", uid)),
    )
    monkeypatch.setattr(
        scanner, "invalidate_runtime",
        lambda uid=None: calls.append(("runtime", uid)),
    )
    monkeypatch.setattr(
        "app.services.gamma_move_runner.invalidate_signal_state",
        lambda uid, reason="": calls.append(("signal", uid, reason)),
    )

    updated = service.set_config(
        {"scan_all_stocks": False, "scan_stocks": ["RELIANCE"]},
        uid="u-config",
    )

    assert updated.scan_all_stocks is False
    assert updated.scan_stocks == ("RELIANCE",)
    assert ("levels", "u-config") in calls
    assert ("runtime", "u-config") in calls
    assert any(row[0:2] == ("signal", "u-config") for row in calls)


@pytest.mark.asyncio
async def test_partial_daily_history_outage_is_not_cached(monkeypatch):
    cfg = GammaMoveConfig().validate()
    day = BASE.date()
    uid = "u-partial"
    scanner.clear_levels(uid)

    class Client:
        async def search_instruments(self, *_args, **_kwargs):
            return [
                {
                    "tradingsymbol": "RELIANCE",
                    "instrument_token": 111,
                    "segment": "NSE",
                },
                {
                    "tradingsymbol": "TCS",
                    "instrument_token": 222,
                    "segment": "NSE",
                },
            ]

    async def dump(_uid):
        return [{"name": "RELIANCE"}, {"name": "TCS"}]

    async def quotes(_client, _keys):
        return {
            "NSE:RELIANCE": {"last_price": 2500.0},
            "NSE:TCS": {"last_price": 3500.0},
        }

    async def partly_failed_history(_client, _pacer, token, *_args, **_kwargs):
        if token == 222:
            raise TimeoutError("temporary TCS history outage")
        return []

    monkeypatch.setattr(scanner, "nfo_dump", dump)
    monkeypatch.setattr(
        scanner, "stock_underlyings",
        lambda _rows, _cfg: ["RELIANCE", "TCS"],
    )
    monkeypatch.setattr(scanner, "_quote_batched", quotes)
    monkeypatch.setattr(scanner, "_historical", partly_failed_history)

    try:
        with pytest.raises(
            RuntimeError, match="broker daily history partially unavailable"
        ):
            await scanner._level_stage(uid, cfg, Client(), day)

        assert (uid, day.isoformat()) not in scanner._levels_cache
    finally:
        scanner.clear_levels(uid)
        scanner._issue.pop(uid, None)

