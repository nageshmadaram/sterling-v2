import {afterEach, describe, expect, it, vi} from 'vitest';
import {cleanup, fireEvent, render, screen} from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import {MediaAccountSettings} from '../MediaAccountSettings';
import {MediaHeaderControls} from '../MediaHeaderControls';

afterEach(()=>cleanup());

describe('mobile media appearance moved to the hamburger',()=>{
  it.each(['tv','radio'] as const)('keeps search in the header and removes the sun for %s',kind=>{
    render(<MediaHeaderControls kind={kind} theme="spotify" query=""
      onTheme={()=>{}} onQuery={()=>{}} settingsInAccountMenu/>);
    expect(screen.getByRole('button',{name:kind==='tv'?'Search TV channels':'Search radio stations'})).toBeInTheDocument();
    expect(screen.queryByRole('button',{name:kind==='tv'?'TV display settings':'Radio display settings'})).toBeNull();
  });

  it.each(['tv','radio'] as const)('preserves appearance and player actions for %s',kind=>{
    const order:string[]=[];const closed=()=>order.push('closed');window.addEventListener('sterling-close-account-menu',closed);
    const change=vi.fn(),features=vi.fn(()=>order.push('features')),command=vi.fn(()=>order.push('command'));
    window.addEventListener(`sterling-${kind}-command`,command);
    try{
      render(<MediaAccountSettings kind={kind} theme="spotify" onTheme={change} onFeatures={features}/>);
      expect(screen.getByRole('group',{name:'Player interface'})).toBeInTheDocument();
      expect(screen.getByRole('button',{name:'Spotify'})).toHaveAttribute('aria-pressed','true');
      fireEvent.click(screen.getByRole('button',{name:kind==='radio'?'YouTube Music':'YouTube'}));
      expect(change).toHaveBeenCalledWith('youtube');
      fireEvent.click(screen.getByRole('button',{name:kind==='tv'?'Default channel':'Default station'}));
      fireEvent.click(screen.getByRole('button',{name:kind==='tv'?'Channel sources':'Station sources'}));
      expect(command.mock.calls.map(call=>call[0].detail)).toEqual([{action:'startup'},{action:'sources'}]);
      fireEvent.click(screen.getByRole('button',{name:'Player tools & preferences'}));
      expect(features).toHaveBeenCalledOnce();
      expect(order).toEqual(['closed','closed','command','closed','command','closed','features']);
    }finally{window.removeEventListener(`sterling-${kind}-command`,command);window.removeEventListener('sterling-close-account-menu',closed);}
  });
});
