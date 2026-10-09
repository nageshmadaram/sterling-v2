"""The journal store: idempotent upserts, derived figures, skips, scoping, and the promise that a
journal failure can never reach the trading code."""
from __future__ import annotations

import pytest

from tj_support import OWNER, SIM, journal_db, ms  # noqa: F401  (journal_db is a fixture)

ENTRY = dict(account_id="ACC1", underlying="NIFTY", option_type="CE", strike=25000.0, expiry="2026-10-29",
             side="BUY", lot_size=75, lots=1, qty=75, signal_id="sig-1", entry_order_id="O1",
             entry_price=120.0, entry_ts_ms=ms(10, 20), initial_sl=96.0, target=150.0)


def _open(tj, uid=OWNER, strategy="supertrend", symbol="NIFTY25OCT25000CE", key="O1", **over):
    fields = {**ENTRY, **over}
    return tj.record_entry(uid=uid, strategy=strategy, symbol=symbol, key=key, status="OPEN",
                           signal_context={"regime": "BULL", "aligned": 3, "adx": 28.0}, **fields)


def _tid(tj, uid=OWNER, strategy="supertrend", symbol="NIFTY25OCT25000CE", key="O1"):
    return tj.trade_id_for(strategy, uid, symbol, key)


def test_trade_id_is_deterministic_and_separates_tenants_and_strategies(journal_db):
    tj = journal_db
    a = tj.trade_id_for("supertrend", OWNER, "SYM", "O1")
    assert a == tj.trade_id_for("supertrend", OWNER, "SYM", "O1") and a.startswith("ST-")
    assert a != tj.trade_id_for("supertrend", SIM, "SYM", "O1")
    assert tj.trade_id_for("gamma", OWNER, "SYM", "O1").startswith("GU-")
    assert tj.trade_id_for("snapback", OWNER, "SYM", "O1").startswith("SB-")


def test_an_entry_replayed_any_number_of_times_is_one_row(journal_db):
    tj = journal_db
    ids = {_open(tj) for _ in range(3)}
    assert len(ids) == 1
    page = tj.list_trades([OWNER])
    assert page["total"] == 1
    t = page["items"][0]
    assert t["status"] == "OPEN" and t["mode"] == "LIVE" and t["entry_price"] == 120.0
    assert t["capital_used"] == 9000.0                      # premium paid: 120 x 75
    assert t["signal_context"]["aligned"] == 3 and t["narrative"].startswith("Bought 1 lot NIFTY 25000 CE at 120.00")


def test_facts_merge_from_any_order_of_calls_and_nothing_overwrites_with_nothing(journal_db):
    tj = journal_db
    # the fill path reports first, with no signal context; the submit path follows with it
    tj.record_entry(uid=OWNER, strategy="supertrend", symbol="S", key="O1", status="OPEN",
                    entry_price=120.0, qty=75, entry_ts_ms=ms(10, 21))
    tj.record_entry(uid=OWNER, strategy="supertrend", symbol="S", key="O1", status="PENDING",
                    signal_context={"adx": 31.0}, initial_sl=96.0, entry_price=None, qty=None)
    t = tj.list_trades([OWNER])["items"][0]
    assert t["status"] == "OPEN"                            # a later PENDING never demotes OPEN
    assert (t["entry_price"], t["qty"], t["initial_sl"]) == (120.0, 75, 96.0)
    assert t["signal_context"] == {"adx": 31.0}


def test_mode_follows_the_uid(journal_db):
    tj = journal_db
    _open(tj)
    _open(tj, uid=SIM)
    assert tj.list_trades([OWNER])["items"][0]["mode"] == "LIVE"
    sim = tj.list_trades([SIM])["items"][0]
    assert sim["mode"] == "SIMULATION" and sim["uid"] == SIM
    assert tj.list_trades([OWNER, SIM])["total"] == 2


