import React from 'react';
import {beforeEach, describe, expect, it, vi} from 'vitest';
import {fireEvent, render, screen} from '@testing-library/react';
import {KiteSessionBar} from '../KiteSessionBar';
let status: any;
vi.mock('../../../../hooks/useKite', () => ({useKiteStatus: () => status}));
beforeEach(() => {status = {data: {connected: true}, isLoading: false, isError: false};});
describe('Kite session truth', () => {
  it('keeps connected accounts quiet', () => {render(<KiteSessionBar />); expect(screen.queryByRole('alert')).toBeNull();});
  it('does not hide unreadable broker status behind the last connected value', () => {
    status = {data: {connected: true}, isError: true};
    render(<KiteSessionBar />);
    expect(screen.getByRole('alert')).toHaveTextContent(/session status could not be verified/i);
    expect(screen.queryByText(/Kite is not logged in/)).toBeNull();
  });
  it('identifies a transient response as unknown instead of logged out', () => {
    status = {data: {connected: false, transient: true}};
    render(<KiteSessionBar />);expect(screen.getByRole('alert')).toHaveTextContent(/could not be verified/i);
  });
  it('offers an explicit connection action for a missing session', () => {
    status = {data: {connected: false}};const nav=vi.fn();window.addEventListener('kite-nav-click',nav);
    render(<KiteSessionBar />);fireEvent.click(screen.getByRole('button',{name:'Log in to Kite'}));
    expect(screen.getByRole('alert')).toHaveTextContent(/not logged in/i);
    expect(nav.mock.calls[0][0].detail).toBe('connect');window.removeEventListener('kite-nav-click',nav);
  });
});
