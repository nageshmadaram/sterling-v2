import {readFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {test,expect,type Page} from '@playwright/test';

async function openPlayer(page:Page,kind:'radio'|'tv',radioConfig:Record<string,unknown>={}){
  // Exercise the real opaque-origin player without authentication or broker fixtures.
  await page.route('**/*',route=>{
    const url=new URL(route.request().url());
    return url.hostname==='127.0.0.1'||url.hostname==='localhost'?route.continue():route.abort();
  });
  await page.route('**/__media_test_host__',route=>route.fulfill({contentType:'text/html',body:`<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><style>body{margin:0}iframe{border:0;width:100vw;height:100dvh}</style><iframe id="player" src="/${kind}/index.html" sandbox="allow-scripts allow-forms" allow="autoplay"></iframe><script>
    const frame=document.getElementById('player');
    addEventListener('message',event=>{if(event.source!==frame.contentWindow||event.origin!=='null')return;if(event.data?.type==='sterling.${kind}.ready'){frame.contentWindow.postMessage({type:'sterling.${kind}.hydrate',hostHeader:false,values:${JSON.stringify(kind==='radio'?{omnicast_v11_config:JSON.stringify({bootBehavior:'quiet',activeCategory:'all',catalogSources:[],...radioConfig})}:{streamos_theme_v14:'spotify'})}},'*');frame.contentWindow.postMessage({type:'sterling.${kind}.ack'},'*');}});
  </script>`}));
  await page.goto('/__media_test_host__');
  const frame=page.frameLocator('#player');
  await expect(frame.locator(kind==='radio'?'html[data-radio-ready="true"] #sp-mchip-all':'#tv-mobile-shell')).toBeVisible();
  return frame;
}

test('Radio categories suppress home shelves and SW/AM is a separate shared player',async({page})=>{
  const errors:string[]=[];page.on('pageerror',error=>errors.push(error.message));
  const frame=await openPlayer(page,'radio');
  await expect(frame.locator('#sp-recents-section')).toBeVisible();
  await frame.locator('#sp-mchip-te').click();
  for(const id of ['radio-home-quick','sp-recents-section','sp-suggested-section','sp-collections'])await expect(frame.locator('#'+id)).toBeHidden();
  await expect(frame.locator('#sp-grid-heading')).toHaveText('Telugu Radio Channels');
  await frame.locator('#sp-mchip-sw').click();await frame.getByRole('button',{name:'Open radio tuner',exact:true}).click();
  const receiver=frame.getByRole('dialog',{name:'Shortwave and AM player'});await expect(receiver).toBeVisible();
  await expect(receiver.getByRole('slider')).toBeVisible();
  const presets=receiver.locator('.sw-player-presets button');
  const firstFrequency=await presets.nth(0).locator('b').innerText();
  const nextFrequency=await presets.nth(1).locator('b').innerText();
  await presets.first().click();
  await expect(receiver.locator('.sw-player-frequency')).toHaveText(firstFrequency);
  await receiver.getByRole('button',{name:'Next shortwave station'}).click();
  await expect(receiver.locator('.sw-player-frequency')).toHaveText(nextFrequency);
  await receiver.press('Escape');await expect(receiver).toBeHidden();
  await frame.locator('#sp-mchip-all').click();await expect(frame.locator('#sp-recents-section')).toBeVisible();
  expect(errors).toEqual([]);
});

test('Radio standalone suggestions select a station and restore its collection',async({page})=>{
  const frame=await openPlayer(page,'radio');
  await frame.locator('#spotify-app nav [data-radio-click="63"]').click();
  await frame.locator('.sps-search-box').click();
  const input=frame.locator('.sps-search-field:visible').first();await input.fill('BBC');
  const suggestion=frame.locator('.sps-row').filter({hasText:'BBC World Service'}).first();await expect(suggestion).toBeVisible();
  await suggestion.focus();
  await page.waitForTimeout(450); // Cross the debounced results refresh without losing keyboard selection.
  await expect(suggestion).toBeFocused();
  await suggestion.press('Enter');
  await expect(frame.locator('#sheet-title')).toHaveText('BBC World Service');
  await expect(frame.locator('.radio-search-suggestions')).toHaveCount(0);
});

test('Spotify TV categories and mobile widths keep the home experience separate',async({page})=>{
  const frame=await openPlayer(page,'tv');
  await expect(frame.locator('.tm-quick button')).toHaveCount(8);
  await frame.locator('#tv-mobile-home .media-browse-grid [data-tm-category="News"]').click();
  await expect(frame.locator('#tv-mobile-home')).toBeHidden();
  await expect(frame.locator('.tm-feed-heading')).toHaveText('News');
  await frame.locator('#tv-mobile-nav [data-tm-action="home"]').click();
  await expect(frame.locator('#tv-mobile-home')).toBeVisible();
  for(const width of [320,390,430,768]){
    await page.setViewportSize({width,height:844});
    expect(await frame.locator('html').evaluate(el=>el.scrollWidth<=el.clientWidth)).toBe(true);
  }
});

test('Radio plays a controlled audio stream and preserves user pause',async({page})=>{
  const frame=await openPlayer(page,'radio');
  const samples=8000,wave=Buffer.alloc(44+samples*2);
  wave.write('RIFF');wave.writeUInt32LE(wave.length-8,4);wave.write('WAVEfmt ',8);wave.writeUInt32LE(16,16);wave.writeUInt16LE(1,20);wave.writeUInt16LE(1,22);wave.writeUInt32LE(8000,24);wave.writeUInt32LE(16000,28);wave.writeUInt16LE(2,32);wave.writeUInt16LE(16,34);wave.write('data',36);wave.writeUInt32LE(samples*2,40);
  await page.route('https://radio-fixture.test/tone.wav',route=>route.fulfill({contentType:'audio/wav',body:wave,headers:{'access-control-allow-origin':'*'}}));
  await frame.locator('body').evaluate(()=>{
    const button=document.createElement('button');button.textContent='Play controlled station';
    button.style.cssText='position:fixed;top:0;left:0;z-index:9999';
    button.onclick=()=>{const audio=document.getElementById('audio-engine') as HTMLAudioElement;audio.loop=true;(window as any).startPlayback({id:'controlled',title:'Controlled station',subtitle:'Test stream',kind:'radio',category:'telugu',streamUrl:'https://radio-fixture.test/tone.wav',backupUrls:[]});};
    document.body.append(button);
  });
  await frame.getByRole('button',{name:'Play controlled station'}).click();
  await expect.poll(()=>frame.locator('#audio-engine').evaluate((el:HTMLAudioElement)=>!el.paused&&el.readyState>=2&&!el.muted)).toBe(true);
  await frame.locator('#spotify-app .solid-sp-miniplayer [data-radio-click="56"]').click();
  await expect.poll(()=>frame.locator('#audio-engine').evaluate((el:HTMLAudioElement)=>el.paused)).toBe(true);
  await page.waitForTimeout(3800); // Cross the background-resume guard interval.
  expect(await frame.locator('#audio-engine').evaluate((el:HTMLAudioElement)=>el.paused)).toBe(true);
  await frame.locator('#spotify-app .solid-sp-miniplayer [data-radio-click="56"]').click();
  await expect.poll(()=>frame.locator('#audio-engine').evaluate((el:HTMLAudioElement)=>!el.paused)).toBe(true);
  await frame.locator('#spotify-app .solid-sp-miniplayer').click();
  await frame.locator('html').evaluate(el=>el.setAttribute('data-color-scheme','light'));
  const sheetPlay=frame.locator('#sheet-play');
  await expect(sheetPlay.locator('svg path')).toHaveAttribute('d','M6 5h4v14H6zm8 0h4v14h-4z');
  await expect(sheetPlay.locator('..')).toHaveAttribute('aria-label','Pause radio');
  const contrast=await sheetPlay.evaluate(el=>{const luminance=(color:string)=>{const values=color.match(/[\d.]+/g)!.slice(0,3).map(Number).map(v=>{v/=255;return v<=.04045?v/12.92:((v+.055)/1.055)**2.4;});return .2126*values[0]+.7152*values[1]+.0722*values[2];};const a=luminance(getComputedStyle(el).color),b=luminance(getComputedStyle(el.closest('button')!).backgroundColor);return (Math.max(a,b)+.05)/(Math.min(a,b)+.05);});
  expect(contrast).toBeGreaterThanOrEqual(4.5);
  await sheetPlay.locator('..').click();
  await expect(sheetPlay.locator('svg path')).toHaveAttribute('d','M8 5v14l11-7z');
  await expect(sheetPlay.locator('..')).toHaveAttribute('aria-label','Play radio');
  await expect.poll(()=>frame.locator('#audio-engine').evaluate((el:HTMLAudioElement)=>el.paused)).toBe(true);
  await sheetPlay.locator('..').click();
  await expect(sheetPlay.locator('svg path')).toHaveAttribute('d','M6 5h4v14H6zm8 0h4v14h-4z');
  await page.clock.install();
  await frame.getByRole('button',{name:'Set sleep timer',exact:true}).first().click();
  await frame.getByRole('dialog',{name:'Wind down'}).getByRole('button',{name:'15 minutes',exact:true}).click();
  await page.clock.fastForward(15*60*1000+1000);
  await expect.poll(()=>frame.locator('#audio-engine').evaluate((el:HTMLAudioElement)=>el.paused)).toBe(true);
  await page.clock.fastForward(4000);
  expect(await frame.locator('#audio-engine').evaluate((el:HTMLAudioElement)=>el.paused)).toBe(true);
});

for(const kind of ['radio','tv'] as const){
 test(`${kind} sleep timer is accessible, cancellable and pauses on expiry`,async({page},testInfo)=>{
  const frame=await openPlayer(page,kind);
  if(kind==='radio')await frame.locator('#spotify-app .solid-sp-miniplayer').click();
  else {
   await frame.locator('.tm-quick button').first().click();
  }
  const trigger=frame.locator('.media-timer-trigger:visible').first();
  await trigger.click();
  const timer=frame.getByRole('dialog',{name:'Wind down'});
  await expect(timer).toBeVisible();
  await page.screenshot({path:testInfo.outputPath('sleep-timer.png')});
  await timer.getByRole('button',{name:'30 minutes',exact:true}).click();
  await expect(timer).toBeHidden();
  await expect(trigger).toHaveAccessibleName('Sleep timer: 30 minutes remaining');
  await trigger.click();await timer.getByRole('button',{name:'Cancel timer',exact:true}).click();
  await expect(trigger).toHaveAccessibleName('Set sleep timer');
  await trigger.click();await timer.press('Escape');await expect(timer).toBeHidden();
  // Install a virtual clock only after initial player hydration.
  await page.clock.install();
  await trigger.click();await timer.getByRole('button',{name:'15 minutes',exact:true}).click();
  await page.clock.fastForward(15*60*1000+1000);
  await expect(frame.locator('.media-announcement')).toHaveText('Sleep timer finished · Playback paused');
  await expect(trigger).toHaveAccessibleName('Set sleep timer');
  expect(await frame.locator(kind==='radio'?'#audio-engine':'#video-player').evaluate((el:HTMLMediaElement)=>el.paused)).toBe(true);
 });
}

async function seededTv(page:Page){
 const frame=await openPlayer(page,'tv');
 await frame.locator('body').evaluate(()=>{(window as any).eval(`
  rebuildCombinedChannelCatalog=()=>{};
  allChannels=Array.from({length:84},(_,i)=>({name:'Channel '+String(i+1).padStart(2,'0'),url:'https://fixture.test/'+i+'.m3u8',group:i%2?'Movies':'News',lang:i%3?'English':'Telugu',isIndian:i%3===0,verified:i<4}));
  catalogScope='all';catalogLanguage='All';catalogCategory='All';renderCatalogControls();renderViewport();
 `);});
 await frame.locator('.tm-tabs [data-tm-action="live"]').click();
 return frame;
}

test('TV queue order matches next, previous and explicit queued selection',async({page})=>{
 const frame=await seededTv(page);
 await frame.getByRole('button',{name:'Play Channel 02',exact:true}).first().click();
 await expect(frame.locator('#tm-now-title')).toHaveText('Channel 02');
 await frame.getByRole('button',{name:'Open channel queue',exact:true}).click();
 await expect(frame.locator('.tm-queue-current strong')).toHaveText('Channel 02');
 await expect(frame.locator('#tm-queue-items button').first()).toContainText('Channel 03');
 await frame.getByRole('button',{name:'Close queue',exact:true}).click();
 await frame.locator('#tv-now-playing [data-tm-action="next"]').click();
 await expect(frame.locator('#tm-now-title')).toHaveText('Channel 03');
 await frame.locator('#tv-now-playing [data-tm-action="previous"]').click();
 await expect(frame.locator('#tm-now-title')).toHaveText('Channel 02');
 await frame.getByRole('button',{name:'Open channel queue',exact:true}).click();
 await frame.locator('#tm-queue-items button').nth(1).click();
 await expect(frame.locator('#tm-now-title')).toHaveText('Channel 04');
 await frame.locator('#tv-now-playing [data-tm-action="next"]').click();
 await expect(frame.locator('#tm-now-title')).toHaveText('Channel 05');
});

test('TV Live and Indian badge counts agree with all loadable channels',async({page})=>{
 const frame=await seededTv(page);
 await expect(frame.locator('#catalog-genre-select option').first()).toHaveText('All Categories (84)');
 // Verify every page remains reachable, including channels after the first batch.
 await frame.locator('body').evaluate(()=>{(window as any).eval('for(let page=0;page<4&&renderedItems<filteredChannels.length;page++)renderNextBatch()');});
 await expect(frame.locator('#yt-studio-grid .channel-grid-card')).toHaveCount(84);
 await expect(frame.locator('#catalog-showing-count')).toHaveText('Showing 84 of 84 channels');
 await frame.locator('#tv-mobile-shell .tm-avatar').click();
 await frame.locator('#tv-mobile-sheet [data-tm-action=filters]').click();
 await frame.locator('#tv-mobile-sheet [data-tm-filter=category]').selectOption('News');
 await expect(frame.locator('#catalog-showing-count')).toContainText('of 42 channels');
 await frame.locator('#tv-mobile-shell .tm-avatar').click();
 await frame.locator('#tv-mobile-sheet [data-tm-action=indian]').click();
 await expect(frame.locator('#catalog-genre-select option').first()).toHaveText('All Categories (28)');
 await expect(frame.locator('#catalog-showing-count')).toContainText('of 28 channels');
});

test('TV menu resizes and player exposes honest programme info and instant PiP',async({page})=>{
 const frame=await seededTv(page);
 await frame.getByRole('button',{name:'Play Channel 01',exact:true}).first().click();
 await frame.locator('#tv-now-playing [data-tm-action="more"]').click();
 const menu=frame.locator('#tv-mobile-sheet');
 const height=menu.getByRole('slider',{name:'Menu height'});
 await expect(height).toHaveAttribute('aria-valuenow','72');
 await menu.getByRole('button',{name:'Expand menu',exact:true}).click();
 await expect(height).toHaveAttribute('aria-valuenow','95');
 await height.focus();await height.press('ArrowDown');
 await expect(height).toHaveAttribute('aria-valuenow','85');
 await menu.getByRole('button',{name:'Close TV features',exact:true}).click();
 await frame.locator('#tv-now-playing [data-tm-action="epg"]').click();
 await expect(menu).toContainText('Programme schedule not supplied by this broadcaster');
 await menu.getByRole('button',{name:'Close TV features',exact:true}).click();
 await expect(frame.locator('#tv-now-playing [data-tm-action="fullscreen"]')).toHaveCount(0);
 await expect(frame.locator('#tv-now-playing [data-tm-action="pip"]')).toBeVisible();
 await frame.locator('body').evaluate(()=>{
  const video=document.getElementById('video-player') as any;
  Object.defineProperty(video,'readyState',{configurable:true,get:()=>2});
  video.requestPictureInPicture=()=>{document.body.dataset.testPip='requested';return Promise.resolve();};
 });
 await frame.locator('#tv-now-playing [data-tm-action="pip"]').click();
 await expect(frame.locator('body')).toHaveAttribute('data-test-pip','requested');
 await page.clock.install();
 // Leave the full-screen sheet before interacting with the underlying video.
 await frame.locator('#tv-now-playing [data-tm-action=collapse]').click();
 await frame.locator('#video-surface-card').dispatchEvent('pointermove');
 await expect(frame.locator('#custom-video-hud')).not.toHaveClass(/mobile-hud-hidden/);
 await page.clock.fastForward(2700);
 await expect(frame.locator('#custom-video-hud')).toHaveClass(/mobile-hud-hidden/);
 await frame.locator('#video-surface-card').dispatchEvent('pointerdown');
 await expect(frame.locator('#custom-video-hud')).not.toHaveClass(/mobile-hud-hidden/);
});

for(const theme of ['spotify','youtube','netflix']){
 test(`${theme} bottom scrolling keeps filter rows stable and preserves loaded cards`,async({page})=>{
  const frame=await seededTv(page);
  await frame.locator('body').evaluate((theme)=>{(window as any).eval(`switchTheme(${JSON.stringify(theme)});renderViewport();`);},theme);
  const before=await frame.locator('body').evaluate(()=>{
   const row=document.getElementById('sticky-chip-bar')!;
   const first=document.querySelector('#yt-studio-grid .channel-grid-card')!;
   (window as any).__firstCard=first;
   (window as any).__imageWrites=0;
   new MutationObserver(records=>{(window as any).__imageWrites+=records.length;}).observe(first,{subtree:true,attributes:true,attributeFilter:['src']});
   (window as any).__scrollTransitions=0;
   let hidden=document.body.classList.contains('tm-chips-hidden');
   new MutationObserver(()=>{const next=document.body.classList.contains('tm-chips-hidden');if(next!==hidden){(window as any).__scrollTransitions++;hidden=next;}}).observe(document.body,{attributes:true,attributeFilter:['class']});
   return {height:row.getBoundingClientRect().height,count:document.querySelectorAll('#yt-studio-grid .channel-grid-card').length};
  });
  const main=frame.locator('#main-scroll');
  // Cross the old collapse threshold in both directions, then reach pagination.
  for(const top of [90,50,120,70]){
   await main.evaluate((el,top)=>el.scrollTo({top,behavior:'instant'}),top);
   await page.waitForTimeout(40);
  }
  await main.evaluate(el=>el.scrollTo({top:el.scrollHeight,behavior:'instant'}));
  await expect.poll(()=>frame.locator('#yt-studio-grid .channel-grid-card').count()).toBeGreaterThan(before.count);
  await page.waitForTimeout(200);
  const after=await frame.locator('body').evaluate(()=>({
   height:document.getElementById('sticky-chip-bar')!.getBoundingClientRect().height,
   sameCard:(window as any).__firstCard===document.querySelector('#yt-studio-grid .channel-grid-card'),
   imageWrites:(window as any).__imageWrites,transitions:(window as any).__scrollTransitions
  }));
  expect(after.height).toBe(before.height);expect(after.sameCard).toBe(true);
  expect(after.imageWrites).toBe(0);expect(after.transitions).toBe(0);
 });
}

test('TV availability filter hides exhausted failures, persists and restores recovered channels',async({page})=>{
 const frame=await seededTv(page);
 await frame.locator('body').evaluate(()=>{(window as any).eval(`activeChannel=allChannels[1];startBackupFallback(activeChannel,document.getElementById('video-player'),document.getElementById('player-spinner'),document.getElementById('server-badge'));`);});
 await frame.locator('#tv-mobile-shell .tm-avatar').click();
 await frame.locator('#tv-mobile-sheet [data-tm-action=filters]').click();
 await frame.locator('[data-tm-filter=availability]').selectOption('available');
 await expect(frame.locator('#catalog-genre-select option').first()).toHaveText('All Categories (83)');
 expect(await frame.locator('body').evaluate(()=>JSON.parse((window as any).eval('tvStorage.getItem("streamos_ui_v14")')).hideUnavailable)).toBe(true);
 expect(await frame.locator('body').evaluate(()=>(window as any).eval('getRankedSearchMatches("Channel 02").length'))).toBe(0);
 await frame.locator('#tv-mobile-shell .tm-avatar').click();
 await frame.locator('#tv-mobile-sheet [data-tm-action=filters]').click();
 await frame.locator('[data-tm-filter=availability]').selectOption('all');
 await expect(frame.locator('#catalog-genre-select option').first()).toHaveText('All Categories (84)');
 await frame.locator('body').evaluate(()=>{(window as any).eval(`activeChannel=allChannels[1];document.getElementById('video-player').dispatchEvent(new Event('playing'));`);});
 expect(await frame.locator('body').evaluate(()=>(window as any).eval('unavailableChannels.has(allChannels[1].url)'))).toBe(false);
});

test('portrait TV has no video overlay controls while landscape keeps them',async({page})=>{
 const frame=await seededTv(page);
 await frame.getByRole('button',{name:'Play Channel 01',exact:true}).first().click();
 await expect(frame.locator('#custom-video-hud')).toBeHidden();
 await expect(frame.locator('#tv-now-playing .tm-now-controls [data-tm-action=play]')).toBeVisible();
 await page.setViewportSize({width:844,height:390});
 // Leave the full-screen sheet before interacting with the underlying video.
 await frame.locator('#tv-now-playing [data-tm-action=collapse]').click();
 await frame.locator('#video-surface-card').dispatchEvent('pointermove');
 await expect(frame.locator('#custom-video-hud')).toBeVisible();
});

test('unavailable channels expire after three hours and can be retried immediately',async({page})=>{
 await page.clock.install();
 const frame=await seededTv(page);
 await frame.locator('body').evaluate(()=>{(window as any).eval('hideUnavailableChannels=true;markChannelUnavailable(allChannels[1]);');});
 await expect(frame.locator('#catalog-genre-select option').first()).toHaveText('All Categories (83)');
 await frame.locator('#tv-mobile-shell .tm-avatar').click();
 await frame.locator('#tv-mobile-sheet [data-tm-action=filters]').click();
 await frame.getByRole('button',{name:'Retry unavailable channels now',exact:true}).click();
 await expect(frame.locator('#catalog-genre-select option').first()).toHaveText('All Categories (84)');
 expect(await frame.locator('body').evaluate(()=>(window as any).eval('hideUnavailableChannels'))).toBe(true);
 await frame.locator('body').evaluate(()=>{(window as any).eval('markChannelUnavailable(allChannels[1]);');});
 await page.clock.fastForward(2*60*60*1000);
 await expect(frame.locator('#catalog-genre-select option').first()).toHaveText('All Categories (83)');
 await page.clock.fastForward(60*60*1000+60001);
 await expect(frame.locator('#catalog-genre-select option').first()).toHaveText('All Categories (84)');
});

test('unchanged discovery views preserve existing cards and home shelves',async({page})=>{
 const frame=await seededTv(page);
 const result=await frame.locator('body').evaluate(()=>{return (window as any).eval(`
  renderNextBatch();const first=document.querySelector('#yt-studio-grid .channel-grid-card');
  const count=renderedItems;
  renderViewport();renderViewport();
  const stableGrid=first===document.querySelector('#yt-studio-grid .channel-grid-card')&&renderedItems===count;
  document.querySelector('#tv-mobile-shell .tm-tabs [data-tm-action=home]').click();
  const shelf=document.querySelector('#tv-mobile-home section');
  renderViewport();renderViewport();
  ({stableGrid,stableShelf:shelf===document.querySelector('#tv-mobile-home section')});
 `);});
 expect(result).toEqual({stableGrid:true,stableShelf:true});
});

test('feature menus restore focus to the player instead of their removed controls',async({page})=>{
 const frame=await seededTv(page);
 await frame.getByRole('button',{name:'Play Channel 01',exact:true}).first().click();
 const more=frame.locator('#tv-now-playing [data-tm-action=more]');await more.click();
 await frame.locator('#tv-mobile-sheet [data-tm-action=filters]').click();
 await frame.locator('#tv-mobile-sheet [data-tm-action=close-sheet]').click();
 await expect(more).toBeFocused();
});

test('comfort sheet padding is not treated as a backdrop dismissal',async({page})=>{
 const frame=await seededTv(page);
 await frame.getByRole('button',{name:'Play Channel 01',exact:true}).first().click();
 await frame.getByRole('button',{name:'Set sleep timer',exact:true}).click();
 const dialog=frame.getByRole('dialog',{name:'Wind down'});await expect(dialog).toBeVisible();
 await dialog.click({position:{x:4,y:6}});await expect(dialog).toBeVisible();
 await dialog.getByRole('button',{name:'Close sleep timer'}).click();await expect(dialog).toBeHidden();
});

test('standalone TV boots without a host, persists settings and tolerates damaged libraries',async({page})=>{
 await page.route('**/*',route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort());
 const html=readFileSync(resolve(process.cwd(),'../sterling-iptv/sterling-iptv.html'),'utf8');
 await page.route('**/__standalone_tv__',route=>route.fulfill({contentType:'text/html',body:html}));
 await page.addInitScript(()=>{
  localStorage.setItem('sterling.tv.streamos_theme_v14','spotify');
  localStorage.setItem('sterling.tv.streamos_startup_channel_v14',JSON.stringify({name:'Audit standalone',url:'https://fixture.test/live.m3u8'}));
  localStorage.setItem('sterling.tv.streamos_favs_v14','[null,5,{}]');
  localStorage.setItem('sterling.tv.streamos_stats_v14','broken JSON');
 });
 const errors:string[]=[];page.on('pageerror',error=>errors.push(error.message));
 await page.goto('/__standalone_tv__');
 await expect(page.locator('#tm-now-title')).toHaveText('Audit standalone');
 await page.evaluate(()=>{(window as any).setChannelAvailabilityFilter('available');});
 expect(await page.evaluate(()=>JSON.parse(localStorage.getItem('sterling.tv.streamos_ui_v14')||'{}').hideUnavailable)).toBe(true);
 await page.reload();await expect(page.locator('#catalog-availability-select')).toHaveValue('available');
 expect(errors).toEqual([]);
});

for(const kind of ['tv','radio'] as const)test(`${kind} offline notices support touch dismissal and reconnect`,async({page})=>{
 const frame=await openPlayer(page,kind);
 await frame.locator('body').evaluate(()=>{
  Object.defineProperty(navigator,'onLine',{value:false,configurable:true});window.dispatchEvent(new Event('offline'));
 });
 const notice=frame.locator('.media-connection');await expect(notice).toBeVisible();
 await notice.getByRole('button',{name:'Dismiss offline notice'}).click();await expect(notice).toBeHidden();
 await frame.locator('body').evaluate(()=>{
  Object.defineProperty(navigator,'onLine',{value:true,configurable:true});window.dispatchEvent(new Event('online'));
  Object.defineProperty(navigator,'onLine',{value:false,configurable:true});window.dispatchEvent(new Event('offline'));
 });
 await expect(notice).toBeVisible();
 const bounds=await notice.boundingBox();if(!bounds)throw Error('Missing offline notice');
 await page.mouse.move(bounds.x+12,bounds.y+bounds.height/2);await page.mouse.down();
 await page.mouse.move(bounds.x+100,bounds.y+bounds.height/2);await page.mouse.up();
 await expect(notice).toBeHidden();
});

for(const kind of ['tv','radio'] as const)test(`${kind} echoes bounded search request IDs through the real player`,async({page})=>{
 await openPlayer(page,kind);
 await page.evaluate(kind=>{
  const frame=document.getElementById('player') as HTMLIFrameElement;
  (window as any).__suggestionReplies=[];
  window.addEventListener('message',event=>{
   if(event.source===frame.contentWindow&&event.origin==='null'&&event.data?.type===`sterling.${kind}.suggestions`)(window as any).__suggestionReplies.push(event.data);
  });
  frame.contentWindow!.postMessage({type:`sterling.${kind}.hydrate`,hostHeader:true,values:kind==='radio'?{omnicast_v11_config:JSON.stringify({bootBehavior:'quiet'})}:{}},'*');
  for(const requestId of ['first-request','retry-request'])frame.contentWindow!.postMessage({type:`sterling.${kind}.command`,action:'suggest',value:kind==='tv'?'Red Bull':'BBC',requestId},'*');
 },kind);
 await expect.poll(()=>page.evaluate(()=>(window as any).__suggestionReplies.map((reply:any)=>reply.requestId))).toEqual(['first-request','retry-request']);
 expect(await page.evaluate(()=>(window as any).__suggestionReplies.every((reply:any)=>reply.items.length>0&&reply.items.length<=8))).toBe(true);
});

async function loadRadioDirectoryFixture(page:Page){
 const frame=await openPlayer(page,'radio');
 await expect.poll(()=>frame.locator('body').evaluate(()=>eval('!!radioCatalog && radioCatalog.snapshot().complete'))).toBe(true);
 // The catalog deduplicates curated seeds by stream URL.
 const baseline=await frame.locator('body').evaluate(()=>eval('new Set(CURATED_RADIO_POOL.map(station=>station.streamUrl)).size'));
 await page.route('https://radio-directory.fixture/**',route=>{
  const url=new URL(route.request().url());
  if(url.pathname==='/custom.m3u')return route.fulfill({contentType:'text/plain',headers:{'access-control-allow-origin':'*'},body:'#EXTM3U\n#EXTINF:-1 group-title="Jazz" tvg-language="Telugu",My jazz station\nhttps://station.fixture/my-jazz.mp3'});
  const offset=Number(url.searchParams.get('offset')||0),length=offset?50:1000;
  return route.fulfill({contentType:'application/json',headers:{'access-control-allow-origin':'*'},body:JSON.stringify(Array.from({length},(_,i)=>({stationuuid:`fixture-${offset+i}`,name:`World fixture ${offset+i}`,url_resolved:`https://station.fixture/${offset+i}.mp3`,country:i%2?'India':'France',language:i%2?'telugu':'french',tags:i%2?'news,talk':'jazz',favicon:''})))});
 });
 await frame.locator('body').evaluate(()=>window.dispatchEvent(new MessageEvent('message',{source:window.parent,data:{type:'sterling.radio.hydrate',hostHeader:false,values:{omnicast_v11_config:JSON.stringify({bootBehavior:'quiet',activeCategory:'all',catalogSourcesVersion:5,catalogSources:[{id:'fixture',name:'World fixture directory',url:'https://radio-directory.fixture/stations',kind:'directory',enabled:true}]})}}})));
 await expect.poll(()=>frame.locator('body').evaluate(()=>eval('radioCatalog.snapshot().complete'))).toBe(true);
 return {frame,baseline};
}

test('Radio exposes all loaded stations, matching category counts and append-only pagination',async({page})=>{
 const {frame,baseline}=await loadRadioDirectoryFixture(page);
 await expect(frame.locator('#sp-grid-count')).toHaveText(`${(baseline+1050).toLocaleString()} stations`);
 await expect(frame.locator('#sp-mchip-all')).toHaveText(`All ${(baseline+1050).toLocaleString()}`);
 await page.screenshot({path:'/tmp/sterling-radio-spotify-home.png'});
 const expected=await frame.locator('body').evaluate(()=>eval('radioDirectoryBase("telugu").length'));
 await frame.locator('#sp-mchip-te').click();
 await expect(frame.locator('#sp-grid-count')).toHaveText(`${expected.toLocaleString()} stations`);
 await expect(frame.locator('#radio-directory-browse')).toBeHidden();await expect(frame.locator('#sp-recents-section')).toBeHidden();
 expect(await frame.locator('body').evaluate(()=>eval('catalogItems.every(station=>station.category==="telugu"||(station.languages||[]).includes("telugu"))'))).toBe(true);
 await frame.locator('#sp-mchip-feat').click();
 const categoryTotal=await frame.locator('#sp-mchip-feat').innerText();
 await frame.locator('#radio-home-shelves [data-row=everything]').getByRole('button',{name:'Show all',exact:true}).click();
 await frame.locator('#spotify-app').getByRole('button',{name:'Filter stations',exact:true}).click();
 await frame.getByRole('combobox',{name:'Radio country',exact:true}).selectOption('France');
 await expect(frame.locator('#sp-grid-count')).toHaveText('525 stations');
 expect(await frame.locator('#sp-cards-grid>[data-radio-click]').count()).toBeLessThanOrEqual(60);
 await frame.locator('#sp-cards-grid>[data-radio-click]').first().evaluate(element=>element.setAttribute('data-preserved','yes'));
 await frame.locator('#sp-cards-grid').locator('..').getByRole('button',{name:/Show more stations/}).click();
 expect(await frame.locator('#sp-cards-grid>[data-radio-click]').count()).toBeGreaterThan(60);
 await expect(frame.locator('#sp-cards-grid>[data-radio-click]').first()).toHaveAttribute('data-preserved','yes');

 await frame.getByRole('combobox',{name:'Radio genre',exact:true}).selectOption('jazz');
 await expect(frame.locator('#sp-grid-count')).toHaveText('525 stations');
 // Grid filters narrow results; category chips continue to show their full totals.
 await expect(frame.locator('#sp-mchip-feat')).toHaveText(categoryTotal);
 await expect(frame.locator('#sp-mchip-all')).toHaveText(`All ${(baseline+1050).toLocaleString()}`);
 await page.screenshot({path:'/tmp/sterling-radio-directory-filtered.png'});
});

test('Radio source manager adds, edits, disables and deletes URL feeds without changing playback',async({page})=>{
 const {frame,baseline}=await loadRadioDirectoryFixture(page);
 await frame.locator('nav.solid-sp-mob-nav [data-sps-create]').click();
 const dialog=frame.getByRole('dialog',{name:'Radio sources'});
 await dialog.getByLabel('Source name',{exact:true}).fill('My jazz');
 await dialog.getByLabel('Source URL',{exact:true}).fill('https://radio-directory.fixture/custom.m3u');
 await dialog.getByRole('button',{name:'Save source',exact:true}).click();
 await expect(dialog.getByRole('button',{name:'Edit My jazz',exact:true})).toBeVisible();
 await expect.poll(()=>frame.locator('body').evaluate(()=>eval('MASTER_POOL.some(station=>station.title==="My jazz station")'))).toBe(true);
 await dialog.getByRole('button',{name:'Edit My jazz',exact:true}).click();
 await dialog.getByLabel('Source name',{exact:true}).fill('My edited jazz');
 await dialog.getByRole('button',{name:'Save source',exact:true}).click();
 await expect(dialog.getByRole('button',{name:'Turn off My edited jazz',exact:true})).toBeVisible();
 await dialog.getByRole('button',{name:'Turn off My edited jazz',exact:true}).click();
 await expect.poll(()=>frame.locator('body').evaluate(()=>eval('MASTER_POOL.some(station=>station.title==="My jazz station")'))).toBe(false);
 await dialog.getByRole('button',{name:'Delete My edited jazz',exact:true}).click();
 expect(await frame.locator('body').evaluate(()=>eval('appConfig.catalogSources.length'))).toBe(1);
 expect(await frame.locator('body').evaluate(()=>eval('isPlaying'))).toBe(false);
 await dialog.getByRole('button',{name:'Close radio sources'}).click();
 await expect(dialog).toBeHidden();
 await expect.poll(()=>frame.locator('body').evaluate(()=>eval('MASTER_POOL.length'))).toBe(baseline+1050);
});

test('Spotify TV browse tiles use channel artwork and exact category counts',async({page})=>{
 const frame=await seededTv(page);
 await frame.locator('#tv-mobile-nav [data-tm-action="home"]').click();
 const tile=frame.locator('#tv-mobile-home .media-browse-grid [data-tm-category="News"]');
 const expected=await frame.locator('body').evaluate(()=>eval('allChannels.filter(channel=>channel.group==="News"&&isChannelVisible(channel)).length'));
 await expect(tile.locator('small')).toHaveText(`${expected.toLocaleString()} channels`);
 await expect(tile.locator('img')).toHaveCount(expected?1:0);
 await page.screenshot({path:'/tmp/sterling-tv-spotify-browse.png'});
 await tile.click();await expect(frame.locator('#tv-mobile-home')).toBeHidden();
 await expect(frame.locator('.tm-feed-heading')).toHaveText('News');
 await frame.locator('#tv-mobile-nav [data-tm-action="home"]').click();
 const shelf=frame.locator('#tv-mobile-home section').filter({has:frame.getByRole('heading',{name:/Jump back in/})});
 await expect(shelf.locator('.media-shelf-count')).toHaveText('1 channel');
 await shelf.getByRole('button',{name:'Show all',exact:true}).click();
 await expect(frame.locator('.tm-feed-heading')).toContainText('Recently watched');
});

test('standalone Radio keeps edited source URLs across a reload',async({page})=>{
 await page.route('**/*',route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort());
 const html=readFileSync(resolve(process.cwd(),'../sterling-radio/sterling-radio.html'),'utf8');
 await page.route('**/__standalone_radio__',route=>route.fulfill({contentType:'text/html',body:html}));
 await page.addInitScript(()=>{if(!localStorage.getItem('omnicast_v11_config'))localStorage.setItem('omnicast_v11_config',JSON.stringify({bootBehavior:'quiet',appMode:'spotify',catalogSources:[]}));});
 await page.goto('/__standalone_radio__');
 await page.locator('html[data-radio-ready="true"]').waitFor();
 await page.locator('nav.solid-sp-mob-nav [data-sps-create]').click();
 const dialog=page.getByRole('dialog',{name:'Radio sources'});
 await dialog.getByLabel('Source name',{exact:true}).fill('My direct station');
 await dialog.getByLabel('Source URL',{exact:true}).fill('https://fixture.test/personal.mp3');
 await dialog.getByLabel('Format',{exact:true}).selectOption('stream');
 await dialog.getByRole('button',{name:'Save source',exact:true}).click();
 await expect(dialog.getByRole('button',{name:'Edit My direct station',exact:true})).toBeVisible();
 expect(await page.evaluate(()=>JSON.parse(localStorage.getItem('omnicast_v11_config')||'{}').catalogSources[0].kind)).toBe('stream');
 await page.reload();await page.locator('nav.solid-sp-mob-nav [data-sps-create]').click();
 await expect(dialog.getByRole('button',{name:'Edit My direct station',exact:true})).toBeVisible();
});


test('Radio light discovery has transparent shelf headings and distinct selected pills',async({page})=>{
  const frame=await openPlayer(page,'radio');
  await expect.poll(()=>frame.locator('body').evaluate(()=>eval('radioCatalog.snapshot().complete'))).toBe(true);
  await page.route('https://light-radio.fixture/stations',route=>route.fulfill({
    contentType:'application/json',headers:{'access-control-allow-origin':'*'},
    body:JSON.stringify(['Telangana','Delhi'].map((state,i)=>({
      name:'Light fixture '+i,url:'https://fixture.test/'+i,language:'telugu',state,country:'India'
    })))
  }));
  await frame.locator('body').evaluate(()=>eval(`
    appConfig.catalogSources=[{id:'light-fixture',name:'Light fixtures',url:'https://light-radio.fixture/stations',kind:'json',category:'akashvani',group:'telugu-247',enabled:true}];
    radioCatalog.reload(appConfig.catalogSources);
  `));
  await expect(frame.locator('#sp-mchip-all')).toHaveText('All 46');
  await frame.locator('html').evaluate(el=>el.setAttribute('data-color-scheme','light'));
  const all=frame.locator('#sp-mchip-all'),telugu=frame.locator('#sp-mchip-te');
  await expect(all).toHaveAttribute('aria-pressed','true');
  await expect(telugu).toHaveAttribute('aria-pressed','false');
  await expect(all).toHaveCSS('background-color','rgb(21, 27, 38)');
  await expect(telugu).toHaveCSS('background-color','rgb(255, 255, 255)');
  await expect(telugu).toHaveCSS('border-top-color','rgb(184, 196, 210)');
  for(const id of ['made','telugu','akashvani']){
    const head=frame.locator(`.rh-row[data-row="${id}"] > header`);
    await expect(head).toHaveCSS('background-color','rgba(0, 0, 0, 0)');
    await expect(head.locator('h2')).toHaveCSS('color','rgb(21, 27, 38)');
  }
  await telugu.click();
  await expect(telugu).toHaveAttribute('aria-pressed','true');
  await expect(all).toHaveAttribute('aria-pressed','false');
  await expect(telugu).toHaveCSS('background-color','rgb(21, 27, 38)');
  await frame.locator('#sp-mchip-air').click();
  const region=frame.locator('.radio-sub-chips button').filter({hasText:'South India'}).first();
  await region.click();
  await expect(region).toHaveAttribute('aria-pressed','true');
  await expect(region).toHaveCSS('background-color','rgb(21, 27, 38)');
  await expect(frame.locator('.radio-sub-chips button[aria-pressed="false"]').first()).toHaveCSS('background-color','rgb(255, 255, 255)');
  await telugu.click();
  await frame.locator('html').evaluate(el=>el.setAttribute('data-color-scheme','dark'));
  await expect(telugu).toHaveCSS('background-color','rgb(30, 215, 96)');
});

test('TV expanded video shares its clipped corners and Surprise changes channel',async({page},testInfo)=>{
  const frame=await seededTv(page);
  await frame.getByRole('button',{name:'Play Channel 02',exact:true}).first().click();
  await expect(frame.locator('#tv-now-playing')).toBeVisible();
  const before=await frame.locator('#tm-now-title').innerText();
  await frame.getByRole('button',{name:'Surprise channel',exact:true}).click();
  await expect(frame.locator('#tm-now-title')).not.toHaveText(before);
  await expect(frame.getByText('Watching on this device',{exact:true})).toHaveCount(0);
  for(const width of [320,393,844]){
    await page.setViewportSize({width,height:width===844?393:844});
    for(const theme of ['light','dark']){
      await frame.locator('html').evaluate((el,scheme)=>el.setAttribute('data-color-scheme',scheme),theme);
      const geometry=await frame.locator('#video-surface-card').evaluate(el=>{
        const video=el.querySelector('video')!,card=el.getBoundingClientRect(),picture=video.getBoundingClientRect();
        return {gap:Math.max(Math.abs(card.x-picture.x),Math.abs(card.y-picture.y),Math.abs(card.width-picture.width),Math.abs(card.height-picture.height)),clip:getComputedStyle(el).overflow,border:getComputedStyle(el).borderTopWidth,radius:getComputedStyle(el).borderTopLeftRadius,videoRadius:getComputedStyle(video).borderTopLeftRadius};
      });
      expect(geometry.gap).toBeLessThanOrEqual(1);
      expect(geometry.clip).toBe('hidden');expect(geometry.border).toBe('0px');
      expect(geometry.videoRadius).toBe(geometry.radius);
      await page.screenshot({path:testInfo.outputPath(`tv-corners-${width}-${theme}.png`)});
    }
  }
});

test('Radio expanded player replaces device copy with Surprise station',async({page})=>{
  const frame=await openPlayer(page,'radio');
  await frame.locator('#spotify-app .solid-sp-miniplayer').click();
  await expect(frame.locator('#mobile-player-sheet')).toBeVisible();
  await expect(frame.getByText('Listening on this device',{exact:true})).toHaveCount(0);
  const before=await frame.locator('#sheet-title').innerText();
  await frame.getByRole('button',{name:'Surprise station',exact:true}).click();
  await expect(frame.locator('#sheet-title')).not.toHaveText(before);
});


test('Radio first visible frame is Home even with a saved SW/AM section',async({page})=>{
  await page.addInitScript(()=>{
    if(window===window.top)return;
    const painted:string[]=[];(window as any).__paintedRadioSections=painted;
    const sample=()=>{if(document.body&&getComputedStyle(document.body).visibility==='visible')painted.push(document.documentElement.dataset.radioCategory||'unset');if(painted.length<60)requestAnimationFrame(sample);};
    requestAnimationFrame(sample);
  });
  const frame=await openPlayer(page,'radio',{activeCategory:'shortwave'});
  await expect(frame.locator('html')).toHaveAttribute('data-radio-category','all');
  await expect(frame.locator('#sw-tuner-deck')).toBeHidden();
  await page.waitForTimeout(200);
  const painted=await frame.locator('body').evaluate(()=>(window as any).__paintedRadioSections);
  expect(painted.length).toBeGreaterThan(0);expect([...new Set(painted)]).toEqual(['all']);
});

for(const kind of ['radio','tv'] as const)test(`${kind} expanded artwork uses width and keeps title near controls`,async({page})=>{
  const frame=kind==='tv'?await seededTv(page):await openPlayer(page,'radio');
  if(kind==='tv')await frame.getByRole('button',{name:'Play Channel 02',exact:true}).first().click();
  else await frame.locator('#spotify-app .solid-sp-miniplayer').click();
  for(const width of [320,393,600]){
    await page.setViewportSize({width,height:844});
    const geometry=await frame.locator('body').evaluate((_,kind)=>{
      const box=(s:string)=>document.querySelector(s)!.getBoundingClientRect();
      const art=box(kind==='radio'?'.radio-mobile-art':'#video-surface-card');
      const title=box(kind==='radio'?'#sheet-title':'#tm-now-title');
      const controls=box(kind==='radio'?'.radio-np-controls':'.tm-now-controls');
      return {width:art.width,artTitleGap:title.top-art.bottom,titleControlsGap:controls.top-title.bottom};
    },kind);
    expect(geometry.width).toBeGreaterThanOrEqual(Math.min(width-50,844-380));
    expect(geometry.artTitleGap).toBeGreaterThanOrEqual(0);
    expect(geometry.artTitleGap).toBeLessThan(80);
    expect(geometry.titleControlsGap).toBeLessThan(130);
  }
});


async function dragSheet(page:Page,handle:any,dy:number){
  const box=await handle.boundingBox();expect(box).not.toBeNull();
  const x=box!.x+box!.width/2,y=box!.y+box!.height/2;
  const cdp=await page.context().newCDPSession(page);
  await cdp.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x,y}]});
  for(let n=1;n<=6;n++)await cdp.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x,y:y+dy*n/6}]});
  await cdp.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});
  await cdp.detach();
}
for(const kind of ['radio','tv'] as const)test(`${kind} Wind down follows Light theme and touch drag expands then dismisses`,async({page})=>{
  const frame=await openPlayer(page,kind);
  if(kind==='radio')await frame.locator('#spotify-app .solid-sp-miniplayer').click();
  else await frame.locator('.tm-quick button').first().click();
  await frame.locator('html').evaluate(el=>el.setAttribute('data-color-scheme','light'));
  await frame.getByRole('button',{name:'Set sleep timer',exact:true}).first().click();
  const sheet=frame.getByRole('dialog',{name:'Wind down'});await expect(sheet).toBeVisible();
  expect(await sheet.evaluate(el=>getComputedStyle(el).backgroundColor)).toBe('rgb(248, 250, 252)');
  const width=(await sheet.boundingBox())!.width;await page.waitForTimeout(300);expect((await sheet.boundingBox())!.width).toBeCloseTo(width,0);
  await dragSheet(page,sheet.locator('.media-comfort-handle'),-90);
  await expect(sheet).toHaveAttribute('data-sheet-expanded','true');
  await dragSheet(page,sheet.locator('.media-comfort-handle'),125);await expect(sheet).toBeHidden();
});

