from datetime import date
from types import SimpleNamespace

import pytest

from app.engines.gamma_move import GammaMoveConfig
from app.services import gamma_move as service, gamma_move_history as history
from app.services import gamma_move_runner as runner, gamma_move_positions as positions


@pytest.mark.asyncio
@pytest.mark.parametrize('resolution,expected', [
    ('1minute_ohlcv_proxy', 'minute highs'),
    ('5minute_close_proxy', 'five-minute closes'),
    ('legacy_unknown', 'unverified'),
])
async def test_archive_resolution_governs_provenance(monkeypatch, resolution, expected):
    archive = {'source': 'historical_replay', 'signals': [], 'watchlist': [],
               'replay': {'resolution': resolution, 'complete': True}}
    monkeypatch.setattr(service, 'get_config', lambda uid: GammaMoveConfig(entry_trigger='closed_5m'))
    monkeypatch.setattr(service, 'ist_today', lambda: date(2026, 10, 24))
    monkeypatch.setattr(runner, 'session_for', lambda *a: None)
    monkeypatch.setattr(runner, 'is_paper', lambda uid: True)
    monkeypatch.setattr(history, 'read_day', lambda *a: archive)
    monkeypatch.setattr(history, 'list_days', lambda *a, **kw: [])
    monkeypatch.setattr(positions, 'load_record', lambda uid: SimpleNamespace(as_dict=lambda: {}))
    result = await service.snapshot('offline-user', '2026-10-22')
    warning = ' '.join(result['warnings'])
    assert expected in warning
    assert 'never execution evidence' in warning
    if resolution == '1minute_ohlcv_proxy':
        assert 'intraminute' in warning


@pytest.mark.asyncio
async def test_non_session_archive_is_preserved_and_disclosed(monkeypatch):
    signal = {'id': 'recorded-event'}
    archive = {'source': 'live_recorded', 'signals': [signal], 'watchlist': []}
    monkeypatch.setattr(service, 'get_config', lambda uid: GammaMoveConfig())
    monkeypatch.setattr(service, 'ist_today', lambda: date(2026, 10, 24))
    monkeypatch.setattr(runner, 'session_for', lambda *a: None)
    monkeypatch.setattr(runner, 'is_paper', lambda uid: True)
    monkeypatch.setattr(history, 'read_day', lambda *a: archive)
    monkeypatch.setattr(history, 'list_days', lambda *a, **kw: [
        {'day': '2026-10-18', 'signals': 1, 'watches': 0},
        {'day': '2026-10-20', 'signals': 0, 'watches': 2},
    ])
    monkeypatch.setattr(positions, 'load_record', lambda uid: SimpleNamespace(as_dict=lambda: {}))
    result = await service.snapshot('offline-user', '2026-10-18')
    assert result['candidates'] == [signal]
    assert result['is_trading_day'] is False
    assert 'outside the Gamma reference trading calendar' in ' '.join(result['warnings'])
    days = {row['day']: row for row in result['history_days']}
    assert days['2026-10-18']['is_trading_day'] is False
    assert days['2026-10-20']['is_trading_day'] is False
    assert days['2026-10-22']['is_trading_day'] is True
