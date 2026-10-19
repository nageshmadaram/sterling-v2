"""Mode deadlines, bar finality and live-plan lifecycles against real stores."""
from dataclasses import replace
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from app.core.mode_execution import (carry_entry_refusal, freeze_policy,
    policy_exit_reason, timeframe_minutes, trading_minutes_held)


def at(value='2026-10-07T10:00:00+05:30'):
    return datetime.fromisoformat(value)


@pytest.mark.parametrize('mode,minutes', [('ultra_scalping', 1), ('scalping', 5),
    ('intraday', 15), ('overnight', 60), ('swing', 60)])
def test_each_mode_has_a_finite_immutable_deadline(mode, minutes):
    now = at()
    policy = freeze_policy('test', mode, now)
    assert policy['hard_exit_ms'] > now.timestamp() * 1000
    assert timeframe_minutes(mode) == minutes
    due = datetime.fromtimestamp(policy['hard_exit_ms'] / 1000, now.tzinfo)
    assert policy_exit_reason(policy, due) == 'mode_holding_deadline'
    assert not policy_exit_reason(policy, now)


def test_ultra_deadline_is_twenty_minutes_and_intraday_cannot_cross_square_off():
    ultra = freeze_policy('test', 'ultra_scalping', at())
    assert ultra['hard_exit_ms'] == int((at() + timedelta(minutes=20)).timestamp() * 1000)
    late = at('2026-10-07T14:00:00+05:30')
    policy = freeze_policy('test', 'intraday', late)
    assert datetime.fromtimestamp(policy['hard_exit_ms'] / 1000, late.tzinfo).strftime('%H:%M') == '15:20'
    with pytest.raises(RuntimeError):
        freeze_policy('test', 'intraday', at('2026-10-07T15:10:00+05:30'))


def test_overnight_requires_a_surviving_broker_stop_and_expiry_buffer():
    now = at()
    policy = freeze_policy('test', 'overnight', now)
    assert carry_entry_refusal(policy, expiry='2026-11-26', now=now) == ''
    assert carry_entry_refusal(policy, expiry='2026-11-26', now=now, stop_mode='monitor') == 'overnight_broker_stop_required'
    assert carry_entry_refusal(policy, expiry='2026-10-08', now=now) == 'overnight_expiry_buffer'
    assert carry_entry_refusal(policy, expiry='', now=now) == 'overnight_expiry_unknown'


def test_overnight_refuses_friday_entry_and_flattens_before_the_weekend():
    friday = at('2026-10-09T10:00:00+05:30')
    policy = freeze_policy('test', 'overnight', friday)
    assert carry_entry_refusal(policy, expiry='2026-11-26', now=friday) == 'overnight_closed_session_gap'
    thursday = at('2026-10-08T10:00:00+05:30')
    policy = freeze_policy('test', 'overnight', thursday)
    assert policy_exit_reason(policy, at('2026-10-09T15:20:00+05:30')) == 'mode_closed_session_gap'


def test_closed_nights_do_not_consume_overnight_candle_budgets():
    left = at('2026-10-07T15:00:00+05:30')
    right = at('2026-10-08T09:30:00+05:30')
    assert trading_minutes_held(int(left.timestamp()*1000), int(right.timestamp()*1000)) == 45


@pytest.mark.parametrize('mode,bad', [('ultra_scalping', 5), ('intraday', 60), ('overnight', 1)])
def test_invalid_mode_timeframes_are_refused(mode, bad):
    with pytest.raises(ValueError):
        timeframe_minutes(mode, bad)


def test_one_minute_bar_is_not_treated_as_an_hourly_bar():
    from app.domain.models import Candle
    from app.services.kite_engine.scanner import drop_forming
    start = int(at().timestamp()*1000)
    bar = Candle(timestamp_ms=start, open=100, high=102, low=99, close=101, volume=10)
    assert drop_forming([bar], start+59_999, timeframe_ms=60_000) == []
    assert drop_forming([bar], start+60_000, timeframe_ms=60_000) == [bar]
    assert drop_forming([bar], start+60_000) == []


@pytest.mark.asyncio
async def test_scanner_fetches_the_selected_interval_and_keeps_caches_separate():
    from app.engines.sterling_kite_engine.config import SterlingKiteEngineConfig
    from app.services.kite_engine.scanner import KiteEngineScanner, UserScan
    from app.engines.sterling_kite_engine.engine import SterlingKiteEngine
    calls = []
    class Client:
        async def get_candles(self, inst, interval, limit):
            calls.append(interval)
            return []
    us = UserScan(engine=SterlingKiteEngine(), timeframe_minutes=1)
    scanner = KiteEngineScanner()
    await scanner._fetch_candles(Client(), us, 1, 'NIFTY')
    us.timeframe_minutes = 15
    await scanner._fetch_candles(Client(), us, 1, 'NIFTY')
    assert calls == ['1m', '15m']
    assert len(us.candle_cache) == 2


