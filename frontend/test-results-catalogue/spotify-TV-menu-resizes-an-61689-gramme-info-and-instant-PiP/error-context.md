# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: spotify.spec.ts >> TV menu resizes and player exposes honest programme info and instant PiP
- Location: e2e/media/spotify.spec.ts:187:1

# Error details

```
Error: expect(locator).toBeVisible() failed

Locator: locator('#player').contentFrame().locator('#tv-mobile-shell')
Expected: visible
Timeout: 5000ms
Error: element(s) not found

Call log:
  - Expect "toBeVisible" with timeout 5000ms
  - waiting for locator('#player').contentFrame().locator('#tv-mobile-shell')

```

```yaml
- strong: 127.0.0.1
- text: refused to connect.
```

# Test source

```ts
  1   | import {readFileSync} from 'node:fs';
  2   | import {resolve} from 'node:path';
  3   | import {test,expect,type Page} from '@playwright/test';
  4   | 
  5   | async function openPlayer(page:Page,kind:'radio'|'tv',radioConfig:Record<string,unknown>={}){
  6   |   // Exercise the real opaque-origin player without authentication or broker fixtures.
  7   |   await page.route('**/*',route=>{
  8   |     const url=new URL(route.request().url());
  9   |     return url.hostname==='127.0.0.1'||url.hostname==='localhost'?route.continue():route.abort();
  10  |   });
  11  |   await page.route('**/__media_test_host__',route=>route.fulfill({contentType:'text/html',body:`<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><style>body{margin:0}iframe{border:0;width:100vw;height:100dvh}</style><iframe id="player" src="/${kind}/index.html" sandbox="allow-scripts allow-forms" allow="autoplay; fullscreen; picture-in-picture" allowfullscreen></iframe><script>
  12  |     const frame=document.getElementById('player');
  13  |     addEventListener('message',event=>{if(event.source!==frame.contentWindow||event.origin!=='null')return;if(event.data?.type==='sterling.${kind}.ready'){frame.contentWindow.postMessage({type:'sterling.${kind}.hydrate',hostHeader:false,values:${JSON.stringify(kind==='radio'?{omnicast_v11_config:JSON.stringify({bootBehavior:'quiet',activeCategory:'all',catalogSources:[],...radioConfig})}:{streamos_theme_v14:'spotify'})}},'*');frame.contentWindow.postMessage({type:'sterling.${kind}.ack'},'*');}});
  14  |   </script>`}));
  15  |   await page.goto('/__media_test_host__');
  16  |   const frame=page.frameLocator('#player');
> 17  |   await expect(frame.locator(kind==='radio'?'html[data-radio-ready="true"] #sp-mchip-all':'#tv-mobile-shell')).toBeVisible();
      |                                                                                                                ^ Error: expect(locator).toBeVisible() failed
  18  |   return frame;
  19  | }
  20  | 
  21  | test('Radio categories suppress home shelves and SW/AM is a separate shared player',async({page})=>{
  22  |   const errors:string[]=[];page.on('pageerror',error=>errors.push(error.message));
  23  |   const frame=await openPlayer(page,'radio');
  24  |   await expect(frame.locator('#sp-recents-section')).toBeVisible();
  25  |   await frame.locator('#sp-mchip-te').click();
  26  |   for(const id of ['radio-home-quick','sp-recents-section','sp-suggested-section','sp-collections'])await expect(frame.locator('#'+id)).toBeHidden();
  27  |   await expect(frame.locator('#sp-grid-heading')).toHaveText('Telugu Radio Channels');
  28  |   await frame.locator('#sp-mchip-sw').click();await frame.getByRole('button',{name:'Open radio tuner',exact:true}).click();
  29  |   const receiver=frame.getByRole('dialog',{name:'Shortwave and AM player'});await expect(receiver).toBeVisible();
  30  |   await expect(receiver.getByRole('slider')).toBeVisible();
  31  |   const presets=receiver.locator('.sw-player-presets button');
  32  |   const firstFrequency=await presets.nth(0).locator('b').innerText();
  33  |   const nextFrequency=await presets.nth(1).locator('b').innerText();
  34  |   await presets.first().click();
  35  |   await expect(receiver.locator('.sw-player-frequency')).toHaveText(firstFrequency);
  36  |   await receiver.getByRole('button',{name:'Next shortwave station'}).click();
  37  |   await expect(receiver.locator('.sw-player-frequency')).toHaveText(nextFrequency);
  38  |   await receiver.press('Escape');await expect(receiver).toBeHidden();
  39  |   await frame.locator('#sp-mchip-all').click();await expect(frame.locator('#sp-recents-section')).toBeVisible();
  40  |   expect(errors).toEqual([]);
  41  | });
  42  | 
  43  | test('Radio standalone suggestions select a station and restore its collection',async({page})=>{
  44  |   const frame=await openPlayer(page,'radio');
  45  |   await frame.locator('#spotify-app nav [data-radio-click="63"]').click();
  46  |   await frame.locator('.sps-search-box').click();
  47  |   const input=frame.locator('.sps-search-field:visible').first();await input.fill('BBC');
  48  |   const suggestion=frame.locator('.sps-row').filter({hasText:'BBC World Service'}).first();await expect(suggestion).toBeVisible();
  49  |   await suggestion.focus();
  50  |   await page.waitForTimeout(450); // Cross the debounced results refresh without losing keyboard selection.
  51  |   await expect(suggestion).toBeFocused();
  52  |   await suggestion.press('Enter');
  53  |   await expect(frame.locator('#sheet-title')).toHaveText('BBC World Service');
  54  |   await expect(frame.locator('.radio-search-suggestions')).toHaveCount(0);
  55  | });
  56  | 
  57  | test('Spotify TV categories and mobile widths keep the home experience separate',async({page})=>{
  58  |   const frame=await openPlayer(page,'tv');
  59  |   await expect(frame.locator('.tm-quick button')).toHaveCount(8);
  60  |   await frame.locator('#tv-mobile-home .media-browse-grid [data-tm-category="News"]').click();
  61  |   await expect(frame.locator('#tv-mobile-home')).toBeHidden();
  62  |   await expect(frame.locator('.tm-feed-heading')).toHaveText('News');
  63  |   await frame.locator('#tv-mobile-nav [data-tm-action="home"]').click();
  64  |   await expect(frame.locator('#tv-mobile-home')).toBeVisible();
  65  |   for(const width of [320,390,430,768]){
  66  |     await page.setViewportSize({width,height:844});
  67  |     expect(await frame.locator('html').evaluate(el=>el.scrollWidth<=el.clientWidth)).toBe(true);
  68  |   }
  69  | });
  70  | 
  71  | test('Radio plays a controlled audio stream and preserves user pause',async({page})=>{
  72  |   const frame=await openPlayer(page,'radio');
  73  |   const samples=8000,wave=Buffer.alloc(44+samples*2);
  74  |   wave.write('RIFF');wave.writeUInt32LE(wave.length-8,4);wave.write('WAVEfmt ',8);wave.writeUInt32LE(16,16);wave.writeUInt16LE(1,20);wave.writeUInt16LE(1,22);wave.writeUInt32LE(8000,24);wave.writeUInt32LE(16000,28);wave.writeUInt16LE(2,32);wave.writeUInt16LE(16,34);wave.write('data',36);wave.writeUInt32LE(samples*2,40);
  75  |   await page.route('https://radio-fixture.test/tone.wav',route=>route.fulfill({contentType:'audio/wav',body:wave,headers:{'access-control-allow-origin':'*'}}));
  76  |   await frame.locator('body').evaluate(()=>{
  77  |     const button=document.createElement('button');button.textContent='Play controlled station';
  78  |     button.style.cssText='position:fixed;top:0;left:0;z-index:9999';
  79  |     button.onclick=()=>{const audio=document.getElementById('audio-engine') as HTMLAudioElement;audio.loop=true;(window as any).startPlayback({id:'controlled',title:'Controlled station',subtitle:'Test stream',kind:'radio',category:'telugu',streamUrl:'https://radio-fixture.test/tone.wav',backupUrls:[]});};
  80  |     document.body.append(button);
  81  |   });
  82  |   await frame.getByRole('button',{name:'Play controlled station'}).click();
  83  |   await expect.poll(()=>frame.locator('#audio-engine').evaluate((el:HTMLAudioElement)=>!el.paused&&el.readyState>=2&&!el.muted)).toBe(true);
  84  |   await frame.locator('#spotify-app .solid-sp-miniplayer [data-radio-click="56"]').click();
  85  |   await expect.poll(()=>frame.locator('#audio-engine').evaluate((el:HTMLAudioElement)=>el.paused)).toBe(true);
  86  |   await page.waitForTimeout(3800); // Cross the background-resume guard interval.
  87  |   expect(await frame.locator('#audio-engine').evaluate((el:HTMLAudioElement)=>el.paused)).toBe(true);
  88  |   await frame.locator('#spotify-app .solid-sp-miniplayer [data-radio-click="56"]').click();
  89  |   await expect.poll(()=>frame.locator('#audio-engine').evaluate((el:HTMLAudioElement)=>!el.paused)).toBe(true);
  90  |   await frame.locator('#spotify-app .solid-sp-miniplayer').click();
  91  |   await frame.locator('html').evaluate(el=>el.setAttribute('data-color-scheme','light'));
  92  |   const sheetPlay=frame.locator('#sheet-play');
  93  |   await expect(sheetPlay.locator('svg path')).toHaveAttribute('d','M6 5h4v14H6zm8 0h4v14h-4z');
  94  |   await expect(sheetPlay.locator('..')).toHaveAttribute('aria-label','Pause radio');
  95  |   const contrast=await sheetPlay.evaluate(el=>{const luminance=(color:string)=>{const values=color.match(/[\d.]+/g)!.slice(0,3).map(Number).map(v=>{v/=255;return v<=.04045?v/12.92:((v+.055)/1.055)**2.4;});return .2126*values[0]+.7152*values[1]+.0722*values[2];};const a=luminance(getComputedStyle(el).color),b=luminance(getComputedStyle(el.closest('button')!).backgroundColor);return (Math.max(a,b)+.05)/(Math.min(a,b)+.05);});
  96  |   expect(contrast).toBeGreaterThanOrEqual(4.5);
  97  |   await sheetPlay.locator('..').click();
  98  |   await expect(sheetPlay.locator('svg path')).toHaveAttribute('d','M8 5v14l11-7z');
  99  |   await expect(sheetPlay.locator('..')).toHaveAttribute('aria-label','Play radio');
  100 |   await expect.poll(()=>frame.locator('#audio-engine').evaluate((el:HTMLAudioElement)=>el.paused)).toBe(true);
  101 |   await sheetPlay.locator('..').click();
  102 |   await expect(sheetPlay.locator('svg path')).toHaveAttribute('d','M6 5h4v14H6zm8 0h4v14h-4z');
  103 |   await page.clock.install();
  104 |   await frame.getByRole('button',{name:'Set sleep timer',exact:true}).first().click();
  105 |   await frame.getByRole('dialog',{name:'Wind down'}).getByRole('button',{name:'15 minutes',exact:true}).click();
  106 |   await page.clock.fastForward(15*60*1000+1000);
  107 |   await expect.poll(()=>frame.locator('#audio-engine').evaluate((el:HTMLAudioElement)=>el.paused)).toBe(true);
  108 |   await page.clock.fastForward(4000);
  109 |   expect(await frame.locator('#audio-engine').evaluate((el:HTMLAudioElement)=>el.paused)).toBe(true);
  110 | });
  111 | 
  112 | for(const kind of ['radio','tv'] as const){
  113 |  test(`${kind} sleep timer is accessible, cancellable and pauses on expiry`,async({page},testInfo)=>{
  114 |   const frame=await openPlayer(page,kind);
  115 |   if(kind==='radio')await frame.locator('#spotify-app .solid-sp-miniplayer').click();
  116 |   else {
  117 |    await frame.locator('.tm-quick button').first().click();
```