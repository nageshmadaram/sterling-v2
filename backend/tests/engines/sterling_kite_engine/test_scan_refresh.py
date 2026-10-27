"""Last-known display must never become renewed entry authority."""
from types import SimpleNamespace
import pytest
from app.engines.sterling_kite_engine.schemas import EngineSignalRow, OptionLeg
from app.services.kite_engine.scanner import _compile_rows


def row(name='A', source='derivatives', stamp=100, symbol='A-CE', **kw):
    return EngineSignalRow(underlying=name, token=1, exchange='NFO', regime='BULL',
        alignment=dict(fast=1, mid=1, slow=1), direction='long', option_type='CE',
        spot=100, stop_loss=90, score=1, timestamp_ms=stamp, source=source,
        is_active=True, legs=[OptionLeg(moneyness='ATM', option_type='CE', option_symbol=symbol,
            strike=100, expiry='2026-12-31', signal_timestamp_ms=stamp,
            entry_timestamp_ms=stamp, is_active=True)], **kw)


def test_different_modes_never_share_a_grouped_parent():
    assert len(_compile_rows([row(), row(horizon_mode='scalp', signal_timeframe_minutes=5)])) == 2


def test_pending_and_failed_groups_survive_until_authoritatively_empty():
    from app.services.kite_engine.scan_refresh import RefreshBoard
    old = [row('A'), row('B', symbol='B-CE')]
    board = RefreshBoard(old, {('derivatives', 'A'), ('derivatives', 'B')})
    board.observe(row('A', stamp=200))
    projected = board.project([row('A', stamp=200)])
    assert {r.underlying for r in projected} == {'A', 'B'}
    retained = next(r for r in projected if r.underlying == 'B')
    assert retained.scan_refresh_state == 'pending'
    assert retained.legs[0].scan_refresh_state == 'pending'
    assert old[1].scan_refresh_state is None
    board.finish('derivatives', 'B', success=False)
    assert next(r for r in board.project([]) if r.underlying == 'B').scan_refresh_state == 'failed'
    board.finish('derivatives', 'B', success=True)
    assert all(r.underlying != 'B' for r in board.project([]))


def test_mixed_parent_keeps_unknown_leg_blocked():
    from app.services.kite_engine.scan_refresh import RefreshBoard
    old = row(symbol='A-OLD')
    board = RefreshBoard([old], {('derivatives', 'A')})
    fresh = row(stamp=200, symbol='A-NEW')
    board.observe(fresh)
    parent, = board.project([fresh])
    assert parent.scan_refresh_state is None
    assert {l.option_symbol: l.scan_refresh_state for l in parent.legs} == {'A-NEW': None, 'A-OLD': 'pending'}


def test_entry_guard_binds_symbol_and_exact_bar_even_after_new_empty_scan():
    from app.services.kite_engine.scan_refresh import RefreshBoard
    old = row()
    board = RefreshBoard([old], {('derivatives', 'A')})
    assert board.block_reason('derivatives', 'A-CE', 100)
    board.observe(old)
    assert not board.block_reason('derivatives', 'A-CE', 100)
    assert board.block_reason('derivatives', 'A-CE', 99)
    board.finish('derivatives', 'A', success=True)
    next_board = RefreshBoard([], {('derivatives', 'A')}, previous=board)
    next_board.finish('derivatives', 'A', success=True)
    assert next_board.block_reason('derivatives', 'A-CE', 100)


def test_unobserved_canonical_contract_cannot_borrow_another_contracts_freshness(monkeypatch):
    from app.services.kite_engine.scan_refresh import RefreshBoard, admission_blocker
    from app.services.kite_engine.scanner import scanner
    board = RefreshBoard([], {('derivatives', 'A')})
    board.observe(row(symbol='A-CE'))
    assert board.block_reason('derivatives', 'A-OTHER-CE', 100,
                              mode='swing', timeframe=60, underlying='A') == 'signal_refresh_unverified'
    current = SimpleNamespace(refresh_board=board, horizon_mode='swing', timeframe_minutes=60)
    monkeypatch.setitem(scanner._users, 'unobserved-contract', current)
    request = SimpleNamespace(uid='unobserved-contract', strategy_id='sterling-kite',
                              symbol='A-OTHER-CE', signal_id='derivatives:100:long',
                              payload={'underlying': 'A'})
    assert admission_blocker(request)[0] == 'signal_refresh_unverified'
    assert admission_blocker(SimpleNamespace(**{**request.__dict__, 'symbol': 'A-CE'})) == ('', '')


