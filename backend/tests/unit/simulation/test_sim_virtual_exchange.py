"""The matching engine: fills, order lifecycle, margin, GTT and OCO, persistence, isolation."""
import pytest

from app.services.exchanges.kite.errors import KiteInputError, KiteMarginError, KiteOrderError, KiteTokenError
from app.services.simulation import charges as ch
from app.services.simulation.ledger import SimLedger
from app.services.simulation.quotes import MarketDataUnavailable, QuoteSource
from app.services.simulation.virtual_exchange import VirtualExchange, slipped, snap
from tests.unit.simulation.sim_fakes import FUT, OPT, OPT_PE

SYM = "NIFTY2611325000CE"


async def mkt(ex, side, qty=75, symbol=SYM, exchange="NFO", **kw):
    return await ex.place_order(exchange=exchange, tradingsymbol=symbol, transaction_type=side, quantity=qty,
                                product="NRML", order_type="MARKET", **kw)


async def lim(ex, side, price, qty=75, symbol=SYM, exchange="NFO", **kw):
    return await ex.place_order(exchange=exchange, tradingsymbol=symbol, transaction_type=side, quantity=qty,
                                product="NRML", order_type="LIMIT", price=price, **kw)


def get(ex, oid):
    return next(o for o in ex.orders() if o["order_id"] == oid)


def leg(price, qty=75, side="SELL", symbol=SYM):
    return {"exchange": "NFO", "tradingsymbol": symbol, "transaction_type": side, "quantity": qty,
            "order_type": "LIMIT", "product": "NRML", "price": price}


async def oco(ex, stop=90.0, target=120.0, stop_px=88.2, target_px=117.6):
    return await ex.place_gtt(trigger_type="two-leg", tradingsymbol=SYM, exchange="NFO", last_price=100.0,
                              trigger_values=[stop, target], orders=[leg(stop_px), leg(target_px)])


def test_slippage_and_snapping_are_exact_on_the_tick_grid():
    assert slipped(100.05, 1, 0.05, True) == 100.1 and slipped(99.95, 1, 0.05, False) == 99.9
    assert slipped(100.05, 3, 0.05, True) == 100.2 and slipped(100.05, 0, 0.05, True) == 100.05
    assert snap(100.03, 0.05, "up") == 100.05 and snap(100.03, 0.05, "down") == 100.0
    assert snap(100.02, 0.05) == 100.0


# ── fills ────────────────────────────────────────────────────────────────────────────────
async def test_a_market_buy_fills_at_the_ask_plus_one_tick_and_books_everything(exchange, ledger, real):
    oid = (await mkt(exchange, "BUY"))["order_id"]
    o = get(exchange, oid)
    assert (o["status"], o["average_price"], o["filled_quantity"], o["pending_quantity"]) == ("COMPLETE", 100.1, 75, 0)
    assert len(oid) == 15 and oid.isdigit() and oid[6] == "9"          # Kite-shaped, apart from any real id
    assert [h["status"] for h in exchange.order_history(oid)] == ["PUT ORDER REQ RECEIVED", "OPEN", "COMPLETE"]
    [trade] = exchange.trades()
    assert (trade["order_id"], trade["quantity"], trade["average_price"], trade["transaction_type"]) == (oid, 75, 100.1, "BUY")
    pos = ledger.get_position("default", "NFO", SYM, "NRML")
    assert (pos["quantity"], pos["avg_price"], pos["last_price"]) == (75, 100.1, 100.0)
    fee = ch.compute_charges(exchange="NFO", tradingsymbol=SYM, side="BUY", quantity=75, price=100.1, instrument_type="CE")
    assert ledger.cash("default") == round(1_000_000 - fee.total, 2)
    assert ledger.charges("default")[0]["total"] == fee.total
    assert real.writes == []


async def test_a_market_sell_fills_at_the_bid_minus_one_tick(exchange, ledger):
    await mkt(exchange, "BUY")
    oid = (await mkt(exchange, "SELL"))["order_id"]
    assert get(exchange, oid)["average_price"] == 99.9
    assert [t["_sim"]["realised"] for t in ledger.trades("default")] == [0.0, -15.0]       # (99.90-100.10)*75
    assert ledger.get_position("default", "NFO", SYM, "NRML")["quantity"] == 0


@pytest.mark.parametrize("ticks,expected", [(0, 100.05), (1, 100.1), (3, 100.2)])
async def test_slippage_ticks_are_configurable(exchange, ledger, ticks, expected):
    ledger.set_setting("default", "slippage_ticks", ticks)
    assert get(exchange, (await mkt(exchange, "BUY"))["order_id"])["average_price"] == expected


async def test_a_marketable_limit_fills_at_the_touch_but_never_worse_than_its_limit(exchange):
    assert get(exchange, (await lim(exchange, "BUY", 100.5))["order_id"])["average_price"] == 100.1
    assert get(exchange, (await lim(exchange, "BUY", 100.05))["order_id"])["average_price"] == 100.05
    assert get(exchange, (await lim(exchange, "SELL", 99.5))["order_id"])["average_price"] == 99.9
    assert get(exchange, (await lim(exchange, "SELL", 99.95))["order_id"])["average_price"] == 99.95


async def test_a_resting_limit_waits_and_fills_when_the_touch_crosses_it(exchange, real):
    oid = (await lim(exchange, "BUY", 99.0))["order_id"]
    assert get(exchange, oid)["status"] == "OPEN"
    assert (await exchange.evaluate())["filled"] == 0 and get(exchange, oid)["status"] == "OPEN"
    real.set_quote(OPT, 98.9, bid=98.85, ask=98.95)
    report = await exchange.evaluate()
    o = get(exchange, oid)
    assert report["filled"] == 1 and (o["status"], o["average_price"]) == ("COMPLETE", 99.0)     # min(99.00, 98.95+0.05)


async def test_a_resting_sell_limit_fills_when_the_bid_reaches_it(exchange, real):
    await mkt(exchange, "BUY")
    oid = (await lim(exchange, "SELL", 110.0))["order_id"]
    real.set_quote(OPT, 109.0)
    await exchange.evaluate()
    assert get(exchange, oid)["status"] == "OPEN"
    real.set_quote(OPT, 110.5, bid=110.45, ask=110.55)
    await exchange.evaluate()
    assert (get(exchange, oid)["status"], get(exchange, oid)["average_price"]) == ("COMPLETE", 110.4)


async def test_an_empty_touch_means_no_liquidity_so_the_order_rests_and_nothing_is_invented(exchange, real):
    real.set_quote(OPT, 100.0, bid=99.95, ask=0.0)
    oid = (await mkt(exchange, "BUY"))["order_id"]
    assert get(exchange, oid)["status"] == "OPEN" and exchange.trades() == []
    real.set_quote(OPT, 100.0)
    await exchange.evaluate()
    assert get(exchange, oid)["status"] == "COMPLETE"


async def test_a_fill_is_capped_by_the_displayed_size_and_the_rest_rests(exchange, ledger, real):
    real.set_quote(OPT, 100.0, qty=75)
    oid = (await mkt(exchange, "BUY", qty=225))["order_id"]
    o = get(exchange, oid)
    assert (o["status"], o["filled_quantity"], o["pending_quantity"]) == ("OPEN", 75, 150)      # not the whole order
    real.set_quote(OPT, 100.2, qty=75)
    await exchange.evaluate()
    assert (get(exchange, oid)["filled_quantity"], get(exchange, oid)["pending_quantity"]) == (150, 75)
    real.set_quote(OPT, 100.4, qty=1000)
    await exchange.evaluate()
    o = get(exchange, oid)
    assert (o["status"], o["filled_quantity"]) == ("COMPLETE", 225)
    assert o["average_price"] == round((75 * 100.1 + 75 * 100.3 + 75 * 100.5) / 225, 2)
    assert len(exchange.trades(oid)) == 3


async def test_partial_fills_can_be_switched_off_for_a_deliberately_infinite_liquidity_run(exchange, ledger, real):
    """The documented opt-out (partial_fills=False): the whole order at the touch whatever the displayed size."""
    ledger.set_setting("default", "partial_fills", False)
    real.set_quote(OPT, 100.0, qty=75)
    o = get(exchange, (await mkt(exchange, "BUY", qty=750))["order_id"])
    assert (o["status"], o["filled_quantity"], o["average_price"]) == ("COMPLETE", 750, 100.1)


async def test_a_market_order_walks_the_displayed_levels_one_trade_per_level(exchange, ledger, real):
    real.set_quote(OPT, 100.0, asks=[(100.05, 75), (100.2, 150), (100.5, 300)], bids=[(99.95, 75)])
    oid = (await mkt(exchange, "BUY", qty=300))["order_id"]
    o = get(exchange, oid)
    assert (o["status"], o["filled_quantity"]) == ("COMPLETE", 300)
    assert [(t["quantity"], t["average_price"]) for t in exchange.trades(oid)] == [(75, 100.1), (150, 100.25), (75, 100.55)]
    assert o["average_price"] == round((75 * 100.1 + 150 * 100.25 + 75 * 100.55) / 300, 2)
    assert ledger.get_position("default", "NFO", SYM, "NRML")["quantity"] == 300
    # one order, one brokerage: the levels it swept are not separate orders
    assert round(sum(c["brokerage"] for c in ledger.charges("default")), 2) == 20.0
    whole = ch.compute_charges(exchange="NFO", tradingsymbol=SYM, side="BUY", quantity=300,
                               price=(75 * 100.1 + 150 * 100.25 + 75 * 100.55) / 300, instrument_type="CE")
    assert round(sum(c["total"] for c in ledger.charges("default")), 2) == whole.total


async def test_a_limit_order_never_walks_past_its_limit_and_rests_with_the_remainder(exchange, real):
    real.set_quote(OPT, 100.0, asks=[(100.05, 75), (100.2, 150), (100.5, 300)], bids=[(99.95, 75)])
    oid = (await lim(exchange, "BUY", 100.25, qty=300))["order_id"]
    o = get(exchange, oid)
    assert (o["status"], o["filled_quantity"], o["pending_quantity"]) == ("OPEN", 225, 75)      # 100.50 is beyond 100.25
    assert [t["average_price"] for t in exchange.trades(oid)] == [100.1, 100.25]


async def test_a_sell_walks_the_bids_down(exchange, real):
    await mkt(exchange, "BUY", qty=225)
    real.set_quote(OPT, 100.0, bids=[(99.95, 75), (99.8, 75), (99.5, 300)], asks=[(100.05, 75)])
    oid = (await mkt(exchange, "SELL", qty=225))["order_id"]
    assert [(t["quantity"], t["average_price"]) for t in exchange.trades(oid)] == [(75, 99.9), (75, 99.75), (75, 99.45)]
    assert get(exchange, oid)["status"] == "COMPLETE"


