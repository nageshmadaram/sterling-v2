from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.services.simulation import SimulationRunner, SimConfig, SimSignalEvent, SimTradeEvent
from app.services import replay_strategy_views as views
from app.engines.sterling_kite_engine.config import SterlingKiteEngineConfig

IST = timezone(timedelta(hours=5, minutes=30))


def clock(hour, minute=0):
    return int(datetime(2026, 10, 9, hour, minute, tzinfo=IST).timestamp() * 1000)


def runner():
    r = SimulationRunner()
    r._uid = 'fixture-user'
    r._config = SimConfig(date='2026-10-09', strategies=['supertrend', 'snapback', 'gamma_move'])
    r._start_epoch = clock(9, 15) / 1000
    r._end_epoch = clock(15, 30) / 1000
    r._current_sim_epoch = clock(10, 15) / 1000
    r._strategy_notes = {}
    r._supertrend_cfg = SterlingKiteEngineConfig()
    return r


def bar(hour, price=100):
    return dict(time=clock(hour, 15) / 1000, open=price, high=price+2, low=price-2, close=price+1, volume=500)


def gamma_event(timestamp, ident='G1', **levels):
    return dict(id=ident, at_ms=timestamp, underlying='NIFTY', tier='ENTRY', instrument={
        'tradingsymbol': 'NIFTY26O0925000CE', 'option_type': 'CE', 'strike': 25000},
        levels={'entry': 100, 'stop': 90, 't1': 120, **levels})


def test_hourly_prefix_excludes_forming_and_future_candles():
    r = runner()
    r._supertrend_hourly = {'NIFTY': [bar(9), bar(10, 500)]}
    assert views.hourly_prefix(r, 'NIFTY', clock(10, 14)) == []
    prefix = views.hourly_prefix(r, 'NIFTY', clock(10, 15))
    assert len(prefix) == 1
    assert prefix[0].close == 101
    r._supertrend_hourly['NIFTY'][1]['close'] = 999
    r._supertrend_hourly['NIFTY'][1]['high'] = 1000
    assert views.hourly_prefix(r, 'NIFTY', clock(10, 15)) == prefix


def test_entry_delegates_to_production_core_once_per_closed_hour(monkeypatch):
    r = runner()
    r._supertrend_hourly = {'NIFTY': [bar(9), bar(10)]}
    calls = []
    from app.engines.sterling_kite_engine.engine import SterlingKiteEngine
    def generate(self, candles, symbol):
        calls.append((self.cfg, candles, symbol))
        return [SimpleNamespace(direction='long', strength='STRONG', stop_loss=90)]
    monkeypatch.setattr(SterlingKiteEngine, 'generate', generate)
    assert views.supertrend_entry(r, 'NIFTY', clock(10, 14)) == []
    emitted = views.supertrend_entry(r, 'NIFTY', clock(10, 15))
    assert emitted[0]['target'] is None
    assert emitted[0]['canonical_entry_bar_ms'] == clock(9, 15)
    assert calls[0][0] is r._supertrend_cfg
    assert len(calls[0][1]) == 1
    assert views.supertrend_entry(r, 'NIFTY', clock(10, 50)) == []
    assert len(calls) == 1


def test_missing_hourly_data_never_relabels_five_minute_bars():
    r = runner()
    r._bar_history = {'NIFTY': [bar(9)] * 60}
    assert views.supertrend_entry(r, 'NIFTY', clock(14)) == []
    assert 'not substituted' in r._strategy_notes['supertrend']


def test_gamma_events_are_causal_idempotent_and_never_create_trades():
    r = runner()
    r._gamma_replay_days = {'2026-10-09': {'source': 'live_recorded', 'signals': [
        gamma_event(clock(9, 20)), gamma_event(clock(11, 30), 'G2', stop=None)], 'watchlist': [
        gamma_event(clock(15), 'future-watch')]}}
    views.emit_gamma_until(r, clock(10))
    views.emit_gamma_until(r, clock(10))
    assert len(r._stats.events) == 1
    assert r._stats.trades == []
    snap = views.gamma_snapshot(r)
    assert len(snap['candidates']) == 1
    assert snap['candidates'][0]['execution_eligible'] is False
    assert snap['historical'] is True
    views.emit_gamma_until(r, clock(12))
    assert len(r._stats.events) == 2
    assert r._stats.events[-1].stop is None
    assert r._stats.trades == []
    assert all(event.contract != 'future-watch' for event in r._stats.events)


@pytest.mark.asyncio
async def test_gamma_uses_retained_events_without_broker_or_live_mutations(monkeypatch):
    from app.services import gamma_move_history, gamma_move_historical
    from app.services.exchanges.kite import accounts
    r = runner()
    monkeypatch.setattr(accounts, 'get_active', lambda _: None)
    monkeypatch.setattr(gamma_move_history, 'read_day', lambda uid, day: {'signals': [gamma_event(clock(9, 20))], 'source': 'live_recorded'})
    async def forbidden(*a, **kw):
        raise AssertionError('broker reconstruction must not run for retained evidence')
    monkeypatch.setattr(gamma_move_historical, 'replay_day', forbidden)
    await views.load_gamma_days(r, datetime(2026, 10, 9).date(), datetime(2026, 10, 9).date())
    assert views.gamma_snapshot(r)['candidates'][0]['id'] == 'G1'


def test_kite_board_never_mints_missing_contracts_or_mixes_strategy_ladders():
    r = runner()
    r._stats.events = [SimSignalEvent(time_iso='10:00', timestamp_ms=clock(10), strategy='supertrend', instrument='NIFTY',
                                      direction='BULLISH', strength='STRONG', entry=25000, stop=24900),
                       SimSignalEvent(time_iso='10:00', timestamp_ms=clock(10), strategy='gamma_move', instrument='NIFTY',
                                      direction='BULLISH', strength='ENTRY', entry=100)]
    snapshot = views.kite_snapshot(r)
    assert len(snapshot['rows']) == 1
    assert snapshot['rows'][0]['legs'] == []
    assert snapshot['rows'][0]['target'] is None
    assert snapshot['rows'][0]['is_fresh'] is False
    assert snapshot['generated_ms'] == clock(10, 15)
