from datetime import datetime, timedelta
from types import SimpleNamespace
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
from app.engines.gamma_move import Candle, GammaMoveConfig, InstrumentRef, WatchContract
from app.services import gamma_move_scanner as scanner


@pytest.mark.asyncio
async def test_simulation_reuses_only_owner_market_facts_with_independent_engine(monkeypatch):
    from app.services import gamma_move
    cfg = GammaMoveConfig()
    now = datetime(2026, 10, 23, 10, 0, tzinfo=scanner.IST)
    bar = Candle(int((now-timedelta(minutes=5)).timestamp()*1000), 100, 101, 99, 100, 1000, 5000)
    watch = WatchContract('TEST', 1, 'L1', InstrumentRef('2', 'TEST26NOV100CE', 'CE', 100, '2026-11-26'), 'HOI-1', 5000, 1, 100, 99, 1)
    owner_engine = SimpleNamespace(cfg=cfg)
    parent = scanner.RuntimeWatch(watch, owner_engine, [bar], [bar], 100, oi_peak_day=now.date())
    monkeypatch.setitem(scanner._runtime, 'owner', {2: parent})
    monkeypatch.setitem(scanner._runtime, 'sim:owner', {})
    monkeypatch.setitem(scanner._stats, 'owner', {'stage_b': {}, 'last_run_ms': int(now.timestamp()*1000)})
    monkeypatch.setattr(gamma_move, 'get_config', lambda uid: cfg)
    monkeypatch.setattr(scanner, '_read_bar_inputs', lambda *args: {})
    seen = []
    def replay(w, config, bars, underlying_bars, spot, signals, inputs, day):
        seen.extend(signals)
        engine = SimpleNamespace(cfg=config, last_eval=None, state=SimpleNamespace(value='WAITING'))
        return scanner.RuntimeWatch(w, engine, bars, underlying_bars, spot, oi_peak_day=day)
    monkeypatch.setattr(scanner, '_build_runtime', replay, raising=False)
    body = await scanner.reuse_owner_market_data('sim:owner', cfg, now=now, recorded_signals=['sim-only'])
    assert body['watching'] == 1
    child = scanner.runtimes('sim:owner')[2]
    assert child.engine is not owner_engine
    assert seen == ['sim-only']
    child.closed_bars.append(bar)
    assert len(parent.closed_bars) == 1
    assert await scanner.reuse_owner_market_data('sim:owner', replace(cfg, zone_pivot_len=cfg.zone_pivot_len+1), now=now) is None
