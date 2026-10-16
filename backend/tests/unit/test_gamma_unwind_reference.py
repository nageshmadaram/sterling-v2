from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from app.engines.gamma_move import (
    Candle,
    ContractEngine,
    ContractState,
    GammaMoveConfig,
    GateContext,
    InstrumentRef,
    PositionState,
    TIER_ENTRY,
    TIER_STRONG_WATCHLIST,
    TIER_WATCHLIST,
    WatchContract,
    build_gates,
    lists_for,
    select_strikes,
    target_price,
    tick_action,
    trading_days_between,
)

IST = timezone(timedelta(hours=5, minutes=30))
BASE = datetime(2026, 10, 5, 9, 15, tzinfo=IST)


def bar(i: int, *, close: float = 100.0, volume: float = 100.0,
        oi: float = 1000.0) -> Candle:
    return Candle(
        ts_ms=int((BASE + timedelta(minutes=5 * i)).timestamp() * 1000),
        open=close,
        high=101.0,
        low=99.0,
        close=close,
        volume=volume,
        oi=oi,
    )


def contract(*, carried: bool = False, dte: int = 5) -> WatchContract:
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
        candidates_n=3,
        ref_price=102.0,
        zone_edge=103.0,
        atr_pct=3.0,
        build_oi=10_000,
        chain_ltp=100.0,
        dte=dte,
        carried=carried,
    )


def armed_bars() -> list[Candle]:
    rows = []
    for i in range(20):
        volume = 200.0 if i >= 17 else 100.0
        oi = 900.0 if i == 19 else 1000.0
        close = 100.5 if i == 19 else 100.0
        rows.append(bar(i, close=close, volume=volume, oi=oi))
    return rows


def rising_underlying() -> list[Candle]:
    rows = []
    for i in range(20):
        px = 100.0 + i * 0.1
        rows.append(Candle(
            ts_ms=bar(i).ts_ms,
            open=px,
            high=px + 0.2,
            low=px - 0.2,
            close=px,
            volume=10_000,
            oi=0,
        ))
    return rows


def test_reference_defaults_and_client_hoi_pin():
    cfg = GammaMoveConfig().validate()
    assert cfg.zone_pivot_len == 10
    assert cfg.zone_atr_len == 50
    assert cfg.zone_depth == pytest.approx(2.5)
    assert cfg.stock_band_pct == pytest.approx(2.5)
    assert cfg.strike_band_pct == pytest.approx(2.5)
    assert cfg.top_n_oi == 2
    assert cfg.effective_top_n_oi == 2
    assert cfg.signal_tf_min == 5
    assert cfg.oi_drop_alert == pytest.approx(0.08)
    assert cfg.oi_drop_tolerance == pytest.approx(0.01)
    assert cfg.oi_drop_strong == pytest.approx(0.12)
    assert cfg.swing_days == 5
    assert cfg.mark_levels_times == ("09:10", "09:20", "16:00")
    assert cfg.build_watchlists_times == ("09:20", "11:15", "13:15", "14:15")


def test_expiry_lists_switch_to_four_inside_seven_trading_days():
    cfg = GammaMoveConfig().validate()
    rows = lists_for(
        date(2026, 10, 5),
        ["2026-10-08", "2026-10-15"],
        cfg,
    )
    assert [(r.list_name, r.series) for r in rows] == [
        ("EXPIRY-PE", "CURRENT"),
        ("EXPIRY-CE", "CURRENT"),
        ("BREAKDOWN-PE", "NEXT"),
        ("BREAKOUT-CE", "NEXT"),
    ]
    assert trading_days_between(date(2026, 10, 5), date(2026, 10, 8)) == 3


def test_reference_2026_holiday_calendar_is_used_for_dte():
    # 20 Oct 2026 is Dussehra in the uploaded nse_holidays.yaml.
    assert trading_days_between(
        date(2026, 10, 19),
        date(2026, 10, 21),
    ) == 1


