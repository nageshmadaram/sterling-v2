import {readFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {Script} from 'node:vm';
import {JSDOM} from 'jsdom';
import {describe,it,expect} from 'vitest';

describe('dynamic broadcaster artwork',()=>{
 it('resolves verified channel identity, rejects unsafe logos and coalesces metadata requests',async()=>{
  const dom=new JSDOM('',{runScripts:'outside-only',url:'https://sterling.example'});
  const calls:string[]=[];
  (dom.window as any).Image=class {onload:(()=>void)|null=null;onerror:(()=>void)|null=null;set src(_value:string){queueMicrotask(()=>this.onload?.());}};
  dom.window.fetch=(async(url:string)=>{calls.push(url);return {ok:true,json:async()=>url.endsWith('channels.json')?[{id:'RedBullTV.at',name:'Red Bull TV',alt_names:[]}]:[
    {channel:'RedBullTV.at',url:'javascript:alert(1)',in_use:true},
    {channel:'RedBullTV.at',url:'https://broadcaster.example/official-logo.png',in_use:true,tags:['square']}
  ]};}) as any;
  new Script(readFileSync(resolve(process.cwd(),'media/artwork.js'),'utf8')).runInContext(dom.getInternalVMContext());
  const service=(dom.window as any).SterlingArtwork;
  expect(service.resolveTv({name:'Red Bull TV Global Sports'})).toBe('');
  service.resolveTv({name:'Other channel'});await service.refresh();
  expect(calls).toHaveLength(2);
  service.resolveTv({name:'Red Bull TV Global Sports'});await Promise.resolve();
  expect(service.resolveTv({name:'Red Bull TV Global Sports'})).toBe('https://broadcaster.example/official-logo.png');
  expect(service.resolveTv({name:'Unrelated show'})).toBe('');
  dom.window.close();
 });
 it('retries artwork metadata when an offline startup comes online',async()=>{
  const dom=new JSDOM('',{runScripts:'outside-only',url:'https://sterling.example'});
  let offline=true,calls=0;
  dom.window.fetch=(async()=>{calls++;if(offline)throw Error('offline');return {ok:true,json:async()=>[]};}) as any;
  new Script(readFileSync(resolve(process.cwd(),'media/artwork.js'),'utf8')).runInContext(dom.getInternalVMContext());
  const service=(dom.window as any).SterlingArtwork;
  await service.refresh();expect(calls).toBe(2);
  offline=false;dom.window.dispatchEvent(new dom.window.Event('online'));
  await service.refresh();expect(calls).toBe(4);
  dom.window.close();
 });

});
