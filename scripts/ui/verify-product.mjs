/** Read-only runtime audit. Supply short-lived sessions; never commits credentials. */
import {createRequire} from 'node:module';
import fs from 'node:fs';
import path from 'node:path';
const require=createRequire(new URL('../../frontend/package.json',import.meta.url));
const {chromium}=require('@playwright/test');
const base=process.env.UI_AUDIT_URL||'http://127.0.0.1:5189';
const sessionFile=process.env.UI_AUDIT_SESSION;
if(!sessionFile)throw Error('UI_AUDIT_SESSION must point to short-lived verification sessions.');
const sessions=JSON.parse(fs.readFileSync(sessionFile));
const output=path.resolve(process.env.UI_AUDIT_OUTPUT||path.resolve(import.meta.dirname,'../../.runtime/ui-audit'));fs.mkdirSync(output,{recursive:true});
const results=[],blockedWrites=[],runtimeErrors=[];
const browser=await chromium.launch({executablePath:process.env.UI_CHROME_PATH||'/snap/bin/chromium',headless:true,args:['--no-sandbox']});
async function capture(page,name,metadata={}){
  await page.screenshot({path:path.join(output,name+'.png')});
  const metrics=await page.evaluate(()=>({width:innerWidth,height:innerHeight,overflow:document.documentElement.scrollWidth>innerWidth+2,dialogs:[...document.querySelectorAll('dialog[open],[role=dialog],[role=alertdialog]')].filter(el=>el.getBoundingClientRect().width).map(el=>el.getAttribute('aria-label')||el.getAttribute('aria-labelledby')),buttons:[...document.querySelectorAll('button')].filter(el=>el.getBoundingClientRect().width&&!el.closest('[hidden]')).length,workspaceHeight:document.querySelector('[data-testid=kite-workspace]')?.getBoundingClientRect().height||null}));
  results.push({name,...metadata,...metrics});
  if(metrics.overflow)throw Error(name+': document overflows horizontally');
}
async function contextFor(who,width){
  const context=await browser.newContext({viewport:{width,height:width===844?390:width<900?844:900},hasTouch:width<900});
  context.on('page',page=>page.on('pageerror',error=>runtimeErrors.push({message:error.message,path:new URL(page.url()).pathname})));
  await context.addInitScript(token=>{if(window===window.top)try{sessionStorage.setItem('sterling.auth.access',token);}catch{}},sessions[who].token);
  await context.route('**/api/v1/**',async route=>{
    const request=route.request(),pathname=new URL(request.url()).pathname;
    if(['GET','HEAD','OPTIONS'].includes(request.method())||/^\/api\/v1\/media\/(tv|radio)\/access$/.test(pathname))return route.continue();
    blockedWrites.push({method:request.method(),path:pathname});
    if(pathname==='/api/v1/auth/preferences')return route.fulfill({json:{initialized:true,values:request.postDataJSON()?.values||{}}});
    return route.abort('blockedbyclient');
  });return context;
}
async function menu(page){await page.getByRole('button',{name:'Menu',exact:true}).click();await page.getByRole('dialog',{name:'Account and navigation'}).waitFor();}
try{
  for(const width of (process.env.UI_WIDTHS||'390,1440').split(',').map(Number)){
    const context=await contextFor('sterling',width),page=await context.newPage();
    await page.goto(base+'/kite');await page.getByRole('button',{name:'Menu',exact:true}).waitFor({timeout:45000});
    for(const theme of ['light','dark']){
      await menu(page);await page.getByRole('button',{name:theme==='light'?'☀ Light':'☾ Dark',exact:true}).click();
      const pages=await page.getByRole('navigation',{name:'Workspace pages'}).getByRole('button').allTextContents();await page.getByRole('button',{name:'Close menu'}).click();
      for(const label of pages){
        await menu(page);await page.getByRole('navigation',{name:'Workspace pages'}).getByRole('button',{name:label,exact:true}).click();
        await page.waitForTimeout(350);
        await capture(page,`kite-${label.toLowerCase().replace(/\W+/g,'-')}-${width}-${theme}`,{workspace:'kite',page:label,theme});
        if(label==='Dashboard'){
          const signals=page.getByRole('tab',{name:'Signals',exact:true});
          if(width<=900){
            await signals.waitFor({timeout:45000});
            await signals.click();await page.waitForTimeout(250);
            await capture(page,`signals-${width}-${theme}`,{workspace:'kite',page:'Signals',theme});
            const tabs=page.locator('.signal-engine-tabs [role=tab]');
            for(let i=0;i<await tabs.count();i++){
              const tab=tabs.nth(i),engine=await tab.innerText();await tab.click();await page.waitForTimeout(180);
              await capture(page,`signals-${engine.toLowerCase().replace(/\W+/g,'-')}-${width}-${theme}`,{workspace:'kite',page:'Signals',engine,theme});
            }
          }
          await page.keyboard.press('Control+k');await page.getByRole('dialog',{name:'Global Command Palette'}).waitFor();await capture(page,`command-palette-${width}-${theme}`,{overlay:'command-palette',theme});await page.keyboard.press('Escape');
          const layout=page.getByTitle('Open workspace layouts');
          if(await layout.isVisible()){
            await layout.click();await capture(page,`layout-menu-${width}-${theme}`,{overlay:'layout-menu',theme});
            await page.getByRole('button',{name:/Customize Layout Docks/}).click();await page.getByRole('dialog',{name:'Customize Workspace Layout'}).waitFor();await capture(page,`layout-customize-${width}-${theme}`,{overlay:'layout-customize',theme});await page.keyboard.press('Escape');
          }
        }
        if(label==='PCR'){
          const info=page.getByRole('button',{name:'PCR data information',exact:true});
          if(await info.isVisible()){await info.click();await capture(page,`pcr-information-${width}-${theme}`,{overlay:'pcr-information',theme});await page.getByRole('button',{name:'Close data information'}).click();}
        }
        if(label==='Settings'){
          const picker=page.locator('select[aria-label="Settings section"]'),rail=page.getByRole('navigation',{name:'Kite settings sections'});
          const sections=await picker.locator('option').evaluateAll(options=>options.map(option=>({value:option.value,label:option.label})));
          for(const section of sections){
            if(width<900)await picker.selectOption(section.value);else await rail.getByRole('button',{name:new RegExp(section.label.split(' · ').at(-1).replace(/[.*+?^${}()|[\]\\]/g,'\\$&'))}).first().click();
            await page.waitForTimeout(180);await capture(page,`settings-${section.value}-${width}-${theme}`,{workspace:'kite',settings:section.value,theme});
          }
        }
      }
      await page.getByRole('button',{name:'Sterling Home',exact:true}).click();await page.getByRole('dialog',{name:'Choose your workspace'}).waitFor();await capture(page,`workspace-selector-${width}-${theme}`,{overlay:'workspace-selector',theme});await page.keyboard.press('Escape');
      await menu(page);await page.getByRole('button',{name:'Profile & security',exact:true}).click();await page.getByRole('dialog',{name:'Sterling profile'}).waitFor();await capture(page,`profile-${width}-${theme}`,{overlay:'profile',theme});await page.getByRole('button',{name:'Close profile'}).click();
      await menu(page);await page.getByRole('button',{name:'Broker connection',exact:true}).click();await page.getByRole('dialog',{name:'Zerodha Kite Status'}).waitFor();await capture(page,`broker-status-${width}-${theme}`,{overlay:'broker-status',theme});await page.keyboard.press('Escape');
    }
    for(const [feature,title] of [['radio','Sterling Radio player'],['tv','Sterling TV player']]){
      await page.goto(base+'/'+feature);await page.getByRole('button',{name:feature==='radio'?'Search radio stations':'Search TV channels',exact:true}).waitFor({timeout:45000});
      const frame=page.frameLocator(`iframe[title="${title}"]`);await frame.locator('body').waitFor();
      for(const theme of ['light','dark']){
        await menu(page);await page.getByRole('button',{name:theme==='light'?'☀ Light':'☾ Dark',exact:true}).click();await page.getByRole('button',{name:'Close menu'}).click();
        await frame.locator(`html[data-color-scheme=${theme}]`).waitFor();await capture(page,`${feature}-${width}-${theme}`,{workspace:feature,theme});
        await menu(page);await page.getByRole('group',{name:'Player interface'}).waitFor();await capture(page,`${feature}-display-${width}-${theme}`,{overlay:'media-display',theme});await page.getByRole('button',{name:'Close menu'}).click();
      }
    }
    await context.close();console.log('Member audit complete: '+width);
    const adminContext=await contextFor('admin',width),admin=await adminContext.newPage();await admin.goto(base+'/administration');try{await admin.locator('.directory-person').first().waitFor({timeout:45000});}catch(error){console.error('Administration state:',(await admin.locator('body').innerText()).slice(0,1500));await admin.screenshot({path:path.join(output,'administration-failure.png')});throw error;}
    for(const theme of ['light','dark']){
      await menu(admin);await admin.getByRole('button',{name:theme==='light'?'☀ Light':'☾ Dark',exact:true}).click();await admin.getByRole('button',{name:'Close menu'}).click();
      await admin.getByRole('navigation',{name:'Administration sections'}).getByRole('button',{name:/^Users/}).click();
      await admin.locator('.directory-person>button').filter({hasText:'sterling'}).first().click();
      for(const label of ['Overview','Access','Brokers','Telegram','Activity']){
        await admin.getByRole('navigation',{name:'User management'}).getByRole('button',{name:new RegExp('^'+label)}).click();await admin.waitForTimeout(200);await capture(admin,`admin-user-${label.toLowerCase()}-${width}-${theme}`,{workspace:'administration',userTab:label,theme});
        if(label==='Brokers'){
          const card=admin.locator('.admin-connection').first();
          if(await card.count()){
            await card.locator('.admin-assign-panel>summary').click();
            const assignee=card.locator('.admin-assignees button:not(:disabled)').first();
            if(await assignee.count()){
              await assignee.click();await card.getByRole('button',{name:'Save assignment',exact:true}).click();
              await admin.getByRole('alertdialog').waitFor();await capture(admin,`assignment-confirm-${width}-${theme}`,{overlay:'assignment-confirm',theme});await admin.getByRole('button',{name:'Cancel',exact:true}).click();
            }
          }
        }

      }
      for(const label of ['Requests','Brokers','Login accounts']){
        await admin.getByRole('navigation',{name:'Administration sections'}).getByRole('button',{name:new RegExp('^'+label)}).click();await admin.waitForTimeout(180);await capture(admin,`admin-${label.toLowerCase()}-${width}-${theme}`,{workspace:'administration',section:label,theme});
      }
      await admin.getByRole('button',{name:'Add user',exact:true}).click();
      await admin.getByRole('form',{name:'Create Sterling user'}).waitFor();
      await capture(admin,`admin-create-user-${width}-${theme}`,{workspace:'administration',section:'create-user',theme});
      await admin.getByLabel('Echo · radio').check();await admin.getByLabel('Broker settings',{exact:true}).check();
      await admin.getByLabel('Add connections',{exact:true}).check();
      await capture(admin,`admin-create-access-${width}-${theme}`,{workspace:'administration',section:'initial-access',theme});
      await admin.getByRole('button',{name:'Cancel',exact:true}).click();

    }
    await adminContext.close();console.log('Administrator audit complete: '+width);
    const anonymous=await browser.newContext({viewport:{width,height:900}}),login=await anonymous.newPage();await login.goto(base+'/?sterling_login=1');await login.getByRole('button',{name:'Sign in',exact:true}).waitFor();await capture(login,`sign-in-${width}`,{workspace:'sign-in'});await anonymous.close();
  }
  if(runtimeErrors.length)throw Error(`${runtimeErrors.length} uncaught browser errors`);
}catch(error){results.push({failure:String(error)});process.exitCode=1;console.error(error);}
finally{await browser.close();fs.writeFileSync(path.join(output,'results.json'),JSON.stringify({results,blockedWrites,runtimeErrors},null,2));}