def test_canonical_signal_requires_loaded_scan_authority(monkeypatch):
    from app.services.kite_engine.scan_refresh import admission_blocker
    from app.services.kite_engine.scanner import scanner
    request = SimpleNamespace(uid='no-current-scan', strategy_id='sterling-kite',
                              symbol='A-CE', signal_id='derivatives:100:long', payload={})
    monkeypatch.delitem(scanner._users, 'no-current-scan', raising=False)
    assert admission_blocker(request)[0] == 'signal_refresh_unavailable'
    monkeypatch.setitem(scanner._users, 'no-current-scan', SimpleNamespace(
        refresh_board=None, horizon_mode='swing', timeframe_minutes=60))
    assert admission_blocker(request)[0] == 'signal_refresh_unavailable'
    # Without a canonical scanner signal id, this safety check does not make a
    # claim about unrelated legacy/manual entry evidence.
    request.signal_id = 'manual-idempotency'
    request.uid = 'manual-without-scan'
    assert admission_blocker(request) == ('', '')


def test_confluence_requires_exact_leg_while_spot_can_reference_underlying():
    from app.services.kite_engine.scan_refresh import RefreshBoard
    combined = RefreshBoard([], {('confluence', 'A')})
    combined.observe(row(source='confluence', symbol='A-CE'))
    assert combined.block_reason('confluence', 'A-UNSCANNED-CE', 100,
                                 underlying='A', mode='swing', timeframe=60) == 'signal_refresh_unverified'
    spot = RefreshBoard([], {('spot', 'A')})
    spot.observe(row(source='spot', symbol='A-CE'))
    assert spot.block_reason('spot', 'A-DERIVED-CE', 100,
                             underlying='A', mode='swing', timeframe=60) == ''


def test_older_scan_result_never_reauthorizes_a_superseded_bar():
    from app.services.kite_engine.scan_refresh import RefreshBoard
    prior = RefreshBoard([], {('derivatives', 'A')})
    prior.observe(row(stamp=200))
    following = RefreshBoard([row(stamp=200)], {('derivatives', 'A')},
                             previous=prior)
    following.observe(row(stamp=100))
    assert following.block_reason('derivatives', 'A-CE', 100, mode='swing', timeframe=60)
    assert following.block_reason('derivatives', 'A-CE', 200, mode='swing', timeframe=60)
    following.observe(row(stamp=300))
    assert following.block_reason('derivatives', 'A-CE', 200, mode='swing', timeframe=60)
    assert following.block_reason('derivatives', 'A-CE', 300, mode='swing', timeframe=60) == ''


def test_removed_universe_row_is_visible_but_no_longer_entry_authority():
    from app.services.kite_engine.scan_refresh import RefreshBoard
    signal = row()
    before = RefreshBoard([], {('derivatives', 'A')})
    before.observe(signal)
    after = RefreshBoard([signal], {('spot', 'B')}, previous=before)
    assert [r.underlying for r in after.project([])] == ['A']
    assert after.block_reason('derivatives', 'A-CE', 100, mode='swing', timeframe=60) == 'signal_refresh_unverified'


def test_retained_row_refuses_entry_before_market_checks(monkeypatch):
    from app.services.kite_engine.service import entry_data_block_reason
    from app.services.kite_engine import market_hours
    monkeypatch.setattr(market_hours, 'entry_block_reason', lambda **kw: '')
    assert entry_data_block_reason(row(scan_refresh_state='pending'), exchange='NFO', buffer_minutes=0) == 'signal_refresh_pending'


