from datetime import datetime, timezone
from types import SimpleNamespace
import pytest
from app.services import db, price_rules as runtime, price_rules_store as store
from app.services.execution_service import ExecutionResult
from app.services.kite_engine import order_journal, fill_ledger
from app.services.simulation.quotes import Book, InstrumentMeta
UID='price-rules-test'; SYMBOL='NIFTY99DEC25000CE'
@pytest.fixture
def env(tmp_path,monkeypatch):
 monkeypatch.setattr(db,'_DB_PATH',str(tmp_path/'strategy.db'));monkeypatch.setattr(db,'_available',True);db.init()
 # Existing lifecycle tests explicitly opt in. Missing production setting is OFF.
 store.mutate(UID,lambda doc:doc.update(enabled=True))
 account=SimpleNamespace(id='A',is_paper=False,user_id=UID)
 class Client:
  _account_id='A';_is_paper=False
  def __init__(self):self.orders={};self.gtts={};self.qty=0;self.sends=0;self.gtt_sends=0
  async def get_margins(self):return {'equity':{'available':{'live_balance':1000000}}}
  async def get_positions_raw(self):return {'net':[dict(exchange='NFO',tradingsymbol=SYMBOL,product='NRML',quantity=self.qty)]}
  async def get_order_history(self,oid):return [self.orders[oid]]
  async def place_gtt(self,**p):
   self.gtt_sends+=1;gid=self.gtt_sends;self.gtts[gid]=dict(id=gid,status='active',condition={'exchange':p['exchange'],'tradingsymbol':p['tradingsymbol'],'trigger_values':p['trigger_values']},orders=[dict(o,result=None) for o in p['orders']]);return {'trigger_id':gid}
  async def modify_gtt(self,gid,**p):self.gtts[gid].update(condition={'exchange':p['exchange'],'tradingsymbol':p['tradingsymbol'],'trigger_values':p['trigger_values']},orders=[dict(o,result=None) for o in p['orders']]);return {'trigger_id':gid}
  async def get_gtt(self,gid):return self.gtts[gid]
  async def delete_gtt(self,gid):self.gtts[gid]['status']='deleted';return {'trigger_id':gid}
 client=Client();monkeypatch.setattr(runtime.accounts,'get_active',lambda uid:account if uid==UID else None)
 async def acquire(a):return client
 monkeypatch.setattr(runtime.accounts,'acquire_client',acquire)
 class Source:
  def __init__(self,*a,**k):pass
  async def instrument(self,ex,s):return InstrumentMeta(ex,s,1,.05,50,'CE','NIFTY',25000,'2099-12-31')
  async def book(self,ex,s,**k):return Book(ex,s,100,99,100,1000,1000,0,'fixture',datetime.now(timezone.utc).timestamp())
  async def books(self,keys,**k):return {key:await self.book(*key.split(':',1)) for key in keys}
 monkeypatch.setattr(runtime,'QuoteSource',Source)
 from app.services.kite_engine import market_hours
 monkeypatch.setattr(market_hours,'is_market_open',lambda **kwargs:True)
 async def submit(request,**kwargs):
  i=order_journal.reserve(uid=request.uid,account_id=request.account_id,strategy_id=request.strategy_id,generation_id=request.generation_id,signal_id=request.signal_id,exchange=request.exchange,symbol=request.symbol,side=request.side,quantity=request.quantity,payload=request.payload)
  assert order_journal.claim_submission(i.intent_key)
  client.sends+=1;oid=f'O{client.sends}';client.qty+=request.quantity if request.side=='BUY' else -request.quantity
  client.orders[oid]=dict(order_id=oid,exchange=request.exchange,tradingsymbol=request.symbol,product='NRML',transaction_type=request.side,filled_quantity=request.quantity,average_price=100 if request.side=='BUY' else 110,status='COMPLETE',exchange_timestamp=datetime.now(timezone.utc).isoformat())
  order_journal.acknowledge(i.intent_key,oid);return ExecutionResult(True,'SUBMITTED',intent_key=i.intent_key,order_id=oid)
 monkeypatch.setattr(runtime.execution_service,'submit_order',submit)
 return client
