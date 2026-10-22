"""Scanner audit regressions: closed-bar discipline, tick-path IO, row semantics."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from app.engines.gamma_move import (
    Candle, ContractEngine, GammaMoveConfig, InstrumentRef, WatchContract,
)
from app.services import gamma_move_scanner as scanner

IST = timezone(timedelta(hours=5, minutes=30))
BASE = datetime(2026, 10, 5, 9, 15, tzinfo=IST)


def bar(i: int, *, close: float = 100.0, volume: float = 100.0, oi: float = 1000.0) -> Candle:
    return Candle(
        int((BASE + timedelta(minutes=5 * i)).timestamp() * 1000),
        close, 101.0, 99.0, close, volume, oi,
    )


def watch(symbol: str = "RELIANCE26OCT3000CE", token: str = "222", dte: int = 5) -> WatchContract:
    return WatchContract(
        underlying="RELIANCE", underlying_token=111, list_name="BREAKOUT-CE",
        instrument=InstrumentRef(token, symbol, "CE", 3000, "2026-10-29", 10, 0.05, "NFO"),
        tag="HOI-1", oi=10_000, candidates_n=1, ref_price=102.0, zone_edge=103.0,
        atr_pct=3.0, build_oi=10_000, chain_ltp=100.0, dte=dte,
    )


@pytest.fixture(autouse=True)
def _clean():
    scanner.clear_runtime()
    scanner.reset_bar_input_cache()
    yield
    scanner.clear_runtime()
    scanner.reset_bar_input_cache()


def test_a_bar_that_has_just_opened_is_still_forming():
    rows = [bar(0), bar(1)]
    # Exactly at the second bar's open timestamp: it has no closed data yet.
    assert scanner._completed(rows, 5, rows[-1].ts_ms) == [rows[0]]


def test_bar_close_inputs_are_persisted_once_per_tick_batch(monkeypatch):
    from app.services import db

    writes: list[str] = []
    monkeypatch.setattr(db, "get_config", lambda key, default="": default)
    monkeypatch.setattr(db, "set_config", lambda key, value: writes.append(key))
    monkeypatch.setattr(db, "get_config_strict", lambda key, default="": default)
    monkeypatch.setattr(db, "set_config_strict", lambda key, value: writes.append(key))

    cfg = GammaMoveConfig().validate()
    runtimes = {}
    ticks = []
    next_slot_ts = bar(20).ts_ms + 1_000
    for n in range(12):
        w = watch(symbol=f"SYM{n}CE", token=str(500 + n))
        rt = scanner.RuntimeWatch(
            contract=w, engine=ContractEngine(w, cfg),
            closed_bars=[bar(i) for i in range(19)], underlying_bars=[], spot=102.0,
            forming=bar(19), oi_peak_today=1000.0, oi_peak_day=BASE.date(),
        )
        runtimes[500 + n] = rt
        ticks.append({
            "instrument_token": 500 + n, "last_price": 100.0,
            "volume_traded": 5_000, "oi": 990, "exchange_timestamp": next_slot_ts,
        })
    scanner._runtime["u1"] = runtimes

    scanner.process_ticks("u1", ticks)

    assert len(writes) == 1  # one write for 12 contracts, not 12
    assert all(rt.last_tick_ms == next_slot_ts for rt in runtimes.values())


def test_watch_row_shows_a_premium_stop_only_where_the_reference_has_one():
    cfg = GammaMoveConfig().validate()
    for dte, expect_stop in ((1, True), (5, False)):
        w = watch(dte=dte)
        engine = ContractEngine(w, cfg)
        engine.last_eval = replace(
            __import__("app.engines.gamma_move", fromlist=["Evaluation"]).Evaluation(ts_ms=1),
            consol_lo=97.5,
        )
        rt = scanner.RuntimeWatch(
            contract=w, engine=engine, closed_bars=[bar(0)], underlying_bars=[], spot=102.0,
        )
        stop = scanner.watch_view(rt)["levels"]["stop"]
        assert (stop == 97.5) if expect_stop else (stop is None)


def test_watch_row_is_stamped_at_the_close_of_its_last_closed_bar():
    cfg = GammaMoveConfig().validate()
    w = watch()
    last = bar(3)
    rt = scanner.RuntimeWatch(
        contract=w, engine=ContractEngine(w, cfg),
        closed_bars=[bar(2), last], underlying_bars=[], spot=102.0,
    )
    assert scanner.watch_view(rt)["at_ms"] == last.ts_ms + 5 * 60_000

@pytest.mark.parametrize('stamp', [None, '2026-10-05T15:39:00+05:30', '2026-10-05T08:59:00+05:30'])
def test_missing_and_out_of_session_exchange_times_never_create_bars(stamp):
    w = watch()
    rt = scanner.RuntimeWatch(contract=w, engine=ContractEngine(w, GammaMoveConfig().validate()),
                              closed_bars=[], underlying_bars=[], spot=102)
    scanner._runtime['u1'] = {222: rt}
    tick = {'instrument_token': 222, 'last_price': 102, 'oi': 900}
    if stamp: tick['exchange_timestamp'] = stamp
    assert scanner.process_ticks('u1', [tick]) == []
    assert rt.forming is None and rt.last_tick_ms == 0
    diagnostic = scanner._stats['u1']['tick_diagnostics'][-1]
    assert diagnostic['received_at_ms'] > 0
    assert diagnostic['reason'] == ('timestamp_missing' if stamp is None else 'outside_continuous_session')
