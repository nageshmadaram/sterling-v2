/**
 * The Operations screen renders server truth and nothing else.
 *
 * It must never compute eligibility, must never show a green screen when the
 * backend is unreachable, and must never turn an unknown value into a
 * reassuring number.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import React from 'react';

import { LiveOperationsPanel } from '../LiveOperationsPanel';
import { ModeBanner } from '../ops/ModeBanner';
import { SafetyAdmissionCard } from '../ops/SafetyAdmissionCard';
import { BrokerExposureCard } from '../ops/BrokerExposureCard';
import { ExternalPositionsTable } from '../ops/ExternalPositionsTable';
import { DeploymentCard } from '../ops/DeploymentCard';
import { OperatorActions } from '../ops/OperatorActions';
import type {
  BrokerStatus,
  DeploymentStatus,
  ExternalPosition,
  LiveOperations,
  SafetyStatus,
  SecurityStatus,
} from '../../../types/liveOperations';

const get = vi.fn();
const post = vi.fn();

vi.mock('../../../utils/api', () => ({
  api: {
    get: (...args: unknown[]) => get(...args),
    post: (...args: unknown[]) => post(...args),
  },
}));

const SAFETY: SafetyStatus = {
  safety_state: 'NORMAL',
  execution_control_state: 'CLEAN',
  new_risk_allowed: true,
  family_stop_engaged: false,
  live_execution_enabled: true,
  blockers: [],
};

const EXTERNAL: ExternalPosition = {
  source: 'BROKER_EXTERNAL',
  managed_by_sterling: false,
  account: 'AA0595',
  instrument: 'CDSL26SEP1500CE',
  exchange: 'NFO',
  product: 'NRML',
  quantity: 16625,
  broker_avg_price: 3.271429,
  last_price: 3.35,
  unrealised_pnl: 1306.24,
  discovery_reason: 'RECONCILIATION_UNKNOWN_POSITION',
  protection_known: false,
  sterling_intent: null,
  sterling_fill: null,
  observed_at: '2026-09-18T10:00:00Z',
};

const BROKER: BrokerStatus = {
  account_id: 'AA0595',
  binding_id: 'binding-1',
  connected: true,
  broker_flatness: 'PASS',
  broker_state_readable: true,
  broker_state_detail: '',
  broker_observed_at: '2026-09-18T10:00:00Z',
  managed_positions: [],
  external_positions: [],
  unresolved_intents: 0,
  open_exposure: 'PASS',
};

const DEPLOYMENT: DeploymentStatus = {
  static_egress: 'PASS',
  expected_egress_ip: '203.0.113.7',
  observed_egress_ip: '203.0.113.7',
  deployment_identity: 'PASS',
  lake_mount: 'PASS',
  network_path: 'PASS',
  market_freshness: 'PASS',
  last_tick_at: '2026-09-18T10:00:00Z',
  backend_reachable: true,
};

const SECURITY: SecurityStatus = {
  environment: 'production',
  production_security: 'PASS',
  stored_secret_count: 4,
  dev_fallback_in_use: false,
  last_migration_at: null,
};

function payload(overrides: Partial<LiveOperations> = {}): LiveOperations {
  return {
    schema_version: 1,
    generated_at: new Date().toISOString(),
    runtime_sha: '03ee3d367a31390bcede7eb856527e026e16954c',
    release_tag: 'sterling-runtime-1.0',
    environment: 'production',
    system_status: 'HEALTHY',
    production_mode: 'LIVE',
    new_risk_allowed: true,
    new_risk_blockers: [],
    next_safe_action: 'Nothing. Trading is enabled.',
    safety: SAFETY,
    broker: BROKER,
    deployment: DEPLOYMENT,
    security: SECURITY,
    operator: { stop_available: true, resume_available: false, stop_engaged: false },
    ...overrides,
  };
}

function renderPanel() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <LiveOperationsPanel />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  get.mockReset();
  post.mockReset();
});

describe('ModeBanner', () => {
  it.each([
    ['LIVE', /TRADING ENABLED/],
    ['LIVE_HALTED', /NEW ENTRIES OFF/],
    ['RECOVERY_REQUIRED', /NEW RISK BLOCKED/],
    ['UNKNOWN', /TREAT AS BLOCKED/],
  ])('renders %s', (mode, expected) => {
    render(<ModeBanner mode={mode} />);
    expect(screen.getByLabelText('trading mode').textContent).toMatch(expected);
  });

  it('a mode the browser does not know is treated as blocked, not as live', () => {
    render(<ModeBanner mode={'SOMETHING_NEW' as never} />);
    expect(screen.getByLabelText('trading mode').textContent).toMatch(/TREAT AS BLOCKED/);
  });

  it('an unreachable backend overrides whatever mode was cached', () => {
    render(<ModeBanner mode="LIVE" unreachable />);
    expect(screen.getByLabelText('trading mode').textContent).toMatch(/UNREACHABLE/);
  });

  it('never uses paper, simulation or shadow wording', () => {
    for (const mode of ['LIVE', 'LIVE_HALTED', 'RECOVERY_REQUIRED', 'DEVELOPMENT', 'UNKNOWN']) {
      const { unmount } = render(<ModeBanner mode={mode} />);
      expect(screen.getByLabelText('trading mode').textContent).not.toMatch(/paper|simulat|shadow/i);
      unmount();
    }
  });
});

describe('SafetyAdmissionCard', () => {
  it('shows every blocker, not only the first', () => {
    render(
      <SafetyAdmissionCard
        safety={{
          ...SAFETY,
          new_risk_allowed: false,
          blockers: [
            { code: 'safe_mode', severity: 'BLOCK', message: 'SAFE_MODE engaged', source: 's' },
            { code: 'external_broker_exposure', severity: 'BLOCK', message: 'CDSL', source: 's' },
          ],
        }}
      />,
    );
    const list = screen.getByLabelText('admission blockers');
    expect(within(list).getAllByRole('listitem')).toHaveLength(2);
    expect(screen.getByRole('alert').textContent).toMatch(/NEW RISK BLOCKED/);
  });

  it('treats an unknown admission answer as blocked', () => {
    render(<SafetyAdmissionCard safety={{ ...SAFETY, new_risk_allowed: null }} />);
    expect(screen.getByRole('alert').textContent).toMatch(/UNKNOWN — TREAT AS BLOCKED/);
  });

  it('never infers permission from an empty blocker list', () => {
    render(<SafetyAdmissionCard safety={{ ...SAFETY, new_risk_allowed: false, blockers: [] }} />);
    expect(screen.getByRole('alert').textContent).toMatch(/NEW RISK BLOCKED/);
  });

  it('shows entries enabled as normal and a kill switch as a failure', () => {
    const { unmount } = render(<SafetyAdmissionCard safety={SAFETY} />);
    expect(screen.getByLabelText('global live execution switch: PASS').textContent).toMatch(/ENABLED/);
    unmount();
    render(<SafetyAdmissionCard safety={{ ...SAFETY, live_execution_enabled: false }} />);
    expect(screen.getByLabelText('global live execution switch: FAIL').textContent).toMatch(/OFF/);
  });
});

describe('Broker exposure', () => {
  it('keeps managed and external positions apart', () => {
    render(<BrokerExposureCard broker={{ ...BROKER, external_positions: [EXTERNAL] }} />);
    expect(screen.getByLabelText('Positions not managed by Sterling')).toBeTruthy();
    expect(screen.getByText(/NOT MANAGED BY STERLING/)).toBeTruthy();
  });

  it('offers no action control for a position Sterling does not manage', () => {
    render(<ExternalPositionsTable positions={[EXTERNAL]} />);
    expect(screen.queryByRole('button')).toBeNull();
    expect(screen.getByText(/Act at the broker/)).toBeTruthy();
  });

  it('shows quantity, prices in rupees and the account of the external position', () => {
    render(<ExternalPositionsTable positions={[EXTERNAL]} />);
    expect(screen.getByText('CDSL26SEP1500CE')).toBeTruthy();
    expect(screen.getByText('16,625')).toBeTruthy();
    expect(screen.getByText('₹3.27')).toBeTruthy();
    expect(screen.getByText('₹3.35')).toBeTruthy();
    expect(screen.getByText('AA0595')).toBeTruthy();
  });

  it('an unreadable broker is stated, not rendered as an empty flat table', () => {
    render(
      <BrokerExposureCard
        broker={{
          ...BROKER,
          broker_state_readable: false,
          broker_state_detail: 'ConnectTimeout',
          external_positions: [],
        }}
      />,
    );
    expect(screen.getByRole('alert').textContent).toMatch(/BROKER STATE UNKNOWN/);
    expect(screen.queryByLabelText('Positions not managed by Sterling')).toBeNull();
  });
});

describe('DeploymentCard', () => {
  it('renders each check as a tri-state', () => {
    render(
      <DeploymentCard
        deployment={{ ...DEPLOYMENT, static_egress: 'UNKNOWN', lake_mount: 'FAIL' }}
        security={SECURITY}
      />,
    );
    expect(screen.getByLabelText('static egress: UNKNOWN')).toBeTruthy();
    expect(screen.getByLabelText('lake mount: FAIL')).toBeTruthy();
  });

  it('flags the development fallback key as a blocker', () => {
    render(
      <DeploymentCard
        deployment={DEPLOYMENT}
        security={{ ...SECURITY, dev_fallback_in_use: true, production_security: 'UNKNOWN' }}
      />,
    );
    expect(screen.getByRole('alert').textContent).toMatch(/DEV FALLBACK KEY IN USE/);
  });

  it('never renders secret material', () => {
    const { container } = render(
      <DeploymentCard deployment={DEPLOYMENT} security={{ ...SECURITY, stored_secret_count: 4 }} />,
    );
    expect(container.textContent).not.toMatch(/STERLING_SECRET_KEY=/);
  });
});

describe('OperatorActions', () => {
  it('stops in one click', () => {
    const onStop = vi.fn();
    render(
      <OperatorActions
        operator={{ stop_available: true, resume_available: true, stop_engaged: false }}
        onStop={onStop}
        onResume={vi.fn()}
        onRefresh={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /STOP ALL NEW TRADES/ }));
    expect(onStop).toHaveBeenCalledTimes(1);
  });

  it('requires a second step to resume', () => {
    const onResume = vi.fn();
    render(
      <OperatorActions
        operator={{ stop_available: true, resume_available: true, stop_engaged: true }}
        onStop={vi.fn()}
        onResume={onResume}
        onRefresh={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: /Resume new trades/ }));
    expect(onResume).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: /Confirm resume/ }));
    expect(onResume).toHaveBeenCalledTimes(1);
  });

  it('renders a backend refusal instead of retrying it', () => {
    render(
      <OperatorActions
        operator={{ stop_available: true, resume_available: true, stop_engaged: true }}
        onStop={vi.fn()}
        onResume={vi.fn()}
        onRefresh={vi.fn()}
        error="resume refused: RECOVERY_REQUIRED"
      />,
    );
    expect(screen.getByRole('alert').textContent).toMatch(/RECOVERY_REQUIRED/);
  });

  it('offers no go-live or force-pass control', () => {
    render(
      <OperatorActions
        operator={{ stop_available: true, resume_available: true, stop_engaged: false }}
        onStop={vi.fn()}
        onResume={vi.fn()}
        onRefresh={vi.fn()}
      />,
    );
    const labels = screen.getAllByRole('button').map((b) => b.textContent ?? '');
    expect(labels.join(' ')).not.toMatch(/go live|force/i);
  });
});

describe('LiveOperationsPanel', () => {
  it('renders the backend payload', async () => {
    get.mockResolvedValue(payload());
    renderPanel();
    expect(await screen.findByText(/LIVE — TRADING ENABLED/)).toBeTruthy();
  });

  it('carries no release, evidence, drill or lane sections', async () => {
    get.mockResolvedValue(payload());
    renderPanel();
    await screen.findByText(/LIVE — TRADING ENABLED/);
    for (const title of ['Release', 'Evidence', 'Failure drills', 'Certification']) {
      expect(screen.queryByLabelText(title)).toBeNull();
    }
    expect(screen.queryByText(/Lanes \(/)).toBeNull();
  });

  it('an unreachable backend is never a green screen', async () => {
    get.mockRejectedValue(new Error('network down'));
    renderPanel();
    await waitFor(() =>
      expect(
        screen.getAllByRole('alert').some((el) => /STERLING UNREACHABLE/.test(el.textContent ?? '')),
      ).toBe(true),
    );
    expect(screen.queryByText(/TRADING ENABLED/)).toBeNull();
  });

  it('reads the aggregation endpoint, not ten of them', async () => {
    get.mockResolvedValue(payload());
    renderPanel();
    await waitFor(() => expect(get).toHaveBeenCalled());
    expect(get.mock.calls.every((call) => call[0] === '/api/v1/operations/family')).toBe(true);
  });
});

// The release runs SuperTrend, Snapback and Gamma Unwind only. EMA Trend is a
// paper research engine; its virtual inventory must not appear in Operations.
describe('Operations shows no paper strategy', () => {
  it('ignores an EMA Trend paper payload', async () => {
    const { emaCampaign } = await import('../../../test/emaTrendFixtures');
    get.mockResolvedValue(payload({ paper_strategies: { ema_trend: { status: 'AVAILABLE', execution_mode: 'PAPER',
      live_eligible: false, campaigns: [{ ...emaCampaign(), qty: 103 }], paused: false,
      supervision: { status: 'RUNNING', age_ms: 2000, errors: [] } } } }));
    renderPanel();
    expect(await screen.findByText(/LIVE — TRADING ENABLED/)).toBeTruthy();
    expect(screen.queryByRole('region', { name: 'EMA Trend paper operations' })).toBeNull();
    expect(screen.queryByText(/paper/i)).toBeNull();
  });
});
