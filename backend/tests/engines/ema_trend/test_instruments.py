from decimal import Decimal
from datetime import date
import pytest
from app.engines.ema_trend.instruments import contract,entry_expiry_block
from app.engines.ema_trend.sizing import size_plan
from app.engines.ema_trend.state_machine import apply_fill,finalize_entry,make_action
from .helpers import campaign,config


def test_contract_products_and_expiry_are_explicit():
    raw={"exchange":"NFO","tradingsymbol":"NIFTY26NOV25000CE","instrument_type":"CE","lot_size":25,"tick_size":.05,"expiry":"2026-11-26"}
    assert contract(raw,"swing")["product"]=="NRML"
    assert contract(raw,"scalping")["product"]=="MIS"
    assert entry_expiry_block(contract(raw,"swing"),date(2026,11,26))
    assert not entry_expiry_block(contract(raw,"swing"),date(2026,11,25))
    with pytest.raises(ValueError,match="MCX_MULTIPLIER"):
        contract({**raw,"exchange":"MCX"},"swing")
    with pytest.raises(ValueError,match="INDEX_REQUIRES"):
        contract({**raw,"exchange":"NSE","segment":"INDICES"},"swing")


def test_sizing_never_rounds_up_to_four_lots():
    cfg=config(allocation_inr=100000,fee_bps=0,slippage_bps=0,gap_reserve_atr=0)
    result=size_plan(cfg,100,98,2,100000,lot_size=25)
    assert result["quantity"]==125
    assert size_plan(cfg,100,97,2,100000,lot_size=25)["quantity"]==0


def test_quarters_and_partial_fills_are_whole_lots():
    c=campaign()
    c.lots.clear()
    c.lot_size=25
    c.instrument_type="CE"
    entry=make_action(c,"ENTRY","BUY",175,1,1000,Decimal(100))
    apply_fill(c,entry,"first-100",100,100)
    apply_fill(c,entry,"next-75",75,100)
    finalize_entry(c)
    assert c.q0==175 and c.quarter==25 and c.q0-3*c.quarter==100
    with pytest.raises(ValueError):
        make_action(c,"WEAK_10","SELL",26,2,2000,Decimal(100))
    sell=make_action(c,"WEAK_10","SELL",25,2,2000,Decimal(100))
    with pytest.raises(ValueError):
        apply_fill(c,sell,"invalid-fraction",1,100)
    assert c.qty==175
