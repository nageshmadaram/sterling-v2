"""Execution-adapter traces with controlled, typed setup evidence.

Indicator/setup mathematics is covered independently. These fixtures isolate
next-event fills, stop ordering, dataset-end inventory, and deterministic replay.
"""
from decimal import Decimal

from app.engines.ema_trend import backtest
from app.engines.ema_trend.contracts import Setup
from .helpers import bar, config, feature


def replay_input(monkeypatch, *, stop_bar=False, recovered=False):
    # Adapter fixtures deliberately use a compact recorded stream. Calendar and
    # daily continuity have separate tests; a 253-session synthetic calendar
    # would exceed this repository's currently verified calendar year.
    fs = [feature(0, 100).model_copy(update={"index": i, "bar": bar(i, 100)}) for i in range(253)]
    for f in fs:
        f.ready = f.index >= 249
    if stop_bar:
        fs[251].bar = bar(251, 99, open_=100, low=94, high=101)
    if recovered:
        fs[252].bar = bar(252, 110, open_=110)
    setups = [Setup(id=f"setup-{i}", symbol="NSE:TEST", mode="swing", index=i,
                    close_ms=f.bar.close_ms, cross_index=245,
                    status="armed" if i == 249 else "watching", reason="FIXTURE",
                    path="BOX_5" if i == 249 else None,
                    stop=Decimal(95) if i == 249 else None,
                    ceiling=Decimal(102) if i == 249 else None, feature=f)
              for i, f in enumerate(fs)]
    monkeypatch.setattr(backtest, "build_features", lambda bars, context: fs[:len(bars)])
    monkeypatch.setattr(backtest, "evaluate_setups", lambda symbol, mode, features: setups[:len(features)])
    return fs, [f.bar for f in fs]


def test_next_open_entry_and_dataset_end_position_stays_open(monkeypatch):
    fs, bars = replay_input(monkeypatch)
    result = backtest.run_replay("NSE:TEST", "swing", bars, [],
                                 config(symbols=["NSE:TEST"]), run_id="deterministic")
    entry = result["fills"][0]
    assert entry["kind"] == "ENTRY"
    assert entry["at_ms"] == bars[250].open_ms
    assert entry["at_ms"] >= bars[249].close_ms
    assert result["metrics"]["completed_campaigns"] == 0
    assert result["metrics"]["open_campaigns"] == 1
    assert result["campaigns"][0]["quantity"] > 0
    assert result["evidence_status"] == "INCONCLUSIVE" and not result["live_eligible"]


def test_identical_replay_inputs_produce_identical_actions_fills_and_state(monkeypatch):
    _, bars = replay_input(monkeypatch)
    cfg = config(symbols=["NSE:TEST"])
    first = backtest.run_replay("NSE:TEST", "swing", bars, [], cfg, run_id="same-inputs")
    second = backtest.run_replay("NSE:TEST", "swing", bars, [], cfg, run_id="same-inputs")
    assert first == second


def test_resting_stop_cannot_be_displaced_by_new_close_time_risk_exit(monkeypatch):
    _, bars = replay_input(monkeypatch, stop_bar=True, recovered=True)
    cfg = config(symbols=["NSE:TEST"], allocation_inr=20_000,
                 daily_loss_fraction=Decimal(".0001"))
    result = backtest.run_replay("NSE:TEST", "swing", bars, [], cfg, run_id="stop-priority")
    exits = [f for f in result["fills"] if f["side"] == "SELL"]
    assert len(exits) == 1
    assert exits[0]["kind"] == "STOP", "Resting protection acts before a later close-based risk breaker"
    assert Decimal(exits[0]["price"]) == Decimal(95)
    assert exits[0]["at_ms"] <= bars[251].available_ms


def test_coarse_bar_with_four_r_close_and_stop_touch_uses_stop_first(monkeypatch):
    fs, bars = replay_input(monkeypatch)
    fs[251].bar = bar(251, 123, open_=100, low=94, high=125)
    bars[251] = fs[251].bar
    result = backtest.run_replay("NSE:TEST", "swing", bars, [],
                                 config(symbols=["NSE:TEST"]), run_id="adverse-order")
    exits = [f for f in result["fills"] if f["side"] == "SELL"]
    assert len(exits) == 1 and exits[0]["kind"] == "STOP"
    assert Decimal(exits[0]["price"]) == Decimal(95)


def test_derivative_replay_requires_actual_metadata_and_preserves_whole_lot_fills(monkeypatch):
    import pytest
    fs,bars=replay_input(monkeypatch)
    cfg=config(symbols=["NFO:TEST26NOV100CE"])
    from app.engines.ema_trend.contracts import Setup
    def entries(symbol,mode,features):
        return [Setup(id=f"derivative-{f.index}",symbol=symbol,mode="swing",index=f.index,
            close_ms=f.bar.close_ms,cross_index=245,status="armed" if f.index==249 else "watching",
            reason="FIXTURE",path="BOX_5" if f.index==249 else None,stop=Decimal(95) if f.index==249 else None,
            ceiling=Decimal(102) if f.index==249 else None,feature=f) for f in features]
    monkeypatch.setattr(backtest,"evaluate_setups",entries)
    with pytest.raises(ValueError,match="FROZEN_INSTRUMENT_METADATA"):
        backtest.run_replay("NFO:TEST26NOV100CE","swing",bars,[],cfg)
    result=backtest.run_replay("NFO:TEST26NOV100CE","swing",bars,[],cfg,instrument_meta={
        "exchange":"NFO","tradingsymbol":"TEST26NOV100CE","instrument_type":"CE","expiry":"2026-11-26","lot_size":5,"tick_size":.05})
    assert result["contract"]["lot_size"]==5 and result["product"]=="NRML"
    assert result["fills"] and all(f["quantity"]%5==0 for f in result["fills"])
