"""The Trade Journal through the offline broker: real engines, real hooks, real API.

A Supertrend, a Gamma and a Snapback trade each run entry -> protection -> broker stop-out exactly as in
``test_admission_dry_run`` and must leave one complete, correct journal row; a restart must not duplicate
anything; a refused signal must leave a deduplicated skip; and the numbers must read back through
``/api/v1/journal/*``.
"""
from __future__ import annotations

import pytest

from tests.e2e_live import scenarios as S
from tests.e2e_live.machine import UID
from tests.e2e_live.test_admission_dry_run import _explain, _fresh_live_machine


def _journal(machine):
    return machine.m("app.services.trade_journal")


def _trades(machine, **filters):
    return _journal(machine).list_trades([UID], **filters)["items"]


def _detail(machine, trade):
    return _journal(machine).get_trade(trade["trade_id"], [UID])


def _types(detail):
    return [e["type"] for e in detail["timeline"]]


# ----------------------------------------------------------------------------- one per strategy

@pytest.mark.asyncio
async def test_a_supertrend_trade_is_journalled_from_signal_to_stop_out(machine):
    await _fresh_live_machine(machine)
    S.operator_sets_daily_loss(machine)
    S.configure_supertrend(machine)
    first = await S.drive_supertrend(machine)
    assert first.ordered and first.protected, _explain(first)

    (open_trade,) = _trades(machine)                       # while it is open: a complete OPEN row
    assert open_trade["status"] == "OPEN" and open_trade["mode"] == "LIVE" and open_trade["strategy"] == "supertrend"
    assert open_trade["symbol"] == S.SUPERTREND_SYMBOL and open_trade["qty"] == 75 and open_trade["lots"] == 1
    assert open_trade["entry_price"] and open_trade["initial_sl"] and open_trade["initial_sl"] < open_trade["entry_price"]
    assert open_trade["signal_context"]["aligned"] == 3 and open_trade["signal_context"]["adx"] == 30.0
    assert open_trade["narrative"].endswith("Still open.")

    await S.stop_out(machine, S.SUPERTREND_SYMBOL)
    (trade,) = _trades(machine)
    assert trade["trade_id"] == open_trade["trade_id"]    # the same row, now closed
    d = _detail(machine, trade)
    assert d["status"] == "CLOSED" and d["exit_reason"] == "SL" and d["exit_price"] < d["entry_price"]
    qty = d["qty"]
    assert d["gross_pnl"] == pytest.approx((d["exit_price"] - d["entry_price"]) * qty, abs=0.01)
    assert d["charges"] > 0 and d["net_pnl"] == pytest.approx(d["gross_pnl"] - d["charges"], abs=0.01)
    assert d["net_pnl"] < 0 and d["r_multiple"] < 0 and d["capital_used"] == pytest.approx(d["entry_price"] * qty, abs=0.01)
    assert _types(d) == ["SIGNAL", "ORDER_SUBMITTED", "FILLED", "PROTECTION_PLACED", "EXIT_FILLED"]
    assert d["narrative"].startswith("Bought 1 lot NIFTY 25000 CE at") and "when the stop-loss was hit" in d["narrative"]


@pytest.mark.asyncio
async def test_a_gamma_trade_is_journalled_from_signal_to_stop_out(machine):
    await _fresh_live_machine(machine)
    S.operator_sets_daily_loss(machine)
    S.configure_gamma(machine)
    first = await S.drive_gamma(machine)
    assert first.ordered and first.protected, _explain(first)
    await S.stop_out(machine, S.GAMMA_SYMBOL)
    await machine.m("app.services.gamma_move_runner").reconcile(UID)

    (trade,) = _trades(machine)
    d = _detail(machine, trade)
    assert (d["strategy"], d["status"], d["mode"]) == ("gamma", "CLOSED", "LIVE")
    assert d["signal_id"] == "gamma-e1-nifty-25000ce-1" and d["entry_price"] == 120.0 and d["initial_sl"] == 98.0
    assert d["exit_reason"] == "SL" and d["exit_price"] == 96.0 and d["gross_pnl"] == -1800.0
    assert d["signal_context"]["tier"] == "ENTRY" and d["signal_context"]["oi_drop_pct"] == 9.5
    assert d["signal_context"]["gates_passed"] == ["zone", "liquidity", "direction", "unwind"]
    assert _types(d) == ["SIGNAL", "ORDER_SUBMITTED", "FILLED", "PROTECTION_PLACED", "EXIT_FILLED"]
    assert "Gamma Unwind E1 entry signal" in d["narrative"]


