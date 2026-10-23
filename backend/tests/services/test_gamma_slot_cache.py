from datetime import datetime
from unittest.mock import AsyncMock
from types import SimpleNamespace

import pytest
from app.engines.gamma_move import GammaMoveConfig
from app.services import gamma_move_scanner as scanner


@pytest.mark.asyncio
async def test_recovery_reuses_saved_selection_without_rebuilding_underlyings(monkeypatch):
    from app.services import gamma_move_scan_cache as cache, gamma_move_historical as historical
    from app.services.exchanges.kite import accounts
    from dataclasses import replace
    cfg = replace(GammaMoveConfig(), build_watchlists_times=('09:20',))
    at = datetime(2026, 10, 22, 9, 20, tzinfo=scanner.IST)
    cache.save_slot('cached-owner', cfg, at, [], {}, {})
    monkeypatch.setattr(accounts, 'get_active', lambda uid: object())
    client = SimpleNamespace(get_historical=AsyncMock(side_effect=AssertionError('unexpected history read')))
    monkeypatch.setattr(accounts, 'acquire_client', AsyncMock(return_value=client))
    underlyings = AsyncMock(side_effect=AssertionError('selection already recorded'))
    monkeypatch.setattr(historical, '_underlyings', underlyings)
    monkeypatch.setattr(scanner, '_prime_runtime', AsyncMock(return_value=[]))
    await scanner.recover_scheduled_watchlist('cached-owner', cfg, build_time=at)
    underlyings.assert_not_awaited()
    client.get_historical.assert_not_awaited()
    assert scanner.scan_stats('cached-owner')['cached_build_slots'] == 1


def test_saved_slot_is_bound_to_tenant_day_and_exact_config():
    from app.services import gamma_move_scan_cache as cache
    from dataclasses import replace
    cfg = GammaMoveConfig()
    at = datetime(2026, 10, 22, 9, 20, tzinfo=scanner.IST)
    cache.save_slot('owner-a', cfg, at, [], {}, {})
    assert cache.load_slot('owner-a', cfg, at) is not None
    assert cache.load_slot('owner-b', cfg, at) is None
    assert cache.load_slot('owner-a', replace(cfg, zone_pivot_len=cfg.zone_pivot_len+1), at) is None