async def armed():
 r=await runtime.create(UID,dict(exchange='NFO',symbol=SYMBOL,lots=2,stop_points=20,target_points=40));return runtime.command(UID,r['id'],r['revision'],'arm')
@pytest.mark.asyncio
async def test_complete_lifecycle_partial_exit_and_replay(env):
 r=await armed();await runtime.cycle(UID);await runtime.cycle(UID);p=store.load(UID)['rules'][r['id']]
 assert p['state']=='OPEN' and p['gtt_qty']==100 and p['stop']==80 and p['target']==140
 await runtime.exit_position(UID,r['id'],50);await runtime.cycle(UID);p=store.load(UID)['rules'][r['id']]
 assert runtime.remaining(p)==50 and p['gtt_qty']==50
 await runtime.exit_position(UID,r['id'],100);await runtime.cycle(UID);await runtime.cycle(UID);p=store.load(UID)['rules'][r['id']]
 assert p['state']=='CLOSED' and runtime.remaining(p)==0 and env.sends==3
 assert fill_ledger.inventory('A',UID,SYMBOL).net_quantity==0
 assert fill_ledger.realized_pnl(UID,account_id='A')==1000
@pytest.mark.asyncio
async def test_revision_and_pause_never_originate(env):
 r=await armed()
 with pytest.raises(ValueError,match='stale_rule'):runtime.command(UID,r['id'],1,'cancel')
 runtime.command(UID,r['id'],r['revision'],'pause');await runtime.cycle(UID);assert env.sends==0
@pytest.mark.asyncio
async def test_lost_entry_reply_never_resends(env,monkeypatch):
 r=await armed()
 async def lost(*a,**k):env.sends+=1;raise TimeoutError('reply lost')
 monkeypatch.setattr(runtime.execution_service,'submit_order',lost)
 await runtime.cycle(UID);await runtime.cycle(UID);await runtime.cycle(UID)
 assert env.sends==1 and store.load(UID)['rules'][r['id']]['state']=='UNKNOWN'
@pytest.mark.asyncio
async def test_lost_protection_reply_no_lookalike(env,monkeypatch):
 r=await armed();await runtime.cycle(UID)
 async def lost(**p):env.gtt_sends+=1;raise TimeoutError('GTT reply lost')
 monkeypatch.setattr(env,'place_gtt',lost);await runtime.cycle(UID);await runtime.cycle(UID)
 assert env.gtt_sends==1 and store.open_positions(UID)[0].protection_pending
 from app.services.protection_health import admission_blocker
 assert admission_blocker(UID,'A')[0]=='protection_integrity'
@pytest.mark.asyncio
async def test_foreign_tenant_refused(env):
 with pytest.raises(PermissionError):await runtime.client_for('other','A')
 assert store.load('other')['rules']=={}
@pytest.mark.parametrize('v',[-1,1.5,float('nan'),float('inf'),True])
def test_invalid_quantities(v):
 with pytest.raises(ValueError):runtime.units(v)
@pytest.mark.asyncio
async def test_partial_entry_protects_only_confirmed_fill(env):
 r=await armed();await runtime.cycle(UID);env.qty=50;env.orders['O1'].update(filled_quantity=50,status='OPEN');await runtime.cycle(UID);p=store.load(UID)['rules'][r['id']]
 assert p['filled']==p['gtt_qty']==50
 with pytest.raises(RuntimeError,match='entry_remainder'):await runtime.exit_position(UID,r['id'],100)
 env.qty=100;env.orders['O1'].update(filled_quantity=100,status='COMPLETE');await runtime.cycle(UID);p=store.load(UID)['rules'][r['id']]
 assert p['gtt_qty']==100 and env.gtt_sends==1
@pytest.mark.asyncio
async def test_unconfirmed_cancel_never_sells(env,monkeypatch):
 r=await armed();await runtime.cycle(UID);await runtime.cycle(UID)
 async def cancel(gid):return {'trigger_id':gid}
 monkeypatch.setattr(env,'delete_gtt',cancel)
 with pytest.raises(RuntimeError,match='stop_release'):await runtime.exit_position(UID,r['id'],100)
 assert env.sends==1
