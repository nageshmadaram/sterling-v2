/** Phase 9 offline durable alert inbox smoke tests (no external sends). */
import { describe,it,expect,vi,beforeEach } from 'vitest';
import { render,screen,fireEvent,waitFor } from '@testing-library/react';
import { QueryClient,QueryClientProvider } from '@tanstack/react-query';
import { OfflineAlertInbox } from '../OfflineAlertInbox';

const get=vi.fn();
const post=vi.fn();
const put=vi.fn();
const del=vi.fn();
vi.mock('../../../../utils/api',()=>({api:{
  get:(...args:unknown[])=>get(...args),
  post:(...args:unknown[])=>post(...args),
  put:(...args:unknown[])=>put(...args),
  delete:(...args:unknown[])=>del(...args),
}}));
const strategy='offline-strategy';
const id='a'.repeat(64);
const alert={
  alert_id:id,strategy_id:strategy,priority:'critical',category:'emergency_stop',
  source_system:'offline_gate',source_type:'gate',source_ref:'test-event',
  source_sha256:'f'.repeat(64),run_id:null,decision_id:null,
  message:'Synthetic emergency stop in validation',reason:'offline source verified',
  state:'active',created_at_ms:Date.now()-1000,last_event_at_ms:Date.now()-1000,
  audit_ref:'b'.repeat(64),expires_at_ms:null,escalated:true,
  delivery:{telegram:{state:'failed',attempt:1,last_attempt_at_ms:Date.now()-1000,
    retry_after_ms:Date.now()+60000,receipt:null,reason:'synthetic outage'}},
  history:[{event_id:'event-1',at_ms:Date.now()-1000,kind:'created',
    actor:'owner',reason:'offline source verified',source_sha256:'f'.repeat(64)}],
  execution_authorized:false,
};
const envelope={source:'offline_alert_ledger',read_only:true,execution_authorized:false,
  items:[alert]};
function show(){
  const qc=new QueryClient({defaultOptions:{queries:{retry:false,refetchOnWindowFocus:false}}});
  return render(<QueryClientProvider client={qc}><OfflineAlertInbox strategyId={strategy}/></QueryClientProvider>);
}
beforeEach(()=>{get.mockReset();post.mockReset();put.mockReset();del.mockReset();});
describe('Phase 9 audit-only alert inbox',()=>{
  it('renders priority, provenance, delivery failure, replay and escalation',async()=>{
    get.mockResolvedValue(envelope);
    show();
    const panel=await screen.findByTestId('offline-durable-alerts');
    expect(panel).toHaveTextContent('Synthetic emergency stop in validation');
    expect(panel).toHaveTextContent('critical');
    expect(panel).toHaveTextContent('Escalated');
    expect(panel).toHaveTextContent('telegram: failed (attempt 1)');
    fireEvent.click(screen.getByText('Immutable alert and delivery audit'));
    expect(panel).toHaveTextContent('b'.repeat(64));
    expect(get.mock.calls.every(([url])=>String(url).startsWith('/api/v1/offline-validation/'))).toBe(true);
    expect(post).not.toHaveBeenCalled();expect(put).not.toHaveBeenCalled();expect(del).not.toHaveBeenCalled();
  });
  it('ack requires explicit reason and targets only metadata endpoint',async()=>{
    get.mockResolvedValue(envelope);
    post.mockResolvedValue({changed:true,state:'acknowledged',execution_authorized:false,
      mutation_scope:'alert_metadata_only'});
    show();
    const btn=await screen.findByRole('button',{name:'Acknowledge review'});
    expect(btn).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Acknowledgement reason'),
                     {target:{value:'Reviewed offline emergency stop receipt'}});
    expect(btn).toBeEnabled();
    fireEvent.click(btn);
    await waitFor(()=>expect(post).toHaveBeenCalledTimes(1));
    const [url,body]=post.mock.calls[0];
    expect(url).toBe(`/api/v1/offline-validation/strategies/${strategy}/alerts/${id}/acknowledge`);
    expect(body).toEqual({reason:'Reviewed offline emergency stop receipt'});
    expect(put).not.toHaveBeenCalled();expect(del).not.toHaveBeenCalled();
  });
  it('withholds records for unauthorized or missing ledger',async()=>{
    get.mockRejectedValue(new Error('permission denied'));
    show();
    expect(await screen.findByTestId('offline-alert-untrusted')).toHaveTextContent('UNTRUSTED');
    expect(screen.queryByText('Synthetic emergency stop in validation')).toBeNull();
  });
  it('fails closed if any event claims execution authority or belongs to another owner',async()=>{
    get.mockResolvedValue({...envelope,items:[{...alert,execution_authorized:true}]});
    const r=show();
    await screen.findByTestId('offline-alert-untrusted');
    expect(screen.queryByRole('button',{name:'Acknowledge review'})).toBeNull();
    r.unmount();
    get.mockResolvedValue({...envelope,items:[{...alert,strategy_id:'other'}]});
    show();
    await screen.findByTestId('offline-alert-untrusted');
  });
  it('renders an honest empty record state and no implicit delivery',async()=>{
    get.mockResolvedValue({...envelope,items:[]});
    show();
    expect(await screen.findByText(/No durable alerts recorded/)).toBeInTheDocument();
    expect(post).not.toHaveBeenCalled();
  });
  it('ack failure never assumes success or resolves alert',async()=>{
    get.mockResolvedValue(envelope);post.mockRejectedValue(new Error('network failure'));
    show();
    await screen.findByRole('button',{name:'Acknowledge review'});
    fireEvent.change(screen.getByLabelText('Acknowledgement reason'),
                     {target:{value:'Reviewed offline alert'}});
    fireEvent.click(screen.getByRole('button',{name:'Acknowledge review'}));
    expect(await screen.findByText(/Acknowledgement failed/)).toBeInTheDocument();
    expect(screen.getByText('Synthetic emergency stop in validation')).toBeInTheDocument();
  });
});
