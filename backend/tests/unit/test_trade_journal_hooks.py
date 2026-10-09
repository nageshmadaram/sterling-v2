"""The journal hooks in the three strategies, driven through the REAL registries (no broker).

One Supertrend, one Gamma and one Snapback trade each run the lifecycle the engines run - submit,
fill, protect, trail, exit - for a live uid and a simulation uid, and must leave one complete row with
its timeline. The end-to-end version through the offline fake broker lives in
``tests/e2e_live/test_trade_journal_e2e.py``.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from tj_support import OWNER, SIM, clock, journal_db, ms  # noqa: F401  (journal_db, clock are fixtures)

BACKEND = Path(__file__).resolve().parents[2]
HOOKED = [
    "app/services/kite_engine/state.py", "app/services/kite_engine/positions.py",
    "app/services/kite_engine/monitor.py", "app/services/kite_engine/execution_lifecycle.py",
    "app/services/kite_engine/service.py", "app/services/gamma_move_runner.py",
    "app/services/gamma_move_positions.py", "app/services/snapback_live_positions.py",
    "app/services/snapback_dispatcher.py",
]


@pytest.fixture
def app_db(journal_db, clock):
    """The throwaway database with the application's own tables too (the registries persist there)."""
    from app.services import db
    assert db.init()
    return journal_db


def _types(trade):
    return [e["type"] for e in trade["timeline"]]


def _only(tj, uid):
    page = tj.list_trades([uid])
    assert page["total"] == 1, [t["narrative"] for t in page["items"]]
    return tj.get_trade(page["items"][0]["trade_id"], [uid])


# ------------------------------------------------------------------------- the hook names

def test_every_hook_name_used_in_the_trading_code_exists():
    """A mistyped hook would silently be a no-op at runtime (by design); this is where it is caught."""
    from app.services import trade_journal_adapters as adapters
    used = set()
    for rel in HOOKED:
        text = (BACKEND / rel).read_text(encoding="utf-8")
        assert "trade_journal_adapters as _journal" in text, f"{rel} does not import the journal adapters"
        used |= set(re.findall(r"(?<![A-Za-z0-9])_journal\.(\w+)", text))
    assert used, "no hooks found"
    missing = sorted(n for n in used if n not in vars(adapters))
    assert not missing, f"hook names with no adapter: {missing}"


def test_an_unknown_hook_is_a_no_op_and_never_an_attribute_error():
    from app.services import trade_journal_adapters as adapters
    assert adapters.st_not_a_hook(1, 2, x=3) is None and adapters.gm_nope() is None and adapters.sb_nope() is None
    with pytest.raises(AttributeError):
        adapters.something_else


# ------------------------------------------------------------------------------ Supertrend

def _signal_row(direction="long", adx=28.2):
    from app.engines.sterling_kite_engine.schemas import AlignmentChip, EngineSignalRow, OptionLeg
    trend = 1 if direction == "long" else -1
    return EngineSignalRow(
        underlying="NIFTY", token=256265, exchange="NFO", regime="BULL" if direction == "long" else "BEAR",
        alignment=AlignmentChip(fast=trend, mid=trend, slow=trend), direction=direction,
        option_type="CE" if direction == "long" else "PE",
        legs=[OptionLeg(moneyness="ATM", option_type="CE", option_symbol="NIFTY26O2925000CE", strike=25000.0,
                        expiry="2026-10-29", lot_size=75, token=11)],
        spot=25000.0, underlying_spot=25000.0, stop_loss=24850.0, score=85.0, timestamp_ms=ms(9, 15),
        is_fresh=True, is_active=True, source="spot", adx=adx, atr_pct=60.0)


SYM = "NIFTY26O2925000CE"
PAYLOAD = dict(symbol=SYM, exchange="NFO", quantity=75, lot_size=75, entry_premium=120.0, stop_premium=96.0,
               direction="long", vehicle="otm_options", underlying="NIFTY", strike=25000.0,
               expiry="2026-10-29", target_premium=0.0, exit_mode="one_red", stop_mode="both", entry_delta=0.55)


