// Regression coverage for the trader QA findings.
import { beforeEach, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
const state = vi.hoisted(() => ({ query: {} as any, post: vi.fn() }));
vi.mock('../../../store/useViewStore', () => ({ useIsSimulation: () => false }));
vi.mock('../../../hooks/useKiteOptionChain', () => ({ useKiteOptionChain: () => ({ data: { chain: {}, expiries: [] } }) }));
vi.mock('../../../utils/api', () => ({ api: { post: state.post } }));
vi.mock('@tanstack/react-query', () => ({
  useQuery: () => state.query,
  useQueryClient: () => ({ invalidateQueries: vi.fn() }),
  useMutation: ({ mutationFn }: any) => ({ isPending: false, mutate: mutationFn, mutateAsync: mutationFn }),
}));
import { PriceRulesPanel } from '../PriceRulesPanel';
import { AdaptiveEdgePositionCalculator } from '../AdaptiveEdgePositionCalculator';
beforeEach(() => { state.post.mockReset(); state.post.mockResolvedValue({}); });

it('Escape never requests a financial exit', async () => {
  state.query = { data: { entry_allowed: false, rules: ['one', 'two'].map(id => ({
    id, revision: 1, state: 'OPEN', account_id: 'QA', exchange: 'NFO', symbol: 'QA-CE',
    quantity: 75, filled: 75, exits: {}, comparison: 'MANUAL', error: '',
  })) } };
  render(<PriceRulesPanel />);
  fireEvent.keyDown(document.body, { key: 'Escape' });
  expect(state.post).not.toHaveBeenCalled();
});

it('copied plans preserve an unknown stop and risk reward', () => {
  const writeText = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
  render(<AdaptiveEdgePositionCalculator symbol="QA-CE" lotSize={75}
    defaultEntryPrice={100} defaultExit={140} hideTsl allowOrder={false} />);
  expect(screen.getByText('No strategy stop supplied')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: /Copy Plan/ }));
  expect(writeText).toHaveBeenCalledWith(expect.stringContaining('Stop Loss: Not supplied'));
  console.log('COPIED_PLAN_TRACE', writeText.mock.calls[0][0]);
});
