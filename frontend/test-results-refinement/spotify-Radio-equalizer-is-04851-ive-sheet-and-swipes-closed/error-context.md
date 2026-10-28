# Instructions

- Following Playwright test failed.
- Explain why, be concise, respect Playwright best practices.
- Provide a snippet of code with the fix, if possible.

# Test info

- Name: spotify.spec.ts >> Radio equalizer is a stable native sheet and swipes closed
- Location: e2e/media/spotify.spec.ts:628:1

# Error details

```
Error: expect(locator).toBeHidden() failed

Locator:  locator('#player').contentFrame().getByRole('dialog', { name: 'Audio Quality & Equalizer', exact: true })
Expected: hidden
Received: visible
Timeout:  5000ms

Call log:
  - Expect "toBeHidden" with timeout 5000ms
  - waiting for locator('#player').contentFrame().getByRole('dialog', { name: 'Audio Quality & Equalizer', exact: true })
    14 × locator resolved to <dialog open="" id="audio-dsp-modal" class="media-settings-dialog" aria-label="Audio Quality & Equalizer">…</dialog>
       - unexpected value "visible"

```

```yaml
- dialog "Audio Quality & Equalizer":
  - heading "Audio Quality & Equalizer" [level=3]
  - button "Pin playing station as startup": 
  - button "Close audio settings": 
  - text: Streaming quality
  - button "Very High"
  - button "High"
  - button "Data Saver"
  - text: Presets
  - button "Flat"
  - button "Bass Boost"
  - button "Spoken Word"
  - button "Acoustic"
  - button "AM Voice"
  - button "Classical"
  - text: Bass
  - slider: "4"
  - text: +4dB Midrange
  - slider: "1"
  - text: +1dB Treble
  - slider: "2"
  - text: +2dB
  - button "Done"
```

# Test source

