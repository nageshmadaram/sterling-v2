/**
 * Broker and live-state chips in the footer.
 *
 * The property worth defending is restraint. Only SuperTrend publishes scan
 * timing, so the footer carries no scan schedule for the other strategies, and an
 * invented status on a trading dock is worse than a blank one.
 *
 * And the broker chip has three states, not two: connected, refused, and
 * COULD-NOT-ASK. Collapsing the third into "offline" is the same conflation that
 * produced a session-expired modal over a perfectly good session.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';

import { normalizeLiveStatus } from '../../../hooks/useLiveStatus';

let status: Record<string, unknown> = {};
let signals: Record<string, unknown> = {};
let liveResult: { available: boolean; status: unknown } = { available: false, status: null };

vi.mock('../../../hooks/useKite', () => ({ useKiteStatus: () => ({ data: status }) }));
vi.mock('../../../hooks/useLiveStatus', async () => {
  const actual = await vi.importActual<typeof import('../../../hooks/useLiveStatus')>('../../../hooks/useLiveStatus');
  return { ...actual, useLiveStatus: () => ({ data: liveResult }) };
});
const openSettingsSection = vi.fn();
vi.mock('../config/registry', async () => {
  const actual = await vi.importActual<typeof import('../config/registry')>('../config/registry');
  return { ...actual, openSettingsSection: (...a: unknown[]) => openSettingsSection(...a) };
});
vi.mock('../../../hooks/useSterlingKiteEngine', () => ({
  useEngineSignals: () => ({ data: signals }),
  useEngineConfig: () => ({ data: { engine_enabled: true } }),
}));
vi.mock('../../../hooks/useNavigator', () => ({
  useNavigatorConfig: () => ({ data: { record: { config: { enabled: false } } } }),
}));
vi.mock('../../../hooks/useGammaMove', () => ({ useGammaMoveSnapshot: () => ({ data: null }) }));

import { KiteFooterStatus } from '../KiteFooterStatus';

beforeEach(() => {
  cleanup();
  status = { connected: true, user_name: 'Madaram' };
  signals = { scanning: false, auto_scan: true, market_open: true };
  liveResult = { available: false, status: null };
  openSettingsSection.mockReset();
});

describe('the broker chip', () => {
  it('reads connected when it is', () => {
    render(<KiteFooterStatus onOpenSession={vi.fn()} />);
    expect(screen.getByText('KITE')).toBeInTheDocument();
  });

  it('distinguishes "could not ask" from "refused"', () => {
    // Three states, not two. The stored token is intact in the middle one.
    status = { connected: false, transient: true };
    render(<KiteFooterStatus onOpenSession={vi.fn()} />);
    expect(screen.getByText('KITE ?')).toBeInTheDocument();
    expect(screen.queryByText('KITE OFF'), 'not called offline').toBeNull();

    cleanup();
    status = { connected: false };
    render(<KiteFooterStatus onOpenSession={vi.fn()} />);
    expect(screen.getByText('KITE OFF')).toBeInTheDocument();
  });

  it('says the token is untouched when the check merely failed', () => {
    status = { connected: false, transient: true };
    render(<KiteFooterStatus onOpenSession={vi.fn()} />);
    expect(screen.getByTitle(/nothing has expired/i)).toBeInTheDocument();
  });

  it('opens the session panel even while connected', () => {
    // Worth being able to check on purpose, not only when something has broken.
    const onOpenSession = vi.fn();
    render(<KiteFooterStatus onOpenSession={onOpenSession} />);
    fireEvent.click(screen.getByText('KITE'));
    expect(onOpenSession).toHaveBeenCalled();
  });
});

describe('the strategy chips', () => {
  it('does not render strategy badges in footer status bar', () => {
    render(<KiteFooterStatus onOpenSession={vi.fn()} />);
    for (const label of ['ST', 'NAV', 'AE', 'GM']) {
      expect(screen.queryByText(label)).toBeNull();
    }
  });
});

function setLive(over: Record<string, unknown> = {}) {
  liveResult = {
    available: true,
    status: normalizeLiveStatus({
      safe_mode: { state: 'NORMAL', reason: null, triggers: [] },
      live_execution: true,
      recovery_state: 'NORMAL',
      exposure_observation: { fresh: true, age_s: 3 },
      ...over,
    }),
  };
}

describe('the live-state chip', () => {
  it('renders nothing when the status endpoint is unavailable', () => {
    render(<KiteFooterStatus onOpenSession={vi.fn()} />);
    expect(screen.queryByTestId('footer-live-state')).toBeNull();
  });

  it('reads LIVE when entries are allowed', () => {
    setLive();
    render(<KiteFooterStatus onOpenSession={vi.fn()} />);
    expect(screen.getByTestId('footer-live-state')).toHaveTextContent('LIVE');
  });

  it('reads SAFE MODE, with the reason, when safe mode is on', () => {
    setLive({ safe_mode: { state: 'SAFE_MODE', reason: 'Position mismatch', triggers: ['POSITION_MISMATCH'] } });
    render(<KiteFooterStatus onOpenSession={vi.fn()} />);
    const chip = screen.getByTestId('footer-live-state');
    expect(chip).toHaveTextContent('SAFE MODE');
    expect(chip.title).toMatch(/Position mismatch/);
  });

  it('reads ENTRIES OFF under the kill switch, and RECOVERY while recovery is open', () => {
    setLive({ live_execution: false });
    const first = render(<KiteFooterStatus onOpenSession={vi.fn()} />);
    expect(screen.getByTestId('footer-live-state')).toHaveTextContent('ENTRIES OFF');
    first.unmount();

    setLive({ recovery_state: 'RECOVERY_REQUIRED' });
    render(<KiteFooterStatus onOpenSession={vi.fn()} />);
    expect(screen.getByTestId('footer-live-state')).toHaveTextContent('RECOVERY');
  });

  it('never claims LIVE when safe mode cannot be read', () => {
    setLive({ safe_mode: 'broken' });
    render(<KiteFooterStatus onOpenSession={vi.fn()} />);
    expect(screen.getByTestId('footer-live-state')).toHaveTextContent('LIVE ?');
  });

  it('opens Trading Config on click', () => {
    setLive({ safe_mode: { state: 'SAFE_MODE', triggers: [] } });
    render(<KiteFooterStatus onOpenSession={vi.fn()} />);
    fireEvent.click(screen.getByTestId('footer-live-state'));
    expect(openSettingsSection).toHaveBeenCalledWith('mode');
  });
});
