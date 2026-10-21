import {readFileSync} from 'node:fs';
import {Script} from 'node:vm';
import {JSDOM} from 'jsdom';
import {afterEach,expect,it,vi} from 'vitest';

const opened:JSDOM[]=[];
afterEach(()=>{opened.splice(0).forEach(dom=>{dom.window.dispatchEvent(new dom.window.PageTransitionEvent('pagehide'));dom.window.close();});vi.useRealTimers();});
function mount(){
 const dom=new JSDOM('<div class="tm-now-footer"><div></div></div><audio></audio>',{runScripts:'outside-only',pretendToBeVisual:true});opened.push(dom);
 const w=dom.window;
 w.matchMedia=(()=>({matches:true})) as any;
 w.HTMLDialogElement.prototype.showModal=function(){this.open=true;};
 w.HTMLDialogElement.prototype.close=function(){this.open=false;};
 w.HTMLMediaElement.prototype.pause=vi.fn();
 w.setTimeout=vi.fn(setTimeout) as any;w.clearTimeout=clearTimeout as any;w.Date=Date;
 new Script(readFileSync('media/comfort.js','utf8')).runInContext(dom.getInternalVMContext());
 w.document.dispatchEvent(new w.Event('DOMContentLoaded'));
 return {w,doc:w.document};
}
it('updates the countdown at minute boundaries and pauses once at the deadline',()=>{
 vi.useFakeTimers();const {w,doc}=mount();const sleep=vi.fn();w.addEventListener('sterling-media-sleep',sleep);
 (doc.querySelector('.media-timer-trigger') as HTMLElement).click();
 (doc.querySelector('[data-minutes="15"]') as HTMLElement).click();
 const trigger=doc.querySelector('.media-timer-trigger')!;
 expect(trigger.textContent).toContain('15m');
 // Countdown runs once a minute rather than scheduling a per-second loop.
 expect((w.setTimeout as any).mock.calls.some((call:any[])=>call[1]===60000)).toBe(true);
 expect((w.setTimeout as any).mock.calls.some((call:any[])=>call[1]===1000)).toBe(false);
 vi.advanceTimersByTime(59999);expect(trigger.textContent).toContain('15m');
 vi.advanceTimersByTime(1);expect(trigger.textContent).toContain('14m');
 vi.advanceTimersByTime(14*60000);expect(sleep).toHaveBeenCalledTimes(1);
 expect(w.HTMLMediaElement.prototype.pause).toHaveBeenCalledTimes(1);
 w.document.dispatchEvent(new w.Event('visibilitychange'));expect(sleep).toHaveBeenCalledTimes(1);
});
it('rechecks an elapsed deadline after a suspended page resumes',()=>{
 vi.useFakeTimers();const {w,doc}=mount();const sleep=vi.fn();w.addEventListener('sterling-media-sleep',sleep);
 (doc.querySelector('.media-timer-trigger') as HTMLElement).click();
 (doc.querySelector('[data-minutes="15"]') as HTMLElement).click();
 vi.setSystemTime(Date.now()+16*60000);
 w.dispatchEvent(new w.Event('pageshow'));expect(sleep).toHaveBeenCalledTimes(1);
});
it('dismisses an offline notice and reveals it only for the next connection loss',()=>{
 const {w,doc}=mount();Object.defineProperty(w.navigator,'onLine',{value:false,configurable:true});
 w.dispatchEvent(new w.Event('offline'));const notice=doc.querySelector('.media-connection') as HTMLElement;
 expect(notice.hidden).toBe(false);(notice.querySelector('button') as HTMLElement).click();expect(notice.hidden).toBe(true);
 Object.defineProperty(w.navigator,'onLine',{value:true});w.dispatchEvent(new w.Event('online'));expect(notice.hidden).toBe(true);
 Object.defineProperty(w.navigator,'onLine',{value:false});w.dispatchEvent(new w.Event('offline'));expect(notice.hidden).toBe(false);
});
