import {readFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {test,expect,type Page} from '@playwright/test';

async function openPlayer(page:Page,kind:'radio'|'tv'){
  // Exercise the real opaque-origin player without authentication or broker fixtures.
  await page.route('**/*',route=>{
    const url=new URL(route.request().url());
    return url.hostname==='127.0.0.1'||url.hostname==='localhost'?route.continue():route.abort();
  });
  await page.route('**/__media_test_host__',route=>route.fulfill({contentType:'text/html',body:`<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><style>body{margin:0}iframe{border:0;width:100vw;height:100dvh}</style><iframe id="player" src="/${kind}/index.html" sandbox="allow-scripts allow-forms" allow="autoplay"></iframe><script>
    const frame=document.getElementById('player');
    addEventListener('message',event=>{if(event.source!==frame.contentWindow||event.origin!=='null')return;if(event.data?.type==='sterling.${kind}.ready'){frame.contentWindow.postMessage({type:'sterling.${kind}.hydrate',hostHeader:false,values:${JSON.stringify(kind==='radio'?{omnicast_v11_config:JSON.stringify({bootBehavior:'quiet',activeCategory:'all',catalogSources:[]})}:{streamos_theme_v14:'spotify'})}},'*');frame.contentWindow.postMessage({type:'sterling.${kind}.ack'},'*');}});
  </script>`}));
  await page.goto('/__media_test_host__');
  const frame=page.frameLocator('#player');
  await expect(frame.locator(kind==='radio'?'#sp-mchip-all':'#tv-mobile-shell')).toBeVisible();
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
  await receiver.locator('.sw-player-presets button').first().click();
  await expect(receiver.locator('.sw-player-frequency')).toHaveText('738 kHz');
  await receiver.getByRole('button',{name:'Next shortwave station'}).click();
  await expect(receiver.locator('.sw-player-frequency')).toHaveText('820 kHz');
  await receiver.press('Escape');await expect(receiver).toBeHidden();
  await frame.locator('#sp-mchip-all').click();await expect(frame.locator('#sp-recents-section')).toBeVisible();
  expect(errors).toEqual([]);
});

test('Radio standalone suggestions select a station and restore its collection',async({page})=>{
  const frame=await openPlayer(page,'radio');
  await frame.locator('#spotify-app nav [data-radio-click="63"]').click();
  const input=frame.locator('#sp-search-mobile');await input.fill('BBC');
  const suggestion=frame.getByRole('option',{name:/BBC World Service/});await expect(suggestion).toBeVisible();
  await input.press('ArrowDown');
  await expect(suggestion).toBeFocused();
  await page.waitForTimeout(450); // Cross the debounced results refresh without losing keyboard selection.
  await expect(suggestion).toBeFocused();
  await page.keyboard.press('Enter');
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
 await frame.locator('#yt-studio-grid .channel-grid-card').nth(1).click();
 await expect(frame.locator('#tm-now-title')).toHaveText('Channel 02');
 await frame.locator('#tv-now-playing [data-tm-action="queue"]').click();
 await expect(frame.locator('.tm-queue-current strong')).toHaveText('Channel 02');
 await expect(frame.locator('#tm-queue-items button').first()).toContainText('Channel 03');
 await frame.getByRole('button',{name:'Close queue',exact:true}).click();
 await frame.locator('#tv-now-playing [data-tm-action="next"]').click();
 await expect(frame.locator('#tm-now-title')).toHaveText('Channel 03');
 await frame.locator('#tv-now-playing [data-tm-action="previous"]').click();
 await expect(frame.locator('#tm-now-title')).toHaveText('Channel 02');
 await frame.locator('#tv-now-playing [data-tm-action="queue"]').click();
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
 await frame.locator('#yt-studio-grid .channel-grid-card').first().click();
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
 await frame.locator('#yt-studio-grid .channel-grid-card').first().click();
 await expect(frame.locator('#custom-video-hud')).toBeHidden();
 await expect(frame.locator('#tv-now-playing [data-tm-action=play]')).toBeVisible();
 await page.setViewportSize({width:844,height:390});
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
 await frame.locator('#yt-studio-grid .channel-grid-card').first().click();
 const more=frame.locator('#tv-now-playing [data-tm-action=more]');await more.click();
 await frame.locator('#tv-mobile-sheet [data-tm-action=filters]').click();
 await frame.locator('#tv-mobile-sheet [data-tm-action=close-sheet]').click();
 await expect(more).toBeFocused();
});

