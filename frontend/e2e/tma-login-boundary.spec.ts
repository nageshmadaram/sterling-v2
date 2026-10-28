import {test,expect} from '@playwright/test';
import {execFileSync} from 'node:child_process';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../..');
const html=execFileSync(path.join(root,'backend/.venv/bin/python'),['-c','from app.api.v1.endpoints.tma import _generate_tma_html; print(_generate_tma_html())'],{cwd:path.join(root,'backend'),encoding:'utf8',env:{...process.env,ENVIRONMENT:'development'}});

test('gateway transport failure preserves session and does not masquerade as bad login',async({page})=>{
 await page.route('**/*',route=>{
  const url=route.request().url();
  if(url==='http://sterling.test/tma')return route.fulfill({contentType:'text/html',body:html});
  if(url.includes('/api/v1/'))return route.fulfill({status:503,contentType:'text/plain',body:'Gateway temporarily unavailable'});
  return route.abort();
 });
 await page.addInitScript(()=>sessionStorage.setItem('sterling.auth.access','synthetic-access'));
 await page.goto('http://sterling.test/tma');
 await expect(page.locator('#sync-status')).toContainText('service is unavailable');
 await expect(page.locator('#sterling-login')).toBeHidden();
 await expect.poll(()=>page.evaluate(()=>sessionStorage.getItem('sterling.auth.access'))).toBe('synthetic-access');
});

test('expired authentication clears previously displayed private data',async({page})=>{
 let expired=false;
 await page.route('**/*',route=>{
  const url=route.request().url();
  if(url==='http://sterling.test/tma')return route.fulfill({contentType:'text/html',body:html});
  if(url.includes('/api/v1/tma/data'))return expired
   ? route.fulfill({status:401,contentType:'application/json',body:JSON.stringify({detail:'Session expired'})})
   : route.fulfill({contentType:'application/json',body:JSON.stringify({timestamp_ms:Date.now(),account:{connected:true},entry_controls:{enabled:false,label:'New entries OFF'},pnl:{total:12345},funds:{equity:{available:67890}},positions:[],signals:[],pcr:{},broker_data_available:true})});
  if(url.includes('/api/v1/'))return route.fulfill({status:401,contentType:'application/json',body:'{"detail":"Session expired"}'});
  return route.abort();
 });
 await page.goto('http://sterling.test/tma');
 await expect(page.locator('#summary-margin')).toContainText('67,890');
 expired=true;
 await expect(page.locator('#sterling-login')).toBeVisible({timeout:10000});
 await expect(page.locator('#summary-margin')).toHaveText('—');
 await expect(page.locator('#summary-pnl')).toHaveText('—');
 await expect(page.locator('#positions-list')).toContainText('Sign in');
 await expect(page.locator('#signals-list')).toContainText('Sign in');
});


test('saved operator login survives reload inside an expired Telegram launch',async({page})=>{
 await page.addInitScript(()=>{
  sessionStorage.setItem('sterling.auth.access','synthetic-operator-session');
  (window as any).Telegram={WebApp:{initData:'expired-telegram-proof',ready(){},expand(){}}};
 });
 await page.route('**/*',route=>{
  const url=route.request().url();
  if(url==='http://sterling.test/tma')return route.fulfill({contentType:'text/html',body:html});
  if(url.includes('/api/v1/tma/data')){
   const headers=route.request().headers();
   if(headers.authorization==='Bearer synthetic-operator-session' && !headers['x-telegram-init-data'])
    return route.fulfill({contentType:'application/json',body:JSON.stringify({timestamp_ms:Date.now(),account:{connected:true},entry_controls:{enabled:false,label:'New entries OFF'},positions:[],signals:[],pcr:{},broker_data_available:true})});
   return route.fulfill({status:401,contentType:'application/json',body:'{"detail":"Mini App authorization expired"}'});
  }
  return route.abort();
 });
 await page.goto('http://sterling.test/tma');
 await expect(page.locator('#txt-broker')).toHaveText('Kite connected');
 await page.reload();
 await expect(page.locator('#txt-broker')).toHaveText('Kite connected');
 await expect(page.locator('#sterling-login')).toBeHidden();
 await expect(page.locator('#txt-entry')).toHaveText('New entries OFF');
});
