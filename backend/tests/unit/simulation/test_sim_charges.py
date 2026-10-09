"""Numeric tests for the Zerodha-style charge model (values worked out by hand)."""
import pytest

from app.services.simulation import charges as c


def _opt(side, qty, price, **kw):
    return c.compute_charges(exchange="NFO", tradingsymbol="NIFTY2611325000CE", side=side,
                             quantity=qty, price=price, **kw)


def test_option_buy_pays_flat_brokerage_exchange_sebi_stamp_and_gst_but_no_stt():
    got = _opt("BUY", 75, 100.0)          # turnover 7,500
    assert got.turnover == 7500.0
    assert got.brokerage == 20.0
    assert got.stt == 0.0
    assert got.exchange_txn == 2.63        # 7500 * 0.03503%
    assert got.sebi == 0.01                # 7500 * 0.0001% = 0.0075
    assert got.stamp == 0.23               # 7500 * 0.003% = 0.225, half up
    assert got.gst == 4.07                 # 18% of (20 + 2.62725 + 0.0075)
    assert got.total == 26.94


def test_option_sell_pays_stt_on_premium_and_no_stamp():
    got = _opt("SELL", 75, 110.0)         # turnover 8,250
    assert (got.brokerage, got.stt, got.exchange_txn, got.sebi, got.stamp, got.gst) == (
        20.0, 8.25, 2.89, 0.01, 0.0, 4.12)
    assert got.total == 35.27


def test_option_brokerage_is_flat_even_on_a_tiny_premium():
    assert _opt("BUY", 75, 0.05).brokerage == 20.0


def test_futures_buy_and_sell():
    buy = c.compute_charges(exchange="NFO", tradingsymbol="NIFTY26OCTFUT", side="BUY",
                            quantity=75, price=25000.0)         # turnover 1,875,000
    assert (buy.brokerage, buy.stt, buy.exchange_txn, buy.sebi, buy.stamp, buy.gst) == (
        20.0, 0.0, 34.31, 1.88, 37.5, 10.11)
    assert buy.total == 103.8
    sell = c.compute_charges(exchange="NFO", tradingsymbol="NIFTY26OCTFUT", side="SELL",
                             quantity=75, price=25000.0)
    assert (sell.stt, sell.stamp) == (375.0, 0.0)
    assert sell.total == 441.3


def test_futures_brokerage_is_the_lower_of_flat_and_three_hundredths_of_a_percent():
    small = c.compute_charges(exchange="NFO", tradingsymbol="XYZ26OCTFUT", side="BUY",
                              quantity=1, price=10000.0)
    assert small.brokerage == 3.0


def test_bse_option_uses_the_bse_transaction_rate():
    got = c.compute_charges(exchange="BFO", tradingsymbol="SENSEX2611380000CE", side="SELL",
                            quantity=20, price=200.0)           # turnover 4,000
    assert (got.brokerage, got.stt, got.exchange_txn, got.sebi, got.gst) == (20.0, 4.0, 1.3, 0.0, 3.83)
    assert got.total == 29.13


def test_equity_intraday_buy():
    got = c.compute_charges(exchange="NSE", tradingsymbol="RELIANCE", side="BUY", quantity=10,
                            price=1000.0, product="MIS")
    assert (got.brokerage, got.exchange_txn, got.sebi, got.gst, got.stamp) == (3.0, 0.3, 0.01, 0.6, 0.3)
    assert got.total == 4.21


def test_equity_delivery_has_no_brokerage_and_stt_both_sides():
    buy = c.compute_charges(exchange="NSE", tradingsymbol="RELIANCE", side="BUY", quantity=10,
                            price=1000.0, product="CNC")
    sell = c.compute_charges(exchange="NSE", tradingsymbol="RELIANCE", side="SELL", quantity=10,
                             price=1000.0, product="CNC")
    assert buy.brokerage == 0.0 and buy.stt == 10.0 and sell.stt == 10.0


def test_instrument_kind_prefers_the_master_row_then_the_symbol():
    assert c.instrument_kind("X", "CE") == c.KIND_OPTION
    assert c.instrument_kind("X", "FUT") == c.KIND_FUTURE
    assert c.instrument_kind("NIFTY2611325000PE") == c.KIND_OPTION
    assert c.instrument_kind("NFO:NIFTY26OCTFUT") == c.KIND_FUTURE
    assert c.instrument_kind("RELIANCE") == c.KIND_EQUITY
    assert c.instrument_kind("PEL") == c.KIND_EQUITY      # ends in "EL", no digits: not an option


def test_charges_add_and_total_of():
    a, b = _opt("BUY", 75, 100.0), _opt("SELL", 75, 110.0)
    both = a + b
    assert both.total == round(a.total + b.total, 2) == 62.21
    assert c.total_of([a, b]) == both and c.total_of([]) == c.ZERO
    assert set(both.as_dict()) == {"turnover", "brokerage", "stt", "exchange_txn", "sebi",
                                   "stamp", "gst", "total"}


@pytest.mark.parametrize("qty,price,side", [(0, 10.0, "BUY"), (75, 0.0, "BUY"), (75, 10.0, "HOLD")])
def test_bad_inputs_are_refused(qty, price, side):
    with pytest.raises(ValueError):
        _opt(side, qty, price)


def test_a_rate_change_is_one_schedule_edit():
    from decimal import Decimal
    sched = c.ChargeSchedule(brokerage_flat=Decimal("10"))
    assert _opt("BUY", 75, 100.0, schedule=sched).brokerage == 10.0