@pytest.mark.asyncio
async def test_terminal_partial_stop_reprotects_residual(env):
 r=await armed();await runtime.cycle(UID);await runtime.cycle(UID)
 p=store.load(UID)['rules'][r['id']];g=env.gtts[p['gtt_id']];g['status']='triggered';g['orders'][0]['result']={'order_result':{'order_id':'STOP1'}}
 env.qty=50;env.orders['STOP1']=dict(env.orders['O1'],order_id='STOP1',transaction_type='SELL',filled_quantity=50,status='CANCELLED',average_price=80)
 await runtime.cycle(UID);p=store.load(UID)['rules'][r['id']]
 assert p['state']=='OPEN' and p['gtt_qty']==50 and env.gtt_sends==2
@pytest.mark.asyncio
async def test_working_stop_child_blocks_manual_exit(env):
 r=await armed();await runtime.cycle(UID);await runtime.cycle(UID)
 p=store.load(UID)['rules'][r['id']];g=env.gtts[p['gtt_id']];g['status']='triggered';g['orders'][0]['result']={'order_result':{'order_id':'STOP1'}}
 env.orders['STOP1']=dict(env.orders['O1'],order_id='STOP1',transaction_type='SELL',filled_quantity=0,status='OPEN',average_price=0)
 with pytest.raises(RuntimeError,match='exit_already_working'):await runtime.exit_position(UID,r['id'],100)
 assert env.sends==1 and env.gtt_sends==1
@pytest.mark.asyncio
async def test_crashed_exit_preparation_never_auto_sells(env):
 r=await armed();await runtime.cycle(UID);await runtime.cycle(UID)
 runtime.update(UID,r['id'],exit_preparation='claimed-before-crash')
 await runtime.cycle(UID)
 with pytest.raises(RuntimeError,match='exit_preparation'):await runtime.exit_position(UID,r['id'],100)
 assert env.sends==1 and store.open_positions(UID)[0].protection_pending
@pytest.mark.asyncio
async def test_foreign_trigger_never_modified_or_cancelled(env):
 r=await armed();await runtime.cycle(UID);await runtime.cycle(UID)
 p=store.load(UID)['rules'][r['id']];env.gtts[p['gtt_id']]['condition']['tradingsymbol']='FOREIGN'
 with pytest.raises(RuntimeError,match='trigger_contract'):await runtime.exit_position(UID,r['id'],100)
 assert env.sends==1 and env.gtts[p['gtt_id']]['status']=='active'
@pytest.mark.asyncio
async def test_broker_inventory_deficit_never_sells(env):
 r=await armed();await runtime.cycle(UID);await runtime.cycle(UID);env.qty=50
 with pytest.raises(RuntimeError,match='owned_quantity'):await runtime.exit_position(UID,r['id'],100)
 assert env.sends==1
@pytest.mark.asyncio
async def test_api_tenant_command_and_validation(env):
 from fastapi import FastAPI
 from fastapi.testclient import TestClient
 from app.api.v1.endpoints.price_rules import router
 from app.core.auth import get_current_user
 app=FastAPI();app.include_router(router,prefix='/api/v1');owner={'uid':UID}
 app.dependency_overrides[get_current_user]=lambda:SimpleNamespace(user_id=owner['uid'])
 with TestClient(app) as api:
  bad=api.post('/api/v1/price-rules/plans',json=dict(symbol=SYMBOL,lots=True,stop_points=20))
  assert bad.status_code==422
  good=api.post('/api/v1/price-rules/plans',json=dict(symbol=SYMBOL,lots=1,stop_points=20))
  assert good.status_code==200;rid=good.json()['id'];owner['uid']='other'
  denied=api.post(f'/api/v1/price-rules/plans/{rid}/command',json=dict(action='arm',revision=1))
  assert denied.status_code==404
  assert store.load(UID)['rules'][rid]['state']=='DRAFT' and env.sends==0
@pytest.mark.asyncio
async def test_global_flatten_disarms_and_counts_ack_as_pending(env):
 r=await armed();out=await runtime.flatten_all(UID);assert out['closed']==0 and env.sends==0
 assert store.load(UID)['rules'][r['id']]['state']=='CANCELLED'
 r=await armed();await runtime.cycle(UID);await runtime.cycle(UID);out=await runtime.flatten_all(UID)
 assert out['closed']==0 and 'awaiting fill' in out['skipped'][0] and env.sends==2
 assert store.load(UID)['rules'][r['id']]['state']=='EXITING'
