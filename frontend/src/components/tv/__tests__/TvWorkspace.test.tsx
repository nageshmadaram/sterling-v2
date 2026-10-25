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
  it('retains early header taps until the player is ready', () => {
    render(<TvWorkspace />);
    const frame=screen.getByTitle('Sterling TV player') as HTMLIFrameElement;
    const post=vi.spyOn(frame.contentWindow!, 'postMessage');
    act(()=>window.dispatchEvent(new CustomEvent('sterling-tv-command',{detail:{action:'theme',value:'spotify'}})));
    expect(post).not.toHaveBeenCalled();
    sendFromFrame('sterling.tv.ready');
    expect(post).toHaveBeenCalledWith({type:'sterling.tv.command',action:'theme',value:'spotify'},'*');
  });

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
    // A loaded document may still be stalled on a mobile CDN/script error.
    // Only the verified player-ready handshake dismisses its loading state.
    expect(screen.getByRole('status')).toHaveTextContent('Loading TV');
    sendFromFrame('sterling.tv.ready');
    expect(screen.queryByText('Loading TV…')).not.toBeInTheDocument();
  });

  it('sends TV preference hydration on iframe load to avoid the mobile ready race', () => {
    localStorage.setItem(storagePrefix + 'streamos_startup_channel_v14',
      '{"name":"Saved mobile channel","url":"https://example.com/live.m3u8"}');
    render(<TvWorkspace />);
    const frame = screen.getByTitle('Sterling TV player') as HTMLIFrameElement;
    const post = vi.spyOn(frame.contentWindow!, 'postMessage');
    fireEvent.load(frame);
    expect(post).toHaveBeenCalledWith(expect.objectContaining({
      type: 'sterling.tv.hydrate',
      values: expect.objectContaining({
        streamos_startup_channel_v14: expect.stringContaining('Saved mobile channel'),
      }),
    }), '*');
    expect(screen.getByRole('status')).toHaveTextContent('Loading TV');
    sendFromFrame('sterling.tv.ready');
    expect(screen.queryByRole('status')).toBeNull();
  });

  it('hydrates only explicitly allowlisted TV preferences', () => {
    localStorage.setItem(storagePrefix + 'streamos_favs_v14', '["demo"]');
    localStorage.setItem('sterling.auth.token', 'PRIVATE-CREDENTIAL');
    render(<TvWorkspace />);
    const frame = screen.getByTitle('Sterling TV player') as HTMLIFrameElement;
    const post = vi.spyOn(frame.contentWindow!, 'postMessage');
    sendFromFrame('sterling.tv.ready');
    expect(post).toHaveBeenCalledTimes(1);
    const payload = post.mock.calls[0][0] as { type: string; values: Record<string, string> };
    expect(payload.type).toBe('sterling.tv.hydrate');
    expect(payload.values.streamos_favs_v14).toBe('["demo"]');
    expect(JSON.stringify(payload)).not.toContain('PRIVATE-CREDENTIAL');
    expect(post.mock.calls[0][1]).toBe('*'); // opaque sandbox frame
  });

  it('persists only whitelisted preferences from the exact iframe window', () => {
    render(<TvWorkspace />);
    sendFromFrame('sterling.tv.ready');
    sendFromFrame('sterling.tv.save', {
      key: 'streamos_sources_v14', value: '[{"name":"Demo"}]',
    });
    expect(localStorage.getItem(storagePrefix + 'streamos_sources_v14'))
      .toBe('[{"name":"Demo"}]');
    sendFromFrame('sterling.tv.save', {
      key: 'sterling.auth.token', value: 'ATTACK',
    });
    expect(localStorage.getItem(storagePrefix + 'sterling.auth.token')).toBeNull();
    sendFromFrame('sterling.tv.save', {
      key: 'streamos_stats_v14', value: 'X'.repeat(500_001),
    });
    expect(localStorage.getItem(storagePrefix + 'streamos_stats_v14')).toBeNull();
    expect(TV_STORAGE_KEYS).toHaveLength(10);
  });

  it('does not revive unowned v13 subscriptions when mounting a player', () => {
    localStorage.setItem(storagePrefix + 'streamos_favs_v13', '["older favorite"]');
    localStorage.setItem('sterling.auth.token', 'secret-not-shared');
    render(<TvWorkspace />);
    const frame = screen.getByTitle('Sterling TV player') as HTMLIFrameElement;
    const post = vi.spyOn(frame.contentWindow!, 'postMessage');
    sendFromFrame('sterling.tv.ready');
    const payload = post.mock.calls[0][0] as {
      type: string;
      values: Record<string, string>;
    };
    expect(payload.values.streamos_favs_v14).toBeUndefined();
    expect(localStorage.getItem(storagePrefix + 'streamos_favs_v14'))
      .toBeNull();
    expect(JSON.stringify(payload)).not.toContain('secret-not-shared');
    expect(payload.values.streamos_favs_v13).toBeUndefined();
  });

  it('persists v14 startup channels independently from broker state', () => {
    render(<TvWorkspace />);
    sendFromFrame('sterling.tv.ready');
    const startup = JSON.stringify({
      name: 'Default station',
      url: 'https://example.com/stream.m3u8',
    });
    sendFromFrame('sterling.tv.save', {
      key: 'streamos_startup_channel_v14', value: startup,
    });
    expect(localStorage.getItem(storagePrefix + 'streamos_startup_channel_v14'))
      .toBe(startup);
    expect(localStorage.getItem('sterling.auth.token')).toBeNull();
  });

  it('ignores early player-history writes before stored playlists have hydrated', () => {
    localStorage.setItem(storagePrefix + 'streamos_history_v14', '["saved"]');
    render(<TvWorkspace />);
    sendFromFrame('sterling.tv.save', {
      key: 'streamos_history_v14', value: '["temporary startup channel"]',
    });
    expect(localStorage.getItem(storagePrefix + 'streamos_history_v14')).toBe('["saved"]');
    sendFromFrame('sterling.tv.ready');
    sendFromFrame('sterling.tv.save', {
      key: 'streamos_history_v14', value: '["newly played channel"]',
    });
    expect(localStorage.getItem(storagePrefix + 'streamos_history_v14'))
      .toBe('["newly played channel"]');
  });

  it('ignores iframe messages with the wrong origin or unrelated sender', () => {
    render(<TvWorkspace />);
    sendFromFrame('sterling.tv.save', {
      key: 'streamos_favs_v14', value: '["bad"]',
    }, 'https://example.com');
    expect(localStorage.getItem(storagePrefix + 'streamos_favs_v14')).toBeNull();
    act(() => {
      window.dispatchEvent(new MessageEvent('message', {
        data: { type: 'sterling.tv.save', key: 'streamos_favs_v14', value: '["bad"]' },
        origin: 'null',
        source: window,
      }));
    });
    expect(localStorage.getItem(storagePrefix + 'streamos_favs_v14')).toBeNull();
  });

  it('does not mount broker or order UI inside the TV view', () => {
    render(<TvWorkspace />);
    expect(screen.getByRole('region', { name: 'Sterling TV workspace' })).toBeInTheDocument();
    expect(screen.queryByText('Place Order')).not.toBeInTheDocument();
    expect(screen.queryByText('Order Window')).not.toBeInTheDocument();
  });
});

it('enforces the server byte budget for Unicode TV preferences',()=>{
 render(<TvWorkspace />);sendFromFrame('sterling.tv.ready');
 sendFromFrame('sterling.tv.save',{key:'streamos_favs_v14',value:'త'.repeat(170000)});
 expect(localStorage.getItem(storagePrefix+'streamos_favs_v14')).toBeNull();
});

it('hydrates and persists the subtitle appearance preference through the isolated bridge',()=>{
 const key='streamos_sub_style_v14';const initial=JSON.stringify({size:125,color:'#ffffff',bg:0.7});
 localStorage.setItem(storagePrefix+key,initial);
 render(<TvWorkspace/>);
 const frame=screen.getByTitle('Sterling TV player') as HTMLIFrameElement;
 const post=vi.spyOn(frame.contentWindow!,'postMessage');sendFromFrame('sterling.tv.ready');
 expect(post.mock.calls[0][0].values[key]).toBe(initial);
 const changed=JSON.stringify({size:150,color:'#ffcc00',bg:0.5});
 sendFromFrame('sterling.tv.save',{key,value:changed});
 expect(localStorage.getItem(storagePrefix+key)).toBe(changed);
});