test('Radio equalizer is a stable native sheet and swipes closed',async({page})=>{
  const frame=await openPlayer(page,'radio');await frame.locator('#spotify-app .solid-sp-miniplayer').click();
  await frame.locator('html').evaluate(el=>el.setAttribute('data-color-scheme','light'));
  await frame.getByRole('button',{name:'Equalizer and audio settings',exact:true}).click();
  const sheet=frame.getByRole('dialog',{name:'Audio Quality & Equalizer',exact:true});await expect(sheet).toBeVisible();
  expect(await sheet.evaluate(el=>el.matches(':modal'))).toBe(true);
  const width=(await sheet.boundingBox())!.width;await page.waitForTimeout(350);expect((await sheet.boundingBox())!.width).toBeCloseTo(width,0);
  await sheet.locator('#eq-bass').fill('4');await expect(sheet.locator('#eq-bass')).toHaveValue('4');
  await dragSheet(page,sheet.locator('.media-sheet-handle'),125);await expect(sheet).toBeHidden();
  await expect(frame.locator('html')).not.toHaveClass(/radio-modal-open/);
});

for(const kind of ['radio','tv'] as const)test(`${kind} expanded recommendations require scrolling and glyph stays centered`,async({page})=>{
  const frame=kind==='tv'?await seededTv(page):await openPlayer(page,'radio');
  if(kind==='tv')await frame.getByRole('button',{name:'Play Channel 02',exact:true}).first().click();
  else await frame.locator('#spotify-app .solid-sp-miniplayer').click();
  const positions=await frame.locator('body').evaluate((_,kind)=>{
    const first=document.querySelector(kind==='radio'?'.rnp-more':'.tm-now-more')!.getBoundingClientRect();
    const play=document.querySelector(kind==='radio'?'#sheet-play':'.tm-big-play svg')!,button=play.closest('button')!;
    const a=play.getBoundingClientRect(),b=button.getBoundingClientRect();
    return {nextTop:first.top,height:innerHeight,dx:(a.left+a.width/2)-(b.left+b.width/2),dy:(a.top+a.height/2)-(b.top+b.height/2)};
  },kind);
  expect(positions.nextTop).toBeGreaterThanOrEqual(positions.height-1);
  expect(Math.abs(positions.dx)).toBeLessThanOrEqual(1);expect(Math.abs(positions.dy)).toBeLessThanOrEqual(1);
});