def test_events_are_unique_per_key_and_ordered_in_time(journal_db):
    tj = journal_db
    tid = _open(tj)
    kw = dict(trade_id=tid)
    tj.record_event(event_type="SIGNAL", ts_ms=ms(10, 15), detail="Signal", event_key="signal:sig-1", **kw)
    tj.record_event(event_type="ORDER_SUBMITTED", ts_ms=ms(10, 20), detail="Buy order", event_key="order:O1", **kw)
    for _ in range(3):
        tj.record_event(event_type="FILLED", ts_ms=ms(10, 21), price=120.0, qty=75, event_key="fill:O1:75", **kw)
    tj.record_event(event_type="PROTECTION_PLACED", ts_ms=ms(10, 22), price=96.0, event_key="prot:G1:96", **kw)
    detail = tj.get_trade(tid, [OWNER])
    assert [e["type"] for e in detail["timeline"]] == ["SIGNAL", "ORDER_SUBMITTED", "FILLED", "PROTECTION_PLACED"]
    assert detail["timeline"][2]["price"] == 120.0 and detail["timeline"][2]["qty"] == 75


def test_a_trail_move_replayed_is_one_history_entry(journal_db):
    tj = journal_db
    tid = _open(tj)
    for _ in range(2):
        tj.record_event(trade_id=tid, event_type="TSL_MOVED", ts_ms=ms(11, 20), price=118.0,
                        data={"from": 96.0, "to": 118.0}, event_key="tsl:118.00")
    tj.record_event(trade_id=tid, event_type="TSL_MOVED", ts_ms=ms(11, 50), price=125.0,
                    data={"from": 118.0, "to": 125.0}, event_key="tsl:125.00")
    t = tj.get_trade(tid, [OWNER])
    assert [(m["from"], m["to"]) for m in t["tsl_history"]] == [(96.0, 118.0), (118.0, 125.0)]
    assert t["current_sl"] == 125.0
    assert "Stop was raised 2 times, last to 125.00 at 11:50." in t["narrative"]


def test_a_protection_event_supplies_the_initial_stop_when_the_entry_did_not(journal_db):
    tj = journal_db
    tid = _open(tj, initial_sl=None)
    tj.record_event(trade_id=tid, event_type="PROTECTION_PLACED", ts_ms=ms(10, 22), price=96.0, event_key="p")
    assert tj.get_trade(tid, [OWNER])["initial_sl"] == 96.0


def test_an_event_for_an_unknown_trade_is_dropped_not_invented(journal_db):
    tj = journal_db
    assert tj.record_event(trade_id="ST-nothing", event_type="NOTE", detail="x") is None
    assert tj.list_trades([OWNER])["total"] == 0


def test_exit_computes_gross_net_r_multiple_and_hold_time(journal_db):
    tj = journal_db
    tid = _open(tj)
    assert tj.record_exit(trade_id=tid, exit_price=150.0, qty=75, ts_ms=ms(12, 0), reason="target reached @ 150",
                          charges=60.0, charges_source="broker") == tid
    t = tj.get_trade(tid, [OWNER])
    assert t["status"] == "CLOSED" and t["exit_reason"] == "TARGET" and t["exit_reason_label"] == "Target reached"
    assert t["exit_price"] == 150.0 and t["exit_qty"] == 75
    assert t["gross_pnl"] == 2250.0                          # (150 - 120) x 75
    assert (t["charges"], t["charges_source"], t["net_pnl"]) == (60.0, "broker", 2190.0)
    assert t["r_multiple"] == 1.22                           # 2190 / ((120 - 96) x 75 = 1800)
    assert t["hold_minutes"] == 100.0                        # 10:20 -> 12:00
    assert t["trade_day"] == t["exit_day"] == "2026-10-12" and t["pnl"] == 2190.0
    assert t["narrative"].endswith("Sold at 150.00 on target, +₹2,190 after charges.")
    assert [e["type"] for e in t["timeline"]] == ["EXIT_FILLED"]