@pytest.mark.parametrize("uid,mode", [("st-live", "LIVE"), ("sim:st-sim", "SIMULATION")])
def test_supertrend_lifecycle_leaves_one_complete_row(app_db, uid, mode):
    from app.services import trade_journal_adapters as adapters
    from app.services.kite_engine import positions as pos
    tj = app_db
    pos.reset(uid)
    adapters.st_submitted(uid, _signal_row(), "O1", PAYLOAD, "ACC1", 1, "spot:1:long")
    pos.register(pos.OpenPosition(
        uid=uid, symbol=SYM, exchange="NFO", token=11, qty=75, lot_size=75, entry_premium=120.0, stop_premium=96.0,
        initial_stop_premium=96.0, order_id="O1", status=pos.PENDING, underlying="NIFTY", strike=25000.0,
        expiry="2026-10-29", account_id="ACC1"))
    pos.mark_filled(uid, SYM, 120.0, filled_qty=75, order_id="O1")
    pos.update_stop(uid, SYM, 96.0, gtt_id=7, notify=False)           # the broker stop is armed at the initial level
    pos.update_stop(uid, SYM, 118.0, notify=False)                    # the trail ratchets
    pos.close(uid, SYM, reason="trail breach @ ₹150.00 ≤ ₹118.00", exit_price=150.0)
    t = _only(tj, uid)
    assert (t["mode"], t["strategy"], t["status"]) == (mode, "supertrend", "CLOSED")
    assert (t["entry_price"], t["qty"], t["lots"], t["initial_sl"], t["exit_price"]) == (120.0, 75, 1, 96.0, 150.0)
    assert t["exit_reason"] == "TSL" and t["gross_pnl"] == 2250.0           # the stop had trailed
    assert t["net_pnl"] == round(2250.0 - t["charges"], 2) and t["charges_source"] == "estimate"
    assert t["signal_context"]["aligned"] == 3 and t["signal_context"]["adx"] == 28.2
    assert [(m["from"], m["to"]) for m in t["tsl_history"]] == [(96.0, 118.0)] and t["current_sl"] == 118.0
    assert _types(t) == ["SIGNAL", "ORDER_SUBMITTED", "FILLED", "PROTECTION_PLACED", "TSL_MOVED", "EXIT_FILLED"]
    assert t["narrative"].startswith("Bought 1 lot NIFTY 25000 CE at 120.00 on 3 aligned bullish SuperTrends (ADX 28, score 85).")
    assert "Stop was raised to 118.00" in t["narrative"] and "when the trailing stop was hit" in t["narrative"]
    assert t["capital_used"] == 9000.0 and t["r_multiple"] is not None


def test_a_hook_replayed_by_a_second_code_path_changes_nothing(app_db):
    from app.services import trade_journal_adapters as adapters
    from app.services.kite_engine import positions as pos
    tj, uid = app_db, "st-replay"
    pos.reset(uid)
    pos.register(pos.OpenPosition(uid=uid, symbol=SYM, exchange="NFO", qty=75, lot_size=75, entry_premium=120.0,
                                  stop_premium=96.0, initial_stop_premium=96.0, order_id="O1", status=pos.PENDING,
                                  underlying="NIFTY", account_id="A"))
    for _ in range(3):
        pos.mark_filled(uid, SYM, 120.0, filled_qty=75, order_id="O1")
    p = pos.get(uid, SYM)
    adapters.st_closed(uid, p, 150.0, "target reached @ ₹150.00", 75, "X1")    # the order-level close...
    pos.close(uid, SYM, reason="broker exit; fill @ ₹150.00", exit_price=150.0)    # ...then the registry's
    adapters.st_closed(uid, p, 150.0, "target reached @ ₹150.00", 75, "X1")    # ...and a replay
    t = _only(tj, uid)
    assert t["status"] == "CLOSED" and t["gross_pnl"] == 2250.0 and t["exit_reason"] == "TARGET"
    assert _types(t).count("EXIT_FILLED") == 1 and _types(t).count("FILLED") == 1


