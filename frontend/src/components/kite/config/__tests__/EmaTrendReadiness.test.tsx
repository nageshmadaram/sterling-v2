import React from 'react';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { EmaTrendReadiness } from '../EmaTrendReadiness';
import { api } from '../../../../utils/api';

vi.mock('../../../../utils/api', () => ({ api: { post: vi.fn() } }));
afterEach(() => { cleanup(); vi.clearAllMocks(); });

it('checks saved scope and displays blocked observations without a live activation control', async () => {
  vi.mocked(api.post).mockResolvedValue({ id: 'acceptance-1', status: 'BLOCKED', symbol: 'NSE:INFY', mode: 'swing',
    completed_ms: 1, checks: [{ name: 'owned_broker_protection', status: 'BLOCKED', detail: 'Protection not integrated.' }] });
  render(<EmaTrendReadiness symbols={['NSE:INFY']} modes={['swing']} revision={4} />);
  fireEvent.click(screen.getByRole('button', { name: 'Check connected readiness' }));
  await screen.findByText('Live readiness: BLOCKED');
  expect(api.post).toHaveBeenCalledWith('/api/v1/ema-trend/readiness/check', {
    symbol: 'NSE:INFY', mode: 'swing', expected_config_revision: 4,
  });
  expect(screen.getByText(/Protection not integrated/)).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: /enable live/i })).not.toBeInTheDocument();
});

it('requires a saved instrument before checking', () => {
  render(<EmaTrendReadiness symbols={[]} modes={['swing']} revision={0} />);
  expect(screen.getByRole('button', { name: 'Check connected readiness' })).toBeDisabled();
});