def test_an_exit_replayed_changes_nothing_and_never_double_counts(journal_db):
    tj = journal_db
    tid = _open(tj)
    for later in (ms(12, 0), ms(12, 5), ms(12, 9)):         # three reports of one fill, at three clocks
        tj.record_exit(trade_id=tid, exit_price=150.0, qty=75, ts_ms=later, reason="TARGET", charges=60.0)
    t = tj.get_trade(tid, [OWNER])
    assert t["gross_pnl"] == 2250.0 and t["exit_ts_ms"] == ms(12, 0)
    assert len(t["timeline"]) == 1


def test_an_entry_replayed_after_the_exit_does_not_rewrite_history(journal_db):
    tj = journal_db
    tid = _open(tj)
    tj.record_exit(trade_id=tid, exit_price=150.0, qty=75, ts_ms=ms(12, 0), reason="TARGET", charges=60.0)
    _open(tj, entry_price=999.0, qty=1)                      # a stale replay of the fill hook
    t = tj.get_trade(tid, [OWNER])
    assert t["status"] == "CLOSED" and t["entry_price"] == 120.0 and t["qty"] == 75 and t["net_pnl"] == 2190.0


def test_partial_exits_average_into_one_closed_trade(journal_db):
    tj = journal_db
    tid = _open(tj)
    tj.record_exit(trade_id=tid, exit_price=140.0, qty=25, ts_ms=ms(11, 0), partial=True, fill_key="x1",
                   reason="partial")
    assert tj.get_trade(tid, [OWNER])["status"] == "OPEN"
    tj.record_exit(trade_id=tid, exit_price=150.0, qty=50, ts_ms=ms(12, 0), reason="TARGET", fill_key="x2",
                   charges=0.0, charges_source="broker")
    t = tj.get_trade(tid, [OWNER])
    assert t["exit_qty"] == 75 and t["exit_price"] == 146.6667   # (25 x 140 + 50 x 150) / 75
    assert t["gross_pnl"] == 2000.0                              # 25 x 20 + 50 x 30
    assert [e["type"] for e in t["timeline"]] == ["PARTIAL_EXIT", "EXIT_FILLED"]


def test_create_only_never_touches_an_existing_trade(journal_db):
    tj = journal_db
    tid = _open(tj)
    tj.record_entry(uid=OWNER, strategy="supertrend", symbol="NIFTY25OCT25000CE", key="O1", create_only=True,
                    qty=1, entry_price=1.0)
    t = tj.get_trade(tid, [OWNER])
    assert (t["qty"], t["entry_price"]) == (75, 120.0)
    other = tj.record_entry(uid=OWNER, strategy="supertrend", symbol="NEW", key="N1", create_only=True, qty=5)
    assert tj.get_trade(other, [OWNER])["qty"] == 5 and tj.get_trade(other, [OWNER])["status"] == "PENDING"


def test_cumulative_order_fills_update_one_event_instead_of_adding_legs(journal_db):
    tj = journal_db
    tid = _open(tj)
    tj.record_exit(trade_id=tid, exit_price=140.0, qty=25, partial=True, fill_key="exit:X", cumulative=True)
    tj.record_exit(trade_id=tid, exit_price=146.6667, qty=75, fill_key="exit:X", cumulative=True, reason="TARGET",
                   charges=0.0)
    tj.record_exit(trade_id=tid, exit_price=140.0, qty=25, partial=True, fill_key="exit:X", cumulative=True)  # stale
    t = tj.get_trade(tid, [OWNER])
    assert [e["type"] for e in t["timeline"]] == ["EXIT_FILLED"] and t["timeline"][0]["qty"] == 75
    assert t["status"] == "CLOSED" and t["exit_qty"] == 75 and t["gross_pnl"] == 2000.0


