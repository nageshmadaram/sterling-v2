"""
Unit tests for Zerodha Kite Diagnostics Service.
"""
import pytest
from app.services.providers.kite.diagnostics import (
    KiteDiagnosticSuiteResult,
    run_kite_diagnostics,
    verify_network_connectivity,
    verify_kite_api_reachability,
    verify_kite_session,
    verify_kite_margins,
    verify_kite_instruments,
    verify_kite_historical,
    verify_kite_quotes,
    verify_kite_orders_gtt,
)


@pytest.mark.asyncio
async def test_network_connectivity_check():
    """Verify internet/DNS socket check produces valid results."""
    res = await verify_network_connectivity()
    assert res.id == "internet_network"
    assert res.status in ("PASS", "FAIL")
    assert "internet_status" in res.metrics
    assert len(res.field_checks) == 2


@pytest.mark.asyncio
async def test_kite_api_reachability_check():
    """Verify Kite API gateway reachability check."""
    res = await verify_kite_api_reachability()
    assert res.id == "kite_gateway"
    assert "status_code" in res.metrics


@pytest.mark.asyncio
async def test_kite_session_without_account():
    """Verify session check handles null account gracefully."""
    res = await verify_kite_session(None)
    assert res.id == "kite_session"
    assert res.status == "WARNING"
    assert "None Active" in res.field_checks[0].value


@pytest.mark.asyncio
async def test_kite_margins_without_account():
    """No broker session must never fabricate a cash ledger."""
    res = await verify_kite_margins(None)
    assert res.id == "kite_margins"
    assert res.status == "WARNING"
    assert res.metrics["cash"] is None
    assert res.metrics["collateral"] is None
    assert "not verified" in res.summary.lower()


@pytest.mark.asyncio
async def test_kite_instruments_without_account_is_unverified():
    """A canned instrument count must never certify readiness."""
    res = await verify_kite_instruments(None)
    assert res.id == "kite_instruments"
    assert res.status == "WARNING"
    assert res.metrics["total_instruments"] is None


@pytest.mark.asyncio
async def test_kite_historical_without_account_is_unverified():
    res = await verify_kite_historical(None)
    assert res.id == "kite_historical"
    assert res.status == "WARNING"
    assert res.metrics["candles_count"] == 0
    assert res.metrics["last_close"] is None


@pytest.mark.asyncio
async def test_kite_quotes_without_account_are_unverified():
    res = await verify_kite_quotes(None)
    assert res.id == "kite_quotes"
    assert res.status == "WARNING"
    assert res.metrics["ltp"] is None
    assert res.metrics["bid"] is None
    assert res.metrics["ask"] is None


@pytest.mark.asyncio
async def test_kite_orders_gtt_without_account_has_unknown_mode():
    res = await verify_kite_orders_gtt(None)
    assert res.id == "kite_orders_gtt"
    assert res.status == "WARNING"
    assert res.metrics["is_paper"] is None
    assert res.field_checks[1].value == "Unknown"


@pytest.mark.asyncio
async def test_kite_diagnostics_suite_runner():
    """Verify full suite runner runs all categories."""
    suite = await run_kite_diagnostics("test_user_default")
    assert isinstance(suite, KiteDiagnosticSuiteResult)
    assert suite.total_tests == 8
    assert len(suite.categories) == 8
    cat_ids = [c.id for c in suite.categories]
    assert "internet_network" in cat_ids
    assert "kite_gateway" in cat_ids
    assert "kite_session" in cat_ids
    assert "kite_margins" in cat_ids
    assert "kite_instruments" in cat_ids
    assert "kite_historical" in cat_ids
    assert "kite_quotes" in cat_ids
    assert "kite_orders_gtt" in cat_ids


@pytest.mark.asyncio
async def test_connected_flag_alone_does_not_certify_profile_or_margins(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock
    from app.services.exchanges.kite import accounts as kite_accounts

    acct = MagicMock()
    acct.connected = True
    acct.has_credentials = True
    acct.kite_user_id = "CACHED"
    acct.label = "Cached"
    acct.is_paper = True

    client = MagicMock()
    client.get_profile = AsyncMock(return_value={})
    client.get_margins = AsyncMock(return_value={})
    monkeypatch.setattr(kite_accounts, "acquire_client", AsyncMock(return_value=client))

    session = await verify_kite_session(acct)
    margins = await verify_kite_margins(acct)

    assert session.status == "WARNING"
    assert session.metrics["connected"] is False
    assert "missing user_id" in (session.error_message or "")
    assert margins.status == "WARNING"
    assert margins.metrics["cash"] is None
    assert margins.source_origin == "unverified"


@pytest.mark.asyncio
async def test_kite_authenticated_diagnostics(monkeypatch):
    """Verify diagnostics when user is authenticated with live Kite session."""
    from unittest.mock import AsyncMock, MagicMock
    from app.services.exchanges.kite import accounts as kite_accounts

    mock_acct = MagicMock()
    mock_acct.id = "KITE-TEST01"
    mock_acct.user_id = "test_user_1"
    mock_acct.label = "My Kite"
    mock_acct.api_key = "test_key"
    mock_acct.connected = True
    mock_acct.has_credentials = True
    mock_acct.is_paper = False
    mock_acct.kite_user_id = "AA0595"

    mock_client = MagicMock()
    mock_client.get_profile = AsyncMock(return_value={"user_id": "AA0595", "user_name": "Nagesh Madaram"})
    mock_client.get_margins = AsyncMock(return_value={"equity": {"available": {"cash": 150000.0, "collateral": 25000.0}}})
    mock_client.get_historical = AsyncMock(return_value=[{"close": 24550.0}])
    mock_client.get_quote = AsyncMock(return_value={
        "NSE:NIFTY 50": {
            "last_price": 24550.0,
            "depth": {
                "buy": [{"price": 24549.5}],
                "sell": [{"price": 24550.5}],
            },
        }
    })
    mock_client.get_gtts = AsyncMock(return_value=[{"id": 1, "status": "active"}])
    mock_client.search_instruments = AsyncMock(return_value=[
        {"tradingsymbol": "NIFTY 50", "segment": "INDICES", "exchange": "NSE"},
        {"tradingsymbol": "NIFTY26OCT25000CE", "segment": "NFO-OPT", "exchange": "NFO"},
    ])

    monkeypatch.setattr(kite_accounts, "acquire_client", AsyncMock(return_value=mock_client))

    sess_res = await verify_kite_session(mock_acct)
    assert sess_res.status == "PASS"
    assert "AA0595" in sess_res.summary
    assert "Nagesh Madaram" in sess_res.summary

    margin_res = await verify_kite_margins(mock_acct)
    assert margin_res.status == "PASS"
    assert margin_res.metrics["cash"] == 150000.0

    hist_res = await verify_kite_historical(mock_acct)
    assert hist_res.status == "PASS"
    assert hist_res.metrics["last_close"] == 24550.0

    quote_res = await verify_kite_quotes(mock_acct)
    assert quote_res.status == "PASS"
    assert quote_res.metrics["ltp"] == 24550.0

    gtt_res = await verify_kite_orders_gtt(mock_acct)
    assert gtt_res.status == "PASS"
    assert gtt_res.metrics["gtt_count"] == 1
    assert gtt_res.metrics["is_paper"] is False

    inst_res = await verify_kite_instruments(mock_acct)
    assert inst_res.status == "PASS"
    assert inst_res.metrics["total_instruments"] == 2

