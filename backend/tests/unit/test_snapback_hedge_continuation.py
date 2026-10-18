"""A protected option can complete its bound hedge, never admit unrelated risk."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from app.services import protection_health as health
from app.services.execution_service import ExecutionRequest
from app.services.snapback_live_positions import LivePosition


@pytest.fixture
def continuation(monkeypatch):
    from app.services import snapback_plan_store

    plan = dict(plan_id='plan', status='ARMED', owner_uid='u', account_id='a',
                opportunity_id='signal', policy_snapshot_hash='policy',
                option_symbol='OPTPE', option_quantity=50,
                futures_symbol='FUT', futures_exchange='NFO', futures_lot_size=25,
                target_futures_quantity=25, signal_side='fade_up', option_type='PE')
    monkeypatch.setattr(snapback_plan_store, 'shared_store',
                        lambda: SimpleNamespace(get=lambda _: plan))
    pos = LivePosition(position_id='plan', uid='u', account_id='a',
                       lane='swing', underlying='NIFTY', side='fade_up',
                       option_type='PE', option_exchange='NFO',
                       signal_id='signal', option_symbol='OPTPE', option_qty=50,
                       option_entry_order_id='filled-option', hedge_symbol='FUT',
                       hedge_exchange='NFO', hedge_side='BUY', status='ENTERING',
                       protection_state='ACTIVE', gtt_id=1, gtt_stop=50, gtt_qty=50)
    req = ExecutionRequest(uid='u', account_id='a', strategy_id='snapback',
                           generation_id='policy', signal_id='signal',
                           symbol='FUT', exchange='NFO', side='BUY', quantity=25,
                           tag='snapback:plan')
    return pos, req, plan


def test_only_the_bound_hedge_can_continue(continuation):
    pos, req, _ = continuation
    assert health._protected_snapback_hedge(pos, req)
    assert health.position_fault('snapback', pos)
    assert not health._protected_snapback_hedge(pos, None)


@pytest.mark.parametrize('change', [
    {'uid':'other'}, {'account_id':'other'}, {'strategy_id':'gamma_move'},
    {'generation_id':'other'}, {'signal_id':'replay'}, {'symbol':'OTHER'},
    {'exchange':'NSE'}, {'side':'SELL'}, {'quantity':50}, {'quantity':0},
    {'tag':'snapback:other'},
])
def test_request_mismatch_never_exempts_the_position(continuation, change):
    pos, req, _ = continuation
    assert not health._protected_snapback_hedge(pos, replace(req, **change))


@pytest.mark.parametrize('change', [
    {'status':'EXITING'}, {'status':'RECONCILING'}, {'gtt_id':0}, {'gtt_qty':25},
    {'gtt_pending':True}, {'protection_state':'REQUIRED'}, {'hedge_qty':25},
    {'hedge_entry_order_id':'uncertain-hedge'}, {'option_entry_order_id':''},
    {'flags':['hedge_incomplete']}, {'option_qty':75},
    {'option_exit_qty':25}, {'option_exit_order_id':'pending-exit'},
    {'gtt_stop':float('inf')},
])
def test_unsettled_position_never_gets_a_continuation(continuation, change):
    pos, req, _ = continuation
    for key, value in change.items():
        setattr(pos, key, value)
    assert not health._protected_snapback_hedge(pos, req)


@pytest.mark.parametrize('change', [
    {'status':'RECONCILING'}, {'status':'CONSUMED'}, {'owner_uid':'other'},
    {'target_futures_quantity':50}, {'futures_lot_size':0},
    {'option_type':'CE'}, {'option_quantity':0},
])
def test_changed_plan_cannot_authorize_an_old_hedge(continuation, change):
    pos, req, plan = continuation
    plan.update(change)
    assert not health._protected_snapback_hedge(pos, req)


def test_continuation_does_not_skip_other_positions(continuation, monkeypatch):
    from app.services import gamma_move_positions, snapback_live_positions, price_rules_store
    from app.services.kite_engine import positions

    pos, req, _ = continuation
    monkeypatch.setattr(positions, 'unreadable_reason', lambda _: '')
    monkeypatch.setattr(positions, 'open_positions', lambda _: [])
    monkeypatch.setattr(gamma_move_positions, 'load', lambda _: {})
    monkeypatch.setattr(gamma_move_positions, 'legacy_open_rows', lambda _: [])
    monkeypatch.setattr(gamma_move_positions, 'load_record', lambda _: None)
    monkeypatch.setattr(gamma_move_positions, 'open_positions', lambda _: [])
    monkeypatch.setattr(price_rules_store, 'open_positions', lambda _: [])
    book = [pos]
    monkeypatch.setattr(snapback_live_positions, 'open_positions', lambda _: book)
    assert health.admission_blocker('u', 'a', entry_request=req) == ('', '')
    assert health.admission_blocker('u', 'a')[0] == 'protection_integrity'
    book.append(replace(pos, position_id='another-plan'))
    assert health.admission_blocker('u', 'a', entry_request=req)[0] == 'protection_integrity'


def test_continuation_cannot_bypass_operator_safe_mode(continuation, tmp_path):
    from app.services.safety_supervisor import SafetySupervisor
    from app.services.safe_mode import SafeModeService, SafeModeTrigger

    _, req, _ = continuation
    path = tmp_path / 'safe.json'
    SafeModeService(path).engage(trigger=SafeModeTrigger.OPERATOR, reason='operator stop')
    decision = SafetySupervisor(safe_mode_file=path).authorize(
        uid='u', account_id='a', strategy_id='snapback', entry_request=req)
    assert not decision.allowed
    assert decision.code == 'safe_mode'


@pytest.fixture
def retry_continuation(continuation,monkeypatch):
    from app.services import snapback_plan_store
    from app.services.kite_engine import order_journal
    pos,req,plan=continuation
    plan.update(status="RECONCILING",revision=4)
    pos.flags=["hedge_incomplete"]
    req=replace(req,signal_id="signal:hedge-recovery:4")
    events=[{"event":"RECONCILING","detail_json":'{"reason":"hedge_rejected_zero_fill:cancelled"}'},
            {"event":"RECOVERY_ATTEMPT","revision":4}]
    prior=SimpleNamespace(state="CANCELLED",filled_quantity=0,reconciliation_required=False,quantity=25,order_id="previous")
    monkeypatch.setattr(snapback_plan_store,'shared_store',lambda:SimpleNamespace(get=lambda _:plan,events=lambda _:events))
    monkeypatch.setattr(order_journal,'get',lambda _:prior)
    return pos,req,plan,events,prior


def test_retry_requires_durable_attempt_and_canonical_zero_fill(retry_continuation):
    pos,req,*_=retry_continuation
    assert health._protected_snapback_hedge(pos,req)

@pytest.mark.parametrize("change",[
    {"state":"OPEN"},{"state":"UNKNOWN"},{"state":"FILLED"},
    {"filled_quantity":1},{"reconciliation_required":True},
    {"quantity":50},{"order_id":""}])
def test_retry_cannot_replace_ambiguous_or_filled_hedge(retry_continuation,change):
    pos,req,plan,events,prior=retry_continuation
    for key,value in change.items():setattr(prior,key,value)
    assert not health._protected_snapback_hedge(pos,req)


def test_retry_revision_and_reason_cannot_be_forged(retry_continuation):
    pos,req,plan,events,_=retry_continuation
    assert not health._protected_snapback_hedge(pos,replace(req,signal_id="signal:hedge-recovery:5"))
    events[0]["detail_json"]='{"reason":"hedge_submission_unknown"}'
    assert not health._protected_snapback_hedge(pos,req)


def test_retry_does_not_excuse_other_position_flags(retry_continuation):
    pos,req,*_=retry_continuation
    pos.flags.append("unprotected")
    assert not health._protected_snapback_hedge(pos,req)