def test_unknown_tick_uses_reference_small_tick_below_250():
    from app.engines.gamma_move.selection import default_tick

    assert default_tick(249.99) == pytest.approx(0.01)
    assert default_tick(250.00) == pytest.approx(0.05)


def test_client_selects_highest_oi_otm_contracts_per_list():
    cfg = GammaMoveConfig(min_oi_lots=1).validate()
    rows = [
        {"option_type": "CE", "strike": 103, "oi": 500, "tick_size": 0.05, "last_price": 10},
        {"option_type": "CE", "strike": 104, "oi": 800, "tick_size": 0.05, "last_price": 9},
        {"option_type": "CE", "strike": 99, "oi": 5000, "tick_size": 0.05, "last_price": 20},
    ]
    out = select_strikes(rows, "CE", ref_price=102, zone_edge=103, lot_size=1, cfg=cfg)
    assert len(out) == 2
    assert out[0]["strike"] == 104
    assert out[0]["tag"] == "HOI-1"
    assert out[1]["strike"] == 103
    assert out[1]["tag"] == "HOI-2"
    assert out[0]["candidates_n"] == 2


def test_short_liquidity_history_is_evaluated_not_marked_unknown():
    cfg = GammaMoveConfig(liq_sma_bars=20).validate()
    c = contract()
    option_bars = [
        Candle(bar(i).ts_ms, 100, 101, 99, 100, 100, 1000)
        for i in range(3)
    ]
    und = rising_underlying()
    snapshot = {
        "signal_ts_ms": und[-1].ts_ms + 5 * 60_000,
        "cycle_e2_close": 100.5,
        "oi_peak_bar_close": 100.0,
        "oi_peak_bar_oi": 1000,
        "signal_price": 102.0,
        "oi_now": 900,
    }
    gates = build_gates(
        c,
        spot=102.0,
        option_bars=option_bars,
        underlying_bars=und,
        snapshot=snapshot,
        cfg=cfg,
    )
    assert gates.avg_vol_lots == pytest.approx(10.0)
    assert "liquidity" not in gates.gate_unknown
    assert gates.tier == TIER_ENTRY


def test_four_gate_tiers_are_exact():
    entry = GateContext(0.2, 1.0, 10, 0, True, True, True, (), (), (
        "zone", "liquidity", "direction", "unwind"
    ), "SC")
    strong = GateContext(0.2, 1.0, 10, 0, None, None, True, (), ("direction",), (
        "zone", "liquidity", "unwind"
    ), "SC")
    watch = GateContext(2.0, 1.0, 10, 0, None, None, True, ("zone",), ("direction",), (
        "liquidity", "unwind"
    ), "SC")
    assert entry.tier == TIER_ENTRY
    assert strong.tier == TIER_STRONG_WATCHLIST
    assert watch.tier == TIER_WATCHLIST


def test_closed_bar_e2_then_live_tick_e1_matches_reference_breakout():
    cfg = GammaMoveConfig().validate()
    engine = ContractEngine(contract(), cfg)
    bars = armed_bars()

    engine.on_bar_close(bars)
    assert engine.state is ContractState.ARMED
    assert engine.consol_lo == pytest.approx(99.0)
    assert engine.consol_hi == pytest.approx(101.0)

    forming = Candle(
        ts_ms=bar(20).ts_ms,
        open=100.5,
        high=102.0,
        low=100.5,
        close=102.0,
        volume=200.0,
        oi=900.0,
    )
    sig = engine.breakout(
        bars,
        forming,
        102.0,
        102.0,
        rising_underlying(),
        tick_ts_ms=forming.ts_ms + 60_000,
    )
    assert sig is not None
    assert sig.tier == TIER_ENTRY
    assert sig.state == "armed"
    assert sig.base_low == pytest.approx(99.0)
    assert sig.base_high == pytest.approx(101.0)
    assert engine.state is ContractState.DONE