def test_supertrend_partial_exit_then_final_exit_sums_both_orders(app_db):
    from app.services import trade_journal_adapters as adapters
    from app.services.kite_engine import positions as pos
    tj, uid = app_db, "st-partial"
    pos.reset(uid)
    pos.register(pos.OpenPosition(uid=uid, symbol=SYM, exchange="NFO", qty=75, lot_size=75, entry_premium=120.0,
                                  stop_premium=96.0, initial_stop_premium=96.0, order_id="O1", status=pos.PENDING,
                                  underlying="NIFTY", account_id="A"))
    pos.mark_filled(uid, SYM, 120.0, filled_qty=75, order_id="O1")
    p = pos.get(uid, SYM)
    adapters.st_partial(uid, p, 25, 140.0, "X1", "trail breach")                  # first exit order: 25 sold
    adapters.st_closed(uid, p, 150.0, "trail breach @ 150", 50, "X2")             # second exit order: the rest
    t = _only(tj, uid)
    assert t["status"] == "CLOSED" and t["exit_qty"] == 75 and t["gross_pnl"] == 2000.0   # 25 x 20 + 50 x 30
    assert _types(t) == ["FILLED", "PARTIAL_EXIT", "EXIT_FILLED"]


def test_supertrend_rejected_entry_is_marked_with_the_broker_reason(app_db):
    from app.services import trade_journal_adapters as adapters
    from app.services.kite_engine import positions as pos
    tj, uid = app_db, "st-reject"
    pos.reset(uid)
    adapters.st_submitted(uid, _signal_row(), "O9", PAYLOAD, "ACC1", 1, "spot:1:long")
    pos.register(pos.OpenPosition(uid=uid, symbol=SYM, exchange="NFO", qty=0, lot_size=75, entry_premium=120.0,
                                  stop_premium=96.0, order_id="O9", status=pos.PENDING, entry_requested_qty=75,
                                  underlying="NIFTY", account_id="ACC1"))
    pos.mark_rejected(uid, SYM, "RMS: insufficient margin")
    t = _only(tj, uid)
    assert t["status"] == "REJECTED" and t["extra"]["reject_reason"] == "RMS: insufficient margin"
    assert _types(t) == ["SIGNAL", "ORDER_SUBMITTED", "REJECTED"]


def test_supertrend_refusals_while_deciding_a_signal_become_skips(app_db):
    from app.services import trade_journal_adapters as adapters
    from app.services.kite_engine import state
    tj, uid, row = app_db, "st-skip", _signal_row()
    state.log(uid, "order_blocked", "NIFTY: daily loss ₹5000 ≥ 2% of ₹200000 — new entries halted")   # outside a decision
    assert tj.list_skips([uid])["total"] == 0
    with adapters.st_scope(uid, row):
        state.log(uid, "order_blocked", "NIFTY: daily loss ₹5000 ≥ 2% of ₹200000 — new entries halted for today")
        state.log(uid, "order_blocked", "NIFTY: daily loss ₹5000 ≥ 2% of ₹200000 — new entries halted for today")
        state.log(uid, "info", "NIFTY entry skipped — ADX 18.0 < 25.0")
        state.log(uid, "info", "Fill confirmed: X")                                  # not a refusal
        state.log("someone-else", "order_blocked", "NIFTY: daily loss exceeded")     # another tenant's log line
    state.log(uid, "order_blocked", "after the decision")                            # scope is gone again
    page = tj.list_skips([uid])
    assert page["total"] == 2 and {i["code"] for i in page["items"]} == {"daily_loss_halt", "adx_below_min"}
    item = next(i for i in page["items"] if i["code"] == "daily_loss_halt")
    assert item["signal_id"] == "spot:%d:long" % ms(9, 15) and item["underlying"] == "NIFTY"
    assert item["reason"].startswith("The daily loss limit is reached") and "halted for today" in item["context"]["detail"]
    assert item["context"]["aligned"] == 3
    assert tj.list_skips(["someone-else"])["total"] == 0


def test_a_failure_after_the_order_went_out_is_a_timeline_note_not_a_skip(app_db):
    from app.services import trade_journal_adapters as adapters
    from app.services.kite_engine import state
    tj, uid, row = app_db, "st-fail", _signal_row()
    with adapters.st_scope(uid, row):
        adapters.st_submitted(uid, row, "O5", PAYLOAD, "ACC1", 1, "spot:1:long")
        state.log(uid, "order_failed", f"{SYM} is OPEN and UNPROTECTED — set a stop in Zerodha now.")
    t = _only(tj, uid)
    assert "FAILED" in _types(t) and tj.list_skips([uid])["total"] == 0


