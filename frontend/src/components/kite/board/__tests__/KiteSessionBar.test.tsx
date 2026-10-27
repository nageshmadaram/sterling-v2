import React from 'react';
import {beforeEach, describe, expect, it, vi} from 'vitest';
import {fireEvent, render, screen, cleanup} from '@testing-library/react';
import {KiteSessionBar} from '../KiteSessionBar';
import {useKiteStatusCardStore} from '../../../../store/useKiteStatusCardStore';

let status: any;
let accounts: any;
vi.mock('../../../../hooks/useKite', () => ({
  useKiteStatus: () => status,
  useKiteAccounts: () => ({data:accounts}),
}));
beforeEach(() => {
  cleanup();
  status = {data:{connected:true}, isLoading:false, isError:false};
  accounts = {accounts:[], active_id:null};
  useKiteStatusCardStore.setState({isOpen:false});
});
describe('Kite session truth', () => {
  it('keeps connected accounts quiet', () => {
    render(<KiteSessionBar />);
    expect(screen.queryByRole('button', {name:/Kite/})).toBeNull();
  });

  it('treats unreadable broker status as unverified instead of disconnecting', () => {
    status = {data:{connected:true}, isError:true};
    render(<KiteSessionBar />);
    expect(screen.getByRole('button')).toHaveTextContent('Kite check unavailable');
    expect(screen.queryByText(/Kite disconnected/)).toBeNull();
  });

  it('treats a transient session check as unverified', () => {
    status = {data:{connected:false, transient:true}, isError:false};
    render(<KiteSessionBar />);
    expect(screen.getByRole('button')).toHaveTextContent('Kite check unavailable');
  });

  it('shows the login action if no token remains and opens the broker panel', () => {
    status = {data:{connected:false}, isError:false};
    render(<KiteSessionBar />);
    fireEvent.click(screen.getByRole('button'));
    expect(screen.getByRole('button')).toHaveTextContent('Kite disconnected');
    expect(useKiteStatusCardStore.getState().isOpen).toBe(true);
  });

  it('shows saved and unverified until the active account is validated', () => {
    status = {data:{connected:false, account_id:'active', message:'Cannot reach Kite'}, isError:false};
    accounts = {active_id:'active', accounts:[{id:'active', connected:true, token_expires_at_ms:Date.now()+60_000}]};
    render(<KiteSessionBar />);
    expect(screen.getByRole('button')).toHaveTextContent('Kite session saved · unverified');
    expect(screen.getByRole('button')).toHaveTextContent('Verify');
  });
});