@pytest.mark.asyncio
async def test_a_snapback_trade_is_journalled_with_its_hedge_from_signal_to_stop_out(machine):
    await _fresh_live_machine(machine)
    S.operator_sets_daily_loss(machine)
    S.configure_snapback(machine)
    first = await S.drive_snapback(machine)
    assert first.ordered and first.protected, _explain(first)
    machine.exchange.set_quote(f"NFO:{S.SNAPBACK_OPTION}", 190.0, bid=189.5, ask=190.5)
    await S.stop_out(machine, S.SNAPBACK_OPTION)
    dispatcher = machine.m("app.services.snapback_dispatcher").get_dispatcher()
    for _ in range(3):
        await dispatcher.cycle(UID)

    (trade,) = _trades(machine)
    d = _detail(machine, trade)
    assert (d["strategy"], d["status"], d["mode"]) == ("snapback", "CLOSED", "LIVE")
    assert d["symbol"] == S.SNAPBACK_OPTION and d["entry_price"] == 300.0 and d["initial_sl"] == 195.0
    assert d["exit_reason"] == "SL" and d["exit_price"] < 300.0
    assert d["extra"]["hedge"]["symbol"] == S.SNAPBACK_FUTURE and d["extra"]["hedge"]["exit_price"] > 0
    assert d["signal_context"]["lane"] == "swing" and d["signal_context"]["stretch"] == 2.1
    assert d["signal_context"]["strength"] == "STRONG" and d["signal_context"]["hedge"]["symbol"] == S.SNAPBACK_FUTURE
    assert d["capital_used"] == pytest.approx(157612.5)           # premium cash + hedge margin of the plan
    assert d["net_pnl"] < 0 and d["charges"] > 0
    assert _types(d)[0] == "SIGNAL" and set(_types(d)) >= {"FILLED", "HEDGE_FILLED", "PROTECTION_PLACED", "EXIT_SUBMITTED", "EXIT_FILLED"}
    assert "hedged with" in d["narrative"] and "when the stop-loss was hit" in d["narrative"]


# ------------------------------------------------------------------------------------- restart

@pytest.mark.asyncio
@pytest.mark.parametrize("strategy", ["supertrend", "gamma", "snapback"])
async def test_a_restart_and_a_replayed_signal_leave_the_journal_unchanged(machine, strategy):
    await _fresh_live_machine(machine)
    configure = {"supertrend": S.configure_supertrend, "gamma": S.configure_gamma,
                 "snapback": S.configure_snapback}[strategy]
    drive = {"supertrend": S.drive_supertrend, "gamma": S.drive_gamma, "snapback": S.drive_snapback}[strategy]
    configure(machine)
    first = await drive(machine)
    assert first.ordered and first.protected, _explain(first)
    (before,) = _trades(machine)
    timeline_before = _types(_detail(machine, before))

    await machine.restart()
    await machine.run_live_runtime_once()
    await drive(machine)                                         # the same signal is seen again

    (after,) = _trades(machine)
    assert after["trade_id"] == before["trade_id"]
    assert _types(_detail(machine, after)) == timeline_before, "a restart added or lost journal events"
    assert after["entry_price"] == before["entry_price"] and after["status"] == "OPEN"