@pytest.mark.parametrize(
    "patch, expected",
    [
        ({"base_tf_min": 5}, "base_tf_min is fixed at 1"),
        ({"signal_tf_min": 15}, "signal_tf_min is fixed at 5"),
        ({"display_tf_min": 5}, "display_tf_min is fixed at 15"),
        ({"top_n_oi": 3}, "strategy.yaml reference value 2"),
        ({"token_poll_minutes": 5}, "token_poll_minutes is fixed"),
        ({"gates_all_lists": False}, "gates_all_lists is fixed true"),
        ({"alert_group_rate_per_min": 0}, "alert_group_rate_per_min"),
        ({"alert_per_chat_rate_per_sec": 0}, "alert_per_chat_rate_per_sec"),
        ({"late_day_warn_ist": "25:00"}, "late_day_warn_ist"),
    ],
)
def test_client_fixed_or_invalid_config_is_rejected(patch, expected):
    cfg = GammaMoveConfig(**patch)
    with pytest.raises(ValueError, match=expected):
        cfg.validate()


def test_live_tick_oi_peak_not_bar_close_peak_drives_e2():
    cfg = GammaMoveConfig().validate()
    engine = ContractEngine(contract(), cfg)
    bars = armed_bars()
    # Bar-close data alone: peak 1000 -> current 950 = only 5%, below A's
    # tolerated 7% threshold. The client, however, tracks an intrabar tick peak
    # of 1100, so the true live drop is 13.6% and E2 must arm.
    bars[-1] = Candle(
        ts_ms=bars[-1].ts_ms,
        open=100.5,
        high=101.0,
        low=99.0,
        close=100.5,
        volume=200.0,
        oi=950.0,
    )

    engine.on_bar_close(
        bars,
        oi_now=950.0,
        oi_peak_value=1100.0,
    )

    assert engine.state is ContractState.ARMED
    assert engine.last_eval is not None
    assert engine.last_eval.oi_drop == pytest.approx((1100.0 - 950.0) / 1100.0)


def test_recorded_e1_restores_done_cycle_on_restart():
    cfg = GammaMoveConfig().validate()
    engine = ContractEngine(contract(), cfg)
    bars = armed_bars()
    engine.on_bar_close(bars)
    forming = Candle(bar(20).ts_ms, 100.5, 102, 100.5, 102, 200, 900)
    sig = engine.breakout(
        bars, forming, 102.0, 102.0, rising_underlying(),
        tick_ts_ms=forming.ts_ms + 60_000,
    )
    assert sig is not None

    restored = ContractEngine(contract(), cfg)
    restored.on_bar_close(bars)
    restored.restore_recorded_e1(sig)
    assert restored.state is ContractState.DONE
    assert restored.oi_at_alert == pytest.approx(900.0)
    assert restored.consol_lo == pytest.approx(sig.base_low)
    assert restored.consol_hi == pytest.approx(sig.base_high)


def test_targets_round_up_to_contract_tick():
    assert target_price(100.03, 0.20, 0.05) == pytest.approx(120.05)
    assert target_price(100.03, 0.40, 0.05) == pytest.approx(140.05)


def test_t1_moves_stop_to_entry_then_t2_closes():
    p = PositionState(
        signal_id="s1",
        instrument=contract().instrument,
        list_name="BREAKOUT-CE",
        tier=TIER_ENTRY,
        underlying="RELIANCE",
        underlying_token=111,
        entry=100.0,
        quantity=10,
        lots=1,
        entered_ms=bar(0).ts_ms,
        entry_day="2026-10-05",
        hold="swing",
        zone_edge=103.0,
        base_low=90.0,
        t1=120.0,
        t2=140.0,
        status="open",
        fill_price=100.0,
    )
    event, _ = tick_action(p, 120.0)
    assert event == "T1"
    assert p.trade_state == "T1_TRAIL"
    assert p.stop == pytest.approx(100.0)
    event, _ = tick_action(p, 140.0)
    assert event == "T2"


