import asyncio
from unittest.mock import AsyncMock
from types import SimpleNamespace
import pytest
from app.services import snapback as service

@pytest.mark.asyncio
async def test_multimode_previews_do_not_change_configuration_or_arm_other_modes(monkeypatch):
    uid='multi-mode-fixture'
    cfg=SimpleNamespace(trading_mode='scalp')
    monkeypatch.setattr(service,'get_config',lambda uid:cfg)
    monkeypatch.setattr(service,'snapshot_async',AsyncMock(side_effect=lambda uid:{'rows':service.status(uid).preview_rows}))
    calls=[]
    async def scan(uid, **kwargs):
        calls.append(kwargs.get('mode_override'))
        return {'rows':[{'signal_id':'fixture','trading_mode':kwargs.get('mode_override'),'state':'armed'}]}
    monkeypatch.setattr(service,'scan_once',scan)
    result=await service.scan_modes(uid,['scalp','swing','intraday'])
    assert calls==[None,'swing','intraday']
    assert cfg.trading_mode=='scalp'
    assert all(r['execution_eligible'] is False for r in result['rows'])
    assert not service.status(uid).signals

@pytest.mark.asyncio
async def test_stop_cancels_owned_scan_and_cleans_task_registry(monkeypatch):
    uid='cancel-mode-fixture'
    entered=asyncio.Event()
    monkeypatch.setattr(service,'get_config',lambda uid:SimpleNamespace(trading_mode='scalp'))
    monkeypatch.setattr(service,'snapshot_async',AsyncMock(side_effect=lambda uid:{'scanning':uid in service._interactive_scans}))
    async def scan(uid, **kwargs):
        entered.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(service,'scan_once',scan)
    run=asyncio.create_task(service.scan_modes(uid,['scalp']))
    await entered.wait()
    assert uid in service._interactive_scans
    await service.stop_scan(uid)
    await run
    assert uid not in service._interactive_scans
