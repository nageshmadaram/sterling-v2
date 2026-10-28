/** Real app/CSP, authenticated reads; profile saves are intercepted, never persisted. */
import {createRequire} from 'node:module';
import fs from 'node:fs';
import path from 'node:path';
const require=createRequire(new URL('../../frontend/package.json',import.meta.url));
const {chromium,expect}=require('@playwright/test');
const sessions=JSON.parse(fs.readFileSync(process.env.UI_AUDIT_SESSION));
const base=process.env.UI_AUDIT_URL||'http://127.0.0.1:5190';
const output=path.resolve(process.env.UI_AUDIT_OUTPUT||'.runtime/acceptance-20261028/phone-layout');fs.mkdirSync(output,{recursive:true});
const browser=await chromium.launch({executablePath:'/usr/bin/google-chrome',headless:true});
const results=[],errors=[];let activePage;
try{
 for(const width of [320,360,393,412,600,844,1440]){
  const context=await browser.newContext({viewport:{width,height:width===844?390:844},hasTouch:width<900});
  await context.addInitScript(token=>{sessionStorage.setItem('sterling.auth.access',token);localStorage.setItem('sterling.view','live');},sessions.sterling.token);
  const page=await context.newPage();activePage=page;page.on('pageerror',error=>errors.push(error.message));
  await page.route('**/api/v1/**',route=>{
   const r=route.request();if(new URL(r.url()).pathname==='/api/v1/auth/preferences'&&r.method()==='PATCH')return route.fulfill({json:{initialized:true,values:r.postDataJSON()?.values||{}}});return ['GET','HEAD','OPTIONS'].includes(r.method())||/\/media\/(tv|radio)\/access$/.test(new URL(r.url()).pathname)?route.continue():route.abort();
  });
  await page.goto(base+'/kite');await page.getByRole('button',{name:'Menu',exact:true}).waitFor({timeout:45000});if(width<=900)await page.getByRole('tab',{name:'Signals',exact:true}).waitFor({timeout:45000});
  for(const theme of ['light','dark']){
   await page.getByRole('button',{name:'Menu',exact:true}).click();await page.getByRole('button',{name:theme==='light'?'☀ Light':'☾ Dark',exact:true}).click();await page.getByRole('button',{name:'Close menu'}).click();
   const tab=page.getByRole('tab',{name:'Signals',exact:true});if(width<=900){await tab.waitFor({timeout:45000});await tab.click();}
   const tabs=page.locator('.signal-engine-tabs [role=tab]');
   await tabs.first().waitFor({timeout:15000});
   for(let i=0;i<await tabs.count();i++){
    await tabs.nth(i).click();await page.waitForTimeout(180);
    const engine=await tabs.nth(i).innerText(),name=`signals-${width}-${theme}-${i}`;
    const metrics=await page.evaluate(()=>({overflow:document.documentElement.scrollWidth>innerWidth+2,zoom:getComputedStyle(document.documentElement).getPropertyValue('--app-zoom'),signalViewportHeight:document.querySelector('.signal-engine-viewport')?.getBoundingClientRect().height,visibleCards:[...document.querySelectorAll('.sb-parent-card,.sb-row')].filter(el=>{const r=el.getBoundingClientRect();return r.height&&r.top>=0&&r.bottom<=innerHeight;}).length}));
    await page.screenshot({path:path.join(output,name+'.png')});results.push({width,theme,engine,...metrics});expect(metrics.overflow).toBe(false);
   }
  }
  if(width===393){
   const original=await page.evaluate(async()=>{const r=await fetch('/api/v1/auth/profile',{headers:{Authorization:'Bearer '+sessionStorage.getItem('sterling.auth.access')}});return r.json();});
   await page.getByRole('button',{name:'Menu',exact:true}).click();await page.getByRole('button',{name:'Profile & security',exact:true}).click();
   await page.getByLabel('Choose profile picture').setInputFiles({name:'acceptance.png',mimeType:'image/png',buffer:Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jxXcAAAAASUVORK5CYII=','base64')});
   await expect(page.getByRole('button',{name:'Use picture',exact:true})).toBeEnabled();await page.screenshot({path:path.join(output,'profile-crop-393.png')});
   await page.getByRole('button',{name:'Use picture',exact:true}).click();await expect(page.getByAltText('Profile picture preview')).toBeVisible();
   let saved=false;
   await page.route('**/api/v1/auth/profile',route=>{if(route.request().method()!=='PATCH')return route.fallback();const patch=route.request().postDataJSON();saved=patch.photo.startsWith('data:image/jpeg;base64,');return route.fulfill({json:{...original,...patch}});});
   await page.getByRole('button',{name:'Save profile',exact:true}).click();await expect(page.getByRole('dialog',{name:'Sterling profile',exact:true}).getByRole('status')).toContainText('Profile saved.');expect(saved).toBe(true);
   results.push({test:'mobile profile crop, preview and save payload under production CSP',width,passed:true,persistence:'intercepted PATCH; backend persistence covered separately by pytest'});
  }
  await context.close();console.log('Phone layout verified: '+width);
 }
 expect(errors).toEqual([]);
}catch(error){if(activePage&&!activePage.isClosed()){await activePage.screenshot({path:path.join(output,'failure.png')});fs.writeFileSync(path.join(output,'failure.txt'),(await activePage.locator('body').innerText()).slice(0,6000));}throw error;}finally{await browser.close();fs.writeFileSync(path.join(output,'results.json'),JSON.stringify({results,errors},null,2));}