# --------------------------------------------------------------------------------- Gamma

def _gamma_signal(sid="gamma-e1-1"):
    from app.engines.gamma_move import GammaSignal, TIER_ENTRY
    from app.engines.gamma_move.models import GateContext, InstrumentRef, WatchContract
    inst = InstrumentRef(instrument_id="12345", tradingsymbol="NIFTY26O1325000CE", option_type="CE", strike=25000.0,
                         expiry="2026-10-13", lot_size=75, tick_size=0.05, exchange="NFO")
    contract = WatchContract(underlying="NIFTY", underlying_token=256265, list_name="L1", instrument=inst, tag="HOI-1",
                             oi=300_000, candidates_n=2, ref_price=25000.0, zone_edge=25020.0, atr_pct=0.8,
                             build_oi=330_000.0, chain_ltp=120.0, dte=1)
    gates = GateContext(zone_gap_pct=0.1, zone_limit_pct=1.0, avg_vol_lots=40.0, zero_vol_bars=0, f1=True, f2=True,
                        unwind=True, gate_reasons=(), gate_unknown=(),
                        passes=("zone", "liquidity", "direction", "unwind"), quadrant=None)
    return GammaSignal(id=sid, at_ms=ms(11, 0), contract=contract, ltp=120.0, oi_drop_pct=9.5, vol_ratio=1.8,
                       strong=False, base_low=98.0, base_high=124.0, reason="E1 breakout", snapshot={}, gates=gates,
                       tier=TIER_ENTRY)


@pytest.mark.parametrize("uid,mode", [("gm-live", "LIVE"), ("sim:gm-sim", "SIMULATION")])
def test_gamma_lifecycle_leaves_one_complete_row(app_db, clock, uid, mode):
    from app.engines.gamma_move import PositionState
    from app.services import gamma_move_positions as store
    from app.services import trade_journal_adapters as adapters
    tj = app_db
    clock.at(11, 1)
    store.reset(uid)
    sig = _gamma_signal()
    pos = PositionState.from_signal(sig, fill=120.0, quantity=75, lots=1, at_ms=ms(11, 1), day="2026-10-12")
    pos.order_id, pos.account_id, pos.stop_mode = "G1", "ACC1", "both"
    store.put(uid, pos)
    adapters.gm_submitted(uid, sig, pos, "G1", 120.0)
    store.mark_filled(uid, "NIFTY26O1325000CE", 120.0)
    pos = store.get(uid, "NIFTY26O1325000CE")
    pos.gtt_id, pos.gtt_at = 31, 98.0
    adapters.gm_protection(uid, pos)                                  # broker stop placed at the base low
    old = float(pos.stop)
    pos.trade_state, pos.stop = "T1_TRAIL", 120.0                      # T1: the stop moves to the entry price
    adapters.gm_t1(uid, pos, old)
    adapters.gm_protection(uid, pos, old)                             # ...and the broker stop follows
    adapters.gm_exit_submitted(uid, pos, "TRAIL")
    store.close(uid, "NIFTY26O1325000CE", "TRAIL", exit_price=126.0, quantity_closed=75, pnl_inr=450.0)
    t = _only(tj, uid)
    assert (t["mode"], t["strategy"], t["status"]) == (mode, "gamma", "CLOSED")
    assert (t["entry_price"], t["initial_sl"], t["target"], t["exit_price"]) == (120.0, 98.0, pos.t2, 126.0)
    assert t["exit_reason"] == "TSL" and t["gross_pnl"] == 450.0
    assert t["signal_context"]["tier"] == "ENTRY" and t["signal_context"]["gates_passed"] == ["zone", "liquidity", "direction", "unwind"]
    assert [(m["from"], m["to"]) for m in t["tsl_history"]] == [(98.0, 120.0)]
    assert _types(t) == ["SIGNAL", "ORDER_SUBMITTED", "FILLED", "PROTECTION_PLACED", "TARGET_HIT", "TSL_MOVED",
                         "EXIT_SUBMITTED", "EXIT_FILLED"]
    assert "Gamma Unwind E1 entry signal" in t["narrative"] and "when the trailing stop was hit" in t["narrative"]