def test_a_stop_that_trailed_is_tsl_and_one_that_never_moved_is_sl(journal_db):
    tj = journal_db
    a, b = _open(tj, key="A", entry_order_id="A"), _open(tj, key="B", entry_order_id="B")
    tj.record_exit(trade_id=a, exit_price=96.0, qty=75, reason="trail breach @ 96", trailed=False, charges=0.0)
    tj.record_exit(trade_id=b, exit_price=118.0, qty=75, reason="trail breach @ 118", trailed=True, charges=0.0)
    assert tj.get_trade(a, [OWNER])["exit_reason"] == "SL"
    assert tj.get_trade(b, [OWNER])["exit_reason"] == "TSL"
    assert tj.get_trade(a, [OWNER])["r_multiple"] == -1.0       # -1800 / 1800


def test_a_whole_trade_pnl_from_the_strategy_overrides_the_leg_arithmetic(journal_db):
    tj = journal_db
    tid = _open(tj)
    tj.record_exit(trade_id=tid, exit_price=150.0, qty=75, reason="TARGET", gross_pnl=2100.0, charges=100.0)
    t = tj.get_trade(tid, [OWNER])
    assert t["gross_pnl"] == 2100.0 and t["net_pnl"] == 2000.0


def test_charges_are_estimated_until_the_real_ones_arrive(journal_db):
    tj = journal_db
    tid = _open(tj)
    tj.record_exit(trade_id=tid, exit_price=150.0, qty=75, reason="TARGET")
    t = tj.get_trade(tid, [OWNER])
    assert t["charges_source"] == "estimate" and t["charges"] > 0
    assert t["net_pnl"] == round(2250.0 - t["charges"], 2)
    assert "after estimated charges" in t["narrative"]
    # the real figure replaces it
    tj.record_charges(trade_id=tid, charges=61.5, source="broker")
    t = tj.get_trade(tid, [OWNER])
    assert (t["charges"], t["charges_source"], t["net_pnl"]) == (61.5, "broker", 2188.5)
    assert "after charges" in t["narrative"] and "estimated" not in t["narrative"]


def test_estimate_agrees_with_the_virtual_exchange_charge_schedule(journal_db):
    tj = journal_db
    from app.services.simulation.charges import compute_charges
    buy = compute_charges(exchange="NFO", tradingsymbol="", side="BUY", quantity=75, price=120.0, instrument_type="CE")
    sell = compute_charges(exchange="NFO", tradingsymbol="", side="SELL", quantity=75, price=150.0, instrument_type="CE")
    assert tj.estimate_charges([
        {"kind": "option", "side": "BUY", "price": 120.0, "qty": 75},
        {"kind": "option", "side": "SELL", "price": 150.0, "qty": 75}]) == round(buy.total + sell.total, 2)
    assert tj.estimate_charges([{"kind": "option", "side": "BUY", "price": 0, "qty": 75}]) == 0.0


def test_per_order_charges_only_replace_the_estimate_once_entry_and_exit_are_both_known(journal_db):
    tj = journal_db
    tid = _open(tj)
    tj.record_exit(trade_id=tid, exit_price=150.0, qty=75, reason="TARGET")
    estimate = tj.get_trade(tid, [OWNER])["charges"]
    tj.record_charges(trade_id=tid, charges=30.0, order_key="entry:O1", source="broker")
    assert tj.get_trade(tid, [OWNER])["charges_source"] == "estimate"       # half a bill is not a bill
    assert tj.get_trade(tid, [OWNER])["charges"] == estimate
    tj.record_charges(trade_id=tid, charges=31.0, order_key="exit:O2", source="broker")
    tj.record_charges(trade_id=tid, charges=31.0, order_key="exit:O2", source="broker")   # replay
    t = tj.get_trade(tid, [OWNER])
    assert (t["charges"], t["charges_source"], t["net_pnl"]) == (61.0, "broker", 2189.0)


def test_mfe_and_mae_come_from_the_water_marks_the_strategy_tracked(journal_db):
    tj = journal_db
    tid = _open(tj, extra={"high_water": 160.0, "low_water": 110.0})
    tj.record_exit(trade_id=tid, exit_price=150.0, qty=75, reason="TARGET", charges=0.0)
    t = tj.get_trade(tid, [OWNER])
    assert t["mfe"] == 3000.0 and t["mae"] == 750.0         # (160 - 120) x 75 ; (120 - 110) x 75


