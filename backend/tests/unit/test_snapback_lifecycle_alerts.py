"""Snapback tells the owner when a position opens, trails and closes.

It used to be silent: only operational faults reached Telegram, so a stop-out or a
15-session time exit happened with nobody told. The numbers in each alert must be the
ledger's, in the shared vocabulary (Entry, SL, TSL, Exit, P&L, Qty, Capital).
"""
from __future__ import annotations

import os
import tempfile
from datetime import date, datetime, timedelta, timezone

import pytest

from app.engines.snapback.config import SnapbackConfig
from app.services import snapback_lifecycle_alerts as alerts
from app.services.snapback_eod import finalize_eod_session
from app.services.snapback_instrument_identity import identity_from_instrument
from app.services.snapback_observation_warehouse import SnapbackObservationWarehouse

_IST = timezone(timedelta(hours=5, minutes=30))
OPT_SYM = "NIFTY26NOV25000PE"
FUT_SYM = "NIFTY26NOVFUT"
ENTRY_DAY = datetime(2026, 10, 1, 9, 20, tzinfo=_IST)


@pytest.fixture
def sent(monkeypatch):
    out = []

    def fake(uid, **kw):
        out.append({"uid": uid, **kw})
        return "TGL-x"

    monkeypatch.setattr(
        "app.services.notifications.telegram_lifecycle.enqueue_best_effort", fake)
    return out


@pytest.fixture
def warehouse():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as fh:
        path = fh.name
    wh = SnapbackObservationWarehouse(db_path=path)
    yield wh
    if os.path.exists(path):
        os.remove(path)


def _open(wh, opp="OPP-1", *, entry=100.0, qty=25, runner_peak=None):
    wh.record_opportunity(
        opportunity_id=opp, symbol="NIFTY", signal_type="SNAPBACK_FADE_UP",
        spot_price=24500.0, source="PROSPECTIVE_PAPER",
        identity=identity_from_instrument(
            {"tradingsymbol": "NIFTY 50", "instrument_token": 256265, "exchange": "NSE",
             "name": "NIFTY"}, canonical_symbol="NIFTY"),
    )
    wh.save_paper_position(
        opportunity_id=opp, symbol="NIFTY", option_symbol=OPT_SYM, option_qty=qty,
        option_entry_price=entry, option_expiry="2026-11-15", option_strike=25000.0,
        futures_symbol=FUT_SYM, futures_lot_size=25, current_futures_lots=1,
        avg_futures_entry_price=24500.0, realized_futures_pnl=0.0, entry_spot=24500.0,
        entry_timestamp=ENTRY_DAY.isoformat(), entry_dte=45, entry_iv=0.2, causal_beta=1.0,
    )
    if runner_peak is not None:
        wh.update_paper_position_peak_bid(opp, runner_peak, 1)
    return opp


def _marks(wh, opp, session, *, option_bid, fut_bid=24600.0, spot=24550.0):
    at = datetime(session.year, session.month, session.day, 15, 29, 45, tzinfo=_IST)
    for leg, bid, price, contract in (
        ("OPTION", option_bid, None, OPT_SYM), ("FUTURES", fut_bid, None, FUT_SYM),
        ("UNDERLYING", None, spot, "NIFTY"),
    ):
        wh.record_quote_quality_event(
            event_id=f"QE-{opp}-{leg}", opportunity_id=opp, phase="EOD_MARK", leg=leg,
            contract_id=contract, required_for_economics=1, quote_present=1, bid=bid,
            ask=(bid + 2.0) if bid is not None else None, age_ms=100, accepted=1,
            reason_codes="", provider_timestamp=at.isoformat(), symbol="NIFTY",
            visible_quantity=100, execution_price=price)


def _finalise(wh, session):
    return finalize_eod_session(
        cfg=SnapbackConfig(enabled=True), warehouse=wh, session_date=session,
        finalized_at=datetime(session.year, session.month, session.day, 15, 31, tzinfo=_IST))


# ------------------------------------------------------------------- producers

def test_entry_alert_states_the_fill_the_stop_derived_from_it_and_the_capital(sent):
    assert alerts.entry_filled(
        opportunity_id="OPP-1", option_symbol=OPT_SYM, fill_price=100.0, qty=25,
        stop_pct=35.0, hold_days=15, futures_symbol=FUT_SYM, hedge_lots=2,
        futures_fill_price=24601.0)
    ev = sent[0]
    assert ev["strategy"] == "Snapback" and ev["event_type"] == "ENTRY FILLED" and ev["title"] == OPT_SYM
    assert ev["dedupe_key"] == "snapback:OPP-1:ENTRY"
    d = ev["detail"]
    assert "Entry ₹100.00" in d and "SL ₹65.00" in d
    assert "Qty 25" in d and "Capital ₹2,500.00" in d
    assert "long 2 lot(s)" in d and "15 sessions" in d