test('comfort sheet padding is not treated as a backdrop dismissal',async({page})=>{
 const frame=await seededTv(page);
 await frame.locator('#yt-studio-grid .channel-grid-card').first().click();
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
 const baseline=await frame.locator('body').evaluate(()=>eval('MASTER_POOL.length'));
 await page.route('https://radio-directory.fixture/**',route=>{
  const url=new URL(route.request().url());
  if(url.pathname==='/custom.m3u')return route.fulfill({contentType:'text/plain',headers:{'access-control-allow-origin':'*'},body:'#EXTM3U\n#EXTINF:-1 group-title="Jazz" tvg-language="Telugu",My jazz station\nhttps://station.fixture/my-jazz.mp3'});
  const offset=Number(url.searchParams.get('offset')||0),length=offset?50:1000;
  return route.fulfill({contentType:'application/json',headers:{'access-control-allow-origin':'*'},body:JSON.stringify(Array.from({length},(_,i)=>({stationuuid:`fixture-${offset+i}`,name:`World fixture ${offset+i}`,url_resolved:`https://station.fixture/${offset+i}.mp3`,country:i%2?'India':'France',language:i%2?'telugu':'french',tags:i%2?'news,talk':'jazz',favicon:''})))});
 });
 await frame.locator('body').evaluate(()=>window.dispatchEvent(new MessageEvent('message',{source:window.parent,data:{type:'sterling.radio.hydrate',hostHeader:false,values:{omnicast_v11_config:JSON.stringify({bootBehavior:'quiet',activeCategory:'all',catalogSources:[{id:'fixture',name:'World fixture directory',url:'https://radio-directory.fixture/stations',kind:'directory',enabled:true}]})}}})));
 await expect.poll(()=>frame.locator('body').evaluate(()=>eval('radioCatalog.snapshot().complete'))).toBe(true);
 return {frame,baseline};
}

test('Radio exposes all loaded stations, matching category counts and append-only pagination',async({page})=>{
 const {frame,baseline}=await loadRadioDirectoryFixture(page);
 await expect(frame.locator('#sp-grid-count')).toHaveText(`${(baseline+1050).toLocaleString()} channels`);
 await expect(frame.locator('#sp-mchip-all')).toHaveText(`All ${(baseline+1050).toLocaleString()}`);
 await page.screenshot({path:'/tmp/sterling-radio-spotify-home.png'});
 expect(await frame.locator('#sp-cards-grid>[data-radio-click]').count()).toBeLessThanOrEqual(60);
 await frame.locator('#sp-cards-grid>[data-radio-click]').first().evaluate(element=>element.setAttribute('data-preserved','yes'));
 await frame.locator('#sp-cards-grid').locator('..').getByRole('button',{name:/Show more stations/}).click();
 expect(await frame.locator('#sp-cards-grid>[data-radio-click]').count()).toBeGreaterThan(60);
 await expect(frame.locator('#sp-cards-grid>[data-radio-click]').first()).toHaveAttribute('data-preserved','yes');
 const expected=await frame.locator('body').evaluate(()=>eval('radioDirectoryBase("telugu").length'));
 await frame.locator('#sp-mchip-te').click();
 await expect(frame.locator('#sp-grid-count')).toHaveText(`${expected.toLocaleString()} channels`);
 await expect(frame.locator('#radio-directory-browse')).toBeHidden();await expect(frame.locator('#sp-recents-section')).toBeHidden();
 expect(await frame.locator('body').evaluate(()=>eval('catalogItems.every(station=>station.category==="telugu"||(station.languages||[]).includes("telugu"))'))).toBe(true);
 await frame.locator('#sp-mchip-all').click();
 await frame.locator('#spotify-app .radio-directory-filters summary').click();
 await frame.getByRole('combobox',{name:'Radio country',exact:true}).selectOption('France');
 await expect(frame.locator('#sp-grid-count')).toHaveText('525 channels');
 await frame.getByRole('combobox',{name:'Radio genre',exact:true}).selectOption('jazz');
 await expect(frame.locator('#sp-grid-count')).toHaveText('525 channels');
 await expect(frame.locator('#sp-mchip-all')).toHaveText('All 525');
 await page.screenshot({path:'/tmp/sterling-radio-directory-filtered.png'});
});

test('Radio source manager adds, edits, disables and deletes URL feeds without changing playback',async({page})=>{
 const {frame,baseline}=await loadRadioDirectoryFixture(page);
 await frame.locator('#spotify-app').getByRole('button',{name:'Manage sources',exact:true}).click();
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
 const verified=await frame.locator('body').evaluate(()=>eval('allChannels.filter(channel=>channel.verified&&isChannelVisible(channel)).length'));
 const shelf=frame.locator('#tv-mobile-home section').filter({has:frame.locator('[data-tm-category="verified"]')});
 await expect(shelf.locator('.media-shelf-count')).toHaveText(`${verified} channels`);
 await shelf.getByRole('button',{name:'Show all',exact:true}).click();
 expect(await frame.locator('body').evaluate(()=>eval('getFilteredChannelPool().length'))).toBe(verified);
});

test('standalone Radio keeps edited source URLs across a reload',async({page})=>{
 await page.route('**/*',route=>new URL(route.request().url()).hostname==='127.0.0.1'?route.continue():route.abort());
 const html=readFileSync(resolve(process.cwd(),'../sterling-radio/sterling-radio.html'),'utf8');
 await page.route('**/__standalone_radio__',route=>route.fulfill({contentType:'text/html',body:html}));
 await page.addInitScript(()=>{if(!localStorage.getItem('omnicast_v11_config'))localStorage.setItem('omnicast_v11_config',JSON.stringify({bootBehavior:'quiet',appMode:'spotify',catalogSources:[]}));});
 await page.goto('/__standalone_radio__');
 await page.locator('#spotify-app').getByRole('button',{name:'Manage sources',exact:true}).click();
 const dialog=page.getByRole('dialog',{name:'Radio sources'});
 await dialog.getByLabel('Source name',{exact:true}).fill('My direct station');
 await dialog.getByLabel('Source URL',{exact:true}).fill('https://fixture.test/personal.mp3');
 await dialog.getByLabel('Format',{exact:true}).selectOption('stream');
 await dialog.getByRole('button',{name:'Save source',exact:true}).click();
 await expect(dialog.getByRole('button',{name:'Edit My direct station',exact:true})).toBeVisible();
 expect(await page.evaluate(()=>JSON.parse(localStorage.getItem('omnicast_v11_config')||'{}').catalogSources[0].kind)).toBe('stream');
 await page.reload();await page.locator('#spotify-app').getByRole('button',{name:'Manage sources',exact:true}).click();
 await expect(dialog.getByRole('button',{name:'Edit My direct station',exact:true})).toBeVisible();
});
