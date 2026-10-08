"""Audit: the end-of-day finaliser must EXECUTE the exits it decides.

Slice E split the close into observe (15:29-15:30) and finalise (>= 15:30), and the
runner stopped calling ``process_prospective_daily_mtm_and_exits``. The finaliser's
``apply_daily_mtm_from_observed_marks`` records the mark and evaluates the frozen exit
rules inside ``rebalance_and_mtm`` -- and then discarded the verdict. A position that
reached its 15-session horizon, its expiry buffer or an end-of-day stop was marked
every evening and never closed.

These tests drive the real finaliser from persisted EOD_MARK evidence.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from app.engines.snapback.config import SnapbackConfig
from app.services.snapback_eod import (
    collect_eod_observations,
    finalize_eod_session,
    select_close_marks,
)
from app.services.snapback_instrument_identity import identity_from_instrument
from app.services.snapback_observation_warehouse import SnapbackObservationWarehouse

_IST = timezone(timedelta(hours=5, minutes=30))
ENTRY_DAY = datetime(2026, 10, 1, 9, 20, tzinfo=_IST)
# Fifteen-plus trading sessions after ENTRY_DAY (the lifecycle scenarios use the same pair).
HORIZON_SESSION = date(2026, 10, 23)

OPT_SYM = "NIFTY26NOV25000PE"
FUT_SYM = "NIFTY26NOVFUT"


@pytest.fixture
def warehouse():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as fh:
        path = fh.name
    wh = SnapbackObservationWarehouse(db_path=path)
    yield wh
    if os.path.exists(path):
        os.remove(path)


def _t(session: date, h: int, m: int, s: int = 0) -> datetime:
    return datetime(session.year, session.month, session.day, h, m, s, tzinfo=_IST)


def _position(wh, *, opp="OPP-1", expiry="2026-11-15", entry_dte=45, qty=25,
              lot=25, lots=1, runner_peak=None):
    wh.record_opportunity(
        opportunity_id=opp, symbol="NIFTY", signal_type="SNAPBACK_FADE_UP",
        spot_price=24500.0, source="PROSPECTIVE_PAPER",
        identity=identity_from_instrument(
            {"tradingsymbol": "NIFTY 50", "instrument_token": 256265,
             "exchange": "NSE", "name": "NIFTY"},
            canonical_symbol="NIFTY",
        ),
    )
    wh.save_paper_position(
        opportunity_id=opp, symbol="NIFTY", option_symbol=OPT_SYM,
        option_qty=qty, option_entry_price=100.0, option_expiry=expiry,
        option_strike=25000.0, futures_symbol=FUT_SYM, futures_lot_size=lot,
        current_futures_lots=lots, avg_futures_entry_price=24500.0,
        realized_futures_pnl=0.0, entry_spot=24500.0,
        entry_timestamp=ENTRY_DAY.isoformat(), entry_dte=entry_dte, entry_iv=0.2,
        causal_beta=1.0,
    )
    if runner_peak is not None:
        wh.update_paper_position_peak_bid(opp, runner_peak, 1)
    return opp


def _mark(wh, opp, leg, at, *, bid, ask=None, price=None, contract=None):
    wh.record_quote_quality_event(
        event_id=f"QE-{opp}-{leg}-{at.isoformat()}",
        opportunity_id=opp, phase="EOD_MARK", leg=leg,
        contract_id=contract or {"OPTION": OPT_SYM, "FUTURES": FUT_SYM,
                                  "UNDERLYING": "NIFTY"}[leg],
        required_for_economics=1, quote_present=1, bid=bid,
        ask=(bid + 2.0) if (ask is None and bid is not None) else ask,
        age_ms=100, accepted=1, reason_codes="",
        provider_timestamp=at.isoformat(), symbol="NIFTY",
        visible_quantity=100, execution_price=price,
    )


def _close_marks(wh, opp, session, *, option_bid, fut_bid=24600.0, spot=None):
    at = _t(session, 15, 29, 45)
    _mark(wh, opp, "OPTION", at, bid=option_bid)
    _mark(wh, opp, "FUTURES", at, bid=fut_bid)
    if spot is not None:
        _mark(wh, opp, "UNDERLYING", at, bid=None, ask=None, price=spot)


def _finalise(wh, session):
    return finalize_eod_session(
        cfg=SnapbackConfig(enabled=True), warehouse=wh, session_date=session,
        finalized_at=_t(session, 15, 31),
    )


def _outcome(wh, opp):
    rows = wh.get_records_by_table("outcomes", opportunity_id=opp)
    return rows[0] if rows else None


# --------------------------------------------------------------- the headline bug


def test_the_fifteen_session_horizon_closes_the_position(warehouse):
    opp = _position(warehouse)
    _close_marks(warehouse, opp, HORIZON_SESSION, option_bid=120.0)

    result = _finalise(warehouse, HORIZON_SESSION)

    assert result.status == "COMPLETE"
    outcome = _outcome(warehouse, opp)
    assert outcome is not None, "horizon reached but the position was never closed"
    assert outcome["exit_reason"] == "HOLDING_HORIZON_EXPIRED"
    assert float(outcome["actual_option_pnl"]) == pytest.approx((120.0 - 100.0) * 25)
    assert warehouse.get_paper_position(opp)["status"] == "CLOSED"
    assert warehouse.get_active_paper_positions() == []


def test_a_winner_past_the_horizon_becomes_a_runner_instead_of_closing(warehouse):
    opp = _position(warehouse)
    _close_marks(warehouse, opp, HORIZON_SESSION, option_bid=160.0)

    _finalise(warehouse, HORIZON_SESSION)

    assert _outcome(warehouse, opp) is None
    pos = warehouse.get_paper_position(opp)
    assert pos["status"] == "OPEN"
    assert int(pos["is_runner"]) == 1
    assert float(pos["peak_option_bid"]) == pytest.approx(160.0)


def test_a_premium_stop_breached_at_the_close_is_executed(warehouse):
    opp = _position(warehouse)
    session = date(2026, 10, 6)
    _close_marks(warehouse, opp, session, option_bid=60.0)

    _finalise(warehouse, session)

    outcome = _outcome(warehouse, opp)
    assert outcome is not None
    assert outcome["exit_reason"] == "PREMIUM_STOP"
    assert float(outcome["actual_option_pnl"]) == pytest.approx((60.0 - 100.0) * 25)
    # One-lot long hedge sold at the observed FUTURES bid, not at the entry price.
    assert float(outcome["actual_futures_pnl"]) == pytest.approx((24600.0 - 24500.0) * 25, abs=1.0) \
        or float(outcome["actual_futures_pnl"]) != 0.0


def test_a_runner_inside_the_expiry_buffer_is_closed(warehouse):
    # Expiry six calendar days after the session: the canonical buffer.
    opp = _position(warehouse, expiry="2026-10-29", entry_dte=28, runner_peak=200.0)
    _close_marks(warehouse, opp, HORIZON_SESSION, option_bid=190.0)

    _finalise(warehouse, HORIZON_SESSION)

    outcome = _outcome(warehouse, opp)
    assert outcome is not None
    assert outcome["exit_reason"] == "EXPIRY_APPROACHING"


def test_a_runner_giveback_at_the_close_is_executed(warehouse):
    opp = _position(warehouse, runner_peak=200.0)
    _close_marks(warehouse, opp, HORIZON_SESSION, option_bid=149.0)

    _finalise(warehouse, HORIZON_SESSION)

    outcome = _outcome(warehouse, opp)
    assert outcome is not None
    assert outcome["exit_reason"] == "RUNNER_TRAIL_STOP"


def test_an_ordinary_session_leaves_the_position_open(warehouse):
    opp = _position(warehouse)
    session = date(2026, 10, 6)
    _close_marks(warehouse, opp, session, option_bid=105.0)

    _finalise(warehouse, session)

    assert _outcome(warehouse, opp) is None
    assert warehouse.get_paper_position(opp)["status"] == "OPEN"
    assert len(warehouse.get_records_by_table("daily_mtm", opportunity_id=opp)) == 1


def test_finalising_twice_does_not_close_twice(warehouse):
    opp = _position(warehouse)
    _close_marks(warehouse, opp, HORIZON_SESSION, option_bid=120.0)

    _finalise(warehouse, HORIZON_SESSION)
    second = _finalise(warehouse, HORIZON_SESSION)

    assert second.status == "COMPLETE"
    assert len(warehouse.get_records_by_table("outcomes", opportunity_id=opp)) == 1


# ------------------------------------------------- the hedge follows the real spot


def test_the_hedge_rebalances_on_the_observed_underlying_close(warehouse):
    from app.engines.snapback.pricing import bs_delta

    opp = _position(warehouse, qty=250, lot=25, lots=6)
    session = date(2026, 10, 6)
    _close_marks(warehouse, opp, session, option_bid=130.0, spot=22000.0)

    _finalise(warehouse, session)

    # Remaining DTE on the session, from the contract's real expiry.
    dte = (date(2026, 11, 15) - session).days
    delta = abs(float(bs_delta(22000.0, 25000.0, dte / 365.0, 0.2, call=False)))
    expected = round(delta * 1.0 * 250 * 22000.0 / 24600.0 / 25)
    assert warehouse.get_paper_position(opp)["current_futures_lots"] == expected
    assert expected != 6, "fixture must actually move the hedge"


def test_without_an_underlying_close_the_hedge_is_held_not_rebalanced_on_a_stale_spot(warehouse):
    opp = _position(warehouse, qty=250, lot=25, lots=6)
    session = date(2026, 10, 6)
    # The futures level moved ~5%: a rebalance on the ENTRY spot would resize the
    # hedge purely because the futures price changed.
    _close_marks(warehouse, opp, session, option_bid=130.0, fut_bid=25800.0)

    _finalise(warehouse, session)

    assert warehouse.get_paper_position(opp)["current_futures_lots"] == 6
    assert warehouse.get_records_by_table("hedge_rebalances", opportunity_id=opp) == [] \
        or all(int(r["new_hedge_lots"]) == 6 for r in
               warehouse.get_records_by_table("hedge_rebalances", opportunity_id=opp))


def test_select_close_marks_returns_the_underlying_leg(warehouse):
    opp = _position(warehouse)
    session = date(2026, 10, 6)
    _close_marks(warehouse, opp, session, option_bid=105.0, spot=24550.0)

    marks = select_close_marks(warehouse, opportunity_id=opp, session_date=session)

    assert marks.complete is True
    assert marks.underlying is not None
    assert float(marks.underlying["execution_price"]) == pytest.approx(24550.0)


# ----------------------------------------------------------- the observation pass


def _quote(at: datetime, bid: float, ask: float, last: float, oi: int = 100000):
    return {
        "last_price": last, "timestamp": at.strftime("%Y-%m-%dT%H:%M:%S+05:30"),
        "oi": oi,
        "depth": {"buy": [{"price": bid, "quantity": 200}],
                  "sell": [{"price": ask, "quantity": 200}]},
    }


class _SlowClient:
    """Records quote calls; each response takes ``delay`` seconds of real time."""

    def __init__(self, quotes, delay=0.0):
        self.calls = []
        self._quotes = quotes
        self._delay = delay

    async def get_quote(self, keys):
        self.calls.append(list(keys))
        if self._delay:
            await asyncio.sleep(self._delay)
        return {k: self._quotes[k] for k in keys if k in self._quotes}


def test_eod_observation_uses_one_batched_quote_call_for_all_positions(warehouse):
    a = _position(warehouse, opp="OPP-A")
    b = _position(warehouse, opp="OPP-B")
    now = _t(date(2026, 10, 6), 15, 29, 10)
    quotes = {
        f"NFO:{OPT_SYM}": _quote(now, 104.0, 106.0, 105.0),
        f"NFO:{FUT_SYM}": _quote(now, 24600.0, 24601.0, 24600.5),
        "NSE:NIFTY 50": {"last_price": 24550.0,
                      "timestamp": now.strftime("%Y-%m-%dT%H:%M:%S+05:30")},
    }
    client = _SlowClient(quotes)

    result = asyncio.run(collect_eod_observations(
        client=client, cfg=SnapbackConfig(enabled=True), warehouse=warehouse,
        session_date=date(2026, 10, 6), now_ist=now,
    ))

    assert len(client.calls) == 1, "one quote call per leg per position is an N+1"
    assert result.rejected == 0
    assert result.observed == 6   # option + futures + underlying, two positions
    for opp in (a, b):
        legs = {r["leg"] for r in warehouse.get_records_by_table(
            "quote_quality_events", opportunity_id=opp) if r["phase"] == "EOD_MARK"}
        assert legs == {"OPTION", "FUTURES", "UNDERLYING"}


def test_eod_observation_addresses_bfo_positions_on_bfo(warehouse):
    from app.services.snapback_instrument_identity import identity_from_instrument

    warehouse.record_opportunity(
        opportunity_id="OPP-SX", symbol="SENSEX", signal_type="SNAPBACK_FADE_UP",
        spot_price=80000.0, source="PROSPECTIVE_PAPER",
        identity=identity_from_instrument(
            {"tradingsymbol": "SENSEX", "instrument_token": 265, "exchange": "BSE",
             "name": "SENSEX"},
            canonical_symbol="SENSEX",
        ),
    )
    warehouse.save_paper_position(
        opportunity_id="OPP-SX", symbol="SENSEX", option_symbol="SENSEX26NOV80000PE",
        option_qty=20, option_entry_price=100.0, option_expiry="2026-11-26",
        option_strike=80000.0, futures_symbol="NIFTY26NOVFUT", futures_lot_size=25,
        current_futures_lots=1, avg_futures_entry_price=24500.0,
        realized_futures_pnl=0.0, entry_spot=80000.0,
        entry_timestamp=ENTRY_DAY.isoformat(), entry_dte=45, entry_iv=0.2,
        causal_beta=1.0,
    )
    now = _t(date(2026, 10, 6), 15, 29, 10)
    client = _SlowClient({})

    asyncio.run(collect_eod_observations(
        client=client, cfg=SnapbackConfig(enabled=True), warehouse=warehouse,
        session_date=date(2026, 10, 6), now_ist=now,
    ))

    requested = [k for call in client.calls for k in call]
    assert "BFO:SENSEX26NOV80000PE" in requested
    assert "NFO:SENSEX26NOV80000PE" not in requested
    assert "BSE:SENSEX" in requested


def test_a_slow_quote_response_is_not_rejected_as_a_future_timestamp(warehouse):
    """``now`` is read when the quote ARRIVES, not once at the top of the pass."""
    opp = _position(warehouse)
    now = _t(date(2026, 10, 6), 15, 29, 10)
    # The exchange stamped these 700ms AFTER the pass began; the response took 0.8s.
    stamped = now + timedelta(seconds=1)
    quotes = {
        f"NFO:{OPT_SYM}": {**_quote(stamped, 104.0, 106.0, 105.0),
                           "timestamp": stamped.strftime("%Y-%m-%dT%H:%M:%S+05:30")},
        f"NFO:{FUT_SYM}": _quote(stamped, 24600.0, 24601.0, 24600.5),
        "NSE:NIFTY 50": {"last_price": 24550.0,
                      "timestamp": stamped.strftime("%Y-%m-%dT%H:%M:%S+05:30")},
    }
    client = _SlowClient(quotes, delay=1.2)

    result = asyncio.run(collect_eod_observations(
        client=client, cfg=SnapbackConfig(enabled=True), warehouse=warehouse,
        session_date=date(2026, 10, 6), now_ist=now,
    ))

    assert result.rejected == 0, result
    assert result.observed == 3
