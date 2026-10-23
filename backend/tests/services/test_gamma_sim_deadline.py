from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_gamma_simulation_has_its_own_bounded_deadline(monkeypatch):
    from app.services import gamma_move_runner as runner, sim_scan_lane
    calls = []
    monkeypatch.setattr(runner, '_kite_user_ids', lambda: ['sim:default'])
    monkeypatch.setattr(runner, '_scan_uid', AsyncMock())
    monkeypatch.setattr(sim_scan_lane, 'spawn', lambda kind, uid, factory, **kw: calls.append((kind, uid, kw)))
    await runner.scan_all_once()
    assert calls == [('gamma', 'sim:default', {'timeout_s': 1500.0})]
    assert sim_scan_lane.PASS_TIMEOUT_S == 300.0