def test_a_short_trade_profits_when_price_falls(journal_db):
    tj = journal_db
    tid = tj.record_entry(uid=OWNER, strategy="supertrend", symbol="NIFTYFUT", key="F1", status="OPEN",
                          side="SELL", option_type="FUT", underlying="NIFTY", qty=150, lots=2, lot_size=75,
                          entry_price=25000.0, entry_ts_ms=ms(9, 30), initial_sl=25100.0)
    tj.record_exit(trade_id=tid, exit_price=24900.0, qty=150, ts_ms=ms(13, 0), reason="red count exit",
                   charges=0.0)
    t = tj.get_trade(tid, [OWNER])
    assert t["gross_pnl"] == 15000.0 and t["r_multiple"] == 1.0     # risk (25100 - 25000) x 150


def test_exit_with_no_price_closes_the_trade_with_unknown_pnl(journal_db):
    tj = journal_db
    tid = _open(tj)
    tj.record_exit(trade_id=tid, exit_price=None, qty=None, reason="reconciled closed at broker (exit price unknown)")
    t = tj.get_trade(tid, [OWNER])
    assert t["status"] == "CLOSED" and t["exit_reason"] == "RECONCILED"
    assert t["gross_pnl"] is None and t["net_pnl"] is None and t["pnl"] is None


def test_a_rejected_entry_is_marked_and_carries_the_reason(journal_db):
    tj = journal_db
    tid = tj.record_entry(uid=OWNER, strategy="supertrend", symbol="S", key="O9", status="PENDING", qty=75)
    tj.record_event(trade_id=tid, event_type="REJECTED", detail="Order rejected", data={"reason": "RMS: margin"},
                    event_key="rejected:O9")
    t = tj.get_trade(tid, [OWNER])
    assert t["status"] == "REJECTED" and t["extra"]["reject_reason"] == "RMS: margin"
    assert t["narrative"].endswith("The order was not filled (RMS: margin).")


def test_failure_notes_land_on_the_timeline(journal_db):
    tj = journal_db
    tid = _open(tj)
    tj.record_event(trade_id=tid, event_type="FAILED", detail="Protective stop could not be confirmed",
                    ts_ms=ms(10, 22))
    assert tj.get_trade(tid, [OWNER])["timeline"][0]["type"] == "FAILED"


# ----------------------------------------------------------------------------------- listing

def _book(tj):
    """Three closed trades (one win, two losses), one open, across two days and a sim tenant."""
    plan = [("A", "supertrend", "NIFTY25OCT25000CE", 120.0, 150.0, ms(12, 0, 12)),
            ("B", "gamma", "NIFTY25OCT25100CE", 100.0, 90.0, ms(13, 0, 12)),
            ("C", "snapback", "BANKNIFTY25NOV52000PE", 300.0, 250.0, ms(11, 0, 13))]
    for key, strategy, symbol, entry, exit_, when in plan:
        tid = tj.record_entry(uid=OWNER, strategy=strategy, symbol=symbol, key=key, status="OPEN", qty=75,
                              entry_price=entry, entry_ts_ms=when - 3_600_000, underlying=symbol[:5])
        tj.record_exit(trade_id=tid, exit_price=exit_, qty=75, ts_ms=when, reason="TARGET" if exit_ > entry else "SL",
                       charges=0.0)
    tj.record_entry(uid=OWNER, strategy="supertrend", symbol="NIFTY25OCT25200CE", key="D", status="OPEN", qty=75,
                    entry_price=80.0, entry_ts_ms=ms(14, 0, 13), underlying="NIFTY")
    _open(tj, uid=SIM, key="S1")


