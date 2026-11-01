import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import '@testing-library/jest-dom/vitest';

const stub = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  view: 'live' as 'live' | 'simulation',
}));
vi.mock('../../../utils/api', () => ({ api: { get: stub.get, post: stub.post } }));
vi.mock('../../../store/useViewStore', () => ({ useView: () => stub.view }));

import { PlatformToolsPane, safeToolCatalog } from '../PlatformToolsPane';

const response = {
  version: '0.1.0', execution_authorized: false, tools_ui_mounted: false,
  tools: [
    {
      key: 'pcr_session', label: 'PCR session', category: 'analytics', method: 'GET',
      api_path: '/api/v1/pcr/session', backend_module: 'app.api.v1.endpoints.pcr',
      requires_kite_feature: true, permits_order_write: false, launches_runtime: false,
    },
  ],
};

function mount(client = new QueryClient({ defaultOptions: { queries: { retry: false } } })) {
  const result = render(<QueryClientProvider client={client}><PlatformToolsPane /></QueryClientProvider>);
  return { ...result, client };
}

beforeEach(() => { vi.clearAllMocks(); stub.view = 'live'; });
afterEach(() => { cleanup(); });

it('renders only authenticated metadata without invoking an underlying tool', async () => {
  stub.get.mockResolvedValue(response);
  mount();
  expect(await screen.findByText('PCR session')).toBeInTheDocument();
  expect(screen.getByText('GET /api/v1/pcr/session')).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: /run|execute|trade/i })).toBeNull();
  expect(stub.get).toHaveBeenCalledTimes(1);
  expect(stub.get).toHaveBeenCalledWith('/api/v1/platform/tools');
  expect(stub.post).not.toHaveBeenCalled();
});

it.each([
  { ...response, execution_authorized: true },
  { ...response, tools_ui_mounted: true },
  { ...response, tools: [{ ...response.tools[0], permits_order_write: true }] },
  { ...response, tools: [{ ...response.tools[0], launches_runtime: true }] },
  { ...response, tools: [{ ...response.tools[0], requires_kite_feature: false }] },
  { ...response, tools: [{ ...response.tools[0], method: 'POST' }] },
  { ...response, tools: [{ ...response.tools[0], key: 'placeorder' }] },
  { ...response, tools: [{ ...response.tools[0] }, { ...response.tools[0] }] },
  { ...response, tools: [{ ...response.tools[0], api_path: 'https://evil.invalid/placeorder' }] },
])('fails closed on a changed or unsafe server contract', async payload => {
  expect(safeToolCatalog(payload)).toBeNull();
  stub.get.mockResolvedValue(payload);
  mount();
  expect(await screen.findByRole('alert')).toHaveTextContent('safety contract could not be verified');
  expect(screen.queryByText('PCR session')).toBeNull();
  expect(stub.post).not.toHaveBeenCalled();
});

it('fails closed on an authorization error rather than substituting local tools', async () => {
  stub.get.mockRejectedValue(new Error('Forbidden'));
  mount();
  expect(await screen.findByRole('alert')).toHaveTextContent('unavailable');
  expect(screen.queryByText('PCR session')).toBeNull();
  expect(stub.post).not.toHaveBeenCalled();
});

it('keys the read-only metadata cache by the current Simulation/live view', async () => {
  stub.get.mockResolvedValue(response);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const { rerender } = mount(client);
  await screen.findByText('PCR session');
  stub.view = 'simulation';
  rerender(<QueryClientProvider client={client}><PlatformToolsPane /></QueryClientProvider>);
  await waitFor(() => expect(stub.get).toHaveBeenCalledTimes(2));
  expect(client.getQueryData(['kite', 'platform-tools-readonly', 'live'])).toBeDefined();
  expect(client.getQueryData(['kite', 'platform-tools-readonly', 'simulation'])).toBeDefined();
});
