import {useState} from 'react';
import {afterEach,describe,expect,it,vi} from 'vitest';
import {act,cleanup,fireEvent,render,screen} from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import {MediaHeaderControls,type MediaTheme} from '../MediaHeaderControls';

afterEach(()=>{cleanup();vi.restoreAllMocks();vi.useRealTimers();});

describe('shared IPTV/Radio mobile and desktop media header',()=>{
  function mount(kind:'tv'|'radio'='tv'){
    const selected=vi.fn(),searched=vi.fn(),tools=vi.fn(),entered=vi.fn();
    function Harness(){
      const [theme,setTheme]=useState<MediaTheme>('spotify');
      const [query,setQuery]=useState('');
      return <MediaHeaderControls kind={kind} theme={theme} query={query}
        onTheme={next=>{setTheme(next);selected(next);}}
        onQuery={next=>{setQuery(next);searched(next);}}
        onEnter={entered} onFeatures={tools}/>;
    }
    render(<Harness/>);
    return {selected,searched,tools,entered};
  }

  it.each(['tv','radio'] as const)('opens default settings directly for %s',kind=>{
    const command=vi.fn();window.addEventListener(`sterling-${kind}-command`,command);
    mount(kind);
    fireEvent.click(screen.getByRole('button',{name:kind==='tv'?'TV display settings':'Radio display settings'}));
    fireEvent.click(screen.getByRole('menuitem',{name:/Default (channel|station)/}));
    expect(command.mock.calls[0][0].detail).toEqual({action:'startup'});
    window.removeEventListener(`sterling-${kind}-command`,command);
  });

  it('shows suggestions, ignores stale queries and selects with the keyboard',()=>{
    vi.useFakeTimers();mount('radio');const command=vi.fn();window.addEventListener('sterling-radio-command',command);
    fireEvent.click(screen.getByRole('button',{name:'Search radio stations'}));
    const input=screen.getByRole('combobox',{name:'Search radio stations'});
    fireEvent.change(input,{target:{value:'BBC'}});
    act(()=>vi.advanceTimersByTime(120));
    const requestId=command.mock.calls.at(-1)?.[0].detail.requestId;
    fireEvent(window,new CustomEvent('sterling-radio-suggestions',{detail:{query:'old',items:[{id:'old',label:'Old result'}]}}));
    expect(screen.queryByRole('option')).toBeNull();
    fireEvent(window,new CustomEvent('sterling-radio-suggestions',{detail:{query:'BBC',requestId,items:[{id:'bbc',label:'BBC World Service',subtitle:'World news'}]}}));
    expect(screen.getByRole('option')).toHaveTextContent('BBC World Service');
    fireEvent.keyDown(input,{key:'ArrowDown'});fireEvent.submit(screen.getByRole('search'));
    expect(command.mock.calls.at(-1)?.[0].detail).toEqual({action:'select-suggestion',value:'bbc'});
    window.removeEventListener('sterling-radio-command',command);
  });

  it('starts as two accessible icons with themes hidden inside settings',()=>{
    const {selected}=mount('tv');
    expect(screen.getByRole('button',{name:'Search TV channels'})).toBeInTheDocument();
    expect(screen.getByRole('button',{name:'TV display settings'})).toBeInTheDocument();
    expect(screen.queryByRole('menuitemradio',{name:/Netflix/})).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button',{name:'TV display settings'}));
    fireEvent.click(screen.getByRole('menuitemradio',{name:/Netflix/}));
    expect(selected).toHaveBeenCalledWith('netflix');
    expect(screen.queryByRole('menuitemradio',{name:/Netflix/})).not.toBeInTheDocument();
  });

  it('opens a focused search field and clears/collapses it',()=>{
    const {searched}=mount('radio');
    fireEvent.click(screen.getByRole('button',{name:'Search radio stations'}));
    const field=screen.getByRole('combobox',{name:'Search radio stations'});
    vi.useFakeTimers();
    fireEvent.change(field,{target:{value:'Telugu'}});
    expect(field).toHaveValue('Telugu');
    // The page filter waits for a pause in typing.
    expect(searched).not.toHaveBeenCalledWith('Telugu');
    act(()=>vi.advanceTimersByTime(400));
    expect(searched).toHaveBeenCalledWith('Telugu');
    fireEvent.click(screen.getByRole('button',{name:'Clear search'}));
    expect(screen.getByRole('combobox')).toHaveValue('');
    expect(screen.getByRole('combobox')).toHaveFocus();
  });

  it('restores browse results after closing search with a query',()=>{
    const {searched}=mount('radio');
    fireEvent.click(screen.getByRole('button',{name:'Search radio stations'}));
    fireEvent.change(screen.getByRole('combobox',{name:'Search radio stations'}),{target:{value:'BBC'}});
    fireEvent.click(screen.getByRole('button',{name:'Close search'}));
    expect(searched).toHaveBeenLastCalledWith('');
    expect(screen.queryByRole('combobox',{name:'Search radio stations'})).not.toBeInTheDocument();
  });

  it('does not leave search applied when switching into appearance settings',()=>{
    const {searched}=mount('tv');
    fireEvent.click(screen.getByRole('button',{name:'Search TV channels'}));
    fireEvent.change(screen.getByRole('combobox',{name:'Search TV channels'}),{target:{value:'Sports'}});
    fireEvent.click(screen.getByRole('button',{name:'TV display settings'}));
    expect(searched).toHaveBeenLastCalledWith('');
    expect(screen.getByRole('menuitemradio',{name:/Netflix/})).toBeInTheDocument();
    expect(screen.queryByRole('combobox',{name:'Search TV channels'})).not.toBeInTheDocument();
  });

  it('dismisses settings on Escape and outside pointer down',()=>{
    mount('radio');
    const settings=screen.getByRole('button',{name:'Radio display settings'});
    fireEvent.click(settings);
    fireEvent.keyDown(document,{key:'Escape'});
    expect(settings).toHaveAttribute('aria-expanded','false');
    fireEvent.click(settings);
    fireEvent.pointerDown(document.body);
    expect(settings).toHaveAttribute('aria-expanded','false');
  });

  it('coalesces rapid suggestions and bounds the waiting state',()=>{
    vi.useFakeTimers();mount('radio');
    const command=vi.fn();window.addEventListener('sterling-radio-command',command);
    fireEvent.click(screen.getByRole('button',{name:'Search radio stations'}));
    const input=screen.getByRole('combobox');
    for(const value of ['B','BB','BBC'])fireEvent.change(input,{target:{value}});
    expect(screen.getByRole('listbox')).toHaveAttribute('aria-busy','true');
    act(()=>vi.advanceTimersByTime(120));
    expect(command).toHaveBeenCalledTimes(1);
    expect(command.mock.calls[0][0].detail).toEqual({action:'suggest',value:'BBC',requestId:expect.any(String)});
    act(()=>vi.advanceTimersByTime(4000));
    expect(screen.getByRole('status')).toHaveTextContent('taking longer');
    expect(screen.getByRole('listbox')).toHaveAttribute('aria-busy','false');
    window.removeEventListener('sterling-radio-command',command);
  });

  it('waits for Telugu composition instead of filtering partial input',()=>{
    vi.useFakeTimers();const {searched,entered}=mount('tv');
    fireEvent.click(screen.getByRole('button',{name:'Search TV channels'}));
    const input=screen.getByRole('combobox');
    fireEvent.compositionStart(input);
    fireEvent.change(input,{target:{value:'తెలు'}});
    fireEvent.keyDown(input,{key:'Enter',isComposing:true});
    fireEvent.submit(screen.getByRole('search'));
    expect(entered).not.toHaveBeenCalled();
    expect(searched).not.toHaveBeenCalled();
    expect(input).toHaveValue('తెలు');
    fireEvent.compositionEnd(input,{target:{value:'తెలుగు'}});
    expect(searched).toHaveBeenLastCalledWith('తెలుగు');
  });

  it('supports menu arrows, Home/End, and restores focus on Escape',()=>{
    mount('radio');
    const toggle=screen.getByRole('button',{name:'Radio display settings'});
    fireEvent.click(toggle);
    expect(screen.getByRole('menuitemradio',{name:/Spotify/})).toHaveFocus();
    fireEvent.keyDown(document.activeElement!,{key:'ArrowDown'});
    expect(screen.getByRole('menuitemradio',{name:/YouTube Music/})).toHaveFocus();
    fireEvent.keyDown(document.activeElement!,{key:'End'});
    expect(screen.getByRole('menuitem',{name:/Player tools/})).toHaveFocus();
    fireEvent.keyDown(document.activeElement!,{key:'Home'});
    expect(screen.getByRole('menuitemradio',{name:/Spotify/})).toHaveFocus();
    fireEvent.keyDown(document.activeElement!,{key:'Escape'});
    expect(toggle).toHaveFocus();
  });

  it('cancels queued suggestions when search closes',()=>{
    vi.useFakeTimers();mount('tv');
    const command=vi.fn();window.addEventListener('sterling-tv-command',command);
    fireEvent.click(screen.getByRole('button',{name:'Search TV channels'}));
    fireEvent.change(screen.getByRole('combobox'),{target:{value:'News'}});
    fireEvent.keyDown(screen.getByRole('combobox'),{key:'Escape'});
    act(()=>vi.advanceTimersByTime(5000));
    expect(command).not.toHaveBeenCalled();
    expect(screen.getByRole('button',{name:'Search TV channels'})).toHaveFocus();
    window.removeEventListener('sterling-tv-command',command);
  });

  it('does not retain an interrupted composition after reopening search',()=>{
    const {searched}=mount('radio');
    fireEvent.click(screen.getByRole('button',{name:'Search radio stations'}));
    fireEvent.compositionStart(screen.getByRole('combobox'));
    fireEvent.change(screen.getByRole('combobox'),{target:{value:'తెలు'}});
    fireEvent.click(screen.getByRole('button',{name:'Close search'}));
    fireEvent.click(screen.getByRole('button',{name:'Search radio stations'}));
    expect(screen.getByRole('combobox')).toHaveValue('');
    vi.useFakeTimers();
    fireEvent.change(screen.getByRole('combobox'),{target:{value:'BBC'}});
    act(()=>vi.advanceTimersByTime(400));
    expect(searched).toHaveBeenLastCalledWith('BBC');
  });

  it('dismisses the appearance menu when keyboard focus leaves the header',()=>{
    mount('tv');
    fireEvent.click(screen.getByRole('button',{name:'TV display settings'}));
    fireEvent.blur(screen.getByRole('menuitemradio',{name:/Spotify/}),{relatedTarget:document.body});
    expect(screen.queryByRole('menu')).not.toBeInTheDocument();
  });
  it.each(['tv','radio'] as const)('retries %s suggestions and rejects an older reply for the same query',kind=>{
    vi.useFakeTimers();const {searched,entered}=mount(kind);
    const command=vi.fn();window.addEventListener(`sterling-${kind}-command`,command);
    fireEvent.click(screen.getByRole('button',{name:kind==='tv'?'Search TV channels':'Search radio stations'}));
    fireEvent.change(screen.getByRole('combobox'),{target:{value:'News'}});
    act(()=>vi.advanceTimersByTime(4120));
    const oldRequest=command.mock.calls.at(-1)![0].detail;
    fireEvent.click(screen.getByRole('button',{name:'Retry suggestions'}));
    act(()=>vi.advanceTimersByTime(0));
    const currentRequest=command.mock.calls.at(-1)![0].detail;
    expect(currentRequest.requestId).not.toBe(oldRequest.requestId);
    const reply=(requestId:string,label:string)=>fireEvent(window,new CustomEvent(`sterling-${kind}-suggestions`,{detail:{query:'News',requestId,items:[{id:label,label}]}}));
    reply(oldRequest.requestId,'Stale news');expect(screen.queryByRole('option')).toBeNull();
    reply(currentRequest.requestId,'Current news');expect(screen.getByRole('option')).toHaveTextContent('Current news');
    act(()=>vi.advanceTimersByTime(5000));expect(screen.queryByRole('button',{name:'Retry suggestions'})).toBeNull();
    expect(searched).toHaveBeenCalledTimes(1);expect(entered).not.toHaveBeenCalled();
    expect(screen.getByRole('combobox')).toHaveFocus();
    window.removeEventListener(`sterling-${kind}-command`,command);
  });

});

it.each(['radio','tv'] as const)('opens the %s source manager from the shared header',kind=>{
 const command=vi.fn();window.addEventListener(`sterling-${kind}-command`,command);
 try{
  render(<MediaHeaderControls kind={kind} theme="spotify" query="" onTheme={()=>{}} onQuery={()=>{}}/>);
  fireEvent.click(screen.getByRole('button',{name:kind==='tv'?'TV display settings':'Radio display settings'}));
  fireEvent.click(screen.getByRole('menuitem',{name:kind==='tv'?/Channel sources/:/Station sources/}));
  expect(command.mock.calls[0][0].detail).toEqual({action:'sources'});
 }finally{window.removeEventListener(`sterling-${kind}-command`,command);}
});
