from datetime import datetime
from types import SimpleNamespace
import logging
import pytest


@pytest.mark.asyncio
async def test_logged_out_scheduler_reports_failure_without_flooding_or_broker_mutations(monkeypatch,caplog):
    from app.services import gamma_move_runner as runner
    monkeypatch.setattr(runner,'_last_failure',{})
    monkeypatch.setattr(runner,'get_config',lambda uid: object())
    clock=[0.0];monkeypatch.setattr(runner,'time',SimpleNamespace(monotonic=lambda:clock[0]))
    def logged_out(*args):raise ValueError('No active Kite account')
    monkeypatch.setattr(runner,'session_for',logged_out)
    caplog.set_level(logging.WARNING,logger=runner.__name__)
    now=datetime.fromisoformat('2026-10-23T09:16:00+05:30');out={}
    for tick in range(4000):
        clock[0]=tick/10
        await runner._scan_uid('test-owner',now,out)
    failures=[r for r in caplog.records if 'cycle failed' in r.message]
    assert len(failures)==1
    assert out['test-owner']=={'error':'No active Kite account'}
    clock[0]=600.0;await runner._scan_uid('test-owner',now,out)
    assert len([r for r in caplog.records if 'cycle failed' in r.message])==2
