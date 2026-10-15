import json
import pytest
from app.api.v1.endpoints import pcr
from app.services import db

@pytest.mark.asyncio
async def test_closed_restart_uses_durable_snapshot_without_scraping(monkeypatch):
    monkeypatch.setattr(pcr,'_CACHE',{})
    monkeypatch.setattr(pcr,'session_phase',lambda *a,**kw:'closed')
    monkeypatch.setattr(pcr,'is_trading_day',lambda *a,**kw:False)
    payload={'asOf':'2026-10-12T15:30:00+05:30','source':'live','series':{'NIFTY':{'latest':{'hhmm':'15:30','pcr':0.75}}}}
    monkeypatch.setattr(db,'get_config',lambda key,default:json.dumps(payload))
    async def scrape():raise AssertionError('Must not scrape after market close')
    monkeypatch.setattr(pcr,'_scrape_all',scrape)
    result=await pcr.pcr_session(None)
    assert result['series']==payload['series']
    assert result['source']=='snapshot'
    assert result['marketOpen'] is False
    assert result['asOf']==payload['asOf']


def test_partial_response_preserves_completed_session_marks():
    previous = {'asOf':'2026-10-12T15:30:00+05:30','series':{'NIFTY':{'marks':[{'hhmm':'09:15','pcr':1.0},{'hhmm':'15:30','pcr':0.8}], 'latest':{'hhmm':'15:30','pcr':0.8}}, 'BANKNIFTY':{'marks':[{'hhmm':'15:30','pcr':1.2}]}}}
    current = {'asOf':'2026-10-12T09:15:00+05:30','series':{'NIFTY':{'marks':[{'hhmm':'09:15','pcr':1.1}], 'latest':{'hhmm':'09:15','pcr':1.1}}}}
    result = pcr.merge_session_history(previous, current)
    assert result['series']['NIFTY']['marks'] == [{'hhmm':'09:15','pcr':1.1},{'hhmm':'15:30','pcr':0.8}]
    assert result['series']['NIFTY']['latest']['hhmm'] == '15:30'
    assert 'BANKNIFTY' in result['series']
    current['asOf'] = '2026-10-13T09:15:00+05:30'
    assert pcr.merge_session_history(previous, current) == current

@pytest.mark.asyncio
async def test_closed_weekday_recovers_real_complete_history(monkeypatch):
    from datetime import datetime
    class Clock:
        @staticmethod
        def now(zone): return datetime(2026,10,12,17,0,tzinfo=zone)
    monkeypatch.setattr(pcr,'datetime',Clock)
    monkeypatch.setattr(pcr,'_CACHE',{})
    monkeypatch.setattr(pcr,'_history_retry_at',0)
    monkeypatch.setattr(pcr,'session_phase',lambda *a,**kw:'closed')
    monkeypatch.setattr(pcr,'is_trading_day',lambda *a,**kw:True)
    monkeypatch.setattr(pcr,'calendar_covers',lambda *a:True)
    monkeypatch.setattr(db,'get_config',lambda *a:'{}')
    saved=[]
    monkeypatch.setattr(db,'set_config',lambda key,value:saved.append(json.loads(value)))
    async def scrape(): return {'series':{'NIFTY':{'sessionDate':'2026-10-12','marks':[{'hhmm':'15:30','pcr':0.9}],'latest':{'hhmm':'15:30','pcr':0.9}}}}
    monkeypatch.setattr(pcr,'_scrape_all',scrape)
    result=await pcr.pcr_session(None)
    assert result['marketOpen'] is False
    assert result['series']['NIFTY']['marks'][0]['hhmm']=='15:30'
    assert saved[0]['asOf'].startswith('2026-10-12')
