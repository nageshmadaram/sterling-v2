"""Offline canonical order-fact drills. No API token, broker or network writes."""
from dataclasses import replace
from decimal import Decimal

import pytest

from app.services.ema_trend.live_order_facts import (
    LiveFillDelta, LiveOrderEvidenceError, LiveOrderIdentity,
    broker_order_fact, incremental_confirmed_fill,
)


@pytest.fixture
def identity():
    return LiveOrderIdentity(uid="alice",account_id="KITE-TEST",
        order_id="ORDER-123",exchange="NSE",symbol="INFY",
        product="CNC",side="BUY",quantity=4)


def event(identity, status, filled, price="0", **changes):
    return {
        "order_id":identity.order_id,"exchange":identity.exchange,
        "tradingsymbol":identity.symbol,"product":identity.product,
        "transaction_type":identity.side,"quantity":identity.quantity,
        "status":status,"filled_quantity":filled,"average_price":price,
        **changes,
    }


def fact(identity, events, *,account="KITE-TEST",paper=False):
    return broker_order_fact(identity,events,broker_account_id=account,
        broker_is_paper=paper)


def test_order_ack_is_not_a_fill(identity):
    ack=fact(identity,[event(identity,"OPEN",0)])
    assert ack.unresolved and ack.filled_quantity==0
    assert ack.cumulative_value==0
    delta=incremental_confirmed_fill(None,ack)
    assert delta.quantity==0 and delta.value==0
    with pytest.raises(LiveOrderEvidenceError,match="no_new_confirmed_fill"):
        _=delta.average_price


def test_multiple_partial_fills_use_weighted_cumulative_deltas(identity):
    initial=event(identity,"OPEN",0)
    first=event(identity,"OPEN",2,"100")
    latter=event(identity,"COMPLETE",4,"101")
    a=fact(identity,[initial,first])
    b=fact(identity,[initial,first,latter])
    delta=incremental_confirmed_fill(a,b)
    assert b.terminal and b.filled_quantity==4
    assert delta.quantity==2
    assert delta.value==Decimal("204")
    assert delta.average_price==Decimal("102")
    assert incremental_confirmed_fill(b,b).quantity==0


def test_cancelled_after_partial_fill_is_not_mistaken_for_zero_exposure(identity):
    partial=event(identity,"OPEN",1,"100.5")
    cancelled=event(identity,"CANCELLED",1,"100.5")
    current=fact(identity,[partial,cancelled])
    assert current.terminal and current.filled_quantity==1
    assert incremental_confirmed_fill(None,current).quantity==1


@pytest.mark.parametrize("field,value",[
    ("order_id","WRONG"),("exchange","BSE"),
    ("tradingsymbol","TCS"),("product","MIS"),
    ("transaction_type","SELL"),("quantity",3),
])
def test_any_order_identity_mismatch_fails_closed(identity,field,value):
    with pytest.raises(LiveOrderEvidenceError):
        fact(identity,[event(identity,"OPEN",0,**{field:value})])


@pytest.mark.parametrize("paper,account",[(True,"KITE-TEST"),(None,"KITE-TEST"),(False,"OTHER")])
def test_live_client_identity_is_mandatory(identity,paper,account):
    with pytest.raises(LiveOrderEvidenceError,match="live_broker_account_binding"):
        fact(identity,[event(identity,"OPEN",0)],paper=paper,account=account)


@pytest.mark.parametrize("events",[
    [],
    [{"status":"OPEN"}],
])
def test_missing_history_fails_closed(identity,events):
    with pytest.raises(LiveOrderEvidenceError):
        fact(identity,events)


def test_order_history_cannot_erase_partial_fills(identity):
    with pytest.raises(LiveOrderEvidenceError,match="filled_quantity_conflict"):
        fact(identity,[event(identity,"OPEN",2,"100"),event(identity,"CANCELLED",1,"100")])


def test_terminal_order_cannot_reopen_or_fill_later_without_manual_reconciliation(identity):
    with pytest.raises(LiveOrderEvidenceError,match="terminal_order_history"):
        fact(identity,[event(identity,"CANCELLED",0),event(identity,"OPEN",0)])
    with pytest.raises(LiveOrderEvidenceError,match="terminal_order_history"):
        fact(identity,[event(identity,"CANCELLED",1,"100"),event(identity,"COMPLETE",4,"100")])


def test_complete_ack_requires_full_confirmed_quantity(identity):
    with pytest.raises(LiveOrderEvidenceError,match="complete_order_quantity_mismatch"):
        fact(identity,[event(identity,"COMPLETE",0)])
    with pytest.raises(LiveOrderEvidenceError,match="complete_order_quantity_mismatch"):
        fact(identity,[event(identity,"COMPLETE",2,"100")])


@pytest.mark.parametrize("field,value",[
    ("filled_quantity",-1),("filled_quantity",5),("filled_quantity",None),
    ("average_price","NaN"),("average_price","-1"),("quantity",True),
])
def test_invalid_broker_numbers_never_form_a_fill(identity,field,value):
    with pytest.raises(LiveOrderEvidenceError):
        fact(identity,[event(identity,"OPEN",1,"100",**{field:value})])


def test_identity_cannot_change_between_reconciliation_snapshots(identity):
    first=fact(identity,[event(identity,"OPEN",1,"100")])
    other=replace(identity,order_id="ORDER-OTHER")
    later=fact(other,[event(other,"COMPLETE",4,"100")])
    with pytest.raises(LiveOrderEvidenceError,match="fill_projection_order_identity_conflict"):
        incremental_confirmed_fill(first,later)


def test_nonmonotone_projection_fails_closed(identity):
    original=fact(identity,[event(identity,"OPEN",2,"100")])
    older=fact(identity,[event(identity,"OPEN",1,"100")])
    with pytest.raises(LiveOrderEvidenceError,match="fill_delta_not_monotone"):
        incremental_confirmed_fill(original,older)


def test_average_price_restatement_without_new_shares_is_not_a_new_fill(identity):
    original=fact(identity,[event(identity,"OPEN",2,"100")])
    restated=fact(identity,[event(identity,"OPEN",2,"102")])
    with pytest.raises(LiveOrderEvidenceError,match="fill_delta_not_monotone"):
        incremental_confirmed_fill(original,restated)


@pytest.mark.parametrize("product",["MIS","CNC"])
def test_live_broker_fact_supports_cash_equity_products_but_not_submission(product):
    key=LiveOrderIdentity(uid="alice",account_id="KITE-TEST",order_id="ORDER-1",
        exchange="BSE",symbol="TCS",product=product,side="SELL",quantity=1)
    current=fact(key,[event(key,"COMPLETE",1,"123")])
    assert incremental_confirmed_fill(None,current)==LiveFillDelta(key,1,Decimal("123"))