def test_086_unwinding_cannot_use_legacy_tolerance_to_arm_below_seven_percent():
    from app.engines.gamma_move.trigger import a_holds
    cfg = GammaMoveConfig(oi_drop_tolerance=0.01)
    assert not a_holds(0.06, cfg)
    assert not a_holds(0.069, cfg)
    assert a_holds(0.07, cfg)


def test_explicit_experimental_close_mode_emits_without_a_second_breakout():
    cfg=GammaMoveConfig(entry_trigger="closed_5m").validate()
    bars=armed_bars();engine=ContractEngine(contract(),cfg)
    engine.on_bar_close(bars,oi_now=900,oi_peak_value=1000)
    assert bars[-1].close < engine.consol_hi
    signal=engine.closed_bar_signal(bars,102,rising_underlying(),oi_peak_value=1000,entry_ltp=100.55)
    assert signal is not None
    assert signal.ltp == 100.55
    assert signal.at_ms == bars[-1].ts_ms + 5*60_000
    assert signal.snapshot['entry_price_source']=='observed_tick'
    assert engine.closed_bar_signal(bars,102,rising_underlying(),oi_peak_value=1000) is None


def test_direction_any_honors_any_instead_of_an_undeclared_default(monkeypatch):
    from app.engines.gamma_move import regime
    monkeypatch.setattr(regime,'direction',lambda *_:(True,False))
    bars=armed_bars();engine=ContractEngine(contract(),GammaMoveConfig(entry_trigger="closed_5m"))
    engine.on_bar_close(bars,oi_now=900,oi_peak_value=1000)
    signal=engine.closed_bar_signal(bars,102,rising_underlying(),oi_peak_value=1000)
    assert signal is not None
    assert 'direction' not in signal.gates.as_dict()['gate_reasons']


def test_strict_direction_mode_still_blocks_countertrend_drift(monkeypatch):
    from app.engines.gamma_move import regime
    monkeypatch.setattr(regime,'direction',lambda *_:(True,False))
    bars=armed_bars();engine=ContractEngine(contract(),GammaMoveConfig(v1_direction_mode='all', entry_trigger='closed_5m'))
    engine.on_bar_close(bars,oi_now=900,oi_peak_value=1000)
    signal=engine.closed_bar_signal(bars,102,rising_underlying(),oi_peak_value=1000)
    assert signal is not None
    assert signal.tier != TIER_ENTRY
    assert 'direction' in signal.gates.as_dict()['gate_reasons']


def test_source_default_arms_but_does_not_emit_at_completed_bar_close():
    cfg = GammaMoveConfig().validate()
    assert cfg.entry_trigger == 'live_breakout'
    engine = ContractEngine(contract(), cfg)
    bars = armed_bars()
    engine.on_bar_close(bars, oi_now=900, oi_peak_value=1000)
    assert engine.state is ContractState.ARMED
    assert engine.closed_bar_signal(bars, 102, rising_underlying(), oi_peak_value=1000) is None


def test_source_breakout_does_not_add_second_oi_drop_gate_after_arming():
    engine = ContractEngine(contract(), GammaMoveConfig())
    bars = armed_bars()
    engine.on_bar_close(bars, oi_now=900, oi_peak_value=1000)
    # Original on_tick accepts a still-armed breakout even after OI rebounds.
    forming = Candle(bar(20).ts_ms, 100, 102, 100, 102, 200, 990)
    signal = engine.breakout(bars, forming, 102, 102, rising_underlying(),
                             tick_ts_ms=forming.ts_ms+60000, oi_now=990, oi_peak_value=1000)
    assert signal is not None
    assert signal.oi_drop_pct == pytest.approx(1)
    assert engine.state is ContractState.DONE


def test_source_oi_tolerance_applies_to_operator_configured_line():
    from app.engines.gamma_move.trigger import a_holds
    cfg = GammaMoveConfig(oi_drop_alert=.09, oi_drop_tolerance=.01)
    assert a_holds(.08, cfg)
    assert not a_holds(.079, cfg)