async def test_a_fill_is_a_whole_number_of_lots(exchange, real):
    real.set_quote(OPT, 100.0, qty=100)                              # 100 shown, lot 75: only one lot can trade
    o = get(exchange, (await mkt(exchange, "BUY", qty=225))["order_id"])
    assert (o["status"], o["filled_quantity"], o["pending_quantity"]) == ("OPEN", 75, 150)
    real.set_quote(OPT, 100.0, qty=50)                               # less than a lot is shown: nothing trades
    assert (await exchange.evaluate())["filled"] == 0


async def test_without_depth_detail_the_touch_quantity_alone_caps_the_fill(exchange, real):
    from app.services.simulation.quotes import Book
    real.set_quote(OPT, 100.0, ask=0.0)                              # nothing to buy: the order rests
    oid = (await mkt(exchange, "BUY", qty=225))["order_id"]
    book = Book(exchange="NFO", symbol=SYM, ltp=100.0, bid=99.95, ask=100.05, bid_qty=75, ask_qty=150,
                fetched_at=0.0, source="ws", token=1001)             # a book with a touch size but no level list
    await exchange.evaluate(books={f"NFO:{SYM}": book})
    assert get(exchange, oid)["filled_quantity"] == 150
    unknown = Book(exchange="NFO", symbol=SYM, ltp=100.0, bid=99.95, ask=100.05, bid_qty=0, ask_qty=0,
                   fetched_at=0.0, source="ws", token=1001)          # size unknown: not invented, not blocked
    await exchange.evaluate(books={f"NFO:{SYM}": unknown})
    assert get(exchange, oid)["status"] == "COMPLETE"


async def test_ioc_that_cannot_fill_at_once_is_cancelled(exchange):
    oid = (await lim(exchange, "BUY", 99.0, validity="IOC"))["order_id"]
    o = get(exchange, oid)
    assert (o["status"], o["cancelled_quantity"], o["pending_quantity"]) == ("CANCELLED", 75, 0)


# ── validation, margin ───────────────────────────────────────────────────────────────────
async def test_inputs_are_checked_like_kite_checks_them(exchange):
    with pytest.raises(KiteInputError, match="lot size"):
        await mkt(exchange, "BUY", qty=50)
    with pytest.raises(KiteInputError, match="Tick size"):
        await lim(exchange, "BUY", 100.03)
    with pytest.raises(KiteInputError, match="positive price"):
        await exchange.place_order(exchange="NFO", tradingsymbol=SYM, transaction_type="BUY", quantity=75,
                                   product="NRML", order_type="LIMIT")
    with pytest.raises(KiteInputError, match="Invalid instrument"):
        await mkt(exchange, "BUY", symbol="NOPE")
    with pytest.raises(KiteInputError, match="regular"):
        await mkt(exchange, "BUY", variety="amo")
    with pytest.raises(KiteInputError, match="NRML and MIS"):
        await exchange.place_order(exchange="NFO", tradingsymbol=SYM, transaction_type="BUY", quantity=75,
                                   product="CNC", order_type="MARKET")
    assert exchange.orders() == []                                  # a refusal creates no order


async def test_tick_size_enforcement_can_be_switched_off(exchange, ledger):
    ledger.set_setting("default", "enforce_tick_size", False)
    assert (await lim(exchange, "BUY", 99.03))["order_id"]


async def test_an_unfunded_option_buy_is_refused_with_a_kite_like_message(exchange, ledger):
    ledger.set_capital("default", 5_000.0)
    with pytest.raises(KiteMarginError) as info:
        await mkt(exchange, "BUY")                                  # 75 x 100.10 = 7,507.50
    assert str(info.value) == "Insufficient funds. Required margin is 7,507.50 but available margin is 5,000.00."
    assert (info.value.error_type, info.value.status_code) == ("MarginException", 400)
    assert exchange.orders() == [] and ledger.positions("default") == []


async def test_futures_need_twelve_percent_of_notional(exchange, ledger):
    ledger.set_capital("default", 200_000.0)
    with pytest.raises(KiteMarginError, match="Required margin is 225,001.35"):
        await mkt(exchange, "BUY", symbol="NIFTY26OCTFUT")          # 12% x 75 x 25000.15
    ledger.set_capital("default", 1_000_000.0)
    await mkt(exchange, "BUY", symbol="NIFTY26OCTFUT")
    view = ledger.account_view("default", "2026-10-12")
    assert view["blocked_margin"] == round(0.12 * 25000.15 * 75, 2) and view["open_positions"] == 1


async def test_a_closing_order_needs_no_margin_but_a_naked_short_does(exchange, ledger):
    await mkt(exchange, "BUY")
    ledger.set_capital("default", 1.0)                              # nothing left to fund anything new
    await mkt(exchange, "SELL")                                     # closes: fine
    with pytest.raises(KiteMarginError):
        await mkt(exchange, "SELL")                                 # would open a short: 12% x 25000 x 75


async def test_a_resting_order_that_became_unaffordable_is_rejected_when_it_would_fill(exchange, ledger, real):
    oid = (await lim(exchange, "BUY", 99.0))["order_id"]
    ledger.set_capital("default", 1_000.0)
    real.set_quote(OPT, 98.9, bid=98.85, ask=98.95)
    await exchange.evaluate()
    o = get(exchange, oid)
    assert o["status"] == "REJECTED" and "Insufficient funds" in o["status_message"] and exchange.trades() == []


