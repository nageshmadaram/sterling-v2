from datetime import date
import pytest

from app.services.kite_engine.settlement import settled_holding, whole_quantity


def holding(**changes):
    return {"exchange":"NSE","tradingsymbol":"INFY","product":"CNC","quantity":100,
            "realised_quantity":100,"used_quantity":0,"collateral_quantity":0,
            "t1_quantity":0,"discrepancy":False,"authorised_quantity":100,
            "authorised_date":"2026-10-16 00:00:00",**changes}


def test_settlement_never_adds_t1_or_pledged_or_sold_inventory():
    result=settled_holding([holding(quantity=80,realised_quantity=70,used_quantity=10,
                                    collateral_quantity=20,t1_quantity=500)],"NSE","INFY",date(2026,10,16))
    assert result["quantity"]==40 and result["sale_authorised"]


@pytest.mark.parametrize("changes", [{"discrepancy":True},{"discrepancy":None},
    {"quantity":0,"realised_quantity":0,"t1_quantity":100}])
def test_unsettled_or_discrepant_inventory_is_not_available(changes):
    result=settled_holding([holding(**changes)],"NSE","INFY",date(2026,10,16))
    assert result["quantity"]==0 and not result["sale_authorised"]


@pytest.mark.parametrize("changes", [{"authorised_date":"2026-10-15"},{"authorised_quantity":0}])
def test_inventory_is_not_depository_authorisation(changes):
    result=settled_holding([holding(**changes)],"NSE","INFY",date(2026,10,16))
    assert result["quantity"]==100 and not result["sale_authorised"]


def test_duplicate_and_cross_exchange_holdings_do_not_prove_inventory():
    assert settled_holding([holding(),holding()],"NSE","INFY",date(2026,10,16))["quantity"]==0
    assert settled_holding([holding()],"BSE","INFY",date(2026,10,16))["quantity"]==0


@pytest.mark.parametrize("value", [True,None,"nan","1.5","invalid"])
def test_invalid_quantities_are_not_truncated(value):
    with pytest.raises(ValueError):
        whole_quantity(value)
