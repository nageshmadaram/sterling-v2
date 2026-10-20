import React from 'react';
import { describe, expect, it, beforeEach, afterEach, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { RadioWorkspace } from '../RadioWorkspace';

beforeEach(() => localStorage.clear());
afterEach(() => {cleanup();localStorage.clear();vi.restoreAllMocks();});
function fromFrame(type:string,extra:Record<string,unknown>={},origin='null'){
  const frame=screen.getByTitle('Sterling Radio player') as HTMLIFrameElement;
  act(()=>window.dispatchEvent(new MessageEvent('message',{
    data:{type,...extra},source:frame.contentWindow,origin,
  })));
}
describe('Radio workspace isolation', () => {
  it('does not permit same-origin access to Kite credentials', () => {
    render(<RadioWorkspace />);
    const frame=screen.getByTitle('Sterling Radio player');
    expect(frame).toHaveAttribute('src','/radio/index.html');
    expect(frame).toHaveAttribute('referrerpolicy','no-referrer');
    expect(frame.getAttribute('sandbox')).not.toContain('allow-same-origin');
    expect(frame.getAttribute('sandbox')).not.toContain('allow-top-navigation');
  });
  it('hydrates only Radio preference keys on load', () => {
    localStorage.setItem('sterling.radio.omnicast_v11_liked','["FM"]');
    localStorage.setItem('sterling.auth.token','SECRET');
    render(<RadioWorkspace />);
    const frame=screen.getByTitle('Sterling Radio player') as HTMLIFrameElement;
    const post=vi.spyOn(frame.contentWindow!,'postMessage');
    fireEvent.load(frame);
    expect(post).toHaveBeenCalledWith({type:'sterling.radio.hydrate',
      values:{omnicast_v11_liked:'["FM"]'}},'*');
    fromFrame('sterling.radio.ready');
    expect(screen.queryByText('Loading Radio…')).toBeNull();
  });
  it('ignores unauthorized frame messages and nonallowlisted keys', () => {
    render(<RadioWorkspace />);
    fireEvent.load(screen.getByTitle('Sterling Radio player'));
    fromFrame('sterling.radio.save',{key:'sterling.auth.token',value:'STOLEN'});
    fromFrame('sterling.radio.save',{key:'omnicast_v11_liked',value:'["FM"]'},'https://evil.example');
    expect(localStorage.getItem('sterling.radio.omnicast_v11_liked')).toBeNull();
    fromFrame('sterling.radio.save',{key:'omnicast_v11_liked',value:'["FM"]'});
    expect(localStorage.getItem('sterling.radio.omnicast_v11_liked')).toBe('["FM"]');
  });
  it('acknowledges repeated ready signals and recovers after a missed initial signal',()=>{
    vi.useFakeTimers();
    render(<RadioWorkspace />);
    const frame=screen.getByTitle('Sterling Radio player') as HTMLIFrameElement;
    const post=vi.spyOn(frame.contentWindow!,'postMessage');
    act(()=>vi.advanceTimersByTime(15000));
    expect(screen.getByRole('alert')).toBeInTheDocument();
    fromFrame('sterling.radio.ready');
    expect(screen.queryByRole('alert')).toBeNull();
    expect(post).toHaveBeenCalledWith({type:'sterling.radio.ack'},'*');
    vi.useRealTimers();
  });
  it('reloads from Radio header command',()=>{
    render(<RadioWorkspace />);
    const oldFrame=screen.getByTitle('Sterling Radio player');
    fireEvent(window,new Event('sterling-radio-reload'));
    expect(screen.getByTitle('Sterling Radio player')).not.toBe(oldFrame);
  });
});