@pytest.mark.asyncio
async def test_structurally_corrupt_store_cannot_hide_ownership(env):
 db.set_config(store.PREFIX+UID,'{"revision":1,"rules":{"bad":{"state":"BOGUS"}}}')
 with pytest.raises(RuntimeError,match='record_unreadable'):store.open_positions(UID)
 from app.services.protection_health import admission_blocker
 assert admission_blocker(UID,'A')[0]=='protection_unreadable'
@pytest.mark.asyncio
async def test_protection_claim_prevents_duplicate_gtt(env):
 r=await armed();await runtime.cycle(UID)
 runtime.update(UID,r['id'],protection_claim='other-process')
 await runtime.cycle(UID);assert env.gtt_sends==0
 assert store.open_positions(UID)[0].protection_pending
@pytest.mark.asyncio
async def test_real_canonical_entry_gate_preserves_fourth_strategy_scope(env):
 from app.services.execution_service import CanonicalExecutionService,ExecutionRequest
 from app.core.release_scope import may_originate
 assert may_originate('price_rules')
 req=ExecutionRequest(uid=UID,account_id='A',strategy_id='price_rules',generation_id='g',signal_id='s',exchange='NFO',symbol=SYMBOL,side='BUY',quantity=50,payload={'product':'NRML'})
 result=await CanonicalExecutionService().submit_order(req,broker_client=env)
 assert result.status=='REJECTED' and 'risk approval' in result.error.lower() and env.sends==0
@pytest.mark.asyncio
async def test_definitive_exit_refusal_restores_owned_protection(env,monkeypatch):
 r=await armed();await runtime.cycle(UID);await runtime.cycle(UID)
 async def refuse(*args,**kwargs):return ExecutionResult(False,'REJECTED',error='pre-send refusal')
 monkeypatch.setattr(runtime.execution_service,'submit_order',refuse)
 await runtime.exit_position(UID,r['id'],100);await runtime.cycle(UID)
 p=store.load(UID)['rules'][r['id']]
 assert p['state']=='OPEN' and p['gtt_qty']==100 and not p.get('exit_claim') and env.sends==1
@pytest.mark.asyncio
async def test_canonical_exit_uses_durable_inventory_and_live_reduction(env,monkeypatch):
 from app.services.execution_service import CanonicalExecutionService
 r=await armed();await runtime.cycle(UID);await runtime.cycle(UID)
 async def place(**kwargs):
  assert kwargs['transaction_type']=='SELL' and kwargs['quantity']==50
  env.sends+=1;env.qty-=50;oid='CANONICAL_EXIT'
  env.orders[oid]=dict(env.orders['O1'],order_id=oid,transaction_type='SELL',filled_quantity=50,average_price=110)
  return {'order_id':oid}
 monkeypatch.setattr(env,'place_order',place,raising=False)
 monkeypatch.setattr(runtime.execution_service,'submit_order',CanonicalExecutionService().submit_order)
 result=await runtime.exit_position(UID,r['id'],50)
 assert result['order_id']=='CANONICAL_EXIT',result
 await runtime.cycle(UID)
 p=store.load(UID)['rules'][r['id']]
 assert runtime.remaining(p)==50 and p['gtt_qty']==50 and fill_ledger.inventory('A',UID,SYMBOL).net_quantity==50
