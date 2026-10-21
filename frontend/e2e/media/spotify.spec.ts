import {test,expect,type Page} from '@playwright/test';

async function openPlayer(page:Page,kind:'radio'|'tv'){
  // Exercise the real opaque-origin player without authentication or broker fixtures.
  await page.route('**/*',route=>{
    const url=new URL(route.request().url());
    return url.hostname==='127.0.0.1'||url.hostname==='localhost'?route.continue():route.abort();
  });
  await page.route('**/__media_test_host__',route=>route.fulfill({contentType:'text/html',body:`<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><style>body{margin:0}iframe{border:0;width:100vw;height:100dvh}</style><iframe id="player" src="/${kind}/index.html" sandbox="allow-scripts allow-forms" allow="autoplay"></iframe><script>
    const frame=document.getElementById('player');
    addEventListener('message',event=>{if(event.source!==frame.contentWindow||event.origin!=='null')return;if(event.data?.type==='sterling.${kind}.ready'){frame.contentWindow.postMessage({type:'sterling.${kind}.hydrate',hostHeader:false,values:${JSON.stringify(kind==='radio'?{omnicast_v11_config:JSON.stringify({bootBehavior:'quiet',activeCategory:'all'})}:{streamos_theme_v14:'spotify'})}},'*');frame.contentWindow.postMessage({type:'sterling.${kind}.ack'},'*');}});
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
  await input.press('ArrowDown');await page.keyboard.press('Enter');
  await expect(frame.locator('#sheet-title')).toHaveText('BBC World Service');
  await expect(frame.locator('.radio-search-suggestions')).toHaveCount(0);
});

test('Spotify TV categories and mobile widths keep the home experience separate',async({page})=>{
  const frame=await openPlayer(page,'tv');
  await expect(frame.locator('.tm-quick button')).toHaveCount(8);
  await frame.locator('.tm-mix[data-tm-category="News"]').click();
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
});