def test_listing_filters_sorts_and_pages(journal_db):
    tj = journal_db
    _book(tj)
    ids = lambda **kw: [t["symbol"] for t in tj.list_trades([OWNER], **kw)["items"]]  # noqa: E731
    assert tj.list_trades([OWNER])["total"] == 4                          # the sim trade is not in LIVE scope
    assert ids(strategy="gamma") == ["NIFTY25OCT25100CE"]
    assert ids(outcome="win") == ["NIFTY25OCT25000CE"]
    assert sorted(ids(outcome="loss")) == ["BANKNIFTY25NOV52000PE", "NIFTY25OCT25100CE"]
    assert ids(outcome="open") == ["NIFTY25OCT25200CE"]
    assert ids(date_from="2026-10-13", date_to="2026-10-13") == ["NIFTY25OCT25200CE", "BANKNIFTY25NOV52000PE"]
    assert sorted(ids(symbol="banknifty")) == ["BANKNIFTY25NOV52000PE"]
    assert ids(sort="pnl_desc")[0] == "NIFTY25OCT25000CE" and ids(sort="pnl_asc")[0] == "BANKNIFTY25NOV52000PE"
    assert ids(sort="oldest")[0] == "NIFTY25OCT25000CE" and ids(sort="newest")[0] == "NIFTY25OCT25200CE"
    page = tj.list_trades([OWNER], limit=2, offset=2, sort="oldest")
    assert page["total"] == 4 and len(page["items"]) == 2 and page["offset"] == 2
    with pytest.raises(ValueError):
        tj.list_trades([OWNER], sort="by-mood")
    with pytest.raises(ValueError):
        tj.list_trades([OWNER], outcome="maybe")


def test_listing_with_no_authorised_tenant_is_empty(journal_db):
    tj = journal_db
    _book(tj)
    assert tj.list_trades([])["total"] == 0 and tj.rows_for_stats([]) == [] and tj.list_skips([])["total"] == 0


def test_get_trade_refuses_another_tenants_trade(journal_db):
    tj = journal_db
    tid = _open(tj, uid=SIM, key="S1")
    assert tj.get_trade(tid, [OWNER]) is None
    assert tj.get_trade(tid, [SIM])["mode"] == "SIMULATION"


def test_purge_simulation_clears_only_the_sim_tenant_and_refuses_a_live_uid(journal_db):
    tj = journal_db
    live, sim = _open(tj), _open(tj, uid=SIM, key="S1")
    tj.record_event(trade_id=sim, event_type="NOTE", detail="n", event_key="n")
    tj.record_skip(uid=SIM, strategy="gamma", code="auto_off", signal_id="s")
    assert tj.purge_simulation(OWNER) == 0 and tj.get_trade(live, [OWNER]) is not None   # refused, nothing deleted
    assert tj.purge_simulation(SIM) == 1
    assert tj.get_trade(sim, [SIM]) is None and tj.list_skips([SIM])["total"] == 0
    assert tj.get_trade(live, [OWNER]) is not None


# -------------------------------------------------------------------------------------- skips

def test_a_skip_is_recorded_once_per_signal_and_code_per_day(journal_db):
    tj = journal_db
    kw = dict(uid=OWNER, strategy="supertrend", symbol="NIFTY", signal_id="spot:1:long", underlying="NIFTY")
    assert tj.record_skip(code="daily_loss_halt", reason="Daily loss reached.", **kw) is True
    for _ in range(5):
        assert tj.record_skip(code="daily_loss_halt", **kw) is False          # throttled: not even a write
    assert tj.record_skip(code="adx_below_min", **kw) is True                 # another code is another row
    assert tj.record_skip(code="daily_loss_halt", **{**kw, "signal_id": "spot:2:long"}) is True
    page = tj.list_skips([OWNER])
    assert page["total"] == 3
    assert {c["code"]: c["signals"] for c in page["by_code"]} == {"daily_loss_halt": 2, "adx_below_min": 1}
    first = next(i for i in page["items"] if i["code"] == "daily_loss_halt" and i["signal_id"] == "spot:1:long")
    assert first["reason"] == "Daily loss reached." and first["count"] == 1 and first["mode"] == "LIVE"


