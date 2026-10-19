import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { TvWorkspace, TV_STORAGE_KEYS } from '../TvWorkspace';

const storagePrefix = 'sterling.tv.';

function sendFromFrame(type: string, extra: Record<string, unknown> = {}, origin = 'null') {
  const frame = screen.getByTitle('Sterling TV player') as HTMLIFrameElement;
  act(() => {
    window.dispatchEvent(new MessageEvent('message', {
      data: { type, ...extra },
      source: frame.contentWindow,
      origin,
    }));
  });
}

beforeEach(() => { localStorage.clear(); });
afterEach(() => { cleanup(); localStorage.clear(); vi.restoreAllMocks(); });

describe('Sterling Spectrum IPTV isolation', () => {
  it('fills its workspace without inheriting trading origin, storage or DOM', () => {
    render(<TvWorkspace />);
    const workspace = screen.getByTestId('sterling-tv-workspace');
    expect(workspace.getAttribute('style')).toContain('flex: 1');
    const frame = screen.getByTitle('Sterling TV player') as HTMLIFrameElement;
    expect(frame.getAttribute('src')).toBe('/tv/index.html');
    expect(frame.getAttribute('sandbox')).toContain('allow-scripts');
    expect(frame.getAttribute('sandbox')).not.toContain('allow-same-origin');
    expect(frame.getAttribute('sandbox')).not.toContain('allow-top-navigation');
    expect(frame).toHaveAttribute('referrerpolicy', 'no-referrer');
    expect(frame).toHaveAttribute('allowfullscreen');
    fireEvent.load(frame);
    expect(screen.queryByText('Loading TV…')).not.toBeInTheDocument();
  });

  it('hydrates only explicitly allowlisted TV preferences', () => {
    localStorage.setItem(storagePrefix + 'streamos_favs_v13', '["demo"]');
    localStorage.setItem('sterling.auth.token', 'PRIVATE-CREDENTIAL');
    render(<TvWorkspace />);
    const frame = screen.getByTitle('Sterling TV player') as HTMLIFrameElement;
    const post = vi.spyOn(frame.contentWindow!, 'postMessage');
    sendFromFrame('sterling.tv.ready');
    expect(post).toHaveBeenCalledTimes(1);
    const payload = post.mock.calls[0][0] as { type: string; values: Record<string, string> };
    expect(payload.type).toBe('sterling.tv.hydrate');
    expect(payload.values.streamos_favs_v13).toBe('["demo"]');
    expect(JSON.stringify(payload)).not.toContain('PRIVATE-CREDENTIAL');
    expect(post.mock.calls[0][1]).toBe('*'); // opaque sandbox frame
  });

  it('persists only whitelisted preferences from the exact iframe window', () => {
    render(<TvWorkspace />);
    sendFromFrame('sterling.tv.ready');
    sendFromFrame('sterling.tv.save', {
      key: 'streamos_sources_v13', value: '[{"name":"Demo"}]',
    });
    expect(localStorage.getItem(storagePrefix + 'streamos_sources_v13'))
      .toBe('[{"name":"Demo"}]');
    sendFromFrame('sterling.tv.save', {
      key: 'sterling.auth.token', value: 'ATTACK',
    });
    expect(localStorage.getItem(storagePrefix + 'sterling.auth.token')).toBeNull();
    sendFromFrame('sterling.tv.save', {
      key: 'streamos_stats_v13', value: 'X'.repeat(500_001),
    });
    expect(localStorage.getItem(storagePrefix + 'streamos_stats_v13')).toBeNull();
    expect(TV_STORAGE_KEYS).toHaveLength(5);
  });

  it('ignores early player-history writes before stored playlists have hydrated', () => {
    localStorage.setItem(storagePrefix + 'streamos_history_v13', '["saved"]');
    render(<TvWorkspace />);
    sendFromFrame('sterling.tv.save', {
      key: 'streamos_history_v13', value: '["temporary startup channel"]',
    });
    expect(localStorage.getItem(storagePrefix + 'streamos_history_v13')).toBe('["saved"]');
    sendFromFrame('sterling.tv.ready');
    sendFromFrame('sterling.tv.save', {
      key: 'streamos_history_v13', value: '["newly played channel"]',
    });
    expect(localStorage.getItem(storagePrefix + 'streamos_history_v13'))
      .toBe('["newly played channel"]');
  });

  it('ignores iframe messages with the wrong origin or unrelated sender', () => {
    render(<TvWorkspace />);
    sendFromFrame('sterling.tv.save', {
      key: 'streamos_favs_v13', value: '["bad"]',
    }, 'https://example.com');
    expect(localStorage.getItem(storagePrefix + 'streamos_favs_v13')).toBeNull();
    act(() => {
      window.dispatchEvent(new MessageEvent('message', {
        data: { type: 'sterling.tv.save', key: 'streamos_favs_v13', value: '["bad"]' },
        origin: 'null',
        source: window,
      }));
    });
    expect(localStorage.getItem(storagePrefix + 'streamos_favs_v13')).toBeNull();
  });

  it('does not mount broker or order UI inside the TV view', () => {
    render(<TvWorkspace />);
    expect(screen.getByRole('region', { name: 'Sterling TV workspace' })).toBeInTheDocument();
    expect(screen.queryByText('Place Order')).not.toBeInTheDocument();
    expect(screen.queryByText('Order Window')).not.toBeInTheDocument();
  });
});