async def test_market_hours_are_enforced_only_when_asked_to(exchange, ledger):
    exchange._session_open = lambda now, ex: False
    assert (await mkt(exchange, "BUY"))["order_id"]
    ledger.set_setting("default", "enforce_market_hours", True)
    with pytest.raises(KiteOrderError, match="Markets are closed"):
        await mkt(exchange, "BUY")
    exchange._session_open = lambda now, ex: True
    assert (await mkt(exchange, "BUY"))["order_id"]


# ── no real price, no order ───────────────────────────────────────────────────────────────
async def test_without_a_real_price_nothing_is_placed(exchange, real):
    real.fail = KiteTokenError("Kite session expired", error_type="TokenException")
    with pytest.raises(MarketDataUnavailable) as info:
        await mkt(exchange, "BUY")
    assert isinstance(info.value, PermissionError) and exchange.orders() == []


async def test_evaluation_skips_what_it_cannot_price_and_works_the_rest(exchange, real):
    pe = (await lim(exchange, "BUY", 90.0, symbol="NIFTY2611325000PE"))["order_id"]
    ce = (await lim(exchange, "BUY", 90.0))["order_id"]
    real.set_quote(OPT, 89.0, bid=88.95, ask=89.0)
    del real.quotes[OPT_PE]
    report = await exchange.evaluate()
    assert report["unpriced"] == 1 and get(exchange, ce)["status"] == "COMPLETE" and get(exchange, pe)["status"] == "OPEN"


# ── cancel, modify, stop orders ───────────────────────────────────────────────────────────
async def test_cancel_and_modify_follow_the_order_state(exchange):
    oid = (await lim(exchange, "BUY", 99.0))["order_id"]
    await exchange.modify_order(oid, price=99.5, quantity=150)
    o = get(exchange, oid)
    assert (o["price"], o["quantity"], o["pending_quantity"], o["status"]) == (99.5, 150, 150, "OPEN")
    await exchange.modify_order(oid, price=100.5)                    # now marketable: fills at once
    o = get(exchange, oid)
    assert (o["status"], o["average_price"], o["filled_quantity"]) == ("COMPLETE", 100.1, 150)
    with pytest.raises(KiteOrderError, match="cannot be modified"):
        await exchange.modify_order(oid, price=99.0)
    with pytest.raises(KiteOrderError, match="cannot be cancelled"):
        await exchange.cancel_order(oid)
    with pytest.raises(KiteInputError, match="Order not found"):
        await exchange.cancel_order("123")
    resting = (await lim(exchange, "BUY", 90.0))["order_id"]
    await exchange.cancel_order(resting)
    o = get(exchange, resting)
    assert (o["status"], o["cancelled_quantity"], o["pending_quantity"]) == ("CANCELLED", 75, 0)


async def test_an_sl_market_sell_waits_for_the_trigger_then_fills_as_a_market_order(exchange, real):
    await mkt(exchange, "BUY")
    oid = (await exchange.place_order(exchange="NFO", tradingsymbol=SYM, transaction_type="SELL", quantity=75,
                                      product="NRML", order_type="SL-M", trigger_price=95.0))["order_id"]
    assert get(exchange, oid)["status"] == "TRIGGER PENDING"
    real.set_quote(OPT, 96.0)
    await exchange.evaluate()
    assert get(exchange, oid)["status"] == "TRIGGER PENDING"
    real.set_quote(OPT, 94.0, bid=93.95, ask=94.05)
    await exchange.evaluate()
    o = get(exchange, oid)
    assert (o["status"], o["average_price"]) == ("COMPLETE", 93.9)
    assert [h["status"] for h in exchange.order_history(oid)] == ["PUT ORDER REQ RECEIVED", "TRIGGER PENDING", "OPEN", "COMPLETE"]


async def test_a_stop_buy_whose_trigger_is_already_breached_is_rejected(exchange):
    oid = (await exchange.place_order(exchange="NFO", tradingsymbol=SYM, transaction_type="BUY", quantity=75,
                                      product="NRML", order_type="SL-M", trigger_price=99.0))["order_id"]
    assert get(exchange, oid)["status"] == "REJECTED"


async def test_day_orders_lapse_at_the_close_but_gtts_do_not(exchange, clock):
    oid = (await lim(exchange, "BUY", 90.0))["order_id"]
    gid = (await oco(exchange))["trigger_id"]
    clock.advance(hours=5, minutes=30)                                # 15:46 IST
    report = await exchange.evaluate()
    o = get(exchange, oid)
    assert report["expired"] == 1 and o["status"] == "CANCELLED" and "end of the trading session" in o["status_message"]
    assert exchange.gtt(gid)["status"] == "active"