def test_mode_change_cannot_move_an_existing_supertrend_position(monkeypatch):
    from app.engines.sterling_kite_engine.schemas import EngineConfigModel
    from app.services.kite_engine import state, positions
    from fastapi import HTTPException
    monkeypatch.setattr(state, 'get_config', lambda uid: EngineConfigModel())
    monkeypatch.setattr(positions, 'open_positions', lambda uid: [object()])
    with pytest.raises(HTTPException) as exc:
        state.set_config('u', EngineConfigModel(horizon_mode='intraday'))
    assert exc.value.status_code == 409


def test_mode_position_limits_are_reserved_atomically_and_retries_are_idempotent(tmp_path, monkeypatch):
    from app.services import db
    from app.services.kite_engine import order_journal as journal
    monkeypatch.setattr(db, '_DB_PATH', str(tmp_path/'mode-journal.db'))
    db.init()
    policy = freeze_policy('test', 'ultra_scalping', at())
    args = dict(uid='mode-test', account_id='A', strategy_id='mode-test', generation_id='g',
                signal_id='s1', exchange='NFO', symbol='OPT1', side='BUY', quantity=1,
                payload={'mode_policy': policy})
    first = journal.reserve(**args)
    assert journal.reserve(**args).intent_key == first.intent_key
    with pytest.raises(ValueError, match='mode_concurrent_position_limit'):
        journal.reserve(**{**args, 'signal_id': 's2', 'symbol': 'OPT2'})


def test_daily_entry_cap_survives_closed_orders_and_restart(tmp_path, monkeypatch):
    from app.services import db
    from app.services.kite_engine import order_journal as journal
    monkeypatch.setattr(db, '_DB_PATH', str(tmp_path/'mode-journal.db'))
    db.init()
    policy = freeze_policy('test', 'swing', at())
    args = dict(uid='mode-test', account_id='A', strategy_id='mode-test', generation_id='g',
                signal_id='s1', exchange='NFO', symbol='OPT1', side='BUY', quantity=1,
                payload={'mode_policy': policy})
    journal.reserve(**args)
    journal.reserve(**{**args, 'signal_id': 's2', 'symbol': 'OPT2'})
    with pytest.raises(ValueError, match='mode_daily_entry_limit'):
        journal.reserve(**{**args, 'signal_id': 's3', 'symbol': 'OPT3'})


# The candidate lanes are enabled ONLY in this isolated test; the registry is
# promoted after their real-store entry/protection/exit path passes these tests.
from tests.snapback_live_harness import live_env, cycle, run, scalp_row, SCALP_NOW
_ = live_env


@pytest.mark.parametrize('mode', ['ultra_scalping', 'intraday', 'overnight'])
def test_new_snapback_modes_open_protect_and_exit_through_the_existing_journal(env, mode):
    from app.core import lane_registry
    from app.core.horizon import LaneState
    from app.engines.snapback.config import validate
    from app.services import snapback
    lane_key = f'snapback:{mode}'
    lane = lane_registry.LANES[lane_key]
    env.monkeypatch.setitem(lane_registry.LANES, lane_key, replace(lane, state=LaneState.LIVE_MINIMUM, rules_defined=True))
    cfg = validate({'trading_mode': mode}, base=env.cfg)
    env.monkeypatch.setattr(snapback, 'get_config', lambda uid=None: cfg)
    env.clock = SCALP_NOW
    env.rows = [scalp_row(signal_id=f'new-mode-{mode}', trading_mode=mode)]
    cycle(env)
    opened = env.positions.open_positions(env.uid)
    assert len(opened) == 1, env.dispatcher.status(env.uid)
    p = opened[0]
    assert p.lane == mode
    assert p.protection_state == 'ACTIVE' and p.gtt_id
    policy = p.rules['mode_policy']
    assert policy['mode'] == mode
    # Advance beyond the immutable limit, keeping a usable quote. The canonical
    # exit path must close once, irrespective of later strategy setting changes.
    env.clock = datetime.fromtimestamp(policy['hard_exit_ms']/1000+1, SCALP_NOW.tzinfo)
    if env.clock.hour >= 15 and env.clock.minute > 29:
        env.clock = env.clock.replace(hour=15, minute=21)
    run(env.dispatcher.exit_manager.tick(env.uid, env.broker))
    assert env.positions.get(p.position_id).status == 'CLOSED'