```ts
  536 |   await frame.getByRole('button',{name:'Play Channel 02',exact:true}).first().click();
  537 |   await expect(frame.locator('#tv-now-playing')).toBeVisible();
  538 |   const before=await frame.locator('#tm-now-title').innerText();
  539 |   await frame.getByRole('button',{name:'Surprise channel',exact:true}).click();
  540 |   await expect(frame.locator('#tm-now-title')).not.toHaveText(before);
  541 |   await expect(frame.getByText('Watching on this device',{exact:true})).toHaveCount(0);
  542 |   for(const width of [320,393,844]){
  543 |     await page.setViewportSize({width,height:width===844?393:844});
  544 |     for(const theme of ['light','dark']){
  545 |       await frame.locator('html').evaluate((el,scheme)=>el.setAttribute('data-color-scheme',scheme),theme);
  546 |       const geometry=await frame.locator('#video-surface-card').evaluate(el=>{
  547 |         const video=el.querySelector('video')!,card=el.getBoundingClientRect(),picture=video.getBoundingClientRect();
  548 |         return {gap:Math.max(Math.abs(card.x-picture.x),Math.abs(card.y-picture.y),Math.abs(card.width-picture.width),Math.abs(card.height-picture.height)),clip:getComputedStyle(el).overflow,border:getComputedStyle(el).borderTopWidth,radius:getComputedStyle(el).borderTopLeftRadius,videoRadius:getComputedStyle(video).borderTopLeftRadius};
  549 |       });
  550 |       expect(geometry.gap).toBeLessThanOrEqual(1);
  551 |       expect(geometry.clip).toBe('hidden');expect(geometry.border).toBe('0px');
  552 |       expect(geometry.videoRadius).toBe(geometry.radius);
  553 |       await page.screenshot({path:testInfo.outputPath(`tv-corners-${width}-${theme}.png`)});
  554 |     }
  555 |   }
  556 | });
  557 | 
  558 | test('Radio expanded player replaces device copy with Surprise station',async({page})=>{
  559 |   const frame=await openPlayer(page,'radio');
  560 |   await frame.locator('#spotify-app .solid-sp-miniplayer').click();
  561 |   await expect(frame.locator('#mobile-player-sheet')).toBeVisible();
  562 |   await expect(frame.getByText('Listening on this device',{exact:true})).toHaveCount(0);
  563 |   const before=await frame.locator('#sheet-title').innerText();
  564 |   await frame.getByRole('button',{name:'Surprise station',exact:true}).click();
  565 |   await expect(frame.locator('#sheet-title')).not.toHaveText(before);
  566 | });
  567 | 
  568 | 
  569 | test('Radio first visible frame is Home even with a saved SW/AM section',async({page})=>{
  570 |   await page.addInitScript(()=>{
  571 |     if(window===window.top)return;
  572 |     const painted:string[]=[];(window as any).__paintedRadioSections=painted;
  573 |     const sample=()=>{if(document.body&&getComputedStyle(document.body).visibility==='visible')painted.push(document.documentElement.dataset.radioCategory||'unset');if(painted.length<60)requestAnimationFrame(sample);};
  574 |     requestAnimationFrame(sample);
  575 |   });
  576 |   const frame=await openPlayer(page,'radio',{activeCategory:'shortwave'});
  577 |   await expect(frame.locator('html')).toHaveAttribute('data-radio-category','all');
  578 |   await expect(frame.locator('#sw-tuner-deck')).toBeHidden();
  579 |   await page.waitForTimeout(200);
  580 |   const painted=await frame.locator('body').evaluate(()=>(window as any).__paintedRadioSections);
  581 |   expect(painted.length).toBeGreaterThan(0);expect([...new Set(painted)]).toEqual(['all']);
  582 | });
  583 | 
  584 | for(const kind of ['radio','tv'] as const)test(`${kind} expanded artwork uses width and keeps title near controls`,async({page})=>{
  585 |   const frame=kind==='tv'?await seededTv(page):await openPlayer(page,'radio');
  586 |   if(kind==='tv')await frame.getByRole('button',{name:'Play Channel 02',exact:true}).first().click();
  587 |   else await frame.locator('#spotify-app .solid-sp-miniplayer').click();
  588 |   for(const width of [320,393,600]){
  589 |     await page.setViewportSize({width,height:844});
  590 |     const geometry=await frame.locator('body').evaluate((_,kind)=>{
  591 |       const box=(s:string)=>document.querySelector(s)!.getBoundingClientRect();
  592 |       const art=box(kind==='radio'?'.radio-mobile-art':'#video-surface-card');
  593 |       const title=box(kind==='radio'?'#sheet-title':'#tm-now-title');
  594 |       const controls=box(kind==='radio'?'.radio-np-controls':'.tm-now-controls');
  595 |       return {width:art.width,artTitleGap:title.top-art.bottom,titleControlsGap:controls.top-title.bottom};
  596 |     },kind);
  597 |     expect(geometry.width).toBeGreaterThanOrEqual(Math.min(width-50,500));
  598 |     expect(geometry.artTitleGap).toBeGreaterThanOrEqual(0);
  599 |     expect(geometry.artTitleGap).toBeLessThan(80);
  600 |     expect(geometry.titleControlsGap).toBeLessThan(130);
  601 |   }
  602 | });
  603 | 
  604 | 
  605 | async function dragSheet(page:Page,handle:any,dy:number){
  606 |   const box=await handle.boundingBox();expect(box).not.toBeNull();
  607 |   const x=box!.x+box!.width/2,y=box!.y+box!.height/2;
  608 |   const cdp=await page.context().newCDPSession(page);
  609 |   await cdp.send('Input.dispatchTouchEvent',{type:'touchStart',touchPoints:[{x,y}]});
  610 |   for(let n=1;n<=6;n++)await cdp.send('Input.dispatchTouchEvent',{type:'touchMove',touchPoints:[{x,y:y+dy*n/6}]});
  611 |   await cdp.send('Input.dispatchTouchEvent',{type:'touchEnd',touchPoints:[]});
  612 |   await cdp.detach();
  613 | }
  614 | for(const kind of ['radio','tv'] as const)test(`${kind} Wind down follows Light theme and touch drag expands then dismisses`,async({page})=>{
  615 |   const frame=await openPlayer(page,kind);
  616 |   if(kind==='radio')await frame.locator('#spotify-app .solid-sp-miniplayer').click();
  617 |   else await frame.locator('.tm-quick button').first().click();
  618 |   await frame.locator('html').evaluate(el=>el.setAttribute('data-color-scheme','light'));
  619 |   await frame.getByRole('button',{name:'Set sleep timer',exact:true}).first().click();
  620 |   const sheet=frame.getByRole('dialog',{name:'Wind down'});await expect(sheet).toBeVisible();
  621 |   expect(await sheet.evaluate(el=>getComputedStyle(el).backgroundColor)).toBe('rgb(248, 250, 252)');
  622 |   const width=(await sheet.boundingBox())!.width;await page.waitForTimeout(300);expect((await sheet.boundingBox())!.width).toBeCloseTo(width,0);
  623 |   await dragSheet(page,sheet.locator('.media-comfort-handle'),-90);
  624 |   await expect(sheet).toHaveAttribute('data-sheet-expanded','true');
  625 |   await dragSheet(page,sheet.locator('.media-comfort-handle'),125);await expect(sheet).toBeHidden();
  626 | });
  627 | 
  628 | test('Radio equalizer is a stable native sheet and swipes closed',async({page})=>{
  629 |   const frame=await openPlayer(page,'radio');await frame.locator('#spotify-app .solid-sp-miniplayer').click();
  630 |   await frame.locator('html').evaluate(el=>el.setAttribute('data-color-scheme','light'));
  631 |   await frame.getByRole('button',{name:'Equalizer and audio settings',exact:true}).click();
  632 |   const sheet=frame.getByRole('dialog',{name:'Audio Quality & Equalizer',exact:true});await expect(sheet).toBeVisible();
  633 |   expect(await sheet.evaluate(el=>el.matches(':modal'))).toBe(true);
  634 |   const width=(await sheet.boundingBox())!.width;await page.waitForTimeout(350);expect((await sheet.boundingBox())!.width).toBeCloseTo(width,0);
  635 |   await sheet.locator('#eq-bass').fill('4');await expect(sheet.locator('#eq-bass')).toHaveValue('4');
> 636 |   await dragSheet(page,sheet.locator('.media-sheet-handle'),125);await expect(sheet).toBeHidden();
      |                                                                                      ^ Error: expect(locator).toBeHidden() failed
  637 |   await expect(frame.locator('html')).not.toHaveClass(/radio-modal-open/);
  638 | });
  639 | 
  640 | for(const kind of ['radio','tv'] as const)test(`${kind} expanded recommendations require scrolling and glyph stays centered`,async({page})=>{
  641 |   const frame=kind==='tv'?await seededTv(page):await openPlayer(page,'radio');
  642 |   if(kind==='tv')await frame.getByRole('button',{name:'Play Channel 02',exact:true}).first().click();
  643 |   else await frame.locator('#spotify-app .solid-sp-miniplayer').click();
  644 |   const positions=await frame.locator('body').evaluate((_,kind)=>{
  645 |     const first=document.querySelector(kind==='radio'?'.rnp-more':'.tm-now-more')!.getBoundingClientRect();
  646 |     const play=document.querySelector(kind==='radio'?'#sheet-play':'.tm-big-play svg')!,button=play.closest('button')!;
  647 |     const a=play.getBoundingClientRect(),b=button.getBoundingClientRect();
  648 |     return {nextTop:first.top,height:innerHeight,dx:(a.left+a.width/2)-(b.left+b.width/2),dy:(a.top+a.height/2)-(b.top+b.height/2)};
  649 |   },kind);
  650 |   expect(positions.nextTop).toBeGreaterThanOrEqual(positions.height-1);
  651 |   expect(Math.abs(positions.dx)).toBeLessThanOrEqual(1);expect(Math.abs(positions.dy)).toBeLessThanOrEqual(1);
  652 | });
  653 | 
  654 | for(const kind of ['radio','tv'] as const)test(`${kind} scrolled player keeps live identity and playback reachable`,async({page})=>{
  655 |  const frame=kind==='tv'?await seededTv(page):await openPlayer(page,'radio');
  656 |  if(kind==='tv')await frame.getByRole('button',{name:'Play Channel 02',exact:true}).first().click();
  657 |  else await frame.locator('#spotify-app .solid-sp-miniplayer').click();
  658 |  const surface=frame.locator(kind==='radio'?'#mobile-player-sheet':'#tv-now-playing');
  659 |  const title=await frame.locator(kind==='radio'?'#sheet-title':'#tm-now-title').innerText();
  660 |  await expect(frame.getByRole('region',{name:kind==='radio'?'About this station':'About this channel'})).toBeAttached();
  661 |  await surface.evaluate(el=>el.scrollTo({top:innerHeight+150,behavior:'instant'}));
  662 |  const bar=frame.locator(kind==='radio'?'#radio-scroll-strip':'#tv-scroll-strip');
  663 |  await expect(bar).toHaveAttribute('data-visible','true');
  664 |  await expect(bar.locator('strong')).toHaveText(title);
  665 |  await expect(bar.getByRole('button',{name:/^(Play|Pause)/})).toBeVisible();
  666 |  await bar.locator('.media-scroll-copy').click();await expect.poll(()=>surface.evaluate(el=>el.scrollTop)).toBeLessThan(10);
  667 | });
  668 | 
```