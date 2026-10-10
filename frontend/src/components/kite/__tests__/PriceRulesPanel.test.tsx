import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
const state = vi.hoisted(() => ({ simulation: false, query: {} as any, post: vi.fn(), chain: vi.fn() }));
vi.mock('../../../store/useViewStore', () => ({ useIsSimulation: () => state.simulation }));
vi.mock('../../../hooks/useKiteOptionChain', () => ({ useKiteOptionChain: (symbol: string) => { state.chain(symbol); return { data: { chain: {}, expiries: [] } }; } }));
vi.mock('../../../utils/api', () => ({ api: { post: state.post } }));
vi.mock('@tanstack/react-query', () => ({
  useQuery: () => state.query,
  useQueryClient: () => ({ invalidateQueries: vi.fn() }),
  useMutation: ({ mutationFn }: any) => ({ isPending: false, mutate: mutationFn, mutateAsync: mutationFn }),
}));
import { PriceRulesPanel } from '../PriceRulesPanel';
const rule = { id: 'rule-1', revision: 9, state: 'DRAFT', account_id: 'A', exchange: 'NFO', symbol: 'NIFTYCE', quantity: 50, filled: 0, exits: {}, comparison: 'MANUAL', error: '' };
beforeEach(() => { state.simulation = false; state.post.mockReset(); state.post.mockResolvedValue({}); state.chain.mockClear(); state.query = { data: { entry_allowed: true, rules: [rule] } }; });
describe('Price Rules operator controls', () => {
  it('sends the displayed revision when arming', () => {
    render(<PriceRulesPanel />); fireEvent.click(screen.getByRole('button', { name: 'Arm revision 9' }));
    expect(state.post).toHaveBeenCalledWith('/api/v1/price-rules/plans/rule-1/command', { revision: 9, action: 'arm' });
  });
  it('blocks entries but preserves owned exits under an entry halt', () => {
    state.query.data = { entry_allowed: false, entry_blocker: 'SAFE_MODE', rules: [{ ...rule, state: 'OPEN', filled: 50 }] };
    render(<PriceRulesPanel />); expect(screen.getByRole('button', { name: 'Publish entry plan' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Exit owned 100%' }));
    expect(state.post).toHaveBeenCalledWith('/api/v1/price-rules/positions/rule-1/exit', { percent: 100 });
  });
  it('refuses commands from an unreadable snapshot', () => {
    state.query.isError = true; state.query.error = new Error('offline');
    render(<PriceRulesPanel />); expect(screen.getByRole('button', { name: 'Arm revision 9' })).toBeDisabled();
  });
  it('does not offer live controls or request a live chain in simulation', () => {
    state.simulation = true; render(<PriceRulesPanel />);
    expect(screen.queryByRole('button')).toBeNull(); expect(state.chain).toHaveBeenCalledWith('');
  });
  it('ignores Escape while typing', () => {
    state.query.data.rules = [{ ...rule, state: 'OPEN', filled: 50 }]; render(<PriceRulesPanel />);
    fireEvent.keyDown(screen.getByPlaceholderText('Select a call or put below'), { key: 'Escape' }); expect(state.post).not.toHaveBeenCalled();
  });
});
it('exports confirmed fills as gross history with exact identity', () => {
  const create = vi.fn(() => 'blob:report'); const revoke = vi.fn();
  vi.stubGlobal('URL', { createObjectURL: create, revokeObjectURL: revoke });
  const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
  render(<PriceRulesPanel />); fireEvent.click(screen.getByRole('button', { name: /Export confirmed history/ }));
  expect(create).toHaveBeenCalledWith(expect.any(Blob)); expect(click).toHaveBeenCalled(); expect(revoke).toHaveBeenCalledWith('blob:report');
  click.mockRestore(); vi.unstubAllGlobals();
});