for(const kind of ['radio','tv'] as const)test(`${kind} scrolled player keeps live identity and playback reachable`,async({page})=>{
 const frame=kind==='tv'?await seededTv(page):await openPlayer(page,'radio');
 if(kind==='tv')await frame.getByRole('button',{name:'Play Channel 02',exact:true}).first().click();
 else await frame.locator('#spotify-app .solid-sp-miniplayer').click();
 const surface=frame.locator(kind==='radio'?'#mobile-player-sheet':'#tv-now-playing');
 const title=await frame.locator(kind==='radio'?'#sheet-title':'#tm-now-title').innerText();
 await expect(frame.getByRole('region',{name:kind==='radio'?'About this station':'About this channel'})).toBeAttached();
 await surface.evaluate(el=>el.scrollTo({top:innerHeight+150,behavior:'instant'}));
 const bar=frame.locator(kind==='radio'?'#radio-scroll-strip':'#tv-scroll-strip');
 await expect(bar).toHaveAttribute('data-visible','true');
 await expect(bar.locator('strong')).toHaveText(title);
 await expect(bar.getByRole('button',{name:/^(Play|Pause)/})).toBeVisible();
 await bar.locator('.media-scroll-copy').click();await expect.poll(()=>surface.evaluate(el=>el.scrollTop)).toBeLessThan(10);
});
