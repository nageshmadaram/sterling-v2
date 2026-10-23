"""The position-store reads used by async risk paths cannot block their loop."""
import threading
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from app.engines.snapback.config import SnapbackConfig


@pytest.mark.asyncio
@pytest.mark.parametrize('method', ['process_prospective_daily_mtm_and_exits', 'process_prospective_intraday_risk'])
async def test_async_risk_reads_positions_in_worker(monkeypatch, method):
    from app.services import snapback, snapback_prospective_collector as pc
    loop_thread = threading.get_ident()
    threads = []
    class Warehouse:
        def get_active_paper_positions(self):
            threads.append(threading.get_ident())
            return []
    monkeypatch.setattr(pc, 'SnapbackProspectiveCollector', lambda: SimpleNamespace(warehouse=Warehouse()))
    assert await getattr(snapback, method)(object(), SnapbackConfig()) == 0
    assert threads and all(t != loop_thread for t in threads)


@pytest.mark.asyncio
async def test_eod_reads_positions_in_worker():
    from app.services.snapback_eod import collect_eod_observations
    loop_thread = threading.get_ident()
    threads = []
    class Warehouse:
        def get_active_paper_positions(self):
            threads.append(threading.get_ident())
            return []
    result = await collect_eod_observations(client=object(), cfg=SnapbackConfig(), warehouse=Warehouse(),
        session_date=date(2026, 10, 23), now_ist=datetime(2026, 10, 23, 10, tzinfo=timezone.utc))
    assert result.errors == []
    assert threads and all(t != loop_thread for t in threads)


@pytest.mark.asyncio
async def test_snapshot_probes_run_in_worker_without_mutating_scan_state(monkeypatch):
    from app.services import snapback
    loop_thread = threading.get_ident()
    state = snapback.ScanState(rows=[{'signal_id': 'kept'}])
    monkeypatch.setitem(snapback._state, 'snapshot-thread-test', state)
    threads = []
    def probe(uid, cfg, view):
        threads.append(threading.get_ident())
        assert view is not state and view.rows is not state.rows
        view.rows.clear()
        return {'rows': []}
    monkeypatch.setattr(snapback, '_snapshot_readonly', probe, raising=False)
    monkeypatch.setattr(snapback, 'get_effective_config', lambda uid: SnapbackConfig())
    assert await snapback.snapshot_async('snapshot-thread-test') == {'rows': []}
    assert state.rows == [{'signal_id': 'kept'}]
    assert threads and all(t != loop_thread for t in threads)
