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
    get.mockResolvedValue({ login_url: 'https://kite.zerodha.com/connect/login?api_key=synthetic&v=3' });
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

  it('rejects missing or unsafe login URLs instead of navigating to a blank page', async () => {
    get.mockResolvedValue({ login_url: 'https://example.test/login' });
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
    get.mockResolvedValue({ login_url: 'https://kite.zerodha.com/connect/login?api_key=synthetic' });
    const { result } = renderHook(() => useOpenKiteLogin());
    await act(async () => { await result.current.open(); });
    expect(result.current.error).toContain('popup was blocked');
  });
});