@pytest.mark.asyncio
@pytest.mark.parametrize('outcome', ['empty', 'error', 'cancel'])
async def test_real_scan_keeps_unfinished_group_and_persists_final_truth(monkeypatch, outcome):
    import asyncio
    from app.domain.models import Candle
    from app.engines.sterling_kite_engine.config import SterlingKiteEngineConfig
    from app.services.kite_engine import scanner as module, state
    from app.services.kite_engine.universe import UniverseItem
    cfg = SterlingKiteEngineConfig(fast=(1, 1.), mid=(1, 2.), slow=(1, 3.))
    scanner = module.KiteEngineScanner()
    us = scanner._user('refresh-integration', cfg)
    us.rows = [row('A', source='spot'), row('B', source='spot', symbol='B-CE')]
    us.finalized = lambda candles, *a, **kw: candles
    entered, release = asyncio.Event(), asyncio.Event()
    persisted = []
    monkeypatch.setattr(state, 'load_signal_cache', lambda uid: None)
    monkeypatch.setattr(state, 'save_signal_cache', lambda uid, rows, gen, **kw: persisted.append((rows, kw)))
    candles = [Candle(timestamp_ms=i, open=100, high=110, low=90, close=100, volume=1) for i in range(100, 3100, 100)]
    async def fetch(client, us, item):
        if item.name == 'B':
            entered.set()
            await release.wait()
            if outcome == 'error':
                raise OSError('read failed')
        return candles
    async def no_stamp(*a, **kw):
        pass
    monkeypatch.setattr(scanner, '_fetch_1h', fetch)
    monkeypatch.setattr(scanner, '_stamp_spot_leg_premiums', no_stamp)
    monkeypatch.setattr(module, 'live_red_counts', lambda *a: {'long': 0})
    monkeypatch.setattr(module, 'evaluate_item', lambda engine, item, *a: [row('A', source='spot', stamp=300)] if item.name == 'A' else [])
    monkeypatch.setattr(module, '_retain_signals', lambda rows, now: rows)
    monkeypatch.setattr(module, 'attach_strikes', lambda *a, **kw: None)
    task = asyncio.create_task(scanner.scan(uid='refresh-integration', client=object(),
        universe=[UniverseItem(n, n, i, 'NSE', 'NFO') for i, n in enumerate(('A', 'B'), 1)],
        nfo_rows=[], bfo_rows=[], cfg=cfg))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        for _ in range(100):
            if any(r.underlying == 'A' and r.timestamp_ms == 300 for r in us.rows):
                break
            await asyncio.sleep(.005)
        assert next(r for r in us.rows if r.underlying == 'B').scan_refresh_state == 'pending'
        # A poll while the write is in flight cannot rehydrate an old executable row.
        assert scanner.snapshot('refresh-integration') is us
        if outcome == 'cancel':
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            release.set()
            await asyncio.wait_for(task, 2)
        b = [r for r in us.rows if r.underlying == 'B']
        assert (b == []) if outcome == 'empty' else b[0].scan_refresh_state == 'failed'
        assert persisted[-1][1] == {'replace': True}
        saved_b = [r for r in persisted[-1][0] if r['underlying'] == 'B']
        assert (saved_b == []) if outcome == 'empty' else saved_b[0]['scan_refresh_state'] == 'failed'
        assert us.scanning is False
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def test_authoritative_empty_cache_does_not_resurrect_previous_rows(monkeypatch):
    import json
    from app.services.kite_engine import state
    store = {'rows': [row().model_dump()], 'generated_ms': 1}
    monkeypatch.setattr(state.db, 'get_config', lambda key: json.dumps(store))
    monkeypatch.setattr(state.db, 'set_config_strict', lambda key, value: (store.clear(), store.update(json.loads(value))))
    state.save_signal_cache('empty-refresh', [], 2, replace=True)
    assert state.load_signal_cache('empty-refresh') == ([], 2)


@pytest.mark.parametrize('strategy_id', ['sterling-kite', 'STERLING-KITE'])
def test_transport_admission_rechecks_latest_refresh_and_exits_bypass_it(monkeypatch, strategy_id):
    from app.services.kite_engine.scan_refresh import RefreshBoard, admission_blocker
    from app.services.kite_engine.scanner import scanner
    from app.services.safety_supervisor import SafetySupervisor, ExposureIntent
    board = RefreshBoard([row()], {('derivatives', 'A')})
    current = SimpleNamespace(refresh_board=board, horizon_mode='swing', timeframe_minutes=60)
    monkeypatch.setitem(scanner._users, 'queued-refresh', current)
    request = SimpleNamespace(strategy_id=strategy_id, uid='queued-refresh',
        signal_id='derivatives:100:long', symbol='A-CE', payload={})
    assert admission_blocker(request)[0] == 'signal_refresh_pending'
    board.observe(row())
    assert admission_blocker(request) == ('', '')
    current.refresh_board = RefreshBoard([row()], {('derivatives', 'A')}, previous=board)
    assert admission_blocker(request)[0] == 'signal_refresh_pending'
    # Explicit exposure reductions do not read entry authorities at all.
    monkeypatch.setattr(SafetySupervisor, 'snapshot', lambda *a, **kw: pytest.fail('entry authority used for exit'))
    for effect in (ExposureIntent.CLOSE_POSITION, ExposureIntent.PROTECT_POSITION, ExposureIntent.RECONCILE):
        assert SafetySupervisor().authorize(effect, entry_request=request).allowed


