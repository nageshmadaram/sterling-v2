import {render,screen,cleanup} from '@testing-library/react';
import {afterEach,describe,it,expect} from 'vitest';
import '@testing-library/jest-dom/vitest';
import {MarginCard} from '../KiteDashboard';
afterEach(cleanup);
describe('dashboard margin availability',()=>{
 it('shows missing broker balances as unavailable',()=>{
  render(<MarginCard title="Equity" available={null} used={null} opening={null} loading={false}/>);
  expect(screen.getAllByText('—')).toHaveLength(3);expect(screen.queryByText('0')).not.toBeInTheDocument();
 });
 it('preserves verified negative balances and zero used margin',()=>{
  render(<MarginCard title="Equity" available={-6342.1} used={0} opening={-6342.1} loading={false}/>);
  expect(screen.getAllByText('-6,342.1')).toHaveLength(2);expect(screen.getByText('0')).toBeInTheDocument();
 });
});