async def test_a_day_order_lives_to_the_close_of_its_own_exchange_not_a_fixed_1530(exchange, clock, real):
    """NFO closes at 15:40 from 2026-08-03 (market_hours.continuous_close); BSE keeps 15:30. The market is open
    at 15:35 and a resting order or a fired GTT child there must neither lapse nor miss a fill."""
    from tests.unit.simulation.sim_fakes import ist, row
    real._instruments.rows.append(row("SENSEX2611380000CE", 3001, exchange="BFO", itype="CE", strike=80000.0, lot=20))
    real.set_quote("BFO:SENSEX2611380000CE", 200.0)
    clock.value = ist(15, 10)
    nfo = (await lim(exchange, "BUY", 90.0))["order_id"]
    bfo = (await lim(exchange, "BUY", 190.0, qty=20, symbol="SENSEX2611380000CE", exchange="BFO"))["order_id"]
    clock.value = ist(15, 35)
    report = await exchange.evaluate()
    assert get(exchange, nfo)["status"] == "OPEN" and report["expired"] == 1              # BFO is shut since 15:30
    assert get(exchange, bfo)["status"] == "CANCELLED"
    real.set_quote(OPT, 89.0, bid=88.95, ask=89.05)                                        # the NFO order can still fill
    await exchange.evaluate()
    assert get(exchange, nfo)["status"] == "COMPLETE"
    resting = (await lim(exchange, "BUY", 80.0))["order_id"]                              # placed at 15:35, NFO open
    clock.value = ist(15, 41)
    assert (await exchange.evaluate())["expired"] == 1 and get(exchange, resting)["status"] == "CANCELLED"


async def test_an_order_from_an_earlier_day_lapses_whatever_the_time(exchange, clock, ledger):
    from tests.unit.simulation.sim_fakes import ist
    oid = (await lim(exchange, "BUY", 90.0))["order_id"]
    clock.value = ist(9, 20, day=(2026, 10, 13))
    assert exchange.expire_day_orders() == 1 and ledger.get_order("default", oid)["status"] == "CANCELLED"


# ── GTT ───────────────────────────────────────────────────────────────────────────────────
async def test_an_oco_stop_fires_on_the_last_price_and_its_limit_child_fills_by_the_same_rules(exchange, ledger, real):
    await mkt(exchange, "BUY")                                        # long 75 @ 100.10
    gid = (await oco(exchange))["trigger_id"]
    assert gid > 9_000_000_000 and exchange.gtt(gid)["type"] == "two-leg"
    await exchange.evaluate()
    assert exchange.gtt(gid)["status"] == "active"                    # 100 is between 90 and 120
    real.set_quote(OPT, 89.9, bid=89.85, ask=89.95)
    report = await exchange.evaluate()
    g = exchange.gtt(gid)
    assert report["fired"] == 1 and g["status"] == "triggered"
    result = g["orders"][0]["result"]
    assert result["order_result"]["status"] == "success" and result["triggered_at"] == 89.9
    child = get(exchange, result["order_result"]["order_id"])
    assert (child["order_type"], child["transaction_type"], child["price"], child["tag"]) == ("LIMIT", "SELL", 88.2, "gtt")
    assert (child["status"], child["average_price"]) == ("COMPLETE", 89.8)          # max(88.20, 89.85 - 0.05)
    assert ledger.get_position("default", "NFO", SYM, "NRML")["quantity"] == 0
    assert [t["_sim"]["realised"] for t in ledger.trades("default")] == [0.0, round((89.8 - 100.1) * 75, 2)]
    assert g["orders"][1]["result"] is None                           # the other leg never fired


async def test_an_oco_target_leg_fires_upwards(exchange, real):
    await mkt(exchange, "BUY")
    gid = (await oco(exchange))["trigger_id"]
    real.set_quote(OPT, 121.0, bid=120.95, ask=121.05)
    await exchange.evaluate()
    g = exchange.gtt(gid)
    assert g["orders"][1]["result"]["order_result"]["status"] == "success" and g["orders"][0]["result"] is None
    assert get(exchange, g["orders"][1]["result"]["order_result"]["order_id"])["average_price"] == 120.9


async def test_a_gap_through_the_stop_leaves_the_child_resting_unfilled(exchange, ledger, real):
    await mkt(exchange, "BUY")
    gid = (await oco(exchange))["trigger_id"]
    real.set_quote(OPT, 80.0, bid=79.95, ask=80.05)                   # gapped far below the 88.20 limit
    await exchange.evaluate()
    g = exchange.gtt(gid)
    child_id = g["orders"][0]["result"]["order_result"]["order_id"]
    assert g["status"] == "triggered" and get(exchange, child_id)["status"] == "OPEN"
    assert ledger.get_position("default", "NFO", SYM, "NRML")["quantity"] == 75        # still held, still exposed
    real.set_quote(OPT, 90.0, bid=89.95, ask=90.05)                   # the market comes back to the limit
    await exchange.evaluate()
    assert (get(exchange, child_id)["status"], get(exchange, child_id)["average_price"]) == ("COMPLETE", 89.9)


async def test_a_resting_child_can_be_cancelled_and_the_position_sold_another_way(exchange, ledger, real):
    await mkt(exchange, "BUY")
    gid = (await oco(exchange))["trigger_id"]
    real.set_quote(OPT, 80.0, bid=79.95, ask=80.05)
    await exchange.evaluate()
    child_id = exchange.gtt(gid)["orders"][0]["result"]["order_result"]["order_id"]
    await exchange.cancel_order(child_id)
    assert get(exchange, child_id)["status"] == "CANCELLED"
    await mkt(exchange, "SELL")
    assert ledger.get_position("default", "NFO", SYM, "NRML")["quantity"] == 0