def test_unrelated_source_and_mode_remain_visible_without_becoming_refreshed():
    from app.services.kite_engine.scan_refresh import RefreshBoard
    outside = row(source='confluence')
    other_mode = row(horizon_mode='scalp', signal_timeframe_minutes=5)
    board = RefreshBoard([outside, other_mode, row()], {('derivatives', 'A')})
    board.finish('derivatives', 'A', success=True)
    result = board.project([])
    assert {(r.source, r.horizon_mode) for r in result} == {('confluence', 'swing'), ('derivatives', 'scalp')}
    assert all(r.scan_refresh_state is None for r in result)


def test_observing_older_signal_cannot_replace_latest_contract_authority():
    from app.services.kite_engine.scan_refresh import RefreshBoard
    board = RefreshBoard([], {('derivatives', 'A')})
    board.observe(row(stamp=200))
    board.observe(row(stamp=100))
    assert not board.block_reason('derivatives', 'A-CE', 200)
    assert board.block_reason('derivatives', 'A-CE', 100)


def test_authoritative_cache_failure_is_not_acknowledged(monkeypatch):
    from app.services.kite_engine import state
    before = state.signal_cache_version('failed-refresh-write')
    def fail(*a):
        raise OSError('storage unavailable')
    monkeypatch.setattr(state.db, 'set_config_strict', fail)
    with pytest.raises(OSError, match='storage unavailable'):
        state.save_signal_cache('failed-refresh-write', [], 2, replace=True)
    assert state.signal_cache_version('failed-refresh-write') == before


def test_legacy_simulated_request_cannot_omit_refresh_evidence(monkeypatch):
    from app.services.kite_engine.scan_refresh import RefreshBoard, admission_blocker
    from app.services.kite_engine.scanner import scanner
    old = row()
    board = RefreshBoard([old], {('derivatives', 'A')})
    monkeypatch.setitem(scanner._users, 'paper-refresh', SimpleNamespace(
        refresh_board=board, rows=board.project([]), horizon_mode='swing', timeframe_minutes=60))
    request = SimpleNamespace(strategy_id='sterling-kite', uid='paper-refresh', symbol='A-CE',
                              signal_id='legacy-idempotency', payload={})
    assert admission_blocker(request)[0] == 'signal_refresh_unverified'
    request.payload = {'horizon_mode': 'swing', 'signal_timeframe_minutes': 5}
    assert admission_blocker(request)[0] == 'signal_refresh_mode_changed'
    request.payload['signal_timeframe_minutes'] = 60
    assert admission_blocker(request)[0] == 'signal_refresh_unverified'
    request.strategy_id = 'gamma-unwind'
    assert admission_blocker(request) == ('', '')


def test_partial_confluence_failure_preserves_unknown_sibling_on_same_bar():
    from app.services.kite_engine.scan_refresh import RefreshBoard
    old = row(source='confluence', symbol='A-OLD')
    fresh = row(source='confluence', symbol='A-NEW')
    board = RefreshBoard([old], {('confluence', 'A')})
    board.observe(fresh)
    board.finish('confluence', 'A', success=False)
    parent, = board.project([fresh])
    assert parent.scan_refresh_state is None
    assert {l.option_symbol: l.scan_refresh_state for l in parent.legs} == {'A-NEW': None, 'A-OLD': 'failed'}