def test_a_loss_is_signed_and_names_the_stop_that_closed_it(sent):
    alerts.position_closed(
        opportunity_id="OPP-1", option_symbol=OPT_SYM, reason="PREMIUM_STOP",
        entry_price=100.0, exit_price=60.0, qty=25, option_pnl=-1000.0, futures_pnl=250.0,
        costs=40.0, net_pnl=-790.0)
    ev = sent[0]
    assert ev["event_type"] == "POSITION CLOSED" and ev["severity"] == "WARNING"
    d = ev["detail"]
    assert "Exit ₹60.00" in d and "P&L -₹790.00" in d and "Stop loss hit" in d
    assert "Option -₹1,000.00" in d and "Hedge +₹250.00" in d and "Costs -₹40.00" in d


@pytest.mark.parametrize("reason,words,severity", [
    ("RUNNER_TRAIL_STOP", "Trailing stop hit", "WARNING"),
    ("HOLDING_HORIZON_EXPIRED", "Holding period ended", "INFO"),
    ("EXPIRY_APPROACHING", "Closed ahead of expiry", "INFO")])
def test_every_exit_reason_is_a_closed_position_with_its_own_reason(sent, reason, words, severity):
    alerts.position_closed(
        opportunity_id="O", option_symbol=OPT_SYM, reason=reason, entry_price=100.0,
        exit_price=120.0, qty=25, option_pnl=500.0, futures_pnl=0.0, costs=10.0, net_pnl=490.0)
    assert sent[0]["event_type"] == "POSITION CLOSED"
    assert f"Reason {words}" in sent[0]["detail"] and sent[0]["severity"] == severity


def test_a_trail_that_did_not_rise_is_not_reported(sent):
    assert not alerts.tsl_moved(opportunity_id="O", option_symbol=OPT_SYM, session_date="2026-10-20",
                                old_peak=200.0, new_peak=200.0, trail_pct=25.0, qty=25)
    assert not alerts.tsl_moved(opportunity_id="O", option_symbol=OPT_SYM, session_date="2026-10-20",
                                old_peak=200.0, new_peak=180.0, trail_pct=25.0, qty=25)
    assert sent == []
    assert alerts.tsl_moved(opportunity_id="O", option_symbol=OPT_SYM, session_date="2026-10-21",
                            old_peak=200.0, new_peak=220.0, trail_pct=25.0, qty=25)
    assert "TSL ₹150.00 → ₹165.00" in sent[0]["detail"]


def test_a_broken_outbox_never_reaches_the_trading_path(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("outbox down")

    monkeypatch.setattr("app.services.notifications.telegram_lifecycle.enqueue_best_effort", boom)
    assert alerts.entry_filled(opportunity_id="O", option_symbol=OPT_SYM, fill_price=100.0,
                               qty=25, stop_pct=35, hold_days=15) is False
    assert alerts.entry_filled(opportunity_id="O", option_symbol=OPT_SYM, fill_price=None,
                               qty=None, stop_pct=35, hold_days=15) is False


def test_alerts_are_addressed_to_the_family_owner(sent, monkeypatch):
    monkeypatch.setenv("STERLING_FAMILY_USER_ID", "owner-7")
    monkeypatch.setenv("STERLING_FAMILY_ACCOUNT_ID", "AB1234")
    alerts.entry_filled(opportunity_id="O", option_symbol=OPT_SYM, fill_price=100.0, qty=25,
                        stop_pct=35, hold_days=15)
    assert sent[0]["uid"] == "owner-7"


# --------------------------------------------- driven by the real ledger paths

def test_the_end_of_day_stop_out_sends_one_exit_alert_with_the_ledger_pnl(warehouse, sent):
    opp = _open(warehouse)
    session = date(2026, 10, 6)
    _marks(warehouse, opp, session, option_bid=60.0)
    _finalise(warehouse, session)

    exits = [e for e in sent if e["dedupe_key"].endswith(":EXIT")]
    assert len(exits) == 1 and exits[0]["event_type"] == "POSITION CLOSED"
    outcome = warehouse.get_records_by_table("outcomes", opportunity_id=opp)[0]
    assert f"{float(outcome['actual_total_pnl']):,.2f}".lstrip("-") in exits[0]["detail"]
    assert "Entry ₹100.00" in exits[0]["detail"] and "Exit ₹60.00" in exits[0]["detail"]


def test_a_new_runner_peak_after_the_horizon_reports_the_raised_trail(warehouse, sent):
    opp = _open(warehouse, runner_peak=200.0)
    session = date(2026, 10, 23)
    _marks(warehouse, opp, session, option_bid=230.0)
    _finalise(warehouse, session)
    moved = [e for e in sent if e["event_type"] == "TSL UPDATED"]
    assert len(moved) == 1
    assert "TSL ₹150.00 → ₹172.50" in moved[0]["detail"]
    assert not [e for e in sent if e["event_type"] == "POSITION CLOSED"]


def test_the_winner_that_becomes_a_runner_gets_its_first_trail(warehouse, sent):
    opp = _open(warehouse)
    session = date(2026, 10, 23)
    _marks(warehouse, opp, session, option_bid=160.0)
    _finalise(warehouse, session)
    armed = [e for e in sent if e["event_type"] == "TSL ARMED"]
    assert len(armed) == 1 and "TSL ₹120.00" in armed[0]["detail"]