async def test_a_single_gtt_infers_its_direction_from_the_real_last_price(exchange, real):
    down = (await exchange.place_gtt(trigger_type="single", tradingsymbol=SYM, exchange="NFO", last_price=100.0,
                                     trigger_values=[90.0], orders=[leg(88.2)]))["trigger_id"]
    up = (await exchange.place_gtt(trigger_type="single", tradingsymbol=SYM, exchange="NFO", last_price=100.0,
                                   trigger_values=[110.0], orders=[leg(112.0)]))["trigger_id"]
    real.set_quote(OPT, 95.0)
    await exchange.evaluate()
    assert exchange.gtt(down)["status"] == "active" and exchange.gtt(up)["status"] == "active"
    real.set_quote(OPT, 111.0, bid=110.95, ask=111.05)
    await exchange.evaluate()
    assert exchange.gtt(up)["status"] == "triggered" and exchange.gtt(down)["status"] == "active"


async def test_gtt_direction_comes_from_the_requests_last_price_not_the_sims_own_cached_book(exchange, real):
    """Kite orients a trigger from the last_price in the request. A stop whose engine saw 100 but whose book has
    already dropped to 97 must still be a falling-price trigger: it fires on the next pass, not on a rebound."""
    real.set_quote(OPT, 97.0, bid=96.95, ask=97.05)
    gid = (await exchange.place_gtt(trigger_type="single", tradingsymbol=SYM, exchange="NFO", last_price=100.0,
                                    trigger_values=[98.0], orders=[leg(96.0)]))["trigger_id"]
    assert exchange.ledger.get_gtt("default", gid)["_sim"]["dirs"] == ["down"]
    report = await exchange.evaluate()
    assert report["fired"] == 1 and exchange.gtt(gid)["status"] == "triggered"
    real.set_quote(OPT, 90.0, bid=89.95, ask=90.05)                       # and a request seen BELOW the trigger is rising
    rising = (await exchange.place_gtt(trigger_type="single", tradingsymbol=SYM, exchange="NFO", last_price=90.0,
                                       trigger_values=[95.0], orders=[leg(94.0)]))["trigger_id"]
    assert exchange.ledger.get_gtt("default", rising)["_sim"]["dirs"] == ["up"]


async def test_an_oco_band_is_validated_against_the_requests_last_price(exchange, real):
    """modify_gtt(last_price=119.9, [100, 120]) is valid at Kite even when the sim's own read has moved to 121."""
    gid = (await oco(exchange))["trigger_id"]
    real.set_quote(OPT, 121.0, bid=120.95, ask=121.05)
    await exchange.modify_gtt(gid, trigger_type="two-leg", tradingsymbol=SYM, exchange="NFO", last_price=119.9,
                              trigger_values=[100.0, 120.0], orders=[leg(98.0), leg(119.0)])
    assert exchange.ledger.get_gtt("default", gid)["_sim"]["dirs"] == ["down", "up"]
    with pytest.raises(KiteInputError, match="either side of the last price"):      # a stale price outside the band
        await exchange.place_gtt(trigger_type="two-leg", tradingsymbol=SYM, exchange="NFO", last_price=130.0,
                                 trigger_values=[100.0, 120.0], orders=[leg(98.0), leg(119.0)])


async def test_gtt_payloads_are_kite_shaped_and_listed(exchange):
    gid = (await oco(exchange))["trigger_id"]
    [row] = exchange.gtts()
    assert row["id"] == gid and row["status"] == "active" and "_sim" not in row
    assert row["condition"]["trigger_values"] == [90.0, 120.0] and row["condition"]["tradingsymbol"] == SYM
    assert row["condition"]["instrument_token"] == 1001 and row["orders"][0]["result"] is None
    assert exchange.gtt(gid) == row
    with pytest.raises(KiteInputError, match="not found") as info:
        exchange.gtt(1)
    assert info.value.status_code == 404


async def test_gtt_validation_reuses_kites_own_rules(exchange):
    bad_product = {**leg(88.2), "product": "MIS"}
    with pytest.raises(KiteOrderError, match="MIS is unsupported"):
        await exchange.place_gtt(trigger_type="single", tradingsymbol=SYM, exchange="NFO", last_price=100.0,
                                 trigger_values=[90.0], orders=[bad_product])
    with pytest.raises(KiteOrderError, match="priced LIMIT"):
        await exchange.place_gtt(trigger_type="single", tradingsymbol=SYM, exchange="NFO", last_price=100.0,
                                 trigger_values=[90.0], orders=[{**leg(88.2), "order_type": "MARKET"}])
    with pytest.raises(KiteInputError, match="either side of the last price"):
        await exchange.place_gtt(trigger_type="two-leg", tradingsymbol=SYM, exchange="NFO", last_price=100.0,
                                 trigger_values=[101.0, 120.0], orders=[leg(99.0), leg(117.6)])
    assert exchange.gtts() == []