def test_gamma_stop_out_is_sl_and_a_rejected_entry_is_marked(app_db):
    from app.engines.gamma_move import PositionState
    from app.services import gamma_move_positions as store
    from app.services import trade_journal_adapters as adapters
    tj, uid = app_db, "gm-sl"
    store.reset(uid)
    for sid, outcome in (("gamma-sl", "close"), ("gamma-rej", "reject")):
        sig = _gamma_signal(sid)
        pos = PositionState.from_signal(sig, fill=120.0, quantity=75, lots=1, at_ms=ms(11, 1), day="2026-10-12")
        pos.order_id = sid
        store.put(uid, pos)
        adapters.gm_submitted(uid, sig, pos, sid, 120.0)
        if outcome == "close":
            store.mark_filled(uid, "NIFTY26O1325000CE", 120.0)
            store.close(uid, "NIFTY26O1325000CE", "SL", exit_price=98.0, quantity_closed=75, pnl_inr=-1650.0)
        else:
            store.forget_closed(uid)
            store.put(uid, pos)
            store.mark_rejected(uid, "NIFTY26O1325000CE", "REJECTED")
    rows = {t["signal_id"]: t for t in tj.list_trades([uid])["items"]}
    assert rows["gamma-sl"]["exit_reason"] == "SL" and rows["gamma-sl"]["net_pnl"] == round(-1650.0 - rows["gamma-sl"]["charges"], 2)
    assert rows["gamma-rej"]["status"] == "REJECTED"


def test_gamma_skips_are_coded_deduped_and_mode_choices_are_not_skips(app_db):
    from app.services import trade_journal_adapters as adapters
    tj, uid, sig = app_db, "gm-skip", _gamma_signal()
    adapters.gm_skip(uid, sig, "live_intraday_broker_protection_required", "Live intraday ENTRY is blocked.")
    adapters.gm_skip(uid, sig, "live_intraday_broker_protection_required", "Live intraday ENTRY is blocked.")
    adapters.gm_skip(uid, sig, "tier_alert_only", "Tier E2 is alert-only")
    adapters.gm_skip(uid, sig, "", "Gamma Unwind is switched off")                 # an operator choice, not a refusal
    adapters.gm_skip(uid, sig, "", "already holding NIFTY26O1325000CE")
    page = tj.list_skips([uid])
    assert sorted(i["code"] for i in page["items"]) == ["already_holding", "live_intraday_broker_protection_required", "tier_alert_only"]
    item = next(i for i in page["items"] if i["code"].startswith("live_intraday"))
    assert item["reason"] == "Live intraday entries need a broker-held stop." and item["symbol"] == "NIFTY26O1325000CE"
    assert item["context"]["tier"] == "ENTRY" and item["context"]["detail"] == "Live intraday ENTRY is blocked."


# ------------------------------------------------------------------------------- Snapback

def _position(uid, pid="plan-1", **over):
    from app.services.snapback_live_positions import LivePosition
    fields = dict(
        position_id=pid, uid=uid, account_id="ACC1", signal_id="snapback:fade_up:NIFTY:1", lane="swing",
        underlying="NIFTY", side="fade_up", option_type="PE", option_exchange="NFO", option_symbol="NIFTY26NOV25000PE",
        option_token=5, lot_size=75, option_qty=75, option_entry_price=300.0, option_entry_order_id="O1",
        option_expiry="2026-11-26", hedge_exchange="NFO", hedge_symbol="NIFTY26NOVFUT", hedge_side="BUY",
        hedge_qty=75, hedge_entry_price=25020.5, hedge_entry_order_id="H1", hedge_lot_size=75,
        rules={"lane": "swing", "premium_stop_pct": 35.0, "hold_days": 15, "runner_mult": 1.5, "runner_trail_pct": 25.0},
        status="ENTERING", stop_premium=195.0, desired_stop=195.0, high_water=300.0, opened_ms=ms(9, 40),
        entry_day="2026-10-12")
    fields.update(over)
    return LivePosition(**fields)