@pytest.mark.asyncio
async def test_canonical_entry_with_verified_release_prerequisites(env,monkeypatch):
 from app.services.execution_service import CanonicalExecutionService
 from app.services import continuity_doctor
 r=await armed()
 monkeypatch.setattr(continuity_doctor,'egress_status',lambda:SimpleNamespace(verified=True))
 async def binding(*args,**kwargs):return True,'fixture verified binding'
 monkeypatch.setattr(CanonicalExecutionService,'verify_production_account_binding',binding)
 monkeypatch.setattr('app.core.lane_registry.entry_transport_allowed',lambda client:True)
 async def place(**kwargs):
  assert kwargs['transaction_type']=='BUY' and kwargs['quantity']==100 and kwargs['order_type']=='LIMIT'
  env.sends+=1;env.qty=100;oid='CANONICAL_ENTRY'
  env.orders[oid]=dict(order_id=oid,exchange='NFO',tradingsymbol=SYMBOL,product='NRML',transaction_type='BUY',filled_quantity=100,average_price=100,status='COMPLETE',exchange_timestamp=datetime.now(timezone.utc).isoformat())
  return {'order_id':oid}
 monkeypatch.setattr(env,'place_order',place,raising=False)
 monkeypatch.setattr(runtime.execution_service,'submit_order',CanonicalExecutionService().submit_order)
 await runtime.cycle(UID);p=store.load(UID)['rules'][r['id']]
 assert p['state']=='SUBMITTED',p
 await runtime.cycle(UID);p=store.load(UID)['rules'][r['id']]
 assert p['state']=='OPEN' and p['gtt_qty']==100 and env.sends==1
@pytest.mark.asyncio
async def test_application_emergency_flatten_dispatches_price_rules(env,monkeypatch):
 from unittest.mock import AsyncMock
 from app.services.kite_engine import service
 from app.services import snapback_live_exits,gamma_move_runner
 monkeypatch.setattr(service.positions,'open_positions',lambda uid:[])
 monkeypatch.setattr(service.state,'log',lambda *args,**kwargs:None)
 monkeypatch.setattr(snapback_live_exits,'flatten_all',AsyncMock(return_value={'closed':0,'failed':[],'skipped':[]}))
 monkeypatch.setattr(gamma_move_runner,'flatten_all',AsyncMock(return_value={'closed':0,'failed':[],'skipped':[]}))
 r=await armed();await runtime.cycle(UID);await runtime.cycle(UID)
 result=await service.emergency_square_off_all(env,UID)
 assert result['squared_off']==0 and result['submitted_awaiting_fill']==1 and result['failed']==0
 assert result['details'][0]['strategy']=='price_rules' and env.sends==2
@pytest.mark.asyncio
async def test_owned_exit_available_when_stop_creation_would_cross_price(env,monkeypatch):
 r=await armed();await runtime.cycle(UID)
 async def no_protection(*args,**kwargs):raise AssertionError('exit must not depend on new protection')
 monkeypatch.setattr(runtime,'protect',no_protection)
 result=await runtime.exit_position(UID,r['id'],100)
 assert result['order_id']=='O2' and env.qty==0
@pytest.mark.asyncio
async def test_unknown_stop_creation_blocks_competing_exit(env,monkeypatch):
 r=await armed();await runtime.cycle(UID)
 async def lost(**kwargs):env.gtt_sends+=1;raise TimeoutError('lost')
 monkeypatch.setattr(env,'place_gtt',lost);await runtime.cycle(UID)
 with pytest.raises(RuntimeError,match='uncertain_protection'):await runtime.exit_position(UID,r['id'],100)
 assert env.sends==1
@pytest.mark.asyncio
async def test_snapshot_does_not_offer_entry_for_paper_account(env,monkeypatch):
 from app.api.v1.endpoints.price_rules import snapshot
 from app.services.safety_supervisor import SafetySupervisor
 monkeypatch.setattr(SafetySupervisor,'authorize',lambda *args,**kwargs:SimpleNamespace(allowed=True,reason=''))
 monkeypatch.setattr(runtime.accounts,'get_active',lambda uid:SimpleNamespace(id='A',is_paper=True))
 result=await snapshot(SimpleNamespace(user_id=UID))
 assert result['entry_allowed'] is False and result['entry_blocker']=='bound_live_account_required'
@pytest.mark.asyncio
async def test_one_unreadable_tenant_does_not_stop_other_management(env,monkeypatch):
 import asyncio
 seen=[]
 monkeypatch.setattr(store,'known_uids',lambda:['bad','good'])
 async def one(uid):
  seen.append(uid)
  if uid=='bad':raise RuntimeError('unreadable tenant')
 async def end(seconds):raise asyncio.CancelledError()
 monkeypatch.setattr(runtime,'cycle',one);monkeypatch.setattr(runtime.asyncio,'sleep',end)
 with pytest.raises(asyncio.CancelledError):await runtime.run_loop()
 assert seen==['bad','good']