async def test_modify_and_delete_gtt(exchange, real):
    gid = (await oco(exchange))["trigger_id"]
    await exchange.modify_gtt(gid, trigger_type="two-leg", tradingsymbol=SYM, exchange="NFO", last_price=100.0,
                              trigger_values=[95.0, 130.0], orders=[leg(93.1), leg(127.4)])
    g = exchange.gtt(gid)
    assert g["condition"]["trigger_values"] == [95.0, 130.0] and g["orders"][0]["price"] == 93.1
    assert (await exchange.delete_gtt(gid)) == {"trigger_id": gid}
    assert exchange.gtt(gid)["status"] == "cancelled"
    for call in (exchange.delete_gtt(gid), exchange.delete_gtt(42)):
        with pytest.raises(KiteInputError, match="not found"):
            await call
    with pytest.raises(KiteInputError):
        await exchange.modify_gtt(gid, trigger_type="two-leg", tradingsymbol=SYM, exchange="NFO", last_price=100.0,
                                  trigger_values=[95.0, 130.0], orders=[leg(93.1), leg(127.4)])
    real.set_quote(OPT, 50.0, bid=49.95, ask=50.05)
    await exchange.evaluate()
    assert exchange.gtt(gid)["status"] == "cancelled"                  # a cancelled trigger never fires


async def test_a_gtt_cancelled_while_prices_were_being_fetched_does_not_fire(exchange, quotes, real):
    await mkt(exchange, "BUY")
    gid = (await oco(exchange))["trigger_id"]
    real.set_quote(OPT, 80.0, bid=79.95, ask=80.05)
    original = quotes.books

    async def books_then_cancel(keys, *, strict=True):
        out = await original(keys, strict=strict)
        await exchange.delete_gtt(gid)                                 # the strategy cancels during the read
        return out
    quotes.books = books_then_cancel
    report = await exchange.evaluate()
    assert report["fired"] == 0 and exchange.gtt(gid)["status"] == "cancelled" and len(exchange.trades()) == 1


async def test_an_unfunded_short_from_a_stale_stop_is_rejected_and_the_gtt_records_why(exchange, ledger, real):
    # the position is gone (sold by hand) but the stop is still armed: its SELL would open a naked short
    gid = (await oco(exchange))["trigger_id"]
    ledger.set_capital("default", 1.0)
    real.set_quote(OPT, 89.0, bid=88.95, ask=89.05)
    await exchange.evaluate()
    result = exchange.gtt(gid)["orders"][0]["result"]["order_result"]
    assert result["status"] == "failed" and "Insufficient funds" in result["rejection_reason"]
    assert get(exchange, result["order_id"])["status"] == "REJECTED"


# ── read surfaces ────────────────────────────────────────────────────────────────────────
async def test_positions_are_marked_to_the_real_price_in_kites_shape(exchange, real):
    await mkt(exchange, "BUY")
    real.set_quote(OPT, 110.0)
    await exchange.evaluate()
    raw = exchange.positions()
    [row] = raw["net"]
    assert (row["tradingsymbol"], row["exchange"], row["product"], row["quantity"]) == (SYM, "NFO", "NRML", 75)
    assert (row["average_price"], row["last_price"], row["unrealised"], row["realised"]) == (100.1, 110.0, 742.5, 0.0)
    assert (row["pnl"], row["day_buy_quantity"], row["buy_value"], row["overnight_quantity"]) == (742.5, 75, 7507.5, 0)
    assert raw["day"] == [row]
    await mkt(exchange, "SELL")
    [closed] = exchange.positions()["net"]
    assert (closed["quantity"], closed["average_price"], closed["unrealised"]) == (0, 0.0, 0.0)
    assert closed["realised"] == round((109.9 - 100.1) * 75, 2) == 735.0 and closed["day_sell_quantity"] == 75


async def test_the_funds_view_reports_cash_blocked_margin_and_available(exchange, ledger):
    await mkt(exchange, "BUY")
    m = exchange.margins("equity")
    cash = ledger.cash("default")
    unrealised = (100.0 - 100.1) * 75                                  # marked at the last price: a 7.50 paper loss
    assert m["available"]["live_balance"] == round(cash - 7507.5 + unrealised, 2)
    assert m["utilised"]["option_premium"] == 7507.5 and m["utilised"]["m2m_unrealised"] == -7.5
    assert exchange.margins()["equity"] == m and exchange.margins()["commodity"]["enabled"] is False
    assert m["available"]["opening_balance"] == 1_000_000.0


async def test_the_order_book_is_todays_orders_plus_anything_still_open(exchange, clock):
    first = (await mkt(exchange, "BUY"))["order_id"]
    resting = (await lim(exchange, "BUY", 90.0))["order_id"]
    assert [o["order_id"] for o in exchange.orders()] == [first, resting]
    clock.advance(days=1)
    assert [o["order_id"] for o in exchange.orders()] == [resting]      # yesterday's filled order is gone from the book
    with pytest.raises(KiteInputError, match="not found"):
        exchange.order_history("nope")


# ── postbacks ─────────────────────────────────────────────────────────────────────────────
async def test_order_updates_are_queued_for_the_postback_sink(exchange):
    oid = (await mkt(exchange, "BUY", tag="abc"))["order_id"]
    updates = exchange.drain_postbacks()
    assert [u["status"] for u in updates] == ["OPEN", "COMPLETE"]
    assert all(u["order_id"] == oid and u["tag"] == "abc" and u["user_id"].startswith("SIM-") for u in updates)
    assert "_sim" not in updates[0] and exchange.drain_postbacks() == []


async def test_postbacks_reach_the_sink_and_a_broken_sink_never_breaks_the_exchange(exchange):
    got = []

    async def sink(order):
        got.append(order["status"])
        raise RuntimeError("listener blew up")
    exchange.set_postback_sink(sink)
    exchange._postback_delay_s = 0
    await mkt(exchange, "BUY")
    import asyncio
    await asyncio.sleep(0.05)
    assert got == ["OPEN", "COMPLETE"]