def _mutator(**values):
    def apply(p):
        for k, v in values.items():
            setattr(p, k, v)
    return apply


@pytest.mark.parametrize("uid,mode", [("sb-live", "LIVE"), ("sim:sb-sim", "SIMULATION")])
def test_snapback_lifecycle_is_derived_from_the_position_row(app_db, clock, tmp_path, uid, mode):
    from app.services.snapback_live_positions import LivePositionStore
    tj = app_db
    clock.at(9, 41)
    store = LivePositionStore(tmp_path / f"{mode}.db")
    store.put(_position(uid))                                                                 # option fill + hedge fill
    store.mutate("plan-1", _mutator(status="OPEN", protection_state="ACTIVE", gtt_id=9, gtt_stop=195.0, gtt_qty=75))
    store.mutate("plan-1", _mutator(high_water=340.0))                                         # a tick: nothing to journal
    store.mutate("plan-1", _mutator(gtt_stop=230.0, desired_stop=230.0))                       # the stop ratchets up
    store.mutate("plan-1", _mutator(status="EXITING", exit_reason="PREMIUM_STOP", exit_started_ms=ms(13, 0)))
    store.mutate("plan-1", _mutator(
        status="CLOSED", option_exit_qty=75, option_exit_value=75 * 225.0, option_exit_price=225.0,
        hedge_exit_qty=75, hedge_exit_value=75 * 25100.0, hedge_exit_price=25100.0,
        realised_pnl=round((225.0 - 300.0) * 75 + (25100.0 - 25020.5) * 75, 2), closed_ms=ms(13, 5)))
    t = _only(tj, uid)
    assert (t["mode"], t["strategy"], t["status"]) == (mode, "snapback", "CLOSED")
    assert (t["entry_price"], t["initial_sl"], t["exit_price"], t["entry_ts_ms"], t["exit_ts_ms"]) == (
        300.0, 195.0, 225.0, ms(9, 40), ms(13, 5))
    assert t["gross_pnl"] == 337.5                                                             # -5625 option + 5962.5 hedge
    assert t["exit_reason"] == "TSL"                                                           # the stop had been raised
    assert [(m["from"], m["to"]) for m in t["tsl_history"]] == [(195.0, 230.0)]
    assert t["signal_context"]["lane"] == "swing" and t["signal_context"]["hedge"]["symbol"] == "NIFTY26NOVFUT"
    assert t["extra"]["hedge"]["exit_price"] == 25100.0
    assert _types(t) == ["FILLED", "HEDGE_FILLED", "PROTECTION_PLACED", "TSL_MOVED", "EXIT_SUBMITTED", "EXIT_FILLED"]
    assert t["capital_used"] == 22500.0 and "hedged with NIFTY26NOVFUT" in t["narrative"]


def test_snapback_resync_after_a_restart_adds_nothing(app_db, tmp_path):
    from app.services import trade_journal_adapters as adapters
    from app.services.snapback_live_positions import LivePositionStore
    tj, uid = app_db, "sb-restart"
    store = LivePositionStore(tmp_path / "r.db")
    store.put(_position(uid))
    store.mutate("plan-1", _mutator(status="OPEN", protection_state="ACTIVE", gtt_id=9, gtt_stop=195.0, gtt_qty=75))
    before = _only(tj, uid)
    adapters._sb_seen.clear()                    # a new process has no memory of what it already wrote
    adapters._sb_prev_stop.clear()
    store.mutate("plan-1", _mutator(note="touched"))
    after = _only(tj, uid)
    assert _types(after) == _types(before) and after["narrative"] == before["narrative"]


def test_snapback_runner_trail_is_recorded_as_a_stop_that_moved(app_db, tmp_path):
    from app.services.snapback_live_positions import LivePositionStore
    tj, uid = app_db, "sb-runner"
    store = LivePositionStore(tmp_path / "run.db")
    store.put(_position(uid))
    store.mutate("plan-1", _mutator(status="OPEN", protection_state="ACTIVE", gtt_id=9, gtt_stop=195.0, gtt_qty=75,
                                    is_runner=True, peak_close=480.0))
    store.mutate("plan-1", _mutator(status="CLOSED", exit_reason="RUNNER_TRAIL_STOP", option_exit_qty=75,
                                    option_exit_value=75 * 355.0, option_exit_price=355.0, realised_pnl=4125.0,
                                    closed_ms=ms(15, 0)))
    t = _only(tj, uid)
    assert t["exit_reason"] == "TSL" and any(m["to"] == 360.0 for m in t["tsl_history"])      # 480 x (1 - 25%)
    assert any(e["type"] == "NOTE" and "Runner armed" in e["detail"] for e in t["timeline"])


