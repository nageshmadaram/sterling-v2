import fs from 'node:fs';import path from 'node:path';import {createRequire} from 'node:module';
const require=createRequire(new URL('../../frontend/package.json',import.meta.url));
const {chromium,expect}=require('@playwright/test');
const session=JSON.parse(fs.readFileSync(process.env.ADMIN_ACCEPTANCE_SESSION||'/tmp/sterling-admin-browser-session.json'));
const token=session.token;
const output=path.resolve(process.env.ADMIN_ACCEPTANCE_OUTPUT||'.runtime/admin-create-acceptance');fs.mkdirSync(output,{recursive:true});
const base=process.env.ADMIN_ACCEPTANCE_URL||'http://127.0.0.1:5192';
if(new URL(base).hostname!=='127.0.0.1'||new URL(base).protocol!=='http:')throw Error('Only the loopback HTTP fixture is permitted; refusing to transmit credentials.');
const fixture=await (await fetch(base+'/api/v1/__acceptance_fixture__',{headers:{Authorization:'Bearer '+token}})).json();
if(new URL(base).hostname!=='127.0.0.1'||fixture.disposable!==true||!session.fixture_nonce||fixture.fixture_nonce!==session.fixture_nonce)throw Error('A matching disposable admin API fixture is required; refusing user creation.');
const browser=await chromium.launch({executablePath:'/usr/bin/google-chrome',headless:true});
const page=await browser.newPage({viewport:{width:393,height:844},hasTouch:true});
const errors=[];page.on('pageerror',e=>errors.push(e.message));
await page.route('**/*',route=>['127.0.0.1','localhost'].includes(new URL(route.request().url()).hostname)?route.continue():route.abort());
await page.addInitScript(token=>sessionStorage.setItem('sterling.auth.access',token),token);
const record={at:new Date().toISOString(),release:process.env.ADMIN_ACCEPTANCE_RELEASE||null,release_provenance:process.env.ADMIN_ACCEPTANCE_RELEASE?'Caller-supplied immutable asset release':'Not asserted; provide ADMIN_ACCEPTANCE_RELEASE when using immutable assets',scope:'Real browser -> actual production API handlers -> disposable SQLite; real JWT and policy; no production data or external network.',viewport:{width:393,height:844}};
try{
 await page.goto(base+'/administration');
 await page.getByRole('button',{name:'Add user',exact:true}).click();
 await page.getByRole('form',{name:'Create Sterling user'}).waitFor();
 await page.getByLabel('Username',{exact:true}).fill('browser-created');
 for(const name of ['Password','Repeat password'])await page.getByLabel(name,{exact:true}).fill('Synthetic-browser-member-passphrase-123');
 for(const name of ['Allow kite access','Allow radio access','Allow tv access'])await page.getByLabel(name,{exact:true}).check();
 await page.getByLabel('Show tv in workspace chooser',{exact:true}).uncheck();
 for(const label of ['Broker settings','Telegram settings','Integration settings']){
  await page.getByLabel(label,{exact:true}).check();
  const section=page.locator('.admin-initial-section').filter({has:page.getByLabel(label,{exact:true})});
  for(const box of await section.getByRole('checkbox').all())await box.check();
 }
 await page.getByRole('button',{name:'Save user',exact:true}).click();
 await page.getByRole('heading',{name:'browser-created',exact:true}).waitFor({timeout:20000});
 await page.getByRole('button',{name:'Access',exact:true}).click();
 await expect(page.getByLabel('Allow kite access',{exact:true})).toBeChecked();
 await page.screenshot({path:path.join(output,'admin-created-browser.png')});
 const login=await page.request.post(base+'/api/v1/auth/login',{form:{username:'browser-created',password:'Synthetic-browser-member-passphrase-123'}});
 if(login.status()!==200)throw Error('Created user cannot sign in: '+login.status());
 const auth=await login.json();
 const me=await page.request.get(base+'/api/v1/auth/me',{headers:{Authorization:'Bearer '+auth.access_token}});
 const policy=await me.json();
 expect(policy.features).toEqual({kite:true,tv:true,radio:true});
 expect(policy.visibility).toEqual({kite:true,tv:false,radio:true});record.visibility=policy.visibility;
 const capabilities=await (await page.request.get(base+'/api/v1/settings/capabilities',{headers:{Authorization:'Bearer '+auth.access_token}})).json();
 const grants=Object.fromEntries(Object.entries(capabilities.sections).map(([name,section])=>[name,{visible:section.visible,actions:section.grants.map(g=>g.action).sort()}]));
 expect(grants).toEqual({brokers:{visible:true,actions:['create','update']},telegram:{visible:true,actions:['recipients','test']},integrations:{visible:true,actions:['create','test','update']}});
 record.features=policy.features;record.configuration=grants;record.created_user_can_sign_in=true;record.runtime_errors=errors;record.pass=errors.length===0;
}catch(e){record.pass=false;record.error=String(e);record.visible_excerpt=(await page.locator('body').innerText()).slice(-1800);}
finally{await browser.close();fs.writeFileSync(path.join(output,'admin-create-browser.json'),JSON.stringify(record,null,2)+'\n');}
console.log(JSON.stringify(record));
if(!record.pass)process.exitCode=1;