# ── persistence and isolation ─────────────────────────────────────────────────────────────
async def test_a_new_exchange_over_the_same_books_carries_on_where_the_old_one_stopped(db_factory, real, clock):
    def build():
        ledger = SimLedger(db_factory)
        return VirtualExchange("default", ledger, QuoteSource(lambda: real, owner="default", ttl_s=0, use_websocket=False),
                               clock=clock), ledger
    first, _ = build()
    await mkt(first, "BUY")
    gid = (await oco(first))["trigger_id"]
    resting = (await lim(first, "BUY", 90.0))["order_id"]
    second, ledger2 = build()                                          # "after a restart"
    assert [o["order_id"] for o in second.orders()][-1] == resting and second.gtt(gid)["status"] == "active"
    real.set_quote(OPT, 85.0, bid=84.95, ask=85.05)
    report = await second.evaluate()
    assert report["fired"] == 1 and report["filled"] >= 1
    assert second.gtt(gid)["status"] == "triggered"
    # the resting buy filled at 85.10; the stop's sell (limit 88.20) gapped past its limit and rests
    assert ledger2.get_position("default", "NFO", SYM, "NRML")["quantity"] == 150
    child = second.gtt(gid)["orders"][0]["result"]["order_result"]["order_id"]
    assert get(second, child)["status"] == "OPEN"


async def test_two_owners_never_see_each_others_orders_or_funds(db_factory, clock):
    from tests.unit.simulation.sim_fakes import FakeReal
    ledger = SimLedger(db_factory)

    def build(owner):
        owners_real = FakeReal(owner=owner, clock=clock)
        return VirtualExchange(owner, ledger, QuoteSource(lambda: owners_real, owner=owner, ttl_s=0,
                                                          use_websocket=False), clock=clock)
    a, b = build("alice"), build("bob")
    ledger.set_capital("bob", 50_000.0)
    await mkt(a, "BUY")
    assert b.orders() == [] and b.positions()["net"] == [] and b.gtts() == []
    assert ledger.cash("bob") == 50_000.0 and ledger.cash("alice") < 1_000_000.0
    with pytest.raises(KiteInputError):
        await b.cancel_order(a.orders()[0]["order_id"])
    assert a.account_id != b.account_id


async def test_the_gtt_list_keeps_active_and_recent_triggers_but_old_ones_stay_readable_by_id(exchange, clock):
    old = (await oco(exchange))["trigger_id"]
    await exchange.delete_gtt(old)
    live = (await oco(exchange))["trigger_id"]
    clock.advance(days=3)
    assert [g["id"] for g in exchange.gtts()] == [old, live]
    clock.advance(days=5)                                              # the cancelled one is now 8 days old
    assert [g["id"] for g in exchange.gtts()] == [live]
    assert exchange.gtt(old)["status"] == "cancelled"


async def test_one_poisoned_order_never_stops_the_rest_of_the_pass(exchange, real, monkeypatch):
    bad = (await lim(exchange, "BUY", 90.0))["order_id"]
    good = (await lim(exchange, "BUY", 90.0, symbol="NIFTY2611325000PE"))["order_id"]
    real.set_quote(OPT, 89.0, bid=88.95, ask=89.0)
    real.set_quote(OPT_PE, 89.0, bid=88.95, ask=89.0)
    original = exchange._advance

    def advance(order, *args):
        if order["order_id"] == bad:
            order["status"] = "COMPLETE"                              # a half-done change that must be rolled back
            exchange.ledger.put_order("default", order)
            raise RuntimeError("bug while working this order")
        return original(order, *args)
    monkeypatch.setattr(exchange, "_advance", advance)
    report = await exchange.evaluate()
    assert report["errors"] == 1 and report["filled"] == 1
    assert get(exchange, good)["status"] == "COMPLETE"
    assert get(exchange, bad)["status"] == "OPEN"                      # untouched by the failed attempt


async def test_a_child_that_cannot_be_placed_is_recorded_on_the_gtt_and_leaves_no_order(exchange, real, monkeypatch):
    await mkt(exchange, "BUY")
    gid = (await oco(exchange))["trigger_id"]
    monkeypatch.setattr(exchange, "_advance", lambda *a: (_ for _ in ()).throw(RuntimeError("matching blew up")))
    real.set_quote(OPT, 85.0, bid=84.95, ask=85.05)
    report = await exchange.evaluate()
    g = exchange.gtt(gid)
    assert g["status"] == "triggered" and g["orders"][0]["result"]["order_result"]["status"] == "failed"
    assert "matching blew up" in g["orders"][0]["result"]["order_result"]["rejection_reason"]
    assert [o["tag"] for o in exchange.orders()] == [None]            # only the original entry: no half-made child


async def test_an_evaluation_can_be_fed_prices_it_did_not_fetch_itself(exchange, real):
    from app.services.simulation.quotes import Book
    await mkt(exchange, "BUY")
    gid = (await oco(exchange))["trigger_id"]
    real.calls.clear()
    book = Book(exchange="NFO", symbol=SYM, ltp=85.0, bid=84.95, ask=85.05, bid_qty=1000, ask_qty=1000,
                fetched_at=0.0, source="ws", token=1001)
    report = await exchange.evaluate(books={f"NFO:{SYM}": book})
    assert report["fired"] == 1 and exchange.gtt(gid)["status"] == "triggered" and real.calls == []
