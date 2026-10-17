from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest

from app.services import db
from app.services.ema_trend import config_store, readiness
from app.services.exchanges.kite.client import KiteClient


@pytest.fixture
def configured(tmp_path,monkeypatch):
    monkeypatch.setattr(db,"_DB_PATH",str(tmp_path/"readiness.db"))
    assert db.init()
    config_store.update("alice",0,{"symbols":["NSE:INFY"]})


@pytest.mark.asyncio
async def test_connected_check_never_writes_or_claims_live_acceptance(configured,monkeypatch):
    from app.services.exchanges.kite import accounts
    from app.services.ema_trend import execution,scanner
    account=SimpleNamespace(id="KITE-TEST",connected=True,is_sim=False,is_paper=False,kite_user_id="AB1234")
    client=KiteClient(api_key="test",api_secret="test",access_token="test",is_paper=False)
    client._account_id=account.id
    client.get_profile=AsyncMock(return_value={"user_id":"AB1234"})
    client.get_positions_raw=AsyncMock(return_value={"net":[]})
    client.get_holdings=AsyncMock(return_value=[])
    client.get_orders=AsyncMock(return_value=[])
    client.get_gtts=AsyncMock(return_value=[])
    client.place_order=AsyncMock(side_effect=AssertionError("NO BROKER WRITE"))
    client.place_gtt=AsyncMock(side_effect=AssertionError("NO BROKER WRITE"))
    monkeypatch.setattr(accounts,"get_active",lambda owner:account)
    monkeypatch.setattr(accounts,"acquire_client",AsyncMock(return_value=client))
    meta=SimpleNamespace(token=123,as_meta=lambda:{"exchange":"NSE","tradingsymbol":"INFY",
        "instrument_type":"EQ","lot_size":1,"tick_size":.05})
    monkeypatch.setattr(scanner,"equity_metadata",AsyncMock(return_value=meta))
    monkeypatch.setattr(execution,"quote",AsyncMock(return_value=SimpleNamespace(quote_ts=123,bid=100,ask=100.05)))
    result=await readiness.check("alice","NSE:INFY","swing",1)
    checks={c["name"]:c for c in result["checks"]}
    assert checks["broker_identity"]["status"]=="PASS"
    assert checks["exact_contract"]["status"]=="PASS"
    assert checks["owned_broker_protection"]["status"]=="BLOCKED"
    assert not result["valid_for_execution"] and not result["live_eligible"]
    client.place_order.assert_not_called()
    client.place_gtt.assert_not_called()
