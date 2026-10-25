"""Automatic scan calendar admission must never turn off position maintenance."""
from dataclasses import replace
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.core import session_calendar
from app.engines.gamma_move import GammaMoveConfig
from app.services import gamma_move_runner as runner, gamma_move_scanner as scanner

IST = timezone(timedelta(hours=5, minutes=30))


@pytest.fixture
def cycle(monkeypatch):
    cfg = GammaMoveConfig().validate()
    session = SimpleNamespace(signals={})
    monkeypatch.setattr(runner, 'get_config', lambda _uid: cfg)
    monkeypatch.setattr(runner, 'session_for', lambda *_args: session)
    monkeypatch.setattr(runner, '_last_failure', {})
    monkeypatch.setattr(runner, 'is_paper', lambda _uid: True)
    monkeypatch.setattr(runner.positions_store, 'open_positions', lambda _uid: [object()])
    monkeypatch.setattr(scanner, 'has_watchers', lambda _uid: False)
    monkeypatch.setattr(scanner, 'completed_scan_for_day', lambda *_args: False)
    mocks = {}
    for module, name, result in [
        (runner, 'reconcile', {}),
        (runner, 'scan_once', {}),
        (runner, '_paper_poll_ticks', [{'instrument_token': 123, 'last_price': 100}]),
        (runner, 'on_ticks', None),
        (runner, '_reference_time_sweeps', None),
        (runner, '_subscribe_watched', None),
        (scanner, 'mark_levels', {}),
        (scanner, 'reuse_owner_market_data', None),
    ]:
        mocks[name] = AsyncMock(return_value=result)
        monkeypatch.setattr(module, name, mocks[name])
    mocks['claim'] = Mock(return_value='13:15')
    monkeypatch.setattr(runner, '_claim_latest_due_slot', mocks['claim'])
    monkeypatch.setattr(runner, '_complete_slot', Mock())
    # Test fixtures never publish external alerts.
    from app.services.notifications import gamma_move_alerts
    monkeypatch.setattr(gamma_move_alerts, 'check_and_queue_added_alerts', Mock())
    return cfg, mocks


def assert_maintenance(mocks):
    for name in ['reconcile', '_paper_poll_ticks', 'on_ticks',
                 '_reference_time_sweeps', '_subscribe_watched']:
        mocks[name].assert_awaited_once()


def assert_no_scan(mocks):
    mocks['claim'].assert_not_called()
    for name in ['mark_levels', 'scan_once', 'reuse_owner_market_data']:
        mocks[name].assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('uid', ['calendar-owner', 'sim:calendar-owner'])
@pytest.mark.parametrize('day', ['2026-10-24', '2026-10-25', '2026-10-02'],
                         ids=['saturday', 'sunday', 'exchange-holiday'])
async def test_closed_day_skips_automatic_work_but_keeps_position_maintenance(cycle, uid, day):
    cfg, mocks = cycle
    now = datetime.fromisoformat(day+'T14:00:00').replace(tzinfo=IST)
    assert session_calendar.is_trading_session(now.date(), 'NFO') is False
    out = {}
    await runner._scan_uid(uid, now, out)
    assert_no_scan(mocks)
    assert_maintenance(mocks)
    assert out[uid]['scan_skipped'] == 'non_trading_day'
    assert 'error' not in out[uid]
    assert cfg.enabled is True


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', [session_calendar.CalendarUnavailable('unknown'), RuntimeError('unavailable')])
async def test_unknown_calendar_refuses_scans_and_still_maintains_positions(cycle, monkeypatch, failure):
    cfg, mocks = cycle
    def unavailable(*_args):
        raise failure
    monkeypatch.setattr(session_calendar, 'is_trading_session', unavailable)
    out = {}
    await runner._scan_uid('calendar-owner', datetime(2026, 10, 26, 14, tzinfo=IST), out)
    assert_no_scan(mocks)
    assert_maintenance(mocks)
    assert out['calendar-owner']['scan_skipped'] == 'calendar_unavailable'
    assert 'error' not in out['calendar-owner']
    assert cfg.enabled is True


@pytest.mark.asyncio
@pytest.mark.parametrize('uid', ['calendar-owner', 'sim:calendar-owner'])
async def test_trading_day_keeps_scheduled_work_and_maintenance(cycle, uid):
    _, mocks = cycle
    out = {}
    await runner._scan_uid(uid, datetime(2026, 10, 26, 14, tzinfo=IST), out)
    assert mocks['claim'].call_count == 2
    mocks['mark_levels'].assert_awaited_once()
    mocks['scan_once'].assert_awaited_once()
    assert mocks['reuse_owner_market_data'].await_count == int(uid.startswith('sim:'))
    assert_maintenance(mocks)
    assert 'scan_skipped' not in out.get(uid, {})
    assert 'error' not in out.get(uid, {})


@pytest.mark.asyncio
async def test_disabled_engine_keeps_maintenance_without_reading_calendar(cycle, monkeypatch):
    cfg, mocks = cycle
    cfg = replace(cfg, enabled=False)
    monkeypatch.setattr(runner, 'get_config', lambda _uid: cfg)
    calendar = Mock(side_effect=AssertionError('disabled scanner must not need calendar'))
    monkeypatch.setattr(session_calendar, 'is_trading_session', calendar)
    await runner._scan_uid('calendar-owner', datetime(2026, 10, 26, 14, tzinfo=IST), {})
    calendar.assert_not_called()
    assert_no_scan(mocks)
    assert_maintenance(mocks)
    assert cfg.enabled is False
