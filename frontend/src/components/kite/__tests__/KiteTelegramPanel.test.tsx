import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { KiteTelegramPanel } from '../KiteTelegramPanel';
import { api } from '../../../utils/api';
import type { KiteTelegramTarget } from '../../../types/kiteTelegram';

vi.mock('../../../utils/api', () => ({ api: { get: vi.fn(), post: vi.fn(), put: vi.fn(), delete: vi.fn() } }));
vi.mock('../../../store/useKiteNotifications', () => ({ notifyOrder: vi.fn() }));
let targets: KiteTelegramTarget[];
const status = { control: { state: 'polling', configured: true, commands_registered: true },
  alert_targets: 1, enabled_alert_targets: 1, unreadable_alert_targets: 0, queued_lifecycle_alerts: 2, signal_alerts_enabled: true };
const path = '/api/v1/kite/telegram';
function show() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, refetchInterval: false }, mutations: { retry: false } } });
  return render(<QueryClientProvider client={client}><KiteTelegramPanel /></QueryClientProvider>);
}

beforeEach(() => {
  vi.clearAllMocks();
  status.signal_alerts_enabled = true;
  targets = [];
  vi.mocked(api.get).mockImplementation(async (url) => url.endsWith('/status') ? { ...status } : { targets: [...targets] });
  vi.mocked(api.post).mockImplementation(async (url, input) => {
    if (url.endsWith('/control/check')) return { ok: true, message: 'Menu connection verified.', error_code: '' };
    if (url.endsWith('/test')) { targets[0] = { ...targets[0], reachable: true }; return targets[0]; }
    const body = input as Record<string, unknown>;
    const target = { id: 'one', label: String(body.label), chat_id: String(body.chat_id), bot_token_set: true,
      bot_token_hint: 'masked', enabled: true, reachable: false };
    targets.push(target);
    return target;
  });
  vi.mocked(api.put).mockImplementation(async (_, input) => {
    if (_.endsWith('/preferences')) {
      status.signal_alerts_enabled = (input as { signal_alerts_enabled: boolean }).signal_alerts_enabled;
      return { signal_alerts_enabled: status.signal_alerts_enabled };
    }
    const body = input as Partial<KiteTelegramTarget>;
    targets[0] = { ...targets[0], ...body, credential_error: '' };
    return targets[0];
  });
  vi.mocked(api.delete).mockImplementation(async () => { targets = []; return { ok: true }; });
});

describe('Telegram settings end to end', () => {
  it('adds, tests, disables, edits and removes a target through the API', async () => {
    show();
    fireEvent.click(await screen.findByRole('button', { name: '+ ADD BOT' }));
    fireEvent.change(screen.getByLabelText('LABEL'), { target: { value: 'My alerts' } });
    fireEvent.change(screen.getByLabelText('BOT TOKEN'), { target: { value: 'synthetic-token' } });
    fireEvent.change(screen.getByLabelText('CHAT ID'), { target: { value: '123' } });
    fireEvent.click(screen.getByRole('button', { name: 'ADD' }));
    expect(await screen.findByText('My alerts')).toBeInTheDocument();
    expect(api.post).toHaveBeenCalledWith(path, { label: 'My alerts', bot_token: 'synthetic-token', chat_id: '123', enabled: true });
    fireEvent.click(screen.getByRole('button', { name: 'TEST' }));
    expect(await screen.findByText('✓ Test message delivered')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('checkbox', { name: 'Enabled' }));
    await waitFor(() => expect(api.put).toHaveBeenCalledWith(`${path}/one`, { enabled: false }));
    fireEvent.click(screen.getByRole('button', { name: 'EDIT' }));
    fireEvent.change(screen.getByLabelText('LABEL'), { target: { value: 'Renamed' } });
    fireEvent.click(screen.getByRole('button', { name: 'SAVE' }));
    expect(await screen.findByText('Renamed')).toBeInTheDocument();
    expect(vi.mocked(api.put).mock.calls.at(-1)?.[1]).not.toHaveProperty('bot_token');
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    fireEvent.click(screen.getByRole('button', { name: 'REMOVE' }));
    await waitFor(() => expect(screen.queryByText('Renamed')).not.toBeInTheDocument());
    expect(api.delete).toHaveBeenCalledWith(`${path}/one`);
  });

  it('shows failed loading as an error with retry, rather than new-bot setup', async () => {
    vi.mocked(api.get).mockImplementation(async (url) => {
      if (url.endsWith('/status')) return status;
      throw new Error('Saved bot unavailable');
    });
    show();
    expect(await screen.findByText(/Could not load saved bots/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '+ ADD BOT' })).not.toBeInTheDocument();
    vi.mocked(api.get).mockImplementation(async (url) => url.endsWith('/status') ? status : { targets: [] });
    fireEvent.click(screen.getByRole('button', { name: 'RETRY BOTS' }));
    expect(await screen.findByRole('button', { name: '+ ADD BOT' })).toBeInTheDocument();
  });

  it('keeps unreadable credentials editable and shows a repair instruction', async () => {
    targets = [{ id: 'one', label: 'Broken', chat_id: '123', enabled: true, reachable: false,
      bot_token_set: true, bot_token_hint: '', credential_error: 'Saved bot token cannot be decrypted. Enter its token again.' }];
    show();
    expect(await screen.findByRole('alert')).toHaveTextContent('cannot be decrypted');
    expect(screen.getByRole('button', { name: 'TEST' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'EDIT' }));
    fireEvent.change(screen.getByLabelText('BOT TOKEN'), { target: { value: 'replacement-token' } });
    fireEvent.click(screen.getByRole('button', { name: 'SAVE' }));
    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument());
    expect(screen.getByRole('button', { name: 'TEST' })).toBeEnabled();
  });

  it('checks menu configuration without sending a Telegram test message or changing ALGO', async () => {
    show();
    fireEvent.click(await screen.findByRole('button', { name: 'CHECK MENU CONNECTION' }));
    expect(await screen.findByRole('status')).toHaveTextContent('Menu connection verified');
    expect(api.post).toHaveBeenCalledTimes(1);
    expect(api.post).toHaveBeenCalledWith(`${path}/control/check`);
    expect(screen.getByText(/2 lifecycle alerts queued/)).toBeInTheDocument();
  });
  it('changes signal alerts visibly without changing trading controls', async () => {
    show();
    const toggle = await screen.findByRole('checkbox', { name: 'Signal alerts' });
    expect(toggle).toBeChecked();
    fireEvent.click(toggle);
    await waitFor(() => expect(toggle).not.toBeChecked());
    expect(api.put).toHaveBeenCalledWith(`${path}/preferences`, { signal_alerts_enabled: false });
    expect(screen.getByText(/protection and exit alerts continue/)).toBeInTheDocument();
  });

});
