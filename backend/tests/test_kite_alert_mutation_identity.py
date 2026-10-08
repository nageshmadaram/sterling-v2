"""Unknown alert types/identities must never authorize a broker mutation."""
from unittest.mock import AsyncMock

import pytest

from app.services.exchanges.kite.client import KiteClient
from app.services.exchanges.kite.errors import KitePermissionError


def _client(*, is_paper=False):
    client = KiteClient(api_key="ak", api_secret="sec", access_token="tok", is_paper=is_paper)
    client._auth_post = AsyncMock(return_value={"uuid": "A1"})
    client._auth_put = AsyncMock(return_value={"uuid": "A1"})
    client.get_alert = AsyncMock(return_value={"uuid": "A1", "type": "simple"})
    return client


FIELDS = {
    "name": "alert", "lhs_exchange": "NSE", "lhs_tradingsymbol": "INFY",
    "lhs_attribute": "LastTradedPrice", "operator": ">=", "rhs_constant": 1000,
}


@pytest.mark.parametrize("is_paper", [False, True])
@pytest.mark.parametrize("types", [
    {"alert_type": "UNKNOWN"}, {"alert_type": " "},
    {"type": "future-order-type"}, {"type": "ato", "alert_type": "simple"},
])
async def test_unknown_or_conflicting_create_type_never_posts(is_paper, types):
    client = _client(is_paper=is_paper)
    with pytest.raises(KitePermissionError, match="alert type"):
        await client.create_alert(**FIELDS, **types)
    client._auth_post.assert_not_awaited()


async def test_whitespace_padded_live_ato_cannot_bypass_creation_floor():
    client = _client()
    with pytest.raises(KitePermissionError, match="Live ATO"):
        await client.create_alert(**FIELDS, alert_type=" ATO ")
    client._auth_post.assert_not_awaited()


async def test_create_simple_alias_is_normalized_on_wire():
    client = _client()
    await client.create_alert(**FIELDS, type=" SIMPLE ")
    assert client._auth_post.await_args.args[1]["type"] == "simple"


@pytest.mark.parametrize("current", [
    {"uuid": "A1", "type": "unknown"},
    {"uuid": "A1", "type": "simple", "alert_type": "ato"},
    {"uuid": "OTHER", "type": "simple"},
    {"type": "simple"}, [], None,
])
@pytest.mark.parametrize("is_paper", [False, True])
async def test_unknown_broker_identity_or_type_never_puts(current, is_paper):
    client = _client(is_paper=is_paper)
    client.get_alert.return_value = current
    with pytest.raises(KitePermissionError):
        await client.modify_alert("A1", status="enabled")
    client._auth_put.assert_not_awaited()


@pytest.mark.parametrize("types", [
    {"alert_type": "unknown"}, {"type": " "},
    {"type": "ato", "alert_type": "simple"},
])
async def test_unknown_requested_modify_type_never_puts(types):
    client = _client()
    with pytest.raises(KitePermissionError, match="alert type"):
        await client.modify_alert("A1", **types)
    client._auth_put.assert_not_awaited()


async def test_whitespace_padded_broker_ato_cannot_be_rearmed():
    client = _client()
    client.get_alert.return_value = {"uuid": "A1", "type": " ATO "}
    with pytest.raises(KitePermissionError, match="paper-only"):
        await client.modify_alert("A1", status="enabled")
    client._auth_put.assert_not_awaited()


@pytest.mark.parametrize("mode", [None, "false", 0, 1])
async def test_unknown_mode_cannot_delete_alerts(mode):
    client = _client()
    client._is_paper = mode
    client._auth_request = AsyncMock()
    with pytest.raises(KitePermissionError, match="broker mode"):
        await client.delete_alerts(["A1"])
    client._auth_request.assert_not_awaited()


@pytest.mark.parametrize("current", [
    {"uuid": "A2", "type": "ato"},
    {"uuid": "A2", "type": "UNKNOWN"},
    {"uuid": "OTHER", "type": "simple"},
    {"uuid": "A2", "type": "simple", "basket": [{"transaction_type": "SELL"}]},
])
async def test_live_alert_delete_checks_entire_batch_before_any_write(current):
    client = _client()
    client.get_alert.side_effect = [{"uuid": "A1", "type": "simple"}, current]
    client._auth_request = AsyncMock()
    with pytest.raises(KitePermissionError):
        await client.delete_alerts(["A1", "A2"])
    client._auth_request.assert_not_awaited()


async def test_paper_ato_deletion_is_simulated_without_deleting_broker_protection():
    client = _client(is_paper=True)
    client.get_alert.return_value = {"uuid": "A1", "type": "ato"}
    client._auth_request = AsyncMock()
    result = await client.delete_alerts(["A1"])
    assert result["paper"] is True and result["simulated"] is True
    client._auth_request.assert_not_awaited()


async def test_live_simple_label_cannot_hide_basket_on_disable():
    client = _client()
    client.get_alert.return_value = {"uuid": "A1", "type": "simple", "basket": []}
    with pytest.raises(KitePermissionError, match="paper-only"):
        await client.modify_alert("A1", status="disabled")
    client._auth_put.assert_not_awaited()


@pytest.mark.parametrize("mode", [None, "true", "false", 1, 0])
async def test_unknown_transport_mode_never_mutates_alerts(mode):
    client = _client()
    client._is_paper = mode
    with pytest.raises(KitePermissionError, match="broker mode"):
        await client.create_alert(**FIELDS)
    with pytest.raises(KitePermissionError, match="broker mode"):
        await client.modify_alert("A1", status="enabled")
    client._auth_post.assert_not_awaited()
    client._auth_put.assert_not_awaited()