def test_snapback_dispatcher_outcomes_refusals_rejections_and_context(app_db, tmp_path):
    from app.services import trade_journal_adapters as adapters
    from app.services.snapback_live_positions import LivePositionStore
    tj, uid = app_db, "sb-disp"
    row = {"signal_id": "snapback:fade_up:NIFTY:1", "symbol": "NIFTY", "state": "armed", "side": "fade_up",
           "direction": "BEARISH", "strength": "STRONG", "stretch": 2.1, "distance_pct": 3.3, "mean_target": 24250.0,
           "spot": 25000.0, "timestamp_ms": ms(9, 20), "hold_days": 15, "origin": "live_quote",
           "contract": {"symbol": "NIFTY26NOV25000PE", "exchange": "NFO", "strike": 25000.0, "option_type": "PE"}}
    plan = {"plan_id": "plan-1", "opportunity_id": row["signal_id"], "option_symbol": "NIFTY26NOV25000PE",
            "account_id": "ACC1", "underlying": "NIFTY", "option_type": "PE", "option_expiry": "2026-11-26",
            "lot_size": 75, "lots": 1, "option_quantity": 75, "stop_premium": 195.0, "target_premium": 0.0,
            "lane": "swing", "signal_side": "fade_up", "strength": "STRONG", "cash_required": 22612.5,
            "margin_required": 135000.0, "futures_symbol": "NIFTY26NOVFUT", "target_futures_quantity": 75,
            "hedge_side": "BUY", "hedge_beta": 1.0, "rules": {"lane": "swing", "premium_stop_pct": 35.0}}
    # a position exists: the sync journals it, the dispatcher then adds the signal that caused it
    LivePositionStore(tmp_path / "d.db").put(_position(uid))
    adapters.sb_executed(uid, row, plan, "OPEN", [])
    t = _only(tj, uid)
    assert t["signal_context"]["stretch"] == 2.1 and t["signal_context"]["strength"] == "STRONG"
    assert t["signal_context"]["hedge"]["beta"] == 1.0 and t["signal_context"]["hedge"]["symbol"] == "NIFTY26NOVFUT"
    assert t["strike"] == 25000.0 and t["capital_used"] == 157612.5 and "SIGNAL" in _types(t)
    assert "fading an up-move (STRONG, stretch 2.1, 3.3% from the mean)" in t["narrative"]
    # a pre-flight refusal never sent an order: a skip, no trade
    row2 = {**row, "signal_id": "snapback:fade_up:NIFTY:2"}
    adapters.sb_executed(uid, row2, {**plan, "plan_id": "plan-2", "opportunity_id": row2["signal_id"]}, "REFUSED",
                         ["broker_not_reconciled"])
    # a broker REJECTION is a trade that failed
    row3 = {**row, "signal_id": "snapback:fade_up:NIFTY:3"}
    adapters.sb_executed(uid, row3, {**plan, "plan_id": "plan-3", "opportunity_id": row3["signal_id"]}, "REJECTED",
                         ["RMS: margin"])
    trades = {x["extra"]["plan_id"]: x for x in tj.list_trades([uid])["items"]}
    assert set(trades) == {"plan-1", "plan-3"} and trades["plan-3"]["status"] == "REJECTED"
    skips = tj.list_skips([uid])["items"]
    assert [(s["code"], s["signal_id"]) for s in skips] == [("broker_not_reconciled", row2["signal_id"])]
    assert skips[0]["reason"] == "Broker positions have not been reconciled yet." and skips[0]["context"]["stretch"] == 2.1


