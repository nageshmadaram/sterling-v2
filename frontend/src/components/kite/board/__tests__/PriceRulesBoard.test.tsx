import { render,screen,fireEvent,cleanup } from '@testing-library/react';
import { beforeEach,afterEach,describe,it,expect,vi } from 'vitest';
import '@testing-library/jest-dom/vitest';
const mocks=vi.hoisted(()=>({query:{data:undefined as any,isLoading:false,isError:false,error:null,refetch:vi.fn()},simulation:false,open:vi.fn()}));
vi.mock('@tanstack/react-query',()=>({useQuery:()=>mocks.query}));
vi.mock('../../../../store/useViewStore',()=>({useIsSimulation:()=>mocks.simulation}));
vi.mock('../../config/registry',()=>({openSettingsSection:mocks.open}));
import { PriceRulesBoard } from '../PriceRulesBoard';
beforeEach(()=>{mocks.simulation=false;mocks.open.mockClear();mocks.query.isError=false;mocks.query.isLoading=false;mocks.query.data={entry_allowed:true,rules:[]};});
afterEach(cleanup);
describe('Price Rules signals',()=>{
 it('explains an empty board and opens creation',()=>{
  render(<PriceRulesBoard/>);expect(screen.getByText('No Price Rules signals yet')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button',{name:'Create entry plan'}));expect(mocks.open).toHaveBeenCalledWith('priceRules');
 });
 it('shows actual triggers, protection and owned quantities',()=>{
  mocks.query.data.rules=[{id:'r',symbol:'NIFTYCE',exchange:'NFO',account_id:'A',revision:2,state:'OPEN',watch:'NSE:NIFTY 50',comparison:'>=',level:25000,quantity:50,filled:50,exits:{exit:{filled:25}},entry_price:100,stop:80,target:140,gtt_id:123,gtt_qty:25}];
  render(<PriceRulesBoard/>);expect(screen.getByText('NSE:NIFTY 50 >= 25000')).toBeInTheDocument();expect(screen.getByText('25')).toBeInTheDocument();expect(screen.getByText('GTT · 25 units')).toBeInTheDocument();
 });
 it('filters history separately from live plans',()=>{
  mocks.query.data.rules=[{id:'r',symbol:'OLDCE',state:'CLOSED',exits:{},filled:0,comparison:'MANUAL',quantity:1}];
  render(<PriceRulesBoard/>);expect(screen.queryByText('OLDCE')).not.toBeInTheDocument();fireEvent.click(screen.getByRole('checkbox',{name:'History'}));expect(screen.getByText('OLDCE')).toBeInTheDocument();
 });
 it('never displays live rules as simulation signals',()=>{
  mocks.simulation=true;render(<PriceRulesBoard/>);expect(screen.getByText(/Switch to Live/)).toBeInTheDocument();
 });
});
