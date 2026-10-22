// Mobile UI adapter: share the existing player, settings, catalog and history.
(() => {
  const mobile = matchMedia('(max-width:767px), (max-width:1024px) and (max-height:600px)');
  const icons = {
    home:'M3 10 12 3l9 7v11h-6v-7H9v7H3z', search:'M21 21l-5-5M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0',
    library:'M4 3v18M9 3v18M15 3l6 17M13 3h3', play:'m9 5 11 7-11 7z', pause:'M6 5h4v14H6zM14 5h4v14h-4z',
    next:'m5 5 11 7-11 7zM19 5v14', prev:'m19 5-11 7 11 7zM5 5v14', plus:'M12 5v14M5 12h14',
    check:'m5 12 4 4L19 6', down:'m6 9 6 6 6-6', close:'m6 6 12 12M6 18 18 6', more:'M5 12h.01M12 12h.01M19 12h.01',
    pip:'M3 4h18v16H3zM11 8h7v6h-7z',info:'M12 8h.01M11 11h1v6m0-15a10 10 0 1 0 0 20 10 10 0 0 0 0-20',
    queue:'M4 5h16M4 11h16M4 17h9m4-2 4 3-4 3z', screen:'M3 4h18v13H3zM8 21h8M12 17v4',
    settings:'M4 7h16M4 17h16M8 4v6M16 14v6', share:'m14 3 7 7-7 7M21 10H10a7 7 0 0 0-7 7v4',
    globe:'M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0M3 12h18M12 3c5 5 5 13 0 18-5-5-5-13 0-18',
    volume:'M11 5 6 9H3v6h3l5 4zM15 8c3 2 3 6 0 8M18 5c5 4 5 10 0 14', muted:'M11 5 6 9H3v6h3l5 4zM16 9l6 6M16 15l6-6',
    clock:'M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0M12 7v5l3 2', bell:'M18 8a6 6 0 0 0-12 0c0 8-3 8-3 10h18c0-2-3-2-3-10M10 21h4',
    youtube:'M22 8c0-3-1-4-4-4H6C3 4 2 5 2 8v8c0 3 1 4 4 4h12c3 0 4-1 4-4zM10 8l6 4-6 4z',
    spotify:'M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0M6 9c4-2 9-1 12 1M7 12c4-1 7-1 10 1M8 15c3-1 5-1 8 1'
  };
  const icon = name => `<svg aria-hidden="true" viewBox="0 0 24 24" fill="${['play','pause'].includes(name)?'currentColor':'none'}" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="${icons[name] || icons.screen}"/></svg>`;
  const action = (name,label,glyph,cls='tm-icon') => `<button type="button" class="${cls}" data-tm-action="${name}" aria-label="${label}" title="${label}">${icon(glyph)}</button>`;
  const shell = document.createElement('div');
  shell.id='tv-mobile-shell';
  shell.innerHTML=`<div class="tm-top"><button class="tm-avatar" data-tm-action="more" aria-label="TV settings and features">S</button><div class="tm-brand"><span>Sterling</span></div><div class="tm-tabs"><button data-tm-action="home" aria-pressed="true">All</button><button data-tm-action="telugu">Telugu</button><button data-tm-action="live">Browse</button><button data-tm-action="library">Following</button></div><div class="tm-theme" aria-label="TV appearance"><button data-tm-action="youtube" aria-label="YouTube theme"><span>YT</span></button><button data-tm-action="spotify" aria-label="Spotify theme">${icon('spotify')}</button></div>${action('search','Search channels','search','tm-icon tm-search-open')}${action('more','More TV features','more')}</div><div class="tm-search" hidden><input id="tm-search-input" type="search" placeholder="What do you want to watch?" aria-label="Search all TV channels" autocomplete="off"/>${action('close-search','Close search','close')}</div><div class="tm-context"><button data-tm-action="home" aria-pressed="true">All</button><button data-tm-action="live">Live</button><button data-tm-action="indian">Indian TV</button><button data-tm-action="news">News</button><button data-tm-action="sports">Sports</button><button data-tm-action="history">Recently watched</button></div>`;
  document.getElementById('app-workspace').before(shell);
  const home=document.createElement('section'); home.id='tv-mobile-home'; home.setAttribute('aria-label','TV home');
  document.getElementById('main-scroll').prepend(home);
  const browse=document.createElement('section');browse.id='tm-browse-categories';browse.setAttribute('aria-label','Browse all TV categories');document.getElementById('catalog-section').prepend(browse);
  const nav=document.createElement('nav');nav.id='tv-mobile-nav';nav.setAttribute('aria-label','Mobile TV navigation');
  nav.innerHTML=action('home','Home','home','tm-nav-item')+action('search','Search','search','tm-nav-item')+action('library','Your library','library','tm-nav-item')+action('more','More','more','tm-nav-item');
  const labels=['Home','Search','Your Library','More'];
  [...nav.children].forEach((el,i)=>el.insertAdjacentHTML('beforeend',`<span>${labels[i]}</span>`));
  const mini=document.createElement('div');mini.id='tv-mobile-mini';mini.setAttribute('aria-label','Now playing');
  mini.innerHTML=`<button class="tm-mini-open" data-tm-action="expand" aria-label="Open Now Playing"><img alt=""/><span class="tm-mini-copy"><strong></strong><small class="tm-live-dot">Live</small></span></button>${action('save','Save channel to your library','plus')}${action('play','Play or pause live TV','play')}<div class="tm-mini-live" aria-hidden="true"></div>`;
  const now=document.createElement('section');now.id='tv-now-playing';now.setAttribute('aria-label','Now Playing');now.setAttribute('role','dialog');now.setAttribute('aria-modal','true');now.hidden=true;
  now.innerHTML=`<div class="tm-now-main"><div class="tm-now-head">${action('collapse','Close Now Playing','down')}<div><small>PLAYING FROM</small><strong id="tm-now-group">Your television, everywhere</strong></div>${action('more','Now Playing options','more')}</div><div class="tm-now-spacer" aria-hidden="true"></div><div class="tm-now-copy"><div><h2 id="tm-now-title"></h2><p id="tm-now-subtitle"></p></div>${action('save','Save channel to your library','plus')}</div><div class="tm-timeline"><div class="tm-timeline-track" aria-hidden="true"></div><div class="tm-timeline-label"><span>Live broadcast</span><span class="tm-live-dot">LIVE</span></div></div><div class="tm-now-controls">${action('mute','Mute or unmute TV','volume')}${action('previous','Previous channel','prev')}${action('play','Play or pause live TV','play','tm-big-play')}${action('next','Next channel','next')}${action('pip','Picture in picture','pip')}</div><div class="tm-now-footer"><span>Watching on this device</span><div>${action('epg','Channel information and programme guide','info')}${action('queue','Open channel queue','queue')}</div></div></div><section class="tm-now-more" aria-label="Up next"><div class="tm-now-more-head"><h3>Up next</h3><button data-tm-action="queue">Open queue</button></div><div id="tm-now-next"></div></section><section class="tm-now-more" aria-labelledby="tm-now-related-title"><div class="tm-now-more-head"><h3 id="tm-now-related-title">More like this</h3></div><div id="tm-now-related"></div><button class="tm-now-show-more" data-tm-action="related-more" hidden>Show more</button></section>`;
  const sheet=document.createElement('div');sheet.id='tv-mobile-sheet';sheet.setAttribute('role','dialog');sheet.setAttribute('aria-label','TV features');sheet.setAttribute('aria-modal','true');
  document.body.append(nav,mini,now,sheet);
  let shellPage=null,tvRows=null,tvRowsKey='',tvListView=false;
  let view='home',expanded=false,scheduled=false,returnFocus=null,lastLandscape=innerWidth>innerHeight,hosted=false,hydrated=window.parent===window,lastState='',lastSaved='',pipDismissed=false,tvBackdropMode='auto',lastPublishedPlayerState=null;
  const activeTheme=()=>['spotify','youtube','netflix'].includes(currentTheme);
  const isMobile=()=>mobile.matches && activeTheme();
  const artworkCache=new Map();
  const rejectedArtwork=new Set();
  function artwork(ch) {
    const remote=window.SterlingArtwork?.resolveTv(ch);
    if(remote&&!rejectedArtwork.has(remote))return remote;
    if(!rejectedArtwork.has(ch.logo)&&ch.logo?.startsWith('https://') && !ch.logo.includes('unsplash.com') && !/[<>"'\x00-\x20]/.test(ch.logo))return ch.logo;
    if(artworkCache.has(ch.url))return artworkCache.get(ch.url);
    const hue=[...ch.name].reduce((n,c)=>n+c.charCodeAt(0)*7,0)%360;
    const initials=ch.name.split(/\s+/).slice(0,2).map(w=>w[0]).join('');
    const pattern=hue%3===0?'<circle cx="265" cy="70" r="135" stroke-width="45"/><circle cx="265" cy="70" r="190" stroke-width="2"/>':hue%3===1?'<path d="M30 90v140m44-90v140m44-160v180m44-220v190m44-100v140m44-180v150m44-100v120" stroke-width="22"/>':'<path d="m200 30 150 100-100 160-150-100z" fill="#fff" stroke-width="0"/><path d="M0 260 320 90M0 290 320 120" stroke-width="3"/>';
    const svg=`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 320 320"><defs><linearGradient id="a" x2="1" y2="1"><stop stop-color="hsl(${hue},55%,48%)"/><stop offset="1" stop-color="hsl(${hue},60%,17%)"/></linearGradient></defs><path fill="url(#a)" d="M0 0h320v320H0z"/><g fill="none" stroke="#ffffff" opacity=".12">${pattern}</g><text x="22" y="38" fill="white" font-family="Arial,sans-serif" font-size="13" font-weight="700" letter-spacing="2">${escapeHtml((ch.group||'LIVE TV').toUpperCase().slice(0,24))}</text><text x="18" y="207" fill="white" font-family="Arial,sans-serif" font-size="112" font-weight="900" letter-spacing="-9">${escapeHtml(initials)}</text><rect x="22" y="248" width="36" height="5" fill="#1ed760"/><text x="22" y="284" fill="white" font-family="Arial,sans-serif" font-size="19" font-weight="700">${escapeHtml(ch.name.slice(0,24))}</text></svg>`;
    const uri='data:image/svg+xml;utf8,'+encodeURIComponent(svg);artworkCache.set(ch.url,uri);if(artworkCache.size>128)artworkCache.delete(artworkCache.keys().next().value);return uri;
  }
  function applyBackdrop() {
    document.body.dataset.tvBackdrop=tvBackdropMode;
    const name=activeChannel?.name||'Sterling';
    const hue=[...name].reduce((n,ch)=>n+ch.charCodeAt(0)*7,0)%360;
    document.body.style.setProperty('--tv-channel-hue',String(hue));
  }
  const channelButton=(ch,quick=false)=>`<button type="button" class="${quick?'':'tm-album'}" data-tm-channel="${escapeAttr(ch.url)}" aria-label="Watch ${escapeAttr(ch.name)}">${quick?`<img src="${escapeAttr(artwork(ch))}" alt="" loading="lazy"/><span>${escapeHtml(ch.name)}</span>`:`<div class="tm-cover"><img src="${escapeAttr(artwork(ch))}" alt="" loading="lazy"/><span class="tm-live">LIVE TV</span></div><strong>${escapeHtml(ch.name)}</strong><small>${escapeHtml(ch.group || 'Television')} · ${escapeHtml(ch.lang || 'Global')}</small>`}</button>`;
  function shelf(title,pool,filter) {
    pool=pool.filter(isChannelVisible);
    if(!pool.length)return '';
    return `<section><div class="tm-section-title"><h2>${title}<small class="media-shelf-count">${pool.length.toLocaleString()} ${filter==='all'?(pool.length===1?'pick':'picks'):(pool.length===1?'channel':'channels')}</small></h2><button data-tm-category="${filter}">${filter==='all'?'Browse all':'Show all'}</button></div><div class="tm-shelf">${pool.slice(0,10).map(ch=>channelButton(ch)).join('')}</div></section>`;
  }
  let lastHomeCatalog=null,lastHomeKey='';
  function renderHome() {
    const key=JSON.stringify([activeChannel?.url,recentHistory.slice(0,8).map(ch=>ch.url),favorites.map(ch=>ch.url),hideUnavailableChannels,[...unavailableChannels]]);
    if(lastHomeCatalog===allChannels&&key===lastHomeKey)return;
    lastHomeCatalog=allChannels;lastHomeKey=key;

    const categories=['News','Sports','Movies','Music','Documentary','Entertainment','Kids & Anime','Devotional'];
    const groups=new Map(categories.map(category=>[category,allChannels.filter(channel=>channel.group===category&&isChannelVisible(channel))]));
    const categoryTiles=categories.map(category=>{const pool=groups.get(category),sample=pool[0];return `<button type="button" data-tm-category="${escapeAttr(category)}"><strong>${escapeHtml(category)}</strong><small>${pool.length.toLocaleString()} ${pool.length===1?'channel':'channels'}</small>${sample?`<img src="${escapeAttr(artwork(sample))}" alt="" loading="lazy" decoding="async">`:''}</button>`;}).join('');
    browse.classList.add('media-browse-grid');browse.innerHTML=categoryTiles;
    const verifiedPool=allChannels.filter(channel=>channel.verified&&isChannelVisible(channel));
    const recent=(recentHistory.length?recentHistory:verifiedPool.length?verifiedPool:allChannels).filter(isChannelVisible);
    const quick=[],quickSeen=new Set();for(const channel of [...recent,...allChannels]){if(!isChannelVisible(channel)||quickSeen.has(channel.url))continue;quickSeen.add(channel.url);quick.push(channel);if(quick.length===8)break;}
    const seed=activeChannel||recentHistory[0];
    const savedUrls=new Set(favorites.map(ch=>ch.url)),ranked=[];
    for(const ch of allChannels){
      if(ch.url===seed?.url||!isChannelVisible(ch))continue;
      const score=(seed&&ch.group===seed.group?5:0)+(seed&&ch.lang===seed.lang?3:0)+(savedUrls.has(ch.url)?2:0)+(ch.verified?1:0);
      const index=ranked.findIndex(item=>score>item.score);
      if(index>=0)ranked.splice(index,0,{ch,score});else if(ranked.length<10)ranked.push({ch,score});
      if(ranked.length>10)ranked.pop();
    }
    const recommendations=ranked.map(item=>item.ch);
    const html=`<div class="tm-quick">${quick.map(ch=>channelButton(ch,true)).join('')}</div>${shelf(recentHistory.length?'Jump back in':'Start watching',recent,recentHistory.length?'recent':verifiedPool.length?'verified':'all')}${shelf(seed?'Recommended for you':'Made for your screen',recommendations,'all')}<section><div class="tm-section-title"><h2>Find your next favourite</h2></div><div class="media-browse-grid">${categoryTiles}</div></section>${shelf('Live from India',allChannels.filter(ch=>ch.isIndian||INDIAN_LANG_IDS.includes(ch.lang)),'indian')}${shelf('Your saved channels',favorites,'favs')}${['News','Movies','Sports','Music','Documentary','Kids & Anime','Devotional'].map(cat=>shelf(cat,allChannels.filter(ch=>ch.group===cat),cat)).join('')}`;
    // Patch section by section: unchanged rows keep their DOM and loaded logos, so a catalog refresh
    // or a channel change does not flash the whole page.
    const next=document.createElement('div');next.innerHTML=html;const fresh=[...next.children],current=[...home.children];
    fresh.forEach((node,index)=>{const old=current[index];if(old&&old.outerHTML===node.outerHTML)return;if(old)old.replaceWith(node);else home.append(node);});
    for(const extra of current.slice(fresh.length))extra.remove();
  }
  function setPlayerText(element,text){if(element.textContent!==text)element.textContent=text;}
  function setPlayerGlyph(button,name){if(button.dataset.glyph!==name){button.innerHTML=icon(name);button.dataset.glyph=name;}}
  function sync() {
    scheduled=false;
    const enabled=isMobile();
    document.body.dataset.mediaView=view;
    document.body.classList.toggle('tm-host-header',hosted);
    applyBackdrop();
    publishPlayerState();
    publishState();
    saveUi();
    const stage=document.getElementById('player-stage');
    if(stage)stage.inert=enabled&&currentTheme==='spotify'&&!expanded;
    home.inert=shell.inert=enabled&&expanded&&currentTheme==='spotify';
    if(lastLandscape!==(innerWidth>innerHeight)){lastLandscape=innerWidth>innerHeight;renderHome();}
    document.body.classList.toggle('tm-catalog-view',enabled&&view!=='home'&&view!=='watch');
    document.body.classList.toggle('tm-browse-search',enabled&&currentTheme==='spotify'&&view==='search'&&!searchInputEl.value.trim());
    document.body.classList.toggle('tm-search-active',Boolean(searchInputEl.value.trim()));
    document.body.classList.toggle('tm-watch-view',enabled&&view==='watch');
    document.body.classList.toggle('tm-now-open',enabled&&expanded&&currentTheme==='spotify');
    now.hidden=!(enabled&&expanded&&currentTheme==='spotify');
    if(!enabled) { now.hidden=true; return; }
    shell.querySelectorAll('.tm-theme button').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.tmAction===currentTheme)));
    nav.querySelectorAll('button').forEach(b=>{const selected=b.dataset.tmAction===view;b.toggleAttribute('aria-current',selected);if(selected)b.setAttribute('aria-current','page');});markShellNav();
    shell.querySelectorAll('.tm-tabs button,.tm-context button').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.tmAction===view)));
    if(activeChannel) {
      const source=artwork(activeChannel),miniArt=mini.querySelector('img');if(miniArt.getAttribute('src')!==source)miniArt.src=source;
      setPlayerText(mini.querySelector('strong'),activeChannel.name);
      setPlayerText(document.getElementById('tm-now-title'),activeChannel.name);
      setPlayerText(document.getElementById('tm-now-subtitle'),`${activeChannel.group||'Channel'} · ${activeChannel.lang||'Global'}`);
      setPlayerText(document.getElementById('tm-now-group'),activeChannel.group||'Your live channels');
      if(expanded&&!now.hidden)renderNowMore();
      const saved=favorites.some(c=>c.url===activeChannel.url);
      [mini,now].forEach(el=>{const b=el.querySelector('[data-tm-action="save"]');setPlayerGlyph(b,saved?'check':'plus');b.setAttribute('aria-label',saved?'Remove channel from your library':'Save channel to your library');b.setAttribute('aria-pressed',String(saved));});
    }
    nav.querySelector('[data-tm-action="library"] span').textContent=currentTheme==='youtube'?'Subscriptions':'Your Library';
    nav.querySelector('[data-tm-action="more"] span').textContent=currentTheme==='youtube'?'You':currentTheme==='spotify'?'Create':'More';
    {const more=nav.querySelector('[data-tm-action="more"]'),glyph=currentTheme==='spotify'?'plus':'more';if(more.dataset.glyph!==glyph){more.querySelector('svg')?.remove();more.insertAdjacentHTML('afterbegin',icon(glyph));more.dataset.glyph=glyph;more.setAttribute('aria-label',currentTheme==='spotify'?'Create: add a playlist source':'More');}}
    const player=document.getElementById('video-player');
    const paused=player.classList.contains('hidden')?tvUserPaused:player.paused;
    const mute=now.querySelector('[data-tm-action="mute"]');
    const muted=document.getElementById('video-player').muted;
    setPlayerGlyph(mute,muted?'muted':'volume');mute.setAttribute('aria-label',muted?'Unmute TV':'Mute TV');
    [mini,now].forEach(el=>{const b=el.querySelector('[data-tm-action="play"]');setPlayerGlyph(b,paused?'play':'pause');b.setAttribute('aria-label',paused?'Play live TV':'Pause live TV');});
    if(typeof renderTvRows==='function')renderTvRows();
  }
  // Below the player, like Spotify: the next few queued channels, then every channel in the same category
  // (same language first). Picking one plays that category in order, so Next keeps following it.
  let relatedFor='',relatedPool=[],relatedShown=0,nextKey='';
  const relatedRow=(ch,attr)=>`<button data-tm-channel="${escapeAttr(ch.url)}" ${attr}><img src="${escapeAttr(artwork(ch))}" alt="" loading="lazy" decoding="async"><span><strong>${escapeHtml(ch.name)}</strong><small>${escapeHtml(ch.group||'Channel')} · ${escapeHtml(ch.lang||'Global')}</small></span></button>`;
  function renderNowMore(){
    if(!activeChannel||now.hidden||typeof getUpcomingPlaybackQueue!=='function')return;
    const upcoming=getUpcomingPlaybackQueue().slice(0,5),key=activeChannel.url+'|'+upcoming.map(c=>c.url).join('|');
    if(key!==nextKey){nextKey=key;document.getElementById('tm-now-next').innerHTML=upcoming.map(ch=>relatedRow(ch,'data-tm-queued="true"')).join('');}
    if(relatedFor===activeChannel.url)return;relatedFor=activeChannel.url;
    const group=(activeChannel.group||'').toLowerCase(),lang=activeChannel.lang;
    const same=allChannels.filter(c=>c.url!==activeChannel.url&&isChannelVisible(c)&&(c.group||'').toLowerCase()===group);
    relatedPool=[...same.filter(c=>c.lang===lang),...same.filter(c=>c.lang!==lang)];relatedShown=0;
    document.getElementById('tm-now-related-title').textContent=`More ${activeChannel.group||'live TV'} · ${relatedPool.length.toLocaleString()} ${relatedPool.length===1?'channel':'channels'}`;
    document.getElementById('tm-now-related').innerHTML='';appendRelated();
  }
  function appendRelated(){
    const batch=relatedPool.slice(relatedShown,relatedShown+30);relatedShown+=batch.length;
    document.getElementById('tm-now-related').insertAdjacentHTML('beforeend',batch.map(ch=>relatedRow(ch,'data-tm-related="true"')).join(''));
    now.querySelector('[data-tm-action="related-more"]').hidden=relatedShown>=relatedPool.length;
  }
  now.addEventListener('scroll',()=>{document.body.style.setProperty('--tm-now-scroll',`${now.scrollTop}px`);if(now.scrollTop+now.clientHeight>now.scrollHeight-200&&relatedShown<relatedPool.length)appendRelated();},{passive:true});
  function schedule() { if(!scheduled){scheduled=true;requestAnimationFrame(sync);} }
  let sheetReturnFocus=null;
  let sheetNav=null;
  function closeSheet() { const open=sheet.classList.contains('tm-sheet-open');sheet.classList.remove('tm-sheet-open');sheetReturnFocus?.focus();if(open&&sheetNav&&!tvNav?.isRestoring())tvNav?.drop(sheetNav);sheetNav=null; }
  function setExpanded(value) {
    if(!value){now.scrollTop=0;document.body.style.setProperty('--tm-now-scroll','0px');}
    expanded=value;sync();
    if(value){returnFocus=document.activeElement;now.querySelector('button').focus();}
    else mini.querySelector('button').focus();
  }
  function showSheet(kind='more') {
    if(!sheet.classList.contains('tm-sheet-open'))sheetNav=tvNav?.push(()=>closeSheet())||null;
    if(!sheet.classList.contains('tm-sheet-open'))sheetReturnFocus=document.activeElement;
    let title='Your TV, your way';
    let items=[['sources','Playlist sources','library'],['startup','Default startup channel','screen'],['indian','Indian TV & languages','globe'],['filters','Channel availability & filters','search'],['queue','Playing queue','queue'],['epg','Channel information & programme guide','info'],['equalizer','Sound equalizer','settings'],['sleep','Sleep timer','clock'],['backdrop','Player background: black / auto','screen'],['quality','Quality, captions & fit','screen'],['share','Share current channel','share'],['pip','Picture in picture','screen'],['multiview','Four-channel multiview','screen'],['extras','Extras: Surprise me, backup, shortcuts','settings'],['tools','All TV tools & themes','settings']];
    if(['filters','equalizer','quality','epg'].includes(kind)) {
      title={filters:'Languages & categories',equalizer:'Sound equalizer',quality:'Playback settings',epg:'Programme guide'}[kind];
      let content='';
      if(kind==='filters') content=`<label class="tm-filter-label">Channel availability<select data-tm-filter="availability"><option value="all" ${!hideUnavailableChannels?'selected':''}>All channels, including unavailable</option><option value="available" ${hideUnavailableChannels?'selected':''}>Hide unavailable channels</option></select></label><p class="tm-feature-note">Hides channels that failed playback on this account for 3 hours. Untested channels remain visible. You can reset failures and retry anytime.</p><button type="button" data-tm-action="retry-unavailable" class="tm-feature">Retry unavailable channels now</button><label class="tm-filter-label">Language<select data-tm-filter="language">${document.getElementById('catalog-lang-select').innerHTML}</select></label><label class="tm-filter-label">Category<select data-tm-filter="category">${document.getElementById('catalog-genre-select').innerHTML}</select></label>`;
      if(kind==='equalizer') content=Object.entries(EQ_PRESETS).map(([key,preset])=>`<button data-tm-eq="${key}">${icon('settings')}<span>${escapeHtml(preset.label)}</span></button>`).join('');
      if(kind==='quality') content=`${action('fit','Change video fit','screen','tm-feature').replace('</button>','<span>Fit / Fill / Zoom</span></button>')}<button data-tm-quality="-1">${icon('check')}<span>Automatic quality</span></button>${(hls?.levels||[]).map((level,i)=>`<button data-tm-quality="${i}">${icon('screen')}<span>${level.height||'Auto'}p · ${Math.round((level.bitrate||0)/1000)} kbps</span></button>`).join('')}<p class="tm-feature-note">${hasNativeHls(document.getElementById('video-player'))&&!hls?'Your browser manages stream quality automatically.':'Quality options depend on the current broadcast.'}</p>`;
      if(kind==='epg') content=activeChannel?`<div class="tm-queue-current"><strong>${escapeHtml(activeChannel.name)}</strong><small>${escapeHtml(activeChannel.group||'Television')} · ${escapeHtml(activeChannel.lang||'Global')}</small></div><p class="tm-feature-note">Live channel information. Programme listings appear only when supplied by the broadcaster.</p>${getChannelEpgSchedule(activeChannel).map(item=>`<div class="tm-guide-row"><strong>${escapeHtml(item.title||'Live broadcast')}</strong><small>${escapeHtml(item.time||item.start||'Channel')}</small></div>`).join('')}`:'<p>Select a channel to view its guide.</p>';
      sheet.innerHTML=`<div class="tm-sheet-content"><div class="tm-sheet-handle"></div><div class="tm-sheet-title"><h2>${title}</h2>${action('close-sheet','Close TV features','close')}</div>${content}</div>`;
    } else if(kind==='queue') {
      title='Up next';
      const pool=getUpcomingPlaybackQueue();
      sheet.innerHTML=`<div class="tm-sheet-content"><div class="tm-sheet-handle"></div><div class="tm-sheet-title"><h2>${title}</h2>${action('close-sheet','Close queue','close')}</div>${activeChannel?`<div class="tm-queue-current"><small>PLAYING NOW</small><strong>${escapeHtml(activeChannel.name)}</strong></div>`:''}<p class="tm-feature-note">${pool.length.toLocaleString()} channels · Next follows this order</p><div id="tm-queue-items"></div><button data-tm-action="queue-more">Show more channels</button></div>`;
      let offset=0;
      const appendQueue=()=>{
        const batch=pool.slice(offset,offset+50);offset+=batch.length;
        document.getElementById('tm-queue-items').insertAdjacentHTML('beforeend',batch.map(ch=>`<button data-tm-channel="${escapeAttr(ch.url)}" data-tm-queued="true">${icon('screen')}<span>${escapeHtml(ch.name)}<small>${escapeHtml(ch.group||'Channel')} · ${escapeHtml(ch.lang||'Global')}</small></span></button>`).join(''));
        sheet.querySelector('[data-tm-action="queue-more"]').hidden=offset>=pool.length;
      };
      sheet.querySelector('[data-tm-action="queue-more"]').addEventListener('click',appendQueue);appendQueue();
    } else {
      sheet.innerHTML=`<div class="tm-sheet-content"><div class="tm-sheet-handle"></div><div class="tm-sheet-title"><h2>${title}</h2>${action('close-sheet','Close TV features','close')}</div>${items.map(([name,label,glyph])=>action(name,label,glyph,'tm-feature').replace('</button>',`<span>${label}</span></button>`)).join('')}</div>`;
    }
    sheet.querySelector('.tm-sheet-title').insertAdjacentHTML('beforeend',action('resize-sheet','Expand menu','screen'));
    const handle=sheet.querySelector('.tm-sheet-handle');
    handle.tabIndex=0;handle.setAttribute('role','slider');handle.setAttribute('aria-label','Menu height');handle.setAttribute('aria-valuemin','40');handle.setAttribute('aria-valuemax','95');
    const resize=height=>{const value=Math.max(40,Math.min(95,height));sheet.style.setProperty('--tm-sheet-height',value+'dvh');handle.setAttribute('aria-valuenow',String(Math.round(value)));};
    resize(72);
    let drag=null;
    handle.addEventListener('pointerdown',e=>{drag={y:e.clientY,height:sheet.querySelector('.tm-sheet-content').getBoundingClientRect().height};handle.setPointerCapture(e.pointerId);});
    handle.addEventListener('pointermove',e=>{if(drag)resize((drag.height+drag.y-e.clientY)/innerHeight*100);});
    ['pointerup','pointercancel','lostpointercapture'].forEach(name=>handle.addEventListener(name,()=>{drag=null;}));
    handle.addEventListener('keydown',e=>{if(['ArrowUp','ArrowDown','Home','End'].includes(e.key)){e.preventDefault();resize(e.key==='Home'?40:e.key==='End'?95:Number(handle.getAttribute('aria-valuenow'))+(e.key==='ArrowUp'?10:-10));}});
    sheet.classList.add('tm-sheet-open');sheet.querySelector('button')?.focus();
  }
  // Filter icon at the end of the category chips, as in Radio; it opens TV's filters sheet.
  {const pills=document.getElementById('dynamic-category-pills-row');if(pills&&!document.getElementById('tm-filter-btn')){const b=document.createElement('button');b.id='tm-filter-btn';b.type='button';b.className='radio-filter-btn tm-filter-btn';b.dataset.tmAction='filters';b.setAttribute('aria-label','Filter channels');b.title='Filter channels';b.innerHTML='<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 5h18l-7 8.2V19l-4 2v-7.8z"/></svg>';pills.after(b);}}
  // Browse opens as Spotify rows, like Radio's sections (media/radio-home.js): Popular, Telugu, then a row
  // per category (or per language inside a category) and All channels. Show all, search and Following use the grid.
  const TV_CATEGORIES=['News','Movies','Entertainment','Music','Sports','Kids & Anime','Documentary','Devotional','Comedy','Business','Lifestyle & Food','Education & Public'];
  function tvRowsMode(){return isMobile()&&currentTheme==='spotify'&&view==='live'&&!tvListView&&!searchInputEl.value.trim()&&!['favs','recently_played'].includes(catalogScope);}
  function openTvList(apply){rememberTvPlace();apply?.();tvListView=true;renderCatalogControls();renderViewport();sync();document.getElementById('main-scroll').scrollTo({top:0,behavior:'instant'});}
  function buildTvRows(pool){
    const item=ch=>({kind:'station',key:ch.url,station:{id:ch.url,title:ch.name,country:[ch.group,ch.lang].filter(Boolean).join(' · '),ch}});
    const rows=[{id:'popular',title:catalogCategory==='All'?'Popular channels':`Popular ${catalogCategory}`,size:'large',items:[...pool].sort((a,b)=>(b.verified?1:0)-(a.verified?1:0)).slice(0,300).map(item),open:()=>openTvList()}];
    const telugu=pool.filter(c=>c.lang==='Telugu');
    if(telugu.length&&catalogLanguage!=='Telugu')rows.push({id:'telugu',title:'Telugu',items:telugu.map(item),open:()=>openTvList(()=>{catalogLanguage='Telugu';})});
    if(catalogCategory==='All'){for(const cat of TV_CATEGORIES){const list=pool.filter(c=>(c.group||'Entertainment')===cat);if(list.length>=3)rows.push({id:'cat-'+cat,title:cat,items:list.map(item),open:()=>openTvList(()=>{catalogCategory=cat;})});}}
    const langs=new Map();for(const c of pool)if(c.lang&&c.lang!=='Telugu'){langs.set(c.lang,(langs.get(c.lang)||0)+1);}
    for(const [lang,count] of [...langs].sort((a,b)=>b[1]-a[1]).slice(0,catalogCategory==='All'?4:8))if(count>=3&&catalogLanguage==='All')rows.push({id:'lang-'+lang,title:lang,items:pool.filter(c=>c.lang===lang).map(item),open:()=>openTvList(()=>{catalogLanguage=lang;})});
    rows.push({id:'everything',title:`All ${pool.length.toLocaleString()} channels`,items:pool.map(item),open:()=>openTvList()});
    return rows;
  }
  function renderTvRows(){
    const on=tvRowsMode()&&Boolean(window.SterlingRadioHome);document.body.classList.toggle('tm-rows-view',on);
    if(!on)return;
    const pool=computeFilteredCatalogPool(),key=[catalogScope,catalogCategory,catalogLanguage,pool.length,allChannels.length].join('|');
    if(!tvRows){const holder=document.createElement('div');holder.id='tv-rows-holder';document.getElementById('sticky-chip-bar')?.after(holder);
      tvRows=window.SterlingRadioHome.mount({container:holder,artwork:s=>artwork(s.ch),fallback:s=>{rejectedArtwork.add(artwork(s.ch));return artwork(s.ch);},onPlay:url=>{const ch=findChannel(url);if(ch){playStream(ch,0);if(currentTheme==='spotify')setExpanded(true);}}});}
    if(key===tvRowsKey)return;tvRowsKey=key;tvRows.render(buildTvRows(pool));
  }
  // Back: every place opened from here records how to return (media/nav-stack.js).
  const tvNav=window.SterlingNav?.create({kind:'tv',hosted:()=>hosted});
  function tvPlace(){
    return {view,list:tvListView,scope:catalogScope,category:catalogCategory,language:catalogLanguage,query:searchInputEl.value,shell:shellPage,scroll:document.getElementById('main-scroll').scrollTop};
  }
  function restoreTvPlace(place){
    view=place.view;tvListView=Boolean(place.list);tvRowsKey='';catalogScope=place.scope;catalogCategory=place.category;catalogLanguage=place.language;
    searchInputEl.value=place.query;const box=document.getElementById('tm-search-input');if(box)box.value=place.query;
    if(place.shell)tvShell?.show(place.shell);else{shellPage=null;tvShell?.hide();}
    if(view==='home')document.querySelector('.tm-feed-heading')?.remove();
    renderCatalogControls();renderViewport();sync();
    requestAnimationFrame(()=>document.getElementById('main-scroll').scrollTo({top:place.scroll,behavior:'instant'}));
  }
  function rememberTvPlace(){const place=tvPlace();tvNav?.push(()=>restoreTvPlace(place));}
  function catalog(next,scope='all',category='All') {
    rememberTvPlace();tvListView=false;
    view=next;expanded=false;catalogScope=scope;catalogCategory=category;catalogLanguage='All';
    searchInputEl.value='';document.getElementById('tm-search-input').value='';
    renderCatalogControls();renderViewport();sync();
    const heading=document.createElement('h2');heading.className='tm-feed-heading';heading.textContent=next==='library'?'Your Library':next==='search'?'Search':next==='history'?'Recently watched':category!=='All'?category:'Browse';
    document.querySelector('.tm-feed-heading')?.remove();if(currentTheme==='spotify'&&next!=='home')document.getElementById('catalog-section').prepend(heading);
    document.getElementById('main-scroll').scrollTo({top:0,behavior:'instant'});
  }
  function run(name) {
    switch(name) {
      case 'pip-return': pipDismissed=false;document.getElementById('main-scroll').scrollTo({top:0,behavior:'smooth'});break;
      case 'pip-dismiss': pipDismissed=true;setScrollPip(false);break;
      case 'netflix': case 'spotify': case 'youtube': expanded=false;switchTheme(name);renderHome();sync();break;
      case 'home': catalog('home');shell.querySelector('.tm-search').hidden=true;renderHome();break;
      case 'live': catalog('live','all');break;
      case 'telugu': catalog('live','all');catalogLanguage='Telugu';renderCatalogControls();renderViewport();sync();break;
      case 'library': catalog('library','favs');break;
      case 'history': catalog('history','recently_played');break;
      case 'news': catalog('news','all','News');break;
      case 'sports': catalog('sports','all','Sports');break;
      case 'indian': closeSheet();catalog('live','indian_hub');activateIndianSection();break;
      case 'search': if(hosted){window.parent.postMessage({type:'sterling.tv.focus-search'},'*');}else{catalog('search');shell.querySelector('.tm-search').hidden=false;document.getElementById('tm-search-input').focus();}break;
      case 'queue-more': break;
      case 'close-search': shell.querySelector('.tm-search').hidden=true;run('home');break;
      case 'expand': setExpanded(true);break;
      case 'collapse': setExpanded(false);break;
      case 'play': { const video=document.getElementById('video-player'); if(video.classList.contains('hidden'))togglePlayPause();else if(video.paused && video.readyState===0)resumeTvPlayback();else togglePlayPause();schedule();break; }
      case 'save': toggleSubscribeAndFavorite();renderHome();sync();break;
      case 'next': playNextChannel();schedule();break;
      case 'previous': playPrevChannel();schedule();break;
      case 'mute': toggleMuteAudio();break;
      case 'fullscreen': toggleFullscreenPlayer();break;
      case 'backdrop':
        tvBackdropMode=tvBackdropMode==='auto'?'black':'auto';
        applyBackdrop();saveUi();
        showToast(tvBackdropMode==='auto'?'Dynamic channel background':'Black player background');
        closeSheet();break;
      case 'share': copyStreamShareLink();break;
      case 'more': showSheet();break;
      case 'queue': showSheet('queue');break;
      case 'related-more': appendRelated();break;
      case 'retry-unavailable': retryUnavailableChannelsNow();closeSheet();break;
      case 'resize-sheet': { const handle=sheet.querySelector('.tm-sheet-handle');const height=Number(handle.getAttribute('aria-valuenow'))>=90?72:95;sheet.style.setProperty('--tm-sheet-height',height+'dvh');handle.setAttribute('aria-valuenow',String(height));const button=sheet.querySelector('[data-tm-action=resize-sheet]');button.setAttribute('aria-label',height===95?'Reduce menu':'Expand menu');break; }
      case 'close-sheet': closeSheet();break;
      case 'extras': closeSheet();window.tvExtras?.open();break;
      case 'tools': closeSheet();if(expanded)setExpanded(false);toggleSidebarDrawer(true);break;
      case 'sources': closeSheet();openSourceManagerModal();break;
      case 'startup': closeSheet();openStartupChannelModal();break;
      case 'sleep': cycleSleepTimer();break;
      case 'equalizer': showSheet('equalizer');break;
      case 'quality': showSheet('quality');break;
      case 'fit': cycleVideoFitMode();break;
      case 'pip': closeSheet();requestNativeBrowserPip();break;
      case 'multiview': closeSheet();if(currentTheme==='spotify')setExpanded(true);toggleMultiviewQuad();break;
      case 'filters': showSheet('filters');break;
      case 'epg': showSheet('epg');break;
    }
  }
  /* Spotify-style Search / Recents / Your Library shared with Radio (media/spotify-shell.js). */
  const SHELL_GROUPS=['News','Sports','Movies','Music','Entertainment','Documentary','Kids & Anime','Devotional','Lifestyle','Business','Religious','Comedy'];
  const shellItem=ch=>({id:ch.url,title:ch.name,subtitle:[ch.group,ch.lang].filter(Boolean).join(' · '),art:artwork(ch),type:'Channel'});
  const findChannel=url=>allChannels.find(c=>c.url===url)||recentHistory.find(c=>c.url===url)||favorites.find(c=>c.url===url);
  let shellCats={size:-1,list:[]};
  function markShellNav(){if(!shellPage)return;nav.querySelectorAll('button').forEach(b=>{const on=b.dataset.tmAction===shellPage;b.toggleAttribute('aria-current',on);if(on)b.setAttribute('aria-current','page');});}
  window.SterlingSpotifyShell?.pullToRefresh?.(document.getElementById('main-scroll'),()=>{
    if(!isMobile())return;lastHomeKey='';renderHome();if(typeof syncAllActivePlaylists==='function')return syncAllActivePlaylists(false);
  });
  /* Extras (media/extras.js): Surprise me, backup, keyboard help, screen wake lock, lock-screen controls. */
  const tvVideo=()=>document.getElementById('video-player');
  window.tvExtras=window.SterlingMediaExtras?.mount({kind:'tv',noun:'channel',storageKey:'streamos_extras_v14',
    storage:{get:key=>tvStorage.getItem(key),set:(key,value)=>tvStorage.setItem(key,value)},
    features:{wakeLock:true,mediaSession:true},
    isPlaying:()=>{const v=tvVideo();return Boolean(v&&!v.paused&&!v.classList.contains('hidden'));},
    toggle:()=>togglePlayPause(),next:()=>playNextChannel(),prev:()=>playPrevChannel(),like:()=>toggleSubscribeAndFavorite(),
    getVolume:()=>tvVideo()?.volume??1,setVolume:value=>{const v=tvVideo();if(v)v.volume=Math.max(0,Math.min(1,value));},
    current:()=>activeChannel?{id:activeChannel.url,title:activeChannel.name,subtitle:[activeChannel.group,activeChannel.lang].filter(Boolean).join(' · '),art:artwork(activeChannel)}:null,
    pool:()=>computeFilteredCatalogPool().map(c=>({id:c.url,title:c.name})),
    play:url=>{const ch=findChannel(url);if(ch)playStream(ch,0);},toast:message=>showToast(message),video:tvVideo,
    handledKeys:['t','z','c','m','f','p','a','e','/'],
    extraShortcuts:[['T','Theatre mode'],['Z','Video fit'],['C','Captions'],['M','Mute'],['F','Full screen'],['P','Picture in picture'],['A','Audio equalizer'],['E','Programme guide'],['/','Search']],
    exportData:()=>({favorites,history:recentHistory.slice(0,40),sources:iptvSources}),
    importData:data=>{let count=0;
      if(Array.isArray(data.favorites)){favorites.splice(0,favorites.length,...data.favorites.filter(c=>c&&typeof c.url==='string'&&typeof c.name==='string').slice(0,500));count+=favorites.length;try{tvStorage.setItem('streamos_favs_v14',JSON.stringify(favorites));}catch(_){}}
      if(Array.isArray(data.sources)&&data.sources.length){iptvSources=data.sources.filter(s=>s&&typeof s.url==='string').slice(0,40);count+=iptvSources.length;saveSourcesState();if(typeof syncAllActivePlaylists==='function')syncAllActivePlaylists(false);}
      if(typeof updateSubscribeLikeUI==='function')updateSubscribeLikeUI();lastHomeKey='';renderHome();return count;}
  });
  const tvShell=window.SterlingSpotifyShell?.mount({kind:'tv',noun:'channels',itemType:'Channel',likedTitle:'Subscribed Channels',placeholder:'What do you want to watch?',
    avatar:()=>'S',openSettings:()=>showSheet('more'),openExtras:()=>window.tvExtras?.open(),
    items:()=>allChannels.filter(isChannelVisible).map(shellItem),
    fallbackArt:url=>{const ch=findChannel(url);if(!ch)return '';for(let i=0;i<2;i++){const src=artwork(ch);if(src.startsWith('data:'))return src;rejectedArtwork.add(src);}return artwork(ch);},
    search:(q,limit)=>(typeof getRankedSearchMatches==='function'?getRankedSearchMatches(q,limit):allChannels.filter(c=>isChannelVisible(c)&&`${c.name} ${c.group||''}`.toLowerCase().includes(q)).slice(0,limit)).map(shellItem),
    recents:()=>recentHistory.filter(isChannelVisible).slice(0,40).map(shellItem),
    removeRecent:url=>{recentHistory=recentHistory.filter(c=>c.url!==url);try{tvStorage.setItem('streamos_history_v14',JSON.stringify(recentHistory));}catch(_){}},
    favorites:()=>favorites.map(shellItem),
    isFavorite:url=>favorites.some(c=>c.url===url),
    toggleFavorite:url=>{const ch=findChannel(url);if(!ch)return;const i=favorites.findIndex(c=>c.url===url);if(i>-1)favorites.splice(i,1);else favorites.unshift(ch);
      try{tvStorage.setItem('streamos_favs_v14',JSON.stringify(favorites));}catch(_){}if(typeof updateSubscribeLikeUI==='function')updateSubscribeLikeUI();},
    play:url=>{const ch=findChannel(url);if(!ch)return;tvShell.hide();playStream(ch,0);view='home';if(currentTheme==='spotify')setExpanded(true);sync();},
    categories:()=>{if(shellCats.size===allChannels.length)return shellCats.list;const by=new Map();for(const ch of allChannels){if(!isChannelVisible(ch)||!ch.group)continue;const g=by.get(ch.group)||{count:0,mosaic:[]};g.count++;if(g.mosaic.length<4)g.mosaic.push(artwork(ch));by.set(ch.group,g);}
      const names=[...SHELL_GROUPS.filter(n=>by.has(n)),...[...by.keys()].filter(n=>!SHELL_GROUPS.includes(n)).sort((x,y)=>by.get(y).count-by.get(x).count).slice(0,8)];
      shellCats={size:allChannels.length,list:names.map(n=>({key:n,label:n,count:by.get(n).count,art:by.get(n).mosaic[0],mosaic:by.get(n).mosaic,chip:'categories'}))};return shellCats.list;},
    openCategory:cat=>{catalog('live','all',cat);},
    featured:()=>(recentHistory.length?recentHistory:allChannels).filter(isChannelVisible).slice(0,10).map(shellItem),featuredTitle:'Channels you can watch',
    libraryChips:[{key:'playlists',label:'Playlists'},{key:'categories',label:'Categories'},{key:'saved',label:'Subscribed'}],
    create:()=>openSourceManagerModal(),createLabel:'Add a TV playlist source',
    onShow:page=>{shellPage=page;markShellNav();}
  });
  nav.addEventListener('click',event=>{
    const b=event.target.closest('[data-tm-action]');if(!b||!tvShell||currentTheme!=='spotify'||!isMobile())return;
    const name=b.dataset.tmAction;
    if(name==='search'||name==='library'){event.preventDefault();event.stopImmediatePropagation();if(shellPage!==name)rememberTvPlace();tvShell.show(name);}
    else if(name==='more'&&event.target.closest('#tv-mobile-nav')){event.preventDefault();event.stopImmediatePropagation();openSourceManagerModal();}
    else tvShell.hide();
  },true);
  document.addEventListener('click',event=>{
    const button=event.target.closest('[data-tm-action],[data-tm-channel],[data-tm-category],[data-tm-eq],[data-tm-quality]');
    if(!button){if(isMobile()&&event.target.closest('.channel-grid-card')){if(currentTheme==='spotify')setExpanded(true);else{view='watch';sync();document.getElementById('main-scroll').scrollTo({top:0,behavior:'smooth'});}}return;}
    if(button.dataset.tmEq){applyAudioPreset(button.dataset.tmEq);closeSheet();return;}
    if(button.dataset.tmQuality){setHlsQualityLevel(Number(button.dataset.tmQuality));closeSheet();return;}
    if(button.dataset.tmChannel){const ch=allChannels.find(c=>c.url===button.dataset.tmChannel)||recentHistory.find(c=>c.url===button.dataset.tmChannel)||favorites.find(c=>c.url===button.dataset.tmChannel);if(ch){if(button.dataset.tmRelated){playbackQueue=[...relatedPool];playQueuedChannel(ch);}else if(button.dataset.tmQueued)playQueuedChannel(ch);else playStream(ch,0);if(button.closest('#tv-now-playing'))now.scrollTo({top:0,behavior:'smooth'});clearSelectionSearch();closeSheet();if(currentTheme==='spotify')setExpanded(true);else{view='watch';sync();document.getElementById('main-scroll').scrollTo({top:0,behavior:'smooth'});}}return;}
    if(button.dataset.tmCategory){const cat=button.dataset.tmCategory;if(cat==='recent')run('history');else if(cat==='verified')catalog('live','verified');else if(cat==='all')catalog('live','all');else if(cat==='indian')run('indian');else if(cat==='favs')run('library');else catalog('live','all',cat);return;}
    run(button.dataset.tmAction);
  });
  sheet.addEventListener('change',async e=>{if(e.target.dataset.tmFilter==='availability'){hideUnavailableChannels=e.target.value==='available';window.dispatchEvent(new Event('sterling-tv-availability'));closeSheet();}else if(e.target.dataset.tmFilter==='language'){view='live';await handleCatalogLanguageChange(e.target.value);closeSheet();schedule();}else if(e.target.dataset.tmFilter==='category'){view='live';handleCatalogGenreChange(e.target.value);closeSheet();schedule();}});
  sheet.addEventListener('click',e=>{if(e.target===sheet)closeSheet();});
  document.getElementById('tm-search-input').addEventListener('focus',()=>publishSuggestions(searchInputEl.value));
  document.getElementById('tm-search-input').addEventListener('input',e=>applySearch(e.target.value));
  document.getElementById('tm-search-input').addEventListener('keydown',e=>{if(e.key==='Enter'){const hasMatch=getRankedSearchMatches(searchInputEl.value).length>0;triggerSearchNow();if(hasMatch){clearSelectionSearch();if(currentTheme==='spotify')setExpanded(true);}e.target.blur();}});
  document.addEventListener('keydown',e=>{
    if(e.key==='Escape'){if(sheet.classList.contains('tm-sheet-open'))closeSheet();else if(expanded)setExpanded(false);}
    if(e.key==='Tab' && isMobile()){
      const scope=sheet.classList.contains('tm-sheet-open')?sheet:expanded&&currentTheme==='spotify'?now:null;
      if(!scope)return;const focusable=[...scope.querySelectorAll('button,input')].filter(el=>el.getClientRects().length);const first=focusable[0],last=focusable[focusable.length-1];if(e.shiftKey&&document.activeElement===first){last.focus();e.preventDefault();}else if(!e.shiftKey&&document.activeElement===last){first.focus();e.preventDefault();}
    }
  });
  function publishPlayerState() {
    const open=Boolean(isMobile()&&activeChannel&&(expanded||view==='watch'));
    if(!hosted||open===lastPublishedPlayerState)return;
    lastPublishedPlayerState=open;
    window.parent.postMessage({type:'sterling.tv.player-state',expanded:open},'*');
  }
  function publishState() {
    if(!hosted)return;
    const next=JSON.stringify({theme:currentTheme,query:searchInputEl.value.slice(0,200)});
    if(next===lastState)return;lastState=next;
    window.parent.postMessage({type:'sterling.tv.state',...JSON.parse(next)},'*');
  }
  const uiStorage=()=>typeof tvStorage!=='undefined'?tvStorage:window.localStorage;
  function saveUi() {
    if(!hydrated)return;
    const v=document.getElementById('video-player');
    const next=JSON.stringify({view,query:searchInputEl.value.slice(0,200),category:catalogCategory,language:catalogLanguage,volume:v.volume,muted:preferredTvMuted,fit:typeof currentFitMode==='undefined'?'fit-contain':currentFitMode,background:tvBackdropMode,hideUnavailable:hideUnavailableChannels,failedChannels:[...unavailableChannels].filter(([,time])=>Date.now()-time<CHANNEL_FAILURE_TTL)});
    if(next===lastSaved)return;lastSaved=next;
    try{uiStorage().setItem('streamos_ui_v14',next);}catch{}
  }
  function restoreUi() {
    try{
      const saved=JSON.parse(uiStorage().getItem('streamos_ui_v14')||'null');
      if(!saved||typeof saved!=='object')return;
      if(['home','watch','live','library','history','search','news','sports'].includes(saved.view))view=saved.view;
      if(typeof saved.query==='string'&&saved.query.length<=200){searchInputEl.value=saved.query;document.getElementById('tm-search-input').value=saved.query;}
      if(typeof saved.category==='string'&&saved.category.length<=80)catalogCategory=saved.category;
      if(typeof saved.language==='string'&&saved.language.length<=40)catalogLanguage=saved.language;
      catalogScope=view==='library'?'favs':view==='history'?'recently_played':'all';
      const v=document.getElementById('video-player');
      if(typeof saved.volume==='number'&&saved.volume>=0&&saved.volume<=1)v.volume=saved.volume;
      if(typeof saved.muted==='boolean'){preferredTvMuted=saved.muted;v.muted=saved.muted;}
      if(['fit-contain','fit-cover','fit-zoom'].includes(saved.fit)){currentFitMode=saved.fit;v.classList.remove('fit-contain','fit-cover','fit-zoom');v.classList.add(saved.fit);}
      hideUnavailableChannels=saved.hideUnavailable===true;
      if(Array.isArray(saved.failedChannels))for(const entry of saved.failedChannels.slice(-80)){if(Array.isArray(entry)&&typeof entry[0]==='string'&&entry[0].length<=2048&&typeof entry[1]==='number'&&entry[1]<=Date.now()&&Date.now()-entry[1]<CHANNEL_FAILURE_TTL)unavailableChannels.set(entry[0],entry[1]);}
      if(saved.background==='black'||saved.background==='auto')tvBackdropMode=saved.background;
      applyBackdrop();
      renderCatalogControls();renderViewport();
    }catch{}
  }
  function tvSuggestions(query) {
    if(query.trim())return getRankedSearchMatches(query,8);
    const seen=new Set(),items=[];
    for(const ch of [...recentHistory,...favorites,...VERIFIED_STREAMS]) {
      if(!isChannelVisible(ch)||seen.has(ch.url))continue;
      seen.add(ch.url);items.push(ch);if(items.length===8)break;
    }
    return items;
  }
  function publishSuggestions(query, requestId) {
    const items=tvSuggestions(query);
    if(hosted){window.parent.postMessage({type:'sterling.tv.suggestions',query,...(typeof requestId==='string'&&/^[a-zA-Z0-9-]{1,64}$/.test(requestId)?{requestId}:{}),items:items.map(ch=>({id:ch.url,label:ch.name,subtitle:[ch.group,ch.lang].filter(Boolean).join(' · '),artwork:window.SterlingArtwork?.resolveTv(ch)||ch.logo||''}))},'*');return;}
    shell.querySelector('.tm-search-suggestions')?.remove();
    if(shell.querySelector('.tm-search').hidden)return;
    const list=document.createElement('div');list.className='tm-search-suggestions';list.setAttribute('role','listbox');list.setAttribute('aria-label','Channel suggestions');
    for(const ch of items){const button=document.createElement('button');button.type='button';button.setAttribute('role','option');button.textContent=ch.name+' · '+(ch.group||'Channel');button.addEventListener('click',()=>{playStream(ch,0);clearSelectionSearch();setExpanded(true);});list.append(button);}
    shell.querySelector('.tm-search').append(list);
  }
  // Search arrives per keystroke. The first one applies at once; a fast burst redraws once, 220 ms after
  // the last key, so the grid does not flash letter by letter. Clearing is always instant.
  let applySearchTimer=0,lastApplySearch=0;
  function applySearch(query) {
    clearTimeout(applySearchTimer);const now=Date.now(),burst=now-lastApplySearch<220;lastApplySearch=now;
    if(query.trim()&&burst){publishSuggestions(query);applySearchTimer=setTimeout(()=>{lastApplySearch=0;applySearchNow(query);},220);}else applySearchNow(query);
  }
  function applySearchNow(query) {
    view=query?'search':'home';expanded=false;
    searchInputEl.value=query;document.getElementById('tm-search-input').value=query;
    publishSuggestions(query);
    renderViewport();closeSearchDropdown();sync();
    document.getElementById('main-scroll').scrollTo({top:0,behavior:'instant'});
  }
  function clearSelectionSearch() {
    searchInputEl.value='';
    shell.querySelector('.tm-search-suggestions')?.remove();
    document.getElementById('tm-search-input').value='';
    document.getElementById('clear-search-btn')?.classList.add('hidden');
    shell.querySelector('.tm-search').hidden=true;
    closeSearchDropdown();
    publishState();
  }
  window.addEventListener('message',event=>{
    if(event.source!==window.parent)return;
    const data=event.data;
    if(data?.type==='sterling.tv.hydrate'){
      hosted=data.hostHeader===true;hydrated=true;
      // Core hydration finishes first; restore the saved UI after its boot.
      restoreUi();schedule();return;
    }
    if(data?.type!=='sterling.tv.command'||!hosted)return;
    if(data.action==='theme'&&['youtube','spotify','netflix'].includes(data.value))run(data.value);
    if(data.action==='search'&&typeof data.value==='string'&&data.value.length<=200)applySearch(data.value);
    if(data.action==='suggest'&&typeof data.value==='string')publishSuggestions(data.value.slice(0,200),data.requestId);
    if(data.action==='select-suggestion'&&typeof data.value==='string'){const ch=allChannels.find(ch=>ch.url===data.value)||tvSuggestions('').find(ch=>ch.url===data.value);if(ch){playStream(ch,0);clearSelectionSearch();view=currentTheme==='youtube'?'watch':'home';if(currentTheme==='spotify')setExpanded(true);sync();}}
    if(data.action==='back')tvNav?.back();
    if(data.action==='startup')openStartupChannelModal();
    if(data.action==='sources')openSourceManagerModal();
    if(data.action==='play-search'){
      const match=getRankedSearchMatches(searchInputEl.value)[0];
      if(match){playStream(match,0);clearSelectionSearch();view=currentTheme==='youtube'?'watch':'home';if(currentTheme==='spotify')setExpanded(true);sync();document.getElementById('main-scroll').scrollTo({top:0,behavior:'smooth'});}
    }
    if(data.action==='collapse'){
      if(sheet.classList.contains('tm-sheet-open'))closeSheet();
      if(expanded)setExpanded(false);
      view='home';renderHome();schedule();
    }
    if(data.action==='more'){if(mobile.matches)showSheet();else toggleSidebarDrawer(true);}
  });
  function setScrollPip(value) {
    const surface=document.getElementById('video-surface-card');
    const wrap=document.getElementById('mobile-sticky-player-wrap');
    if(!surface||!wrap)return;
    const wasFloating=document.body.classList.contains('tm-scroll-pip');
    if(Boolean(value)===wasFloating)return;
    if(value)wrap.style.minHeight=surface.offsetHeight+'px';
    document.body.classList.toggle('tm-scroll-pip',Boolean(value));
    if(!value)wrap.style.minHeight='';
  }
  function installPlaybackContinuity() {
    const main=document.getElementById('main-scroll'),surface=document.getElementById('video-surface-card');
    if(!main||!surface)return;
    const controls=document.createElement('div');controls.className='tm-pip-controls';controls.innerHTML=action('pip-return','Return to playing channel','screen')+action('pip-dismiss','Dismiss floating player','close');surface.append(controls);
    const onScroll=()=>{
      // Scroll must not change the height of headers or filter rows. Collapsing
      // them changes scrollTop and feeds back into the direction detector.
      if(!mobile.matches)return;
      // Measure the in-flow anchor, not the floating video or sticky stage:
      // those move when PiP activates and would cause visible oscillation.
      const anchor=document.getElementById('mobile-sticky-player-wrap');
      const hidden=anchor && anchor.getBoundingClientRect().bottom < main.getBoundingClientRect().top+16;
      setScrollPip(mobile.matches&&!expanded&&currentTheme!=='spotify'&&!pipDismissed&&!video.paused&&video.readyState>=2&&Boolean(hidden));
    };
    let scrollFramePending=false;
    main.addEventListener('scroll',()=>{
      if(scrollFramePending)return;
      scrollFramePending=true;
      requestAnimationFrame(()=>{scrollFramePending=false;onScroll();});
    },{passive:true});
    video.addEventListener('pause',()=>setScrollPip(false));
    video.addEventListener('playing',onScroll);
    window.addEventListener('resize',()=>{setScrollPip(false);onScroll();});
    let startY=null,startX=0,pull=0,refreshing=false;
    const indicator=document.createElement('div');indicator.id='tm-pull-refresh';indicator.setAttribute('role','status');indicator.setAttribute('aria-label','Pull to refresh TV');indicator.textContent='↻';document.body.append(indicator);
    const reset=()=>{startY=null;pull=0;indicator.style.transform='translate(-50%,-70px)';};
    main.addEventListener('touchstart',e=>{reset();if(!mobile.matches||refreshing||main.scrollTop>0||e.touches.length!==1||e.target.closest('input,select,textarea,[role="dialog"],#video-surface-card'))return;startY=e.touches[0].clientY;startX=e.touches[0].clientX;},{passive:true});
    main.addEventListener('touchmove',e=>{if(startY===null||e.touches.length!==1)return;const dy=e.touches[0].clientY-startY;if(dy<16){pull=0;return;}if(Math.abs(e.touches[0].clientX-startX)>Math.max(24,Math.abs(dy))){reset();return;}pull=Math.min(110,Math.max(0,dy*.55));indicator.style.transform=`translate(-50%,${pull-40}px)`;indicator.textContent=pull>=72?'Release to refresh':'↻';if(pull>0&&e.cancelable)e.preventDefault();},{passive:false});
    main.addEventListener('touchend',async()=>{const ready=pull>=72;reset();if(!ready||refreshing)return;refreshing=true;indicator.textContent='Refreshing TV…';indicator.style.transform='translate(-50%,8px)';try{Object.keys(sourceChannelCache).forEach(key=>delete sourceChannelCache[key]);await syncAllActivePlaylists(true);}finally{refreshing=false;reset();}});
    main.addEventListener('touchcancel',reset);
    const enterPip=async()=>{if(video.paused||video.readyState<2||document.pictureInPictureElement)return;try{if(video.requestPictureInPicture)await video.requestPictureInPicture();else if(video.webkitSupportsPresentationMode?.('picture-in-picture'))video.webkitSetPresentationMode('picture-in-picture');}catch{/* Browser/OS permission or gesture policy owns background PiP. */}};
    video.setAttribute('autopictureinpicture','');
    try{if(navigator.mediaSession){navigator.mediaSession.setActionHandler('enterpictureinpicture',enterPip);navigator.mediaSession.setActionHandler('play',resumeTvPlayback);navigator.mediaSession.setActionHandler('pause',pauseTvPlayback);}}catch{}
    document.addEventListener('visibilitychange',()=>{saveUi();if(document.visibilityState==='hidden')enterPip();});
    window.addEventListener('pagehide',saveUi);
    video.addEventListener('volumechange',schedule);
  }
  if(typeof openRightRailEpg==='function'){const originalGuide=openRightRailEpg;openRightRailEpg=function(){if(isMobile())showSheet('epg');else originalGuide();};}
  const originalTheme=switchTheme;switchTheme=function(...args){originalTheme(...args);schedule();};
  const originalPlay=playStream;playStream=function(...args){
    pipDismissed=false;
    originalPlay(...args);
    // All channel entry points (grid tiles, recommendations, queue, keyboard
    // search and external header) must leave search mode consistently.
    if(searchInputEl.value.trim())clearSelectionSearch();
    applyBackdrop();
    if(isMobile()&&(!args[1]))renderHome();
    schedule();
  };
  const originalBatch=renderNextBatch;renderNextBatch=function(...args){
    const firstNew=renderedItems;
    originalBatch(...args);
    // Appending a page must not rewrite old images/handlers: that restarts
    // artwork decoding and repaints tiles while the user is scrolling.
    const cards=document.getElementById('yt-studio-grid')?.children||[];
    for(let i=firstNew;i<cards.length;i++){
      const card=cards[i],ch=filteredChannels[i];if(!ch)continue;
      card.dataset.tmChannel=ch.url;card.onclick=null;
      if(isMobile()&&currentTheme==='spotify')card.querySelectorAll('img').forEach(img=>{
        const source=artwork(ch);if(img.getAttribute('src')!==source)img.src=source;
        img.onerror=()=>{img.onerror=null;img.src=artwork({...ch,logo:''});};
      });
    }
  };
  const originalRender=renderViewport;renderViewport=function(...args){
    originalRender(...args);
    // Search inputs and filter changes must not rebuild the entire home shelf
    // on every keystroke while the home view is not visible.
    if(isMobile() && view==='home')renderHome();
    schedule();
  };
  const originalRebuild=rebuildCombinedChannelCatalog;rebuildCombinedChannelCatalog=function(...args){originalRebuild(...args);if(searchInputEl.value.trim())renderViewport();};
  const originalDock=dockVideoToTopLeftMini;dockVideoToTopLeftMini=function(value){if(isMobile())return;originalDock(value);};
  window.addEventListener('sterling-tv-availability',()=>{
    renderCatalogControls();renderViewport();renderHome();renderUpNextQueue(true);saveUi();schedule();
  });
  const video=document.getElementById('video-player');video.addEventListener('playing',()=>{if(activeChannel&&unavailableChannels.delete(activeChannel.url))window.dispatchEvent(new Event('sterling-tv-availability'));});['play','pause','playing','error','loadedmetadata'].forEach(event=>video.addEventListener(event,schedule));
  mobile.addEventListener('change',()=>{if(!mobile.matches){expanded=false;closeSheet();}renderHome();schedule();});
  window.addEventListener('resize',schedule);
  window.addEventListener('sterling-tv-playback-intent',schedule);
  home.addEventListener('error',event=>{const img=event.target;if(img.tagName!=='IMG')return;const ch=allChannels.find(c=>c.url===img.closest('[data-tm-channel]')?.dataset.tmChannel);if(ch){rejectedArtwork.add(img.src);img.src=artwork({...ch,logo:''});}},true);
  mini.querySelector('img').addEventListener('error',event=>{if(activeChannel){rejectedArtwork.add(event.target.src);event.target.src=artwork({...activeChannel,logo:''});}});
  const catalogMore=document.getElementById('load-more-wrap');
  if(catalogMore && 'IntersectionObserver' in window)new IntersectionObserver(entries=>{if(entries.some(entry=>entry.isIntersecting)&&renderedItems<filteredChannels.length)renderNextBatch();},{root:document.getElementById('main-scroll'),rootMargin:'300px'}).observe(catalogMore);
  installPlaybackContinuity();
  const surface=document.getElementById('video-surface-card'),hud=document.getElementById('custom-video-hud');
  function revealControls(){if(!hud)return;hud.classList.remove('mobile-hud-hidden');clearTimeout(mobileHudFadeTimer);mobileHudFadeTimer=setTimeout(()=>{if(!hud.querySelector(':focus-visible'))hud.classList.add('mobile-hud-hidden');},2500);}
  // The player surface and HUD exist on the full page; a host without them still runs the rest.
  if(surface){
  surface.addEventListener('pointermove',revealControls,{passive:true});
  surface.addEventListener('pointerdown',revealControls,{passive:true});
  surface.addEventListener('focusin',revealControls);
  surface.addEventListener('focusout',revealControls);
  }
  video.addEventListener('playing',revealControls);
  document.addEventListener('fullscreenchange',revealControls);

  function installPlayerSwipes(element,onDirection) {
    let start=null;
    element.addEventListener('touchstart',e=>{
      if(e.touches.length!==1||e.target.closest('button,input,select,a'))return;
      start={x:e.touches[0].clientX,y:e.touches[0].clientY};
    },{passive:true});
    element.addEventListener('touchend',e=>{
      if(!start||!e.changedTouches.length)return;
      const dx=e.changedTouches[0].clientX-start.x,dy=e.changedTouches[0].clientY-start.y;
      start=null;
      if(Math.abs(dy)>85&&Math.abs(dx)<Math.abs(dy)/2)onDirection(dy>0?'down':'up');
    },{passive:true});
    element.addEventListener('touchcancel',()=>{start=null;},{passive:true});
  }
  installPlayerSwipes(now,dir=>{
    if(dir==='down'&&expanded){setExpanded(false);view='home';schedule();}
  });
  installPlayerSwipes(mini,dir=>{
    if(dir==='up'&&!expanded)setExpanded(true);
  });
  if(hydrated)restoreUi();
  window.addEventListener('sterling-artwork-update',()=>{
    document.querySelectorAll('[data-tm-channel]').forEach(tile=>{const ch=allChannels.find(ch=>ch.url===tile.dataset.tmChannel);if(ch)tile.querySelectorAll('img').forEach(img=>img.src=artwork(ch));});
    schedule();
  });
  renderHome();sync();
})();
