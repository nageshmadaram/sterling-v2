import {useState} from 'react';
import {afterEach,describe,expect,it,vi} from 'vitest';
import {cleanup,fireEvent,render,screen} from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import {MediaHeaderControls,type MediaTheme} from '../MediaHeaderControls';

afterEach(()=>{cleanup();vi.restoreAllMocks();});

describe('shared IPTV/Radio mobile and desktop media header',()=>{
  function mount(kind:'tv'|'radio'='tv'){
    const selected=vi.fn(),searched=vi.fn(),tools=vi.fn();
    function Harness(){
      const [theme,setTheme]=useState<MediaTheme>('spotify');
      const [query,setQuery]=useState('');
      return <MediaHeaderControls kind={kind} theme={theme} query={query}
        onTheme={next=>{setTheme(next);selected(next);}}
        onQuery={next=>{setQuery(next);searched(next);}}
        onEnter={vi.fn()} onFeatures={tools}/>;
    }
    render(<Harness/>);
    return {selected,searched,tools};
  }

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
    const field=screen.getByRole('searchbox',{name:'Search radio stations'});
    fireEvent.change(field,{target:{value:'Telugu'}});
    expect(field).toHaveValue('Telugu');
    expect(searched).toHaveBeenCalledWith('Telugu');
    fireEvent.click(screen.getByRole('button',{name:'Clear search'}));
    expect(screen.queryByRole('searchbox',{name:'Search radio stations'})).not.toBeInTheDocument();
  });

  it('restores browse results after closing search with a query',()=>{
    const {searched}=mount('radio');
    fireEvent.click(screen.getByRole('button',{name:'Search radio stations'}));
    fireEvent.change(screen.getByRole('searchbox',{name:'Search radio stations'}),{target:{value:'BBC'}});
    fireEvent.click(screen.getByRole('button',{name:'Close search'}));
    expect(searched).toHaveBeenLastCalledWith('');
    expect(screen.queryByRole('searchbox',{name:'Search radio stations'})).not.toBeInTheDocument();
  });

  it('does not leave search applied when switching into appearance settings',()=>{
    const {searched}=mount('tv');
    fireEvent.click(screen.getByRole('button',{name:'Search TV channels'}));
    fireEvent.change(screen.getByRole('searchbox',{name:'Search TV channels'}),{target:{value:'Sports'}});
    fireEvent.click(screen.getByRole('button',{name:'TV display settings'}));
    expect(searched).toHaveBeenLastCalledWith('');
    expect(screen.getByRole('menuitemradio',{name:/Netflix/})).toBeInTheDocument();
    expect(screen.queryByRole('searchbox',{name:'Search TV channels'})).not.toBeInTheDocument();
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
});
