/** Actual host menu -> opaque media-frame settings, without production settings writes. */
import fs from 'node:fs';
import path from 'node:path';
import {createRequire} from 'node:module';
const require=createRequire(new URL('../../frontend/package.json',import.meta.url));
const {chromium,expect}=require('@playwright/test');
const base=process.env.MEDIA_HOST_URL||'http://127.0.0.1:5196';
if(new URL(base).hostname!=='127.0.0.1')throw Error('Loopback candidate required before loading credentials');
const session=JSON.parse(fs.readFileSync(process.env.MEDIA_HOST_SESSION||'/tmp/sterling-verification-session.json'));
const output=path.resolve(process.env.MEDIA_HOST_OUTPUT||'.runtime/media-handoffs');fs.mkdirSync(output,{recursive:true});
const browser=await chromium.launch({executablePath:'/usr/bin/google-chrome',headless:true});
const results=[],errors=[];
try{
 for(const width of [393,1440])for(const kind of ['radio','tv']){
  const context=await browser.newContext({viewport:{width,height:844},hasTouch:width<900});
  await context.addInitScript(token=>{if(window.top!==window)return;sessionStorage.setItem('sterling.auth.access',token);localStorage.setItem('sterling_theme','light');},session.sterling.token);
  const page=await context.newPage();page.on('pageerror',error=>errors.push(error.message));
  await page.route('**/*',route=>{
   const request=route.request(),url=new URL(request.url());
   if(!['127.0.0.1','localhost'].includes(url.hostname))return route.abort();
   if(url.pathname==='/api/v1/auth/preferences'&&request.method()==='PATCH')return route.fulfill({json:{initialized:true,values:request.postDataJSON()?.values||{}}});
   if(url.pathname.startsWith('/api/')&&!['GET','HEAD','OPTIONS'].includes(request.method())&&!/\/media\/(tv|radio)\/access$/.test(url.pathname))return route.abort();
   return route.continue();
  });
  await page.goto(base+'/'+kind);await page.getByRole('button',{name:'Menu',exact:true}).waitFor({timeout:60000});
  const frame=page.frameLocator(`iframe[title="Sterling ${kind==='radio'?'Radio':'TV'} player"]`);
  await frame.locator(kind==='radio'?'html[data-radio-ready=true]':'#video-player').waitFor({state:'attached',timeout:60000});
  await page.getByRole('button',{name:'Menu',exact:true}).click();
  await page.getByRole('button',{name:'☀ Light',exact:true}).click();
  await page.getByRole('button',{name:'Close menu',exact:true}).click();
  await page.getByRole('button',{name:'Menu',exact:true}).click();
  await page.getByRole('button',{name:kind==='radio'?'Default station':'Default channel',exact:true}).click();
  await expect(page.getByRole('dialog',{name:'Account and navigation'})).not.toBeVisible();
  const startup=frame.locator(kind==='radio'?'#startup-modal':'#startup-channel-modal');await expect(startup).toBeVisible({timeout:15000});
  if(kind==='radio'){await startup.getByRole('searchbox',{name:'Find default radio station'}).fill('FM Rainbow');await expect(startup.locator('#startup-channel-select')).toBeEnabled();await startup.getByRole('button',{name:'Close startup settings',exact:true}).click();}
  else {await startup.locator('#custom-startup-name').fill('Acceptance draft only');await expect(startup.locator('#custom-startup-name')).toHaveValue('Acceptance draft only');await startup.getByRole('button',{name:/Close/}).first().click();}
  await page.getByRole('button',{name:'Menu',exact:true}).click();
  await page.getByRole('button',{name:kind==='radio'?'Station sources':'Channel sources',exact:true}).click();
  await expect(page.getByRole('dialog',{name:'Account and navigation'})).not.toBeVisible();
  const sources=kind==='radio'?frame.getByRole('dialog',{name:'Radio sources'}):frame.locator('#source-manager-modal');
  await expect(sources).toBeVisible({timeout:15000});
  if(kind==='radio'){await sources.getByRole('textbox',{name:'Source name',exact:true}).fill('Acceptance draft only');await expect(sources.getByRole('textbox',{name:'Source name',exact:true})).toHaveValue('Acceptance draft only');await sources.getByRole('button',{name:'Close radio sources',exact:true}).click();}
  else {await sources.locator('#source-name-input').fill('Acceptance draft only');await expect(sources.locator('#source-name-input')).toHaveValue('Acceptance draft only');await sources.getByRole('button',{name:/Close/}).first().click();}
  await page.getByRole('button',{name:'Menu',exact:true}).click();await page.getByRole('button',{name:'Player tools & preferences',exact:true}).click();
  await expect(page.getByRole('dialog',{name:'Account and navigation'})).not.toBeVisible();
  const tools=kind==='radio'?frame.getByRole('dialog',{name:'Radio extras'}):frame.locator(width<900?'#tv-mobile-sheet':'#overlay-sidebar');await expect(tools).toBeVisible({timeout:15000});
  await page.screenshot({path:path.join(output,`${kind}-tools-${width}-light.png`)});
  if(width>=1024){const input=page.getByRole('combobox',{name:kind==='radio'?'Search radio stations':'Search TV channels'});await expect(input).toBeVisible();const box=await input.boundingBox();expect(Math.abs(box.x+box.width/2-width/2)).toBeLessThan(80);}
  results.push({kind,width,account_menu_closed_before_player_settings:true,startup_editable:true,sources_editable:true,tools_foreground:true});await context.close();
 }
 expect(errors).toEqual([]);
}catch(error){results.push({pass:false,error:String(error)});process.exitCode=1;}
finally{await browser.close();fs.writeFileSync(path.join(output,'results.json'),JSON.stringify({at:new Date().toISOString(),scope:'Authenticated candidate UI; private settings mutations intercepted; media access credentials allowed; external streams blocked',results,errors},null,2)+'\n');}
console.log(JSON.stringify({checks:results.length,errors,pass:!process.exitCode}));
