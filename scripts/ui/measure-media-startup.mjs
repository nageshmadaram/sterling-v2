/** Controlled browser decoding evidence, never a claim of phone/broadcaster latency.
 * Run the loopback media-preview server first. Requires ffmpeg and Chrome.
 */
import {createRequire} from 'node:module';
import {execFileSync} from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
const require=createRequire(new URL('../../frontend/package.json',import.meta.url));
const {chromium}=require('@playwright/test');
const output=path.resolve(process.env.MEDIA_TIMING_OUTPUT||'.runtime/acceptance-20261028/media-timing');
fs.mkdirSync(output,{recursive:true});
execFileSync('ffmpeg',['-hide_banner','-loglevel','error','-y','-f','lavfi','-i','sine=frequency=440:sample_rate=44100','-t','3',path.join(output,'tone.wav')]);
execFileSync('ffmpeg',['-hide_banner','-loglevel','error','-y','-f','lavfi','-i','testsrc=size=320x180:rate=24','-f','lavfi','-i','sine=frequency=440:sample_rate=44100','-t','4','-c:v','libx264','-pix_fmt','yuv420p','-preset','ultrafast','-g','24','-c:a','aac','-f','hls','-hls_time','1','-hls_list_size','0',path.join(output,'stream.m3u8')]);
const base=process.env.MEDIA_AUDIT_URL||'http://127.0.0.1:5188';
const browser=await chromium.launch({executablePath:process.env.UI_CHROME_PATH||'/usr/bin/google-chrome',headless:true});
const samples=[],appearance=[];
try{
 for(const kind of ['radio','tv']){
  const page=await browser.newPage({viewport:{width:393,height:844},hasTouch:true,isMobile:true,reducedMotion:'reduce'});
  page.on('pageerror',error=>console.error('Player error:',error.message));
  await page.route('**/*',route=>{
   const url=new URL(route.request().url());
   if(url.pathname.startsWith('/__fixture/')){
    const name=path.basename(url.pathname),file=path.join(output,name);
    if(!fs.existsSync(file))return route.abort();
    return route.fulfill({body:fs.readFileSync(file),contentType:name.endsWith('.wav')?'audio/wav':name.endsWith('.m3u8')?'application/vnd.apple.mpegurl':'video/mp2t',headers:{'access-control-allow-origin':'*'}});
   }
   return url.origin===base?route.continue():route.abort();
  });
  await page.route('**/__timing_host__',route=>route.fulfill({contentType:'text/html',body:`<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><style>body{margin:0}iframe{border:0;width:100vw;height:100dvh}</style><iframe id="player" src="/${kind}/index.html" sandbox="allow-scripts allow-forms" allow="autoplay"></iframe><script>const f=document.getElementById('player');addEventListener('message',e=>{if(e.source===f.contentWindow&&e.data?.type==='sterling.${kind}.ready')f.contentWindow.postMessage({type:'sterling.${kind}.hydrate',hostHeader:true,appearance:'light',values:${JSON.stringify(kind==='radio'?{omnicast_v11_config:JSON.stringify({bootBehavior:'quiet',catalogSources:[]})}:{streamos_theme_v14:'spotify'})}},'*');});</script>`}));
  await page.goto(base+'/__timing_host__');
  const frame=await page.locator('#player').contentFrame();
  await frame.locator(kind==='radio'?'#sp-mchip-all':'#tv-mobile-shell').waitFor();
  for(let i=0;i<5;i++){
   await frame.locator('body').evaluate((_body,{kind,index,base})=>{
    document.getElementById('measure-start')?.remove();window.__timing=null;
    const button=document.createElement('button');button.id='measure-start';button.textContent='Measure playback';button.style.cssText='position:fixed;inset:0 auto auto 0;z-index:2147483647;padding:16px';
    button.onclick=()=>{
     window.__clicked=true;const start=performance.now(),media=document.getElementById(kind==='radio'?'audio-engine':'video-player');
     const finish=()=>{window.__timing={elapsed_ms:performance.now()-start,readyState:media.readyState,muted:media.muted,currentTime:media.currentTime,videoWidth:media.videoWidth||null};};
     if(kind==='tv'&&media.requestVideoFrameCallback)media.requestVideoFrameCallback(finish);
     else media.addEventListener('playing',finish,{once:true});
     if(kind==='radio')window.startPlayback({id:'timing-'+index,title:'Controlled 440 Hz tone',kind:'radio',streamUrl:base+'/__fixture/tone.wav?switch='+index,backupUrls:[]});
     else window.playStream({name:'Controlled HLS video '+index,url:base+'/__fixture/stream.m3u8?switch='+index,group:'Test',lang:'Test'},0);
    };document.body.append(button);
   },{kind,index:i,base:"https://media-fixture.test"});
   await frame.getByRole('button',{name:'Measure playback',exact:true}).click();
   await frame.locator('body').evaluate(()=>new Promise((resolve,reject)=>{const start=Date.now();const poll=()=>window.__timing?resolve():Date.now()-start>12000?reject(Error('No decoded playback within 12s clicked='+window.__clicked+' ready='+document.documentElement.dataset.radioReady+': '+JSON.stringify([...document.querySelectorAll('audio,video')].map(m=>({src:m.currentSrc,ready:m.readyState,paused:m.paused,error:m.error?.message}))))):setTimeout(poll,20);poll();}));
   samples.push({kind,switch:i,...await frame.locator('body').evaluate(()=>window.__timing)});
  }
  await frame.locator('#measure-start').evaluate(button=>button.remove());
  if(kind==='radio')await frame.locator('#spotify-app .solid-sp-miniplayer').click();
  else await frame.getByRole('button',{name:'Open Now Playing',exact:true}).click();
  for(const scheme of ['light','dark']){
   await page.evaluate(({kind,scheme})=>document.getElementById('player').contentWindow.postMessage({type:`sterling.${kind}.command`,action:'appearance',value:scheme},'*'),{kind,scheme});
   await frame.locator(`html[data-color-scheme=${scheme}]`).waitFor();
   const surface=frame.locator(kind==='radio'?'#mobile-player-sheet':'#tv-now-playing');
   await surface.waitFor();
   appearance.push({kind,scheme,...await surface.evaluate(el=>({background:getComputedStyle(el).backgroundColor,backgroundImage:getComputedStyle(el).backgroundImage,color:getComputedStyle(el).color,width:el.getBoundingClientRect().width,height:el.getBoundingClientRect().height}))});
   await page.screenshot({path:path.join(output,kind+'-'+scheme+'.png')});
  }
  const light=appearance.find(row=>row.kind===kind&&row.scheme==='light');
  const dark=appearance.find(row=>row.kind===kind&&row.scheme==='dark');
  if(light.backgroundImage===dark.backgroundImage)throw Error(kind+' Light retained the Dark player backdrop');
  if(light.color!=='rgb(21, 33, 48)'||!light.backgroundImage.includes('248, 250, 252'))throw Error(kind+' Light player palette regressed: '+JSON.stringify(light));
  await page.close();
 }
}finally{
 await browser.close();
 const summary=['radio','tv'].map(kind=>{const values=samples.filter(s=>s.kind===kind).map(s=>s.elapsed_ms).sort((a,b)=>a-b);return {kind,n:values.length,median_ms:values[Math.floor(values.length/2)]??null,p95_ms:values[Math.max(0,Math.ceil(values.length*.95)-1)]??null};});
 const evidence={recorded_at:new Date().toISOString(),scope:'Local controlled WAV playing event and HLS first decoded video frame in Chrome mobile emulation. No physical speaker, phone, broadcaster or 4G claim.',viewport:{width:393,height:844},samples,summary,appearance};
 fs.writeFileSync(path.join(output,'results.json'),JSON.stringify(evidence,null,2));console.log(JSON.stringify(summary));
}
