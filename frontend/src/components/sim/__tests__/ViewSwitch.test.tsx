import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, screen, waitFor } from '@testing-library/react';

const get = vi.fn();
vi.mock('../../../utils/api', () => ({ api: { get: (...a: unknown[]) => get(...a), put: vi.fn(), post: vi.fn() } }));

import { ViewSwitch } from '../ViewSwitch';
import { useViewStore } from '../../../store/useViewStore';
import { resetDraftGuard, setDraftDirty } from '../../kite/config/unsavedDraftGuard';
import { apiError, makeStatus, renderWithClient } from './simTestUtils';

beforeEach(() => {
  get.mockReset();
  useViewStore.setState({ view: 'live' });
  resetDraftGuard();
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); resetDraftGuard(); useViewStore.setState({ view: 'live' }); });

describe('ViewSwitch', () => {
  it('offers a compact mobile toggle with clear mode state', async () => {
    get.mockResolvedValue(makeStatus());
    renderWithClient(<ViewSwitch />);
    const toggle=await screen.findByRole('switch',{name:'Live trading view'});
    expect(toggle).toHaveAttribute('aria-checked','true');
    fireEvent.click(toggle);
    expect(useViewStore.getState().view).toBe('simulation');
    expect(toggle).toHaveTextContent('KITE SIMULATION');
    fireEvent.click(toggle);
    expect(useViewStore.getState().view).toBe('live');
  });
  it('uses one consistent mode control on desktop and mobile', async () => {
    get.mockResolvedValue(makeStatus());renderWithClient(<ViewSwitch />);
    const toggle=await screen.findByRole('switch',{name:'Live trading view'});
    expect(toggle).toHaveTextContent(/^KITE$/);expect(screen.queryByRole('group',{name:'Trading view'})).toBeNull();
  });

  it('switches the whole app to the Simulation view and back', async () => {
    get.mockResolvedValue(makeStatus());
    renderWithClient(<ViewSwitch />);
    fireEvent.click(await screen.findByRole('switch', { name: 'Live trading view' }));
    expect(useViewStore.getState().view).toBe('simulation');
    expect(screen.getByRole('switch', { name: 'Live trading view' })).toHaveAttribute('aria-checked', 'false');
    fireEvent.click(screen.getByRole('switch', { name: 'Live trading view' }));
    expect(useViewStore.getState().view).toBe('live');
  });

  it('is not shown to anyone but the owner (the status route says 403)', async () => {
    get.mockRejectedValue(apiError(403, 'Admin access required'));
    renderWithClient(<ViewSwitch />);
    await waitFor(() => expect(get).toHaveBeenCalled());
    await act(async () => { await new Promise((r) => setTimeout(r, 20)); });
    expect(screen.queryByTestId('view-switch')).toBeNull();
  });

  it('is hidden until the status arrives, so a non-owner never sees it flash', () => {
    get.mockReturnValue(new Promise(() => {}));
    renderWithClient(<ViewSwitch />);
    expect(screen.queryByTestId('view-switch')).toBeNull();
  });

  it('stays available in the Simulation view even when the status cannot load: it is the way back', async () => {
    useViewStore.setState({ view: 'simulation' });
    get.mockRejectedValue(apiError(503, { code: 'tenant_unavailable', message: 'x' }));
    renderWithClient(<ViewSwitch />);
    fireEvent.click(await screen.findByRole('switch', { name: 'Live trading view' }));
    expect(useViewStore.getState().view).toBe('live');
  });

  it('asks before throwing away an unapplied settings draft', async () => {
    get.mockResolvedValue(makeStatus());
    renderWithClient(<ViewSwitch />);
    const sim = await screen.findByRole('switch', { name: 'Live trading view' });
    setDraftDirty('settings', true);
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    fireEvent.click(sim);
    expect(confirm).toHaveBeenCalledTimes(1);
    expect(useViewStore.getState().view).toBe('live');
    confirm.mockReturnValue(true);
    fireEvent.click(sim);
    expect(useViewStore.getState().view).toBe('simulation');
  });

  it('never changes mode simply by rendering', async () => {
    get.mockResolvedValue(makeStatus());renderWithClient(<ViewSwitch />);
    await screen.findByRole('switch',{name:'Live trading view'});
    expect(useViewStore.getState().view).toBe('live');
  });
});
