vi.mock('../../../hooks/useProtectedMediaDocument',()=>({useProtectedMediaDocument:(kind:string)=>({url:'/api/v1/media/'+kind+'/document',error:''})}));
import {act,cleanup,render,screen} from '@testing-library/react';
import {afterEach,expect,it,vi} from 'vitest';
import '@testing-library/jest-dom/vitest';
import {TvWorkspace} from '../../tv/TvWorkspace';
import {RadioWorkspace} from '../../radio/RadioWorkspace';

afterEach(()=>{cleanup();vi.restoreAllMocks();vi.useRealTimers();});
it.each(['tv','radio'] as const)('queues only the latest valid %s suggestion request and retains its correlation',kind=>{
 const Workspace=kind==='tv'?TvWorkspace:RadioWorkspace;render(<Workspace/>);
 const frame=screen.getByTitle(kind==='tv'?'Sterling TV player':'Sterling Radio player') as HTMLIFrameElement;
 const post=vi.spyOn(frame.contentWindow!,'postMessage');
 const command=(detail:unknown)=>act(()=>{window.dispatchEvent(new CustomEvent(`sterling-${kind}-command`,{detail}));});
 const receive=(type:string,data:Record<string,unknown>={})=>act(()=>{window.dispatchEvent(new MessageEvent('message',{source:frame.contentWindow,origin:'null',data:{type,...data}}));});
 command({action:'suggest',value:'News',requestId:'request-old'});
 command({action:'suggest',value:'News',requestId:'request-new',token:'must-not-cross'});
 command({action:'suggest',value:'x'.repeat(201),requestId:'invalid'});
 receive(`sterling.${kind}.ready`);
 const suggestions=post.mock.calls.map(call=>call[0]).filter(message=>message.action==='suggest');
 expect(suggestions).toEqual([{type:`sterling.${kind}.command`,action:'suggest',value:'News',requestId:'request-new'}]);
 const relayed=vi.fn();window.addEventListener(`sterling-${kind}-suggestions`,relayed);
 try {
  receive(`sterling.${kind}.suggestions`,{query:'News',requestId:'request-new',items:[{id:'news',label:'News'}]});
  expect(relayed.mock.calls[0][0].detail).toMatchObject({query:'News',requestId:'request-new',items:[{id:'news',label:'News'}]});
  receive(`sterling.${kind}.suggestions`,{query:'News',requestId:{bad:true},items:[]});
  expect(relayed).toHaveBeenCalledTimes(1);
 } finally {window.removeEventListener(`sterling-${kind}-suggestions`,relayed);}
});

it('keeps the TV initialization deadline stable across callback changes and uses the newest callback',()=>{
 vi.useFakeTimers();const first=vi.fn(),latest=vi.fn();
 const {rerender}=render(<TvWorkspace onState={first}/>);
 act(()=>vi.advanceTimersByTime(10000));
 rerender(<TvWorkspace onState={latest}/>);
 act(()=>vi.advanceTimersByTime(5000));
 expect(screen.getByRole('alert')).toBeInTheDocument();
 const frame=screen.getByTitle('Sterling TV player') as HTMLIFrameElement;
 act(()=>{
  for(const data of [{type:'sterling.tv.ready'},{type:'sterling.tv.state',theme:'spotify',query:'News'}])window.dispatchEvent(new MessageEvent('message',{source:frame.contentWindow,origin:'null',data}));
 });
 expect(screen.queryByRole('alert')).toBeNull();
 expect(first).not.toHaveBeenCalled();expect(latest).toHaveBeenCalledWith({theme:'spotify',query:'News'});
});

it('clears a prior TV failure while reloading hydrated preferences',()=>{
 vi.useFakeTimers();render(<TvWorkspace/>);
 act(()=>vi.advanceTimersByTime(15000));expect(screen.getByRole('alert')).toBeInTheDocument();
 act(()=>window.dispatchEvent(new Event('sterling-preferences-hydrated')));
 expect(screen.queryByRole('alert')).toBeNull();expect(screen.getByRole('status')).toHaveTextContent('Loading TV');
});
