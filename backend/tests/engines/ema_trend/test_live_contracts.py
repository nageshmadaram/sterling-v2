"""Pure real-account EMA invariants; no Kite network or order send."""
from decimal import Decimal
import pytest
from pydantic import ValidationError

from app.engines.ema_trend.live_contracts import (
    BrokerIntent, BrokerStop, LiveCampaign,
)


def campaign(**extra):
    data=dict(
        id="ema-live-1",owner_uid="alice",account_id="KITE-REAL-1",
        exchange="NSE",symbol="INFY",mode="swing",product="CNC",
        strategy_version="1",config_hash="frozen-hash",signal_id="s-1",
        stop=Decimal("95"),
    )
    data.update(extra)
    return LiveCampaign(**data)


def order(kind,ident,qty,filled=0,status="RESERVED"):
    return BrokerIntent(intent_key=ident,side=kind,requested_qty=qty,
        confirmed_qty=filled,cumulative_value=Decimal(filled)*100,
        order_id=("OID-"+ident) if status in {
            "SUBMITTED","ACKNOWLEDGED","PARTIAL","COMPLETE"} else "",
        status=status)


def stop(**extra):
    row=dict(trigger_id="GTT-1",exchange="NSE",symbol="INFY",
        product="CNC",qty=4,trigger=Decimal("95"),verified_active=True)
    row.update(extra)
    return BrokerStop(**row)


def test_live_owner_can_never_be_simulated():
    with pytest.raises(ValidationError,match="CANNOT_BIND_SIMULATION"):
        campaign(owner_uid="sim:alice")
    with pytest.raises(ValidationError,match="CANNOT_BIND_SIMULATION"):
        campaign(account_id="SIM-alice")


def test_ack_and_working_order_cannot_claim_closed_without_fills():
    with pytest.raises(ValidationError,match="CLOSED_REQUIRES_TERMINAL"):
        campaign(phase="CLOSED", entries=[order("BUY","entry",4,status="ACKNOWLEDGED")])


def test_partial_and_addback_exits_preserve_exact_accounting():
    buys=[order("BUY","first",4,4,"COMPLETE"),order("BUY","add",2,2,"COMPLETE")]
    sells=[order("SELL","quarter",1,1,"COMPLETE"),order("SELL","half",1,1,"COMPLETE")]
    c=campaign(phase="PROTECTION_REQUIRED",entries=buys,exits=sells,
        confirmed_bought=6,confirmed_sold=2)
    assert c.held_qty==4
    assert c.protection is None
    with pytest.raises(ValidationError,match="ENTRY_ACCOUNTING_DIVERGENCE"):
        campaign(phase="PROTECTION_REQUIRED",entries=buys,exits=sells,
            confirmed_bought=5,confirmed_sold=2)


def test_protected_requires_exact_broker_cover_instrument_and_product():
    buy=order("BUY","entry",4,4,"COMPLETE")
    data=dict(phase="PROTECTED",entries=[buy],confirmed_bought=4)
    assert campaign(**data,protection=stop()).held_qty==4
    for s in (None,stop(qty=3),stop(verified_active=False),
              stop(symbol="TCS"),stop(product="MIS"),
              stop(trigger=Decimal("94")),stop(child_order_unresolved=True)):
        with pytest.raises(ValidationError):
            campaign(**data,protection=s)
    with pytest.raises(ValidationError,match="PROTECTED_REQUIRES_EXACT"):
        campaign(**data,protection=stop(),protection_submission_unknown=True)


def test_no_stop_can_exceed_owned_shares_or_share_a_duplicate_intent():
    buys=[order("BUY","same",4,4,"COMPLETE")]
    with pytest.raises(ValidationError,match="LIVE_STOP_QUANTITY_EXCEEDS"):
        campaign(phase="PROTECTION_REQUIRED",entries=buys,
            confirmed_bought=4,protection=stop(qty=5))
    with pytest.raises(ValidationError,match="LIVE_ORDER_INTENT_DUPLICATED"):
        campaign(entries=buys+[order("BUY","same",2,0)])


def test_closed_requires_no_holdings_pending_orders_or_broker_stop():
    buy=order("BUY","e",4,4,"COMPLETE")
    sell=order("SELL","x",4,4,"COMPLETE")
    c=campaign(phase="CLOSED",entries=[buy],exits=[sell],
        confirmed_bought=4,confirmed_sold=4)
    assert c.held_qty==0
    with pytest.raises(ValidationError):
        campaign(phase="CLOSED",entries=[buy],confirmed_bought=4)
    with pytest.raises(ValidationError):
        campaign(phase="CLOSED",protection_submission_unknown=True)


@pytest.mark.parametrize("status,filled",[
    ("COMPLETE",0),("COMPLETE",2),("ACKNOWLEDGED",5)])
def test_order_evidence_cannot_invent_shares(status,filled):
    with pytest.raises(ValidationError):
        order("BUY","broken",4,filled,status)


def test_unknown_submission_has_no_broker_order_id_and_cannot_close():
    prepared=campaign(phase="ENTRY_PENDING",entries=[order("BUY","entry",4,0,"UNKNOWN")])
    assert prepared.held_qty==0
    with pytest.raises(ValidationError):
        prepared.model_copy(update={"phase":"CLOSED"}).model_validate(
            {**prepared.model_dump(mode="json"),"phase":"CLOSED"})