# --------------------------------------------------------------------------------- through the API

@pytest.mark.asyncio
async def test_the_trade_reads_back_through_the_journal_api(machine):
    await _fresh_live_machine(machine)
    S.operator_sets_daily_loss(machine)
    S.configure_supertrend(machine)
    first = await S.drive_supertrend(machine)
    assert first.ordered and first.protected, _explain(first)
    await S.stop_out(machine, S.SUPERTREND_SYMBOL)

    listing = S.http(machine, "GET", "/journal/trades")
    assert listing.status_code == 200, listing.text
    body = listing.json()
    assert body["mode"] == "LIVE" and body["total"] == 1
    tid = body["items"][0]["trade_id"]
    one = S.http(machine, "GET", f"/journal/trades/{tid}").json()["trade"]
    assert one["status"] == "CLOSED" and [e["type"] for e in one["timeline"]][0] == "SIGNAL"
    summary = S.http(machine, "GET", "/journal/summary").json()["summary"]
    assert summary["trades"] == 1 and summary["losses"] == 1 and summary["net_pnl"] == one["net_pnl"]
    reasons = S.http(machine, "GET", "/journal/exit-reasons").json()["reasons"]
    assert [(r["reason"], r["count"]) for r in reasons] == [("SL", 1)]
    by = {g["strategy"]: g for g in S.http(machine, "GET", "/journal/by-strategy").json()["strategies"]}
    assert by["supertrend"]["trades"] == 1 and by["gamma"]["trades"] == 0 and by["supertrend"]["rank"] == 1
    # the simulation tenant has nothing, and the live view cannot be talked into reading it
    sim = S.http(machine, "GET", "/journal/trades?mode=simulation").json()
    assert sim["mode"] == "SIMULATION" and sim["total"] == 0
    assert S.http(machine, "GET", "/journal/trades?mode=all").json()["total"] == 1


# ------------------------------------------------------------------------------------- refusals

@pytest.mark.asyncio
async def test_a_refused_supertrend_signal_is_a_skip_recorded_once(machine):
    await _fresh_live_machine(machine)
    S.configure_supertrend(machine)                              # the shipped daily-loss default (-1,500)
    first = await S.drive_supertrend(machine)
    assert first.ordered and first.protected, _explain(first)
    await S.stop_out(machine, S.SUPERTREND_SYMBOL)               # ~-6,500: the day's loss limit is now breached
    await machine.refresh_exposure()
    assert not machine.admission()["allowed"]

    ts = S.supertrend_bar_ms() + 1
    for _ in range(3):                                           # the same signal is re-decided on every pass
        refused = await S.drive_supertrend(machine, signal_ts_ms=ts)
        assert not refused.ordered
    skips = _journal(machine).list_skips([UID])
    assert skips["total"] == 1, skips["items"]
    item = skips["items"][0]
    assert item["strategy"] == "supertrend" and item["signal_id"] == f"spot:{ts}:long" and item["mode"] == "LIVE"
    assert item["code"] and item["reason"] and item["context"]["detail"]
    assert len(_trades(machine)) == 1                            # the refused signal made no trade


@pytest.mark.asyncio
async def test_a_refused_gamma_signal_is_a_skip_with_the_strategys_own_code(machine):
    await _fresh_live_machine(machine)
    S.configure_gamma(machine, stop_mode="monitor")             # live intraday needs a broker-held stop
    refused = await S.drive_gamma(machine)
    assert not refused.ordered, _explain(refused)
    skips = _journal(machine).list_skips([UID])
    assert [(i["code"], i["strategy"]) for i in skips["items"]] == [("live_intraday_broker_protection_required", "gamma")]
    assert skips["items"][0]["reason"] == "Live intraday entries need a broker-held stop."
    assert skips["items"][0]["signal_id"] == "gamma-e1-nifty-25000ce-1" and _trades(machine) == []