def test_snapback_refusals_blockers_and_mode_choices(app_db):
    from app.services import trade_journal_adapters as adapters
    tj, uid = app_db, "sb-block"
    armed = {"signal_id": "s1", "symbol": "NIFTY", "state": "armed", "contract": {"symbol": "NIFTY26NOV25000PE"}}
    watching = {"signal_id": "s2", "symbol": "NIFTY", "state": "watching", "contract": {"symbol": "X"}}
    adapters.sb_blocked(uid, [armed, watching], ["auto_off"])                                   # a mode, not a refusal
    adapters.sb_blocked(uid, [armed, watching], ["auto_off", "broker_not_reconciled", "kill_switch"])
    adapters.sb_blocked(uid, [armed, watching], ["auto_off", "broker_not_reconciled", "kill_switch"])
    adapters.sb_refused(uid, armed, "plan_exists:OPEN")                                         # already planned
    adapters.sb_refused(uid, armed, "quote_not_executable", "two-sided book unavailable")
    adapters.sb_refused(uid, {**armed, "signal_id": "s9"}, "stale", "", manual=True)           # operator clicked: not a skip
    items = tj.list_skips([uid])["items"]
    assert sorted((i["code"], i["signal_id"]) for i in items) == [
        ("broker_not_reconciled", "s1"), ("quote_not_executable", "s1")]
    assert next(i for i in items if i["code"] == "broker_not_reconciled")["context"]["blockers"] == [
        "broker_not_reconciled", "kill_switch"]


# --------------------------------------------------------------- never into the trading path

def test_a_hook_given_garbage_never_raises(app_db):
    from app.services import trade_journal_adapters as adapters
    uid = "garbage"
    for fn, args in [
        (adapters.st_filled, (uid, None)), (adapters.st_stop, (uid, object(), 1.0, 2)),
        (adapters.st_closed, (uid, object(), "x", None)), (adapters.st_submitted, (uid, None, "o", {}, "a", 1)),
        (adapters.gm_filled, (uid, object())), (adapters.gm_closed, (uid, None, "SL")),
        (adapters.gm_skip, (uid, object(), "", None)), (adapters.sb_sync, (object(),)),
        (adapters.sb_executed, (uid, None, None, "OPEN", [])), (adapters.sb_blocked, (uid, None, None)),
        (adapters.st_note_log, (uid, None, None)),
    ]:
        assert fn(*args) is None, fn.__name__
    with adapters.st_scope(uid, object()):
        pass


def test_a_broken_journal_does_not_change_what_the_registry_does(app_db, monkeypatch):
    from app.services import trade_journal
    from app.services.kite_engine import positions as pos, state
    uid = "broken"
    pos.reset(uid)

    def boom(*a, **k):
        raise RuntimeError("journal exploded")

    monkeypatch.setattr(trade_journal, "_write", boom)
    monkeypatch.setattr(trade_journal, "_read", boom)
    p = pos.register(pos.OpenPosition(uid=uid, symbol=SYM, exchange="NFO", qty=75, lot_size=75, entry_premium=120.0,
                                      stop_premium=96.0, initial_stop_premium=96.0, order_id="O1",
                                      status=pos.PENDING, underlying="NIFTY"))
    pos.mark_filled(uid, SYM, 120.0, filled_qty=75, order_id="O1")
    pos.update_stop(uid, SYM, 110.0, gtt_id=4, notify=False)
    closed = pos.close(uid, SYM, reason="trail breach", exit_price=150.0)
    assert closed is p and p.status == pos.CLOSED and p.stop_premium == 110.0 and p.gtt_id == 4
    assert state.log(uid, "info", "still logging") is None


def test_the_journal_database_being_unavailable_does_not_change_the_registry(app_db, monkeypatch):
    from app.services import db
    from app.services.kite_engine import positions as pos
    uid = "nodb"
    pos.reset(uid)
    p = pos.register(pos.OpenPosition(uid=uid, symbol=SYM, exchange="NFO", qty=75, lot_size=75, entry_premium=120.0,
                                      stop_premium=96.0, order_id="O1", status=pos.PENDING, underlying="NIFTY"))
    monkeypatch.setattr(db, "_available", False)
    pos.mark_filled(uid, SYM, 120.0, filled_qty=75, order_id="O1")
    assert pos.close(uid, SYM, reason="manual", exit_price=130.0) is p and p.status == pos.CLOSED
