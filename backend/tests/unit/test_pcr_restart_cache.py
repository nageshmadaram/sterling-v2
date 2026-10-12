import json
import pytest
from app.api.v1.endpoints import pcr
from app.services import db

@pytest.mark.asyncio
async def test_closed_restart_uses_durable_snapshot_without_scraping(monkeypatch):
    monkeypatch.setattr(pcr,'_CACHE',{})
    monkeypatch.setattr(pcr,'session_phase',lambda *a,**kw:'closed')
    payload={'asOf':'2026-10-12T15:30:00+05:30','source':'live','series':{'NIFTY':{'latest':{'hhmm':'15:30','pcr':0.75}}}}
    monkeypatch.setattr(db,'get_config',lambda key,default:json.dumps(payload))
    async def scrape():raise AssertionError('Must not scrape after market close')
    monkeypatch.setattr(pcr,'_scrape_all',scrape)
    result=await pcr.pcr_session(None)
    assert result['series']==payload['series']
    assert result['source']=='snapshot'
    assert result['marketOpen'] is False
    assert result['asOf']==payload['asOf']
