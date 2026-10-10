"""Unknown charges fence increases; calculated costs are exact and restart safe."""
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
import pytest
from app.services import db, fee_safety
from app.services.execution_service import CanonicalExecutionService, ExposureEffect
from app.services.kite_engine import fill_ledger as ledger, state

@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(db, '_DB_PATH', str(tmp_path / 'fees.db'))
    db.init()

def fill():
    ledger.record(account_id='a', uid='u', symbol='OPT', exchange='NFO', side='BUY',
        order_id='o1', cumulative_quantity=10, cumulative_value=1000, source='entry')

def client(total=5):
    return NS(_is_paper=False, _account_id='a',
        get_orders=AsyncMock(return_value=[dict(order_id='o1', tradingsymbol='OPT',
            exchange='NFO', transaction_type='BUY', filled_quantity=10,
            average_price=100, product='NRML')]),
        order_charges=AsyncMock(return_value=[dict(order_id='o1', charges=dict(total=total))]))

@pytest.mark.parametrize('origin', ['sterling-kite', 'gamma_move', 'snapback'])
def test_unknown_cost_blocks_all_origins_but_management_remains_available(origin):
    fill()
    assert fee_safety.admission_blocker('u','a')[0] == 'broker_fees_pending'
    allowed, reason = CanonicalExecutionService().is_trading_allowed(uid='u',account_id='a',strategy_id=origin)
    assert not allowed and 'charges' in reason.lower()
    for effect in (ExposureEffect.REDUCE_EXPOSURE, ExposureEffect.PROTECT_POSITION):
        assert CanonicalExecutionService().is_trading_allowed(uid='u',account_id='a',exposure_effect=effect)[0]
    assert fee_safety.admission_blocker('u','other') == ('','')
    assert fee_safety.admission_blocker('u','paper') == ('','')

@pytest.mark.asyncio
@pytest.mark.parametrize('total', [0, 5])
async def test_explicit_zero_is_known_and_positive_fees_reduce_pnl(total):
    fill(); c=client(total)
    await fee_safety.refresh(c,'u','a')
    assert fee_safety.admission_blocker('u','a') == ('','')
    inv=ledger.inventory('a','u','OPT')
    assert inv.fees == total and inv.realized_pnl == -total and inv.fees_complete
    await fee_safety.refresh(c,'u','a')
    assert c.order_charges.await_count == 1
    db.init()
    assert fee_safety.admission_blocker('u','a') == ('','')

@pytest.mark.asyncio
@pytest.mark.parametrize('quote', [[], [{}], [{'charges':{}}], [{'charges':{'total':-1}}],
    [{'charges':{'total':float('nan')}}], [{'order_id':'foreign','charges':{'total':0}}]])
async def test_missing_invalid_or_foreign_quote_never_becomes_zero(quote):
    fill(); c=client();c.order_charges.return_value=quote
    await fee_safety.refresh(c,'u','a')
    assert fee_safety.admission_blocker('u','a')[0]
    assert not ledger.inventory('a','u','OPT').fees_complete

@pytest.mark.asyncio
async def test_read_failure_is_durable_and_repair_clears_fault():
    fill(); c=client(); c.order_charges.side_effect=RuntimeError('offline')
    await fee_safety.refresh(c,'u','a'); db.init()
    assert fee_safety.admission_blocker('u','a')[0]=='fee_reconciliation_required'
    c.order_charges.side_effect=None
    await fee_safety.refresh(c,'u','a')
    assert fee_safety.admission_blocker('u','a')==('','')

@pytest.mark.asyncio
async def test_gamma_exit_cost_is_additive_and_deduplicated():
    day=ledger.ist_today()
    state.record_realized_pnl_once('u','gamma-unwind:a:o1',100,day_iso=day)
    c=client(7)
    await fee_safety.refresh(c,'u','a'); await fee_safety.refresh(c,'u','a')
    with db._conn() as conn:
        events=conn.execute('SELECT event_key,pnl FROM kite_realized_pnl_events WHERE uid=?',('u',)).fetchall()
    assert sum(r['pnl'] for r in events)==93
    assert any(r['event_key']=='broker-fees:a:o1:10' for r in events)
    assert fee_safety.admission_blocker('u','a')==('','')
    assert c.order_charges.await_count==1

@pytest.mark.asyncio
async def test_gamma_partial_charge_watermarks_book_only_additional_cost():
    import time
    now=int(time.time()*1000)
    with db.connection() as conn:
        conn.execute('''INSERT INTO kite_order_intents
            (intent_key,uid,account_id,strategy_id,generation_id,signal_id,exchange,symbol,
             side,quantity,tag,state,order_id,payload_json,observed_quantity,observed_value,created_ms,updated_ms)
            VALUES ('i','u','a','gamma_move','g','s','NFO','OPT','BUY',20,'t','FILLED_PARTIAL','o1',
                    '{"product":"NRML"}',10,1000,?,?)''',(now,now))
    c=client(5)
    await fee_safety.refresh(c,'u','a')
    with db.connection() as conn:
        conn.execute("UPDATE kite_order_intents SET observed_quantity=20,observed_value=2000 WHERE intent_key='i'")
    assert fee_safety.admission_blocker('u','a')[0]=='broker_fees_pending'
    c.order_charges.return_value=[dict(order_id='o1',charges=dict(total=8))]
    await fee_safety.refresh(c,'u','a'); await fee_safety.refresh(c,'u','a')
    with db._conn() as conn:
        rows=conn.execute("SELECT pnl FROM kite_realized_pnl_events WHERE uid='u'").fetchall()
    assert sorted(r['pnl'] for r in rows)==[-5,-3]
    assert fee_safety.admission_blocker('u','a')==('','')

@pytest.mark.asyncio
async def test_fill_arriving_during_quote_cannot_be_marked_costed():
    fill(); c=client()
    async def quote(_):
        ledger.record(account_id='a',uid='u',symbol='OPT',exchange='NFO',side='BUY',
            order_id='o1',cumulative_quantity=20,cumulative_value=2000,source='entry')
        return [dict(order_id='o1',charges=dict(total=5))]
    c.order_charges.side_effect=quote
    await fee_safety.refresh(c,'u','a')
    assert fee_safety.admission_blocker('u','a')[0]=='fee_reconciliation_required'
    assert not ledger.inventory('a','u','OPT').fees_complete

@pytest.mark.asyncio
async def test_foreign_history_cannot_cost_our_execution():
    fill(); c=client(); c.get_orders.return_value=[]
    c.get_order_history=AsyncMock(return_value=[dict(order_id='foreign')])
    await fee_safety.refresh(c,'u','a')
    assert fee_safety.admission_blocker('u','a')[0]=='fee_reconciliation_required'
    c.order_charges.assert_not_awaited()
