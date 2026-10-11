import {render,fireEvent,screen} from '@testing-library/react';
import {describe,it,expect,vi,beforeEach} from 'vitest';
import {MobilePullToRefresh} from '../MobilePullToRefresh';
import * as draftGuard from '../config/unsavedDraftGuard';

vi.mock('../config/unsavedDraftGuard',()=>({hasUnsavedDraft:vi.fn(()=>false)}));

function touch(host:HTMLElement,type:string,y:number){
  const event=new Event(type,{bubbles:true,cancelable:true});
  Object.defineProperty(event,'touches',{value:[{clientX:0,clientY:y}]});
  fireEvent(host,event);
}

describe('mobile pull to refresh',()=>{
  beforeEach(()=>{
    vi.mocked(draftGuard.hasUnsavedDraft).mockReturnValue(false);
  });

  it('reloads only when an armed pull is released',()=>{
    const refresh=vi.fn();
    const {container}=render(<div><MobilePullToRefresh onRefresh={refresh}/><p>Content</p></div>);
    const host=container.firstElementChild as HTMLElement;
    touch(host,'touchstart',0);
    touch(host,'touchmove',160);
    expect(refresh).not.toHaveBeenCalled();
    expect(screen.getByRole('status')).not.toHaveTextContent('Release to refresh');
    fireEvent.touchEnd(host);
    expect(refresh).toHaveBeenCalledOnce();
  });

  it('cancels when the user pulls back or the touch is cancelled',()=>{
    const refresh=vi.fn();
    const {container}=render(<div><MobilePullToRefresh onRefresh={refresh}/></div>);
    const host=container.firstElementChild as HTMLElement;
    touch(host,'touchstart',0);
    touch(host,'touchmove',180);
    touch(host,'touchmove',0);
    fireEvent.touchEnd(host);
    expect(refresh).not.toHaveBeenCalled();
    touch(host,'touchstart',0);
    touch(host,'touchmove',180);
    fireEvent.touchCancel(host);
    fireEvent.touchEnd(host);
    expect(refresh).not.toHaveBeenCalled();
  });

  it('does not refresh from a scrolled pane or editable field',()=>{
    const refresh=vi.fn();
    const {container}=render(<div><MobilePullToRefresh onRefresh={refresh}/><div data-testid="scroll"><p>Rows</p></div><input aria-label="Search"/></div>);
    const host=container.firstElementChild as HTMLElement;
    const scroll=screen.getByTestId('scroll');
    scroll.scrollTop=20;
    touch(scroll,'touchstart',0);
    touch(scroll,'touchmove',180);
    fireEvent.touchEnd(host);
    expect(refresh).not.toHaveBeenCalled();
    const input=screen.getByRole('textbox');
    touch(input,'touchstart',0);
    touch(input,'touchmove',180);
    fireEvent.touchEnd(host);
    expect(refresh).not.toHaveBeenCalled();
  });

  it('warns and blocks refresh when unsaved draft exists',()=>{
    vi.mocked(draftGuard.hasUnsavedDraft).mockReturnValue(true);
    const refresh=vi.fn();
    const {container}=render(<div><MobilePullToRefresh onRefresh={refresh}/></div>);
    const host=container.firstElementChild as HTMLElement;
    touch(host,'touchstart',0);
    touch(host,'touchmove',160);
    fireEvent.touchEnd(host);
    expect(refresh).not.toHaveBeenCalled();
    expect(screen.getByRole('status')).toHaveTextContent('Save or discard your changes before refreshing.');
  });
});
