from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from app.services.exchanges.kite.margin_summary import margin_segments

PAYLOAD = {
    'equity': {'net': -6342.1, 'available': {'live_balance': 999, 'opening_balance': -6342.1}, 'utilised': {'debits': 0, 'm2m_unrealised': 50}},
    'commodity': {'net': 0, 'available': {'opening_balance': 0}, 'utilised': {'debits': 0}},
}

def test_preserves_negative_net_and_zero():
    assert margin_segments(PAYLOAD) == {'equity': {'available': -6342.1, 'used': 0, 'opening_balance': -6342.1}, 'commodity': {'available': 0, 'used': 0, 'opening_balance': 0}}

def test_missing_invalid_values_are_unknown():
    assert margin_segments({})['equity']['available'] is None
    assert margin_segments({'equity': {'net': 'nan'}})['equity']['available'] is None

@pytest.mark.asyncio
async def test_telegram_both_segments(monkeypatch):
    from app.services.notifications import telegram_kite as tk
    from app.services.exchanges.kite import accounts
    monkeypatch.setattr(tk, '_active_account', lambda uid: SimpleNamespace(user_name='Test <User>', label='Kite', kite_user_id='TEST'))
    monkeypatch.setattr(accounts, 'acquire_client', AsyncMock(return_value=SimpleNamespace(get_margins=AsyncMock(return_value=PAYLOAD))))
    text = await tk.build_kite_funds_text()
    assert '<b>Equity</b>' in text and '<b>Commodity</b>' in text
    assert text.count('-₹6,342.10') == 2 and text.count('₹0.00') == 4
    assert 'Test &lt;User&gt;' in text
    assert '999' not in text and '50.00' not in text

@pytest.mark.asyncio
@pytest.mark.parametrize('inventory_fails', [False, True])
async def test_mini_app_matches_with_independent_inventory_failure(monkeypatch, inventory_fails):
    from app.api.v1.endpoints.tma import get_tma_data
    from app.core.auth import UserContext
    from app.services.exchanges.kite import accounts, auth
    from app.api.v1.endpoints import pcr
    from app.services.notifications import telegram_kite as tk
    monkeypatch.setattr(pcr, 'pcr_session', AsyncMock(return_value={}))
    monkeypatch.setattr(tk, '_load_alerted', lambda uid: None)
    monkeypatch.setattr(tk, '_ready_rows', lambda uid: [])
    monkeypatch.setattr(tk, '_active_rows', lambda uid: [])
    acct = SimpleNamespace(id='test', user_name='Test', user_id='default')
    client = SimpleNamespace(get_positions_raw=AsyncMock(return_value={'net': []}), get_orders=AsyncMock(return_value=[]), get_margins=AsyncMock(return_value=PAYLOAD))
    if inventory_fails:
        client.get_positions_raw.side_effect = RuntimeError('Inventory unavailable')
    monkeypatch.setattr(accounts, 'get_active', lambda uid: acct)
    monkeypatch.setattr(accounts, 'acquire_client', AsyncMock(return_value=client))
    monkeypatch.setattr(auth, 'ensure_session', AsyncMock(return_value=SimpleNamespace(connected=True, transient=False)))
    data = await get_tma_data(UserContext(user_id='default', username='test', role='admin'))
    assert data['funds']['equity'] == margin_segments(PAYLOAD)['equity']
    assert data['funds']['commodity'] == margin_segments(PAYLOAD)['commodity']
    assert data['funds_available'] is True
    assert data['broker_data_available'] is not inventory_fails
