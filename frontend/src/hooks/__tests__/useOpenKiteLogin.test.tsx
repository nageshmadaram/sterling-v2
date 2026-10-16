import { act, cleanup, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useOpenKiteLogin } from '../useKite';
const { get, notify } = vi.hoisted(() => ({ get: vi.fn(), notify: vi.fn() }));
vi.mock('../../utils/api', () => ({ api: { get } }));
vi.mock('../../store/useKiteNotifications', () => ({ notifyOrder: notify }));

describe('Kite popup login', () => {
  let popup: { location: { href: string }; close: ReturnType<typeof vi.fn>; closed: boolean };
  beforeEach(() => {
    vi.clearAllMocks();
    popup = { location: { href: '' }, close: vi.fn(), closed: false };
    vi.spyOn(window, 'open').mockReturnValue(popup as unknown as Window);
  });
  afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.useRealTimers(); });

  it('opens a visible preparation page and navigates to a fresh Kite URL', async () => {
    get.mockResolvedValue({ confirmation_id: 'expected-receipt', account_id: 'a1', login_url: 'https://kite.zerodha.com/connect/login?api_key=synthetic&v=3' });
    const { result } = renderHook(() => useOpenKiteLogin());
    await act(async () => { await result.current.open(); });
    expect(window.open).toHaveBeenCalledWith(expect.stringContaining('/kite-login-opening.html'), 'sterling-kite-login', expect.any(String));
    expect(popup.location.href).toContain('https://kite.zerodha.com/connect/login');
    expect(result.current.phase).toBe('waiting');
  });

  it('closes the popup and displays broker credential rejection', async () => {
    get.mockRejectedValue(new Error('Zerodha rejected the saved Kite API key.'));
    const { result } = renderHook(() => useOpenKiteLogin());
    await act(async () => { await result.current.open(); });
    expect(popup.close).toHaveBeenCalled();
    expect(result.current.error).toContain('rejected');
    expect(result.current.phase).toBe('failed');
  });

  it('keeps the public callback from the backend when Sterling is opened locally', async () => {
    get.mockResolvedValue({
      confirmation_id: 'expected-receipt', account_id: 'a1', login_url: 'https://kite.zerodha.com/connect/login?api_key=synthetic&v=3',
      redirect_uri: 'https://sterling.example.com/api/v1/kite/callback',
    });
    const { result } = renderHook(() => useOpenKiteLogin());
    await act(async () => { await result.current.open(); });
    expect(result.current.redirectUri).toBe('https://sterling.example.com/api/v1/kite/callback');
  });

  it('resolves relative callbacks against the current app origin', async () => {
    get.mockResolvedValue({
      confirmation_id: 'expected-receipt', account_id: 'a1', login_url: 'https://kite.zerodha.com/connect/login?api_key=synthetic&v=3',
      redirect_uri: '/api/v1/kite/callback',
    });
    const { result } = renderHook(() => useOpenKiteLogin());
    await act(async () => { await result.current.open(); });
    expect(result.current.redirectUri).toBe(`${window.location.origin}/api/v1/kite/callback`);
  });

  it('verifies a new saved session when mobile returns without an opener message', async () => {
    vi.useFakeTimers();
    let lastLogin = 100;
    let loginConfirmation: string | null = null;
    get.mockImplementation((path: string) => Promise.resolve(path.endsWith('/login-url')
      ? { confirmation_id: 'expected-receipt', account_id: 'a1', login_url: 'https://kite.zerodha.com/connect/login?api_key=synthetic&v=3' }
      : { active_id: 'a1', accounts: [{ id: 'a1', connected: true, last_login_at_ms: lastLogin, login_confirmation_id: loginConfirmation }] }));
    const handoff = vi.spyOn(window, 'postMessage').mockImplementation(() => {});
    const { result } = renderHook(() => useOpenKiteLogin());
    await act(async () => { await result.current.open(); });

    await act(async () => { await vi.advanceTimersByTimeAsync(2_000); });
    expect(result.current.phase).toBe('waiting');

    lastLogin = 200;
    loginConfirmation = 'expected-receipt';
    await act(async () => { window.dispatchEvent(new Event('focus')); });
    expect(result.current.phase).toBe('done');
    expect(popup.close).toHaveBeenCalled();
    expect(handoff).toHaveBeenCalledWith({ type: 'kite-connected' }, window.location.origin);
  });

  it('checks saved session before treating a closed mobile login tab as cancelled', async () => {
    vi.useFakeTimers();
    let lastLogin: number | null = null;
    let loginConfirmation: string | null = null;
    get.mockImplementation((path: string) => Promise.resolve(path.endsWith('/login-url')
      ? { confirmation_id: 'expected-receipt', account_id: 'a1', login_url: 'https://kite.zerodha.com/connect/login?api_key=synthetic&v=3' }
      : { active_id: 'a1', accounts: [{ id: 'a1', connected: lastLogin != null, last_login_at_ms: lastLogin, login_confirmation_id: loginConfirmation }] }));
    vi.spyOn(window, 'postMessage').mockImplementation(() => {});
    const { result } = renderHook(() => useOpenKiteLogin());
    await act(async () => { await result.current.open(); });

    popup.closed = true;
    lastLogin = 200;
    loginConfirmation = 'expected-receipt';
    await act(async () => { await vi.advanceTimersByTimeAsync(2_000); });
    expect(result.current.phase).toBe('done');
    expect(result.current.error).toBeNull();
  });

  it('rejects missing or unsafe login URLs instead of navigating to a blank page', async () => {
    get.mockResolvedValue({ confirmation_id: 'expected-receipt', account_id: 'a1', login_url: 'https://example.test/login' });
    const { result } = renderHook(() => useOpenKiteLogin());
    await act(async () => { await result.current.open(); });
    expect(popup.close).toHaveBeenCalled();
    expect(popup.location.href).toBe('');
    expect(result.current.error).toContain('invalid Kite login URL');
  });

  it('ends a hung login request after 15 seconds', async () => {
    vi.useFakeTimers(); get.mockReturnValue(new Promise(() => {}));
    const { result } = renderHook(() => useOpenKiteLogin());
    await act(async () => {
      const attempt = result.current.open();
      await vi.advanceTimersByTimeAsync(15_000);
      await attempt;
    });
    expect(popup.close).toHaveBeenCalled();
    expect(result.current.error).toContain('15 seconds');
    expect(result.current.opening).toBe(false);
  });

  it('reports blocked popups without navigating away from Sterling', async () => {
    vi.mocked(window.open).mockReturnValue(null);
    get.mockResolvedValue({ confirmation_id: 'expected-receipt', account_id: 'a1', login_url: 'https://kite.zerodha.com/connect/login?api_key=synthetic' });
    const { result } = renderHook(() => useOpenKiteLogin());
    await act(async () => { await result.current.open(); });
    expect(result.current.error).toContain('popup was blocked');
    expect(result.current.loginUrl).toBe('https://kite.zerodha.com/connect/login?api_key=synthetic');
    act(() => result.current.waitForLogin());
    expect(result.current.phase).toBe('waiting');
    expect(result.current.error).toBeNull();
  });

  it('stops polling when the login is dismissed', async () => {
    vi.useFakeTimers();
    get.mockImplementation((path: string) => Promise.resolve(path.endsWith('/login-url')
      ? { confirmation_id: 'expected-receipt', account_id: 'a1', login_url: 'https://kite.zerodha.com/connect/login?api_key=synthetic' }
      : { active_id: 'a1', accounts: [{ id: 'a1', connected: false, last_login_at_ms: null }] }));
    const { result } = renderHook(() => useOpenKiteLogin());
    await act(async () => { await result.current.open(); });
    await act(async () => { await vi.advanceTimersByTimeAsync(2_000); });
    expect(get).toHaveBeenCalledTimes(2);
    act(() => result.current.dismiss());
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    expect(get).toHaveBeenCalledTimes(2);
  });

  it('keeps the callback listener after leaving the overlay for mobile recovery', async () => {
    let confirmation: string | null = null;
    get.mockImplementation((path: string) => Promise.resolve(path.endsWith('/login-url')
      ? { confirmation_id: 'expected-receipt', account_id: 'a1', login_url: 'https://kite.zerodha.com/connect/login?api_key=synthetic' }
      : { active_id: 'a1', accounts: [{ id: 'a1', connected: true, login_confirmation_id: confirmation }] }));
    const { result } = renderHook(() => useOpenKiteLogin());
    await act(async () => { await result.current.open(); });
    act(() => result.current.dismiss());
    expect(result.current.phase).toBe('idle');
    confirmation = 'expected-receipt';
    await act(async () => { window.dispatchEvent(new MessageEvent('message', {
      origin: window.location.origin,
      data: { type: 'kite-connected' },
    })); });
    expect(result.current.phase).toBe('done');
  });

  it('does not confirm an unfinished login when silent renewal changes its timestamp', async () => {
    vi.useFakeTimers();
    let timestamp = 100;
    let receipt: string | null = null;
    get.mockImplementation((path: string) => Promise.resolve(path.endsWith('/login-url')
      ? { confirmation_id: 'expected-receipt', account_id: 'a1', login_url: 'https://kite.zerodha.com/connect/login?api_key=synthetic' }
      : { active_id: 'a1', accounts: [{ id: 'a1', connected: true, last_login_at_ms: timestamp, login_confirmation_id: receipt }] }));
    const { result } = renderHook(() => useOpenKiteLogin());
    await act(async () => { await result.current.open(); });
    timestamp = 200;
    await act(async () => { await vi.advanceTimersByTimeAsync(2_000); });
    expect(result.current.phase).toBe('waiting');
    receipt = 'another-login-receipt';
    await act(async () => { window.dispatchEvent(new MessageEvent('message', {
      origin: window.location.origin, data: { type: 'kite-connected' },
    })); });
    expect(result.current.phase).toBe('waiting');
    receipt = 'expected-receipt';
    await act(async () => { await vi.advanceTimersByTimeAsync(2_000); });
    expect(result.current.phase).toBe('done');
  });

  it('checks the account bound by the login URL when another account becomes active', async () => {
    vi.useFakeTimers();
    let boundReceipt: string | null = null;
    get.mockImplementation((path: string) => Promise.resolve(path.endsWith('/login-url')
      ? { confirmation_id: 'expected-receipt', account_id: 'a1', login_url: 'https://kite.zerodha.com/connect/login?api_key=synthetic' }
      : { active_id: 'a2', accounts: [
        { id: 'a1', connected: true, login_confirmation_id: boundReceipt },
        { id: 'a2', connected: true, login_confirmation_id: 'expected-receipt' },
      ] }));
    const { result } = renderHook(() => useOpenKiteLogin());
    await act(async () => { await result.current.open(); });
    await act(async () => { await vi.advanceTimersByTimeAsync(2_000); });
    expect(result.current.phase).toBe('waiting');
    boundReceipt = 'expected-receipt';
    await act(async () => { await vi.advanceTimersByTimeAsync(2_000); });
    expect(result.current.phase).toBe('done');
  });

  it('does not use timestamps when the backend omits the login receipt', async () => {
    get.mockResolvedValue({ account_id: 'a1', login_url: 'https://kite.zerodha.com/connect/login?api_key=synthetic' });
    const { result } = renderHook(() => useOpenKiteLogin());
    await act(async () => { await result.current.open(); });
    expect(result.current.phase).toBe('failed');
    expect(result.current.error).toContain('login confirmation');
  });

  it('expires unused copied links and stops waiting after fifteen minutes', async () => {
    vi.useFakeTimers();
    vi.spyOn(window, 'setInterval').mockReturnValue(123);
    const clearPoll = vi.spyOn(window, 'clearInterval');
    get.mockResolvedValue({ confirmation_id: 'expected-receipt', account_id: 'a1', login_url: 'https://kite.zerodha.com/connect/login?api_key=synthetic' });
    const { result } = renderHook(() => useOpenKiteLogin());
    await act(async () => { await result.current.open(); });
    await act(async () => { await vi.advanceTimersByTimeAsync(15 * 60_000); });
    expect(result.current.phase).toBe('failed');
    expect(result.current.loginUrl).toBeNull();
    expect(result.current.error).toContain('expired');
    expect(clearPoll).toHaveBeenCalledWith(123);
  });
  it('starts login for the chosen linked account instead of the active account', async () => {
    get.mockResolvedValue({ confirmation_id: 'receipt-second', account_id: 'second', login_url: 'https://kite.zerodha.com/connect/login?api_key=second-key&v=3' });
    const { result } = renderHook(() => useOpenKiteLogin('second'));
    await act(async () => { await result.current.open(); });
    expect(get).toHaveBeenCalledWith('/api/v1/kite/login-url?account_id=second');
    expect(popup.location.href).toContain('api_key=second-key');
  });

});
