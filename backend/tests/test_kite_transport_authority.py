"""Real broker writes require canonical intent, including raw transport calls."""
import httpx
import pytest

from app.services.exchanges.kite.client import KiteClient
from app.services.snapback_family_mode import (
    INTENT_PROTECT,
    INTENT_REDUCE,
    canonical_broker_capability,
)


def broker_client():
    calls = []

    def handle(request):
        calls.append((request.method, request.url.path))
        return httpx.Response(200, json={
            "status": "success", "data": {"order_id": "O1", "trigger_id": 1},
        })

    client = KiteClient(api_key="key", access_token="token", is_paper=False)
    client._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handle), base_url="https://api.kite.trade",
    )
    return client, calls


@pytest.mark.parametrize("family", ["true", "false"])
@pytest.mark.parametrize("method,path,body", [
    ("POST", "/orders/regular", {"transaction_type": "BUY"}),
    ("PUT", "/orders/regular/O1", {"order_type": "MARKET"}),
    ("PUT", "/portfolio/positions", {"new_product": "MIS"}),
    ("POST", "/gtt/triggers", {"orders": "[]"}),
    ("PUT", "/gtt/triggers/1", {"orders": "[]"}),
    ("POST", "/mf/orders", {"transaction_type": "BUY"}),
    ("POST", "/mf/sips", {"amount": 1000}),
    ("PUT", "/mf/sips/1", {"amount": 2000}),
    ("POST", "/alerts", {"type": "ato", "basket": "[]"}),
    ("PUT", "/alerts/a1", {"type": "ato", "status": "enabled"}),
])
async def test_raw_live_write_cannot_bypass_canonical_authority(monkeypatch, family, method, path, body):
    monkeypatch.setenv("STERLING_FAMILY_MODE", family)
    client, calls = broker_client()
    try:
        with pytest.raises(PermissionError):
            await client._auth_request(method, path, data=body)
        assert not [call for call in calls if call[0] != "GET"]
    finally:
        await client.close()


async def test_increase_capability_cannot_override_production_shadow(monkeypatch):
    monkeypatch.setenv("STERLING_FAMILY_MODE", "false")
    client, calls = broker_client()
    try:
        with canonical_broker_capability("entry"):
            with pytest.raises(PermissionError, match="live execution"):
                await client._auth_post("/orders/regular", {"transaction_type": "BUY"})
        assert calls == []
    finally:
        await client.close()


@pytest.mark.parametrize("intent,method,path", [
    (INTENT_REDUCE, "POST", "/orders/regular"),
    (INTENT_PROTECT, "POST", "/gtt/triggers"),
    (INTENT_PROTECT, "PUT", "/gtt/triggers/1"),
])
async def test_canonical_exit_and_protection_survive_shadow_floor(intent, method, path):
    client, calls = broker_client()
    try:
        with canonical_broker_capability("verified-position", intent=intent):
            await client._auth_request(method, path, data={})
        assert calls == [(method, path)]
    finally:
        await client.close()


async def test_generic_canonical_modification_is_not_live_entry_authority(monkeypatch):
    from app.services.execution_service import CanonicalExecutionService

    service = CanonicalExecutionService()
    monkeypatch.setattr(service, "is_trading_allowed", lambda **kw: (True, "OK"))
    monkeypatch.setattr("app.services.execution_service.db.get_execution_control", lambda **kw: {
        "operator_state": "RUNNING", "recovery_state": "CLEAN",
    })
    client, calls = broker_client()
    client._account_id = "a1"
    try:
        result = await service.modify_order(
            uid="u1", account_id="a1", order_id="O1",
            changes={"order_type": "MARKET"}, broker_client=client,
        )
        assert result.success is False
        assert calls == []
    finally:
        await client.close()
