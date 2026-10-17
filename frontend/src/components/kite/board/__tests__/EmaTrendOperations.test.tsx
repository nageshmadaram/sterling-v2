import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { EmaTrendOperations } from '../EmaTrendOperations';
import { emaCampaign } from '../../../../test/emaTrendFixtures';

const mutate = vi.hoisted(() => vi.fn());
const state = vi.hoisted(() => ({ data: undefined as unknown }));
vi.mock('../../../../hooks/useEmaTrend', () => ({ useEmaTrendOperation: () => ({ mutate, data: state.data, isPending: false, isSuccess: Boolean(state.data), error: null }) }));

describe('EMA paper operations', () => {
  beforeEach(() => { mutate.mockClear(); state.data = undefined; });
  it('shows all-profile held and unresolved exposure and requires a second click to flatten', () => {
    const held = { ...emaCampaign('swing'), qty: 103 };
    const uncertain = { ...emaCampaign('ultra_scalping'), id: 'uncertain', qty: 7, state: 'RECOVERY_REQUIRED' };
    render(<EmaTrendOperations campaigns={[held, uncertain]} />);
    expect(screen.getByText('2 active campaigns · 110 confirmed shares · 1 pending or unresolved campaigns')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Close all EMA paper campaigns' }));
    expect(mutate).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Confirm paper close all' }));
    expect(mutate).toHaveBeenCalledExactlyOnceWith('flatten');
  });
  it('cannot flatten when inventory is unavailable or in historical replay', () => {
    const { rerender } = render(<EmaTrendOperations campaigns={[emaCampaign()]} unavailable />);
    expect(screen.getByRole('button', { name: 'Close all EMA paper campaigns' })).toBeDisabled();
    rerender(<EmaTrendOperations campaigns={[emaCampaign()]} readOnly />);
    expect(screen.queryByRole('region', { name: 'EMA Trend paper operations' })).not.toBeInTheDocument();
    expect(mutate).not.toHaveBeenCalled();
  });
  it('shows individual unresolved close-all outcomes instead of claiming closure', () => {
    state.data = { status: 'UNRESOLVED', results: [{ campaign_id: 'uncertain', status: 'UNRESOLVED', reason: 'UNKNOWN settlement' }] };
    render(<EmaTrendOperations campaigns={[emaCampaign()]} />);
    expect(screen.getByText('Unresolved uncertain: UNKNOWN settlement')).toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent('A request does not confirm closure');
  });
  it('allows cancellation without submitting an order', () => {
    render(<EmaTrendOperations campaigns={[emaCampaign()]} />);
    fireEvent.click(screen.getByRole('button', { name: 'Close all EMA paper campaigns' }));
    fireEvent.click(screen.getByRole('button', { name: 'Keep campaigns' }));
    expect(screen.queryByRole('button', { name: 'Confirm paper close all' })).not.toBeInTheDocument();
    expect(mutate).not.toHaveBeenCalled();
  });
});

it('warns when supervision is stale without disabling the emergency close request', () => {
  render(<EmaTrendOperations campaigns={[emaCampaign()]} supervision={{ status: 'STALE', age_ms: 45000, errors: [{ reason: 'quote feed unavailable' }] }} />);
  expect(screen.getByText(/Supervision: STALE/)).toHaveTextContent('45s ago');
  expect(screen.getByText(/Paper supervision needs attention/)).toBeInTheDocument();
  expect(screen.getByText('quote feed unavailable')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Close all EMA paper campaigns' })).toBeEnabled();
});