def test_the_counter_ticks_when_the_same_refusal_returns_later(journal_db, monkeypatch):
    tj = journal_db
    monkeypatch.setattr(tj, "SKIP_REWRITE_EVERY_S", 0.0)
    kw = dict(uid=OWNER, strategy="gamma", signal_id="g1", code="auto_off")
    tj.record_skip(**kw)
    tj.record_skip(**kw)
    tj.record_skip(**kw)
    item = tj.list_skips([OWNER])["items"][0]
    assert item["count"] == 3 and tj.list_skips([OWNER])["total"] == 1
    assert item["reason"] == "Automatic trading is off for this strategy."     # default sentence for the code


def test_skips_are_scoped_filtered_and_paged(journal_db):
    tj = journal_db
    tj.record_skip(uid=OWNER, strategy="gamma", code="auto_off", signal_id="1", ts_ms=ms(10, 0, 12))
    tj.record_skip(uid=OWNER, strategy="snapback", code="kill_switch", signal_id="2", ts_ms=ms(10, 0, 13))
    tj.record_skip(uid=SIM, strategy="gamma", code="auto_off", signal_id="3", ts_ms=ms(10, 0, 13))
    assert tj.list_skips([OWNER])["total"] == 2 and tj.list_skips([SIM])["items"][0]["mode"] == "SIMULATION"
    assert [i["code"] for i in tj.list_skips([OWNER], strategy="snapback")["items"]] == ["kill_switch"]
    assert [i["code"] for i in tj.list_skips([OWNER], date_to="2026-10-12")["items"]] == ["auto_off"]
    assert tj.list_skips([OWNER, SIM], limit=1)["total"] == 3 and len(tj.list_skips([OWNER, SIM], limit=1)["items"]) == 1


# ------------------------------------------------------------------------ never into trading

def test_no_recording_function_raises_when_the_database_is_unavailable(journal_db, monkeypatch):
    tj = journal_db
    from app.services import db
    monkeypatch.setattr(db, "_available", False)
    assert tj.record_entry(uid=OWNER, strategy="gamma", symbol="S", key="k") is None
    assert tj.record_event(event_type="NOTE", uid=OWNER, strategy="gamma", symbol="S", key="k") is None
    assert tj.record_exit(uid=OWNER, strategy="gamma", symbol="S", key="k", exit_price=1.0, qty=1) is None
    assert tj.record_charges(uid=OWNER, strategy="gamma", symbol="S", key="k", charges=1.0) is None
    assert tj.record_skip(uid=OWNER, strategy="gamma", code="x") is False
    assert tj.purge_simulation(SIM) == 0


def test_no_recording_function_raises_on_garbage_input_or_a_broken_store(journal_db, monkeypatch):
    tj = journal_db
    assert tj.record_entry(uid=OWNER, strategy="not-a-strategy", symbol="S", key="k") is None
    assert tj.record_entry(uid=OWNER, strategy="gamma", symbol="S", key="k", entry_price=object(), qty="many") is not None
    tj.record_exit(uid=OWNER, strategy="gamma", symbol="S", key="k", exit_price="x", qty="y", reason=object())

    def boom():
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(tj, "_write", boom)
    assert tj.record_entry(uid=OWNER, strategy="gamma", symbol="S", key="k2") is None
    assert tj.record_skip(uid=OWNER, strategy="gamma", code="x", signal_id="boom") is False


def test_a_failing_journal_logs_once_not_on_every_call(journal_db, monkeypatch, caplog):
    tj = journal_db
    monkeypatch.setattr(tj, "_write", lambda: (_ for _ in ()).throw(RuntimeError("down")))
    caplog.clear()
    for _ in range(20):
        tj.record_entry(uid=OWNER, strategy="gamma", symbol="S", key="k")
    assert len([r for r in caplog.records if "trade journal" in r.getMessage()]) == 1