@pytest.mark.asyncio
async def test_new_trigger_ack_cannot_adopt_already_owned_id(env,monkeypatch):
 from app.services import external_positions
 r=await armed();await runtime.cycle(UID)
 monkeypatch.setattr(external_positions,'owned_gtt_ids',lambda account:{'1'})
 await runtime.cycle(UID);p=store.load(UID)['rules'][r['id']]
 assert p['state']=='RECOVERY_REQUIRED' and not p.get('gtt_id') and p['protection_pending']
 assert 'already_owned' in p['error'] and env.sends==1
@pytest.mark.asyncio
@pytest.mark.parametrize('blocker',['master','egress','binding'])
async def test_snapshot_reports_server_readiness_brakes(env,monkeypatch,blocker):
 from app.api.v1.endpoints.price_rules import snapshot
 from app.services.safety_supervisor import SafetySupervisor
 from app.services import continuity_doctor,account_binding_service
 from app.core import lane_registry
 monkeypatch.setattr(SafetySupervisor,'authorize',lambda *args,**kwargs:SimpleNamespace(allowed=True,reason=''))
 monkeypatch.setattr(lane_registry,'LIVE_EXECUTION_ENABLED',blocker!='master')
 monkeypatch.setattr(continuity_doctor,'egress_status',lambda:SimpleNamespace(verified=blocker!='egress'))
 monkeypatch.setattr(account_binding_service,'active_binding',lambda:None)
 result=await snapshot(SimpleNamespace(user_id=UID))
 expected={'master':'LIVE_EXECUTION_DISABLED','egress':'verified_static_egress_required','binding':'current_live_broker_binding_required'}
 assert result['entry_allowed'] is False and result['entry_blocker']==expected[blocker]


@pytest.mark.asyncio
async def test_price_rules_switch_off_blocks_new_entries_not_owned_protection(env):
 r=await armed()
 assert (await runtime.set_enabled(UID,False,True))['enabled'] is False
 await runtime.cycle(UID)
 assert env.sends==0 and store.load(UID)['rules'][r['id']]['state']=='ARMED'
 with pytest.raises(ValueError,match='switch_stale'):
  await runtime.set_enabled(UID,True,True)
 await runtime.set_enabled(UID,True,False)
 await runtime.cycle(UID)
 assert env.sends==1
 await runtime.set_enabled(UID,False,True)
 await runtime.cycle(UID)
 p=store.load(UID)['rules'][r['id']]
 assert env.sends==1 and p['state']=='OPEN' and p['gtt_qty']==100

@pytest.mark.asyncio
async def test_price_rules_switch_defaults_off_and_persists_per_user(env):
 assert store.load('fresh-user')['enabled'] is False
 await runtime.set_enabled(UID,False,True)
 assert store.load(UID)['enabled'] is False and store.load('fresh-user')['enabled'] is False
 with pytest.raises(RuntimeError,match='enabled_unreadable'):
  store.mutate(UID,lambda doc:doc.update(enabled='yes'))

@pytest.mark.asyncio
async def test_price_rules_switch_http_and_tenant_isolation(env):
 from fastapi import FastAPI
 from fastapi.testclient import TestClient
 from app.api.v1.endpoints.price_rules import router
 from app.core.auth import get_current_user
 app=FastAPI();app.include_router(router,prefix='/api/v1')
 owner={'uid':UID}
 app.dependency_overrides[get_current_user]=lambda:SimpleNamespace(user_id=owner['uid'])
 with TestClient(app) as client:
  off=client.post('/api/v1/price-rules/enabled',json={'enabled':False,'expected_enabled':True})
  assert off.status_code==200 and off.json()['enabled'] is False
  stale=client.post('/api/v1/price-rules/enabled',json={'enabled':True,'expected_enabled':True})
  assert stale.status_code==409
  invalid=client.post('/api/v1/price-rules/enabled',json={'enabled':'true','expected_enabled':False})
  assert invalid.status_code==422
  owner['uid']='another-user'
  own=client.post('/api/v1/price-rules/enabled',json={'enabled':True,'expected_enabled':False})
  assert own.status_code==200
 assert store.load(UID)['enabled'] is False and store.load('another-user')['enabled'] is True
