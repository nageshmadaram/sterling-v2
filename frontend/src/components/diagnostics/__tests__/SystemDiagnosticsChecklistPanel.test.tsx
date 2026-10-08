import React from 'react';
import { beforeEach, describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { SystemDiagnosticsChecklistPanel } from '../SystemDiagnosticsChecklistPanel';

const diagnostics = vi.hoisted(() => ({ runKite: vi.fn(), runTrueData: vi.fn() }));

vi.mock('../../../hooks/useKiteDiagnostics', () => ({
  useKiteDiagnosticsSummary: () => ({
    data: {
      authenticated: true,
      account_label: 'Primary Kite',
      kite_user_id: 'AB1234',
      has_credentials: true,
    },
    isLoading: false,
  }),
  useRunKiteDiagnostics: () => ({
    mutateAsync: diagnostics.runKite,
    isPending: false,
  }),
}));

vi.mock('../../../hooks/useTrueData', () => ({
  useTrueDataDiagnosticsSummary: () => ({
    data: {
      authenticated: true,
      username_hint: 'TD_USER',
      is_active: true,
      realtime_port: 8084,
      has_credentials: true,
    },
    isLoading: false,
  }),
  useRunTrueDataDiagnostics: () => ({
    mutateAsync: diagnostics.runTrueData,
    isPending: false,
  }),
}));

describe('SystemDiagnosticsChecklistPanel', () => {
  beforeEach(() => {
    diagnostics.runKite.mockReset();
    diagnostics.runTrueData.mockReset();
  });
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });

  it('renders title, summary strip, and primary action button', () => {
    render(
      <QueryClientProvider client={queryClient}>
        <SystemDiagnosticsChecklistPanel />
      </QueryClientProvider>
    );

    expect(screen.getByText(/Feed & API Health & Verification Checklist/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Run All Verification Checks/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /All Checkpoints/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Zerodha Kite & Network/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /TrueData Market Feeds/i })).toBeInTheDocument();
  });

  it('starts unverified and does not show sample market values as proof', () => {
    render(
      <QueryClientProvider client={queryClient}>
        <SystemDiagnosticsChecklistPanel />
      </QueryClientProvider>
    );

    expect(screen.getAllByText('NOT RUN').length).toBeGreaterThan(0);
    expect(screen.queryByText('₹24,535.80')).toBeNull();
    expect(screen.queryByText(/92,450 tokens indexed/i)).toBeNull();
    expect(screen.queryByText(/₹2,50,000/)).toBeNull();
    expect(screen.getByText('/ 18 Verified')).toBeInTheDocument();
  });

  it('shows the measured source and target instead of placeholder provenance', async () => {
    diagnostics.runKite.mockResolvedValue({ categories: [{
      id: 'kite_margins', status: 'WARNING', latency_ms: 12,
      source_origin: 'broker_not_verified', symbol_tested: 'Requested account',
      summary: 'Cash unavailable', metrics: { cash: null }, field_checks: [], raw_sample: {},
    }] });
    render(
      <QueryClientProvider client={queryClient}>
        <SystemDiagnosticsChecklistPanel />
      </QueryClientProvider>
    );

    fireEvent.click(screen.getByRole('button', { name: /Run Kite Suite/i }));
    expect(await screen.findByText('Cash unavailable')).toBeInTheDocument();
    fireEvent.click(screen.getByText(/Kite Margins & Capital Ledger/i));
    expect(screen.getByText('broker_not_verified')).toBeInTheDocument();
    expect(screen.getAllByText('Requested account').length).toBeGreaterThan(0);
    expect(screen.queryByText(/₹2,50,000/)).toBeNull();
  });

  it('displays core checkpoints for both Kite and TrueData', () => {
    render(
      <QueryClientProvider client={queryClient}>
        <SystemDiagnosticsChecklistPanel />
      </QueryClientProvider>
    );

    expect(screen.getByText(/Internet & DNS Gateway/i)).toBeInTheDocument();
    expect(screen.getByText(/Kite Session & User Profile/i)).toBeInTheDocument();
    expect(screen.getByText(/Kite Connect HTTPS Gateway/i)).toBeInTheDocument();
    expect(screen.getByText(/TrueData REST WebAPI & Auth Handshake/i)).toBeInTheDocument();
    expect(screen.getByText(/Indices Feed \(Spot OHLC\)/i)).toBeInTheDocument();
    expect(screen.getByText(/Options Greeks Solver/i)).toBeInTheDocument();
  });

  it('supports tab switching and expansion of the proof drawer', () => {
    render(
      <QueryClientProvider client={queryClient}>
        <SystemDiagnosticsChecklistPanel />
      </QueryClientProvider>
    );

    // Switch to Kite tab
    const kiteTab = screen.getByRole('button', { name: /Zerodha Kite & Network/i });
    fireEvent.click(kiteTab);
    expect(screen.getByText(/Zerodha Kite & Network Execution Stack/i)).toBeInTheDocument();

    // Click on row to expand proof drawer
    const row = screen.getByText(/Internet & DNS Gateway/i);
    fireEvent.click(row);
    expect(screen.getByText(/Verified Field Health Checks/i)).toBeInTheDocument();
    expect(screen.queryByText(/Verifiable Server Telemetry & Proof Payload/i)).toBeNull();
  });

  it('shows no proof payload for a check that has not run', () => {
    render(
      <QueryClientProvider client={queryClient}>
        <SystemDiagnosticsChecklistPanel />
      </QueryClientProvider>
    );

    fireEvent.click(screen.getByText(/Internet & DNS Gateway/i));
    // An unverified row has no measured payload; a placeholder must not stand in for one.
    expect(screen.queryByText(/Verifiable Server Telemetry & Proof Payload/i)).toBeNull();
    expect(screen.getAllByText('Not measured').length).toBeGreaterThan(0);
  });

  it('shows the measured payload once the check has run', async () => {
    diagnostics.runKite.mockResolvedValue({
      categories: [{
        id: 'internet_network',
        status: 'PASS',
        latency_ms: 9.5,
        summary: 'DNS resolved',
        metrics: {},
        field_checks: [{ name: 'DNS Root Handshake', status: 'PASS', value: '9.5 ms', description: 'ping' }],
        raw_sample: { host: '8.8.8.8', status: 'ESTABLISHED' },
        error_message: null,
      }],
    });
    render(
      <QueryClientProvider client={queryClient}>
        <SystemDiagnosticsChecklistPanel />
      </QueryClientProvider>
    );

    fireEvent.click(screen.getByRole('button', { name: /Run All Verification Checks/i }));
    fireEvent.click(await screen.findByText(/DNS resolved/i));
    expect(await screen.findByText(/Verifiable Server Telemetry & Proof Payload/i)).toBeInTheDocument();
    expect(screen.getByText(/ESTABLISHED/)).toBeInTheDocument();
  });

  it('never mentions simulation or paper trading', () => {
    const { container } = render(
      <QueryClientProvider client={queryClient}>
        <SystemDiagnosticsChecklistPanel />
      </QueryClientProvider>
    );
    expect(container.textContent).not.toMatch(/simulat|paper/i);
    expect(screen.getByText(/Execution: Live Broker Direct/)).toBeInTheDocument();
  });
});