@pytest.mark.asyncio
@pytest.mark.parametrize('old_history', ['failed', 'short', 'empty_signal'])
async def test_derivative_partial_refresh_tracks_contract_local_truth(monkeypatch, old_history):
    from app.domain.models import Candle
    from app.engines.sterling_kite_engine.config import SterlingKiteEngineConfig
    from app.services.kite_engine import scanner as module, state
    from app.services.kite_engine.universe import UniverseItem
    cfg = SterlingKiteEngineConfig(fast=(1, 1.), mid=(1, 2.), slow=(1, 3.))
    scanner = module.KiteEngineScanner()
    us = scanner._user('derivative-partial', cfg)
    us.rows = [row(symbol='A-OLD')]
    us.finalized = lambda candles, *a, **kw: candles
    monkeypatch.setattr(state, 'load_signal_cache', lambda uid: None)
    monkeypatch.setattr(state, 'save_signal_cache', lambda *a, **kw: None)
    candles = [Candle(timestamp_ms=i, open=100, high=110, low=90, close=100, volume=1) for i in (100, 200, 300)]
    async def fetch(client, us, token, name):
        if name == 'A-OLD':
            if old_history == 'failed':
                raise OSError('contract read failed')
            if old_history == 'short':
                return candles[:1]
        return candles
    picks = [('ATM', SimpleNamespace(option_symbol='A-NEW', token=2, expiry='2026-12-31', strike=100, option_type='CE')),
             ('OTM1', SimpleNamespace(option_symbol='A-OLD', token=3, expiry='2026-12-31', strike=110, option_type='CE'))]
    monkeypatch.setattr(scanner, '_fetch_candles', fetch)
    monkeypatch.setattr(module, 'chain_rows_for', lambda *a: [])
    monkeypatch.setattr(module, 'pick_contracts', lambda *a, **kw: picks)
    monkeypatch.setattr(module, 'live_red_counts', lambda *a: {'long': 0})
    monkeypatch.setattr(module, '_retain_signals', lambda rows, now: rows)
    monkeypatch.setattr(module, 'evaluate_derivative_contract', lambda item, m, pick, oc, cfg:
        [row(stamp=300, symbol='A-NEW')] if pick.option_symbol == 'A-NEW' else [])
    await scanner.scan(uid='derivative-partial', client=object(), universe=[], deriv_universe=[UniverseItem('A', 'A', 1, 'NSE', 'NFO')],
                       nfo_rows=[], bfo_rows=[], cfg=cfg)
    parent, = us.rows
    phases = {leg.option_symbol: leg.scan_refresh_state for leg in parent.legs}
    assert phases == ({'A-NEW': None} if old_history == 'empty_signal' else {'A-NEW': None, 'A-OLD': 'failed'})
    assert not us.refresh_board.block_reason('derivatives', 'A-NEW', 300)
    assert us.refresh_board.block_reason('derivatives', 'A-OLD', 100)


def test_same_bar_ended_contract_revokes_queued_entry():
    from app.services.kite_engine.scan_refresh import RefreshBoard
    live = row()
    board = RefreshBoard([live], {('derivatives', 'A')})
    board.observe(live)
    ended = live.model_copy(deep=True)
    ended.is_active = False
    ended.legs[0].is_active = False
    board.observe(ended)
    assert board.block_reason('derivatives', 'A-CE', 100)


@pytest.mark.asyncio
async def test_short_confluence_confirmation_is_unknown_not_empty(monkeypatch):
    from app.domain.models import Candle
    from app.engines.sterling_kite_engine.config import SterlingKiteEngineConfig
    from app.services.kite_engine import scanner as module, state
    from app.services.kite_engine.universe import UniverseItem
    cfg = SterlingKiteEngineConfig(fast=(3, 1.), mid=(2, 2.), slow=(1, 3.))
    scanner = module.KiteEngineScanner()
    us = scanner._user('confluence-short', cfg)
    us.rows = [row(source='confluence', symbol='A-OLD')]
    us.finalized = lambda candles, *a, **kw: candles
    monkeypatch.setattr(state, 'load_signal_cache', lambda uid: None)
    monkeypatch.setattr(state, 'save_signal_cache', lambda *a, **kw: None)
    candles = [Candle(timestamp_ms=i, open=100, high=110, low=90, close=100, volume=1) for i in (100, 200, 300, 400, 500)]
    async def underlying(*a):
        return candles
    async def premium(*a):
        return candles[-2:]
    monkeypatch.setattr(scanner, '_fetch_1h', underlying)
    monkeypatch.setattr(scanner, '_fetch_candles', premium)
    monkeypatch.setattr(module, 'live_red_counts', lambda *a: {'long': 0})
    monkeypatch.setattr(module, 'evaluate_item', lambda *a: [row(source='spot', stamp=500, is_fresh=True)])
    monkeypatch.setattr(module, '_retain_signals', lambda rows, now: rows)
    monkeypatch.setattr(module, 'chain_rows_for', lambda *a: [])
    monkeypatch.setattr(module, 'pick_strikes', lambda *a, **kw: [('ATM', SimpleNamespace(token=2, option_symbol='A-OLD'))])
    monkeypatch.setattr(module, 'evaluate_derivative_contract', lambda *a: [])
    await scanner.scan(uid='confluence-short', client=object(), universe=[],
        confluence_universe=[UniverseItem('A', 'A', 1, 'NSE', 'NFO')], nfo_rows=[], bfo_rows=[], cfg=cfg)
    prior, = us.rows
    assert prior.scan_refresh_state == prior.legs[0].scan_refresh_state == 'failed'
