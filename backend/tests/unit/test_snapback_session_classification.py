from types import SimpleNamespace
from app.services import snapback_readiness as readiness
from app.services import snapback_dispatcher as dispatcher


def test_cash_auction_is_session_pause_not_runtime_fault(monkeypatch):
    readiness._conditions_cache.clear()
    monkeypatch.setattr(dispatcher, 'origination_blockers', lambda *a, **kw: ['cash_signal_auction'])
    cfg = SimpleNamespace(enabled=True, auto_execute=False, trading_mode='scalp', short_leg_delta=0, stop_mode='both')
    state = readiness.live_conditions('session-classification-fixture', cfg)
    assert state.blockers == []
    assert state.session == ['cash_signal_auction']
    assert not state.manual_eligible_now
    assert 'closing auction' in readiness.reason_text('cash_signal_auction')
