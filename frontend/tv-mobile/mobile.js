// Mobile UI adapter: share the existing player, settings, catalog and history.
(() => {
  const mobile = matchMedia('(max-width:767px), (max-width:1024px) and (max-height:600px)');
  const icons = {
    home:'M3 10 12 3l9 7v11h-6v-7H9v7H3z', search:'M21 21l-5-5M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0',
    library:'M4 3v18M9 3v18M15 3l6 17M13 3h3', play:'m9 5 11 7-11 7z', pause:'M8 5v14M16 5v14',
    next:'m5 5 11 7-11 7zM19 5v14', prev:'m19 5-11 7 11 7zM5 5v14', plus:'M12 5v14M5 12h14',
    check:'m5 12 4 4L19 6', down:'m6 9 6 6 6-6', close:'m6 6 12 12M6 18 18 6', more:'M5 12h.01M12 12h.01M19 12h.01',
    queue:'M4 5h16M4 11h16M4 17h9m4-2 4 3-4 3z', screen:'M3 4h18v13H3zM8 21h8M12 17v4',
    settings:'M4 7h16M4 17h16M8 4v6M16 14v6', share:'m14 3 7 7-7 7M21 10H10a7 7 0 0 0-7 7v4',
    globe:'M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0M3 12h18M12 3c5 5 5 13 0 18-5-5-5-13 0-18',
    clock:'M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0M12 7v5l3 2', bell:'M18 8a6 6 0 0 0-12 0c0 8-3 8-3 10h18c0-2-3-2-3-10M10 21h4',
    youtube:'M22 8c0-3-1-4-4-4H6C3 4 2 5 2 8v8c0 3 1 4 4 4h12c3 0 4-1 4-4zM10 8l6 4-6 4z',
    spotify:'M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0M6 9c4-2 9-1 12 1M7 12c4-1 7-1 10 1M8 15c3-1 5-1 8 1'
  };
  const icon = name => `<svg aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="${icons[name] || icons.screen}"/></svg>`;
  const action = (name,label,glyph,cls='tm-icon') => `<button type="button" class="${cls}" data-tm-action="${name}" aria-label="${label}" title="${label}">${icon(glyph)}</button>`;
  const shell = document.createElement('div');
  shell.id='tv-mobile-shell';
  shell.innerHTML=`<div class="tm-top"><button class="tm-avatar" data-tm-action="more" aria-label="TV settings and features">S</button><div class="tm-brand">${icon('youtube')}<span>YouTube</span></div><div class="tm-tabs"><button data-tm-action="home" aria-pressed="true">All</button><button data-tm-action="live">Live TV</button><button data-tm-action="library">Following</button></div><div class="tm-theme" aria-label="TV appearance"><button data-tm-action="youtube" aria-label="YouTube theme">${icon('youtube')}</button><button data-tm-action="spotify" aria-label="Spotify theme">${icon('spotify')}</button></div>${action('search','Search channels','search','tm-icon tm-search-open')}${action('more','More TV features','more')}</div><div class="tm-search" hidden><input id="tm-search-input" type="search" placeholder="What do you want to watch?" aria-label="Search all TV channels" autocomplete="off"/>${action('close-search','Close search','close')}</div><div class="tm-context"><button data-tm-action="home" aria-pressed="true">All</button><button data-tm-action="live">Live</button><button data-tm-action="indian">Indian TV</button><button data-tm-action="news">News</button><button data-tm-action="sports">Sports</button><button data-tm-action="history">Recently watched</button></div>`;
  document.getElementById('app-workspace').before(shell);
  const home=document.createElement('section'); home.id='tv-mobile-home'; home.setAttribute('aria-label','TV home');
  document.getElementById('main-scroll').prepend(home);
  const browse=document.createElement('section');browse.id='tm-browse-categories';browse.setAttribute('aria-label','Browse all TV categories');document.getElementById('catalog-section').prepend(browse);
  const nav=document.createElement('nav');nav.id='tv-mobile-nav';nav.setAttribute('aria-label','Mobile TV navigation');
  nav.innerHTML=action('home','Home','home','tm-nav-item')+action('search','Search','search','tm-nav-item')+action('library','Your library','library','tm-nav-item')+action('more','More','more','tm-nav-item');
  const labels=['Home','Search','Your Library','More'];
  [...nav.children].forEach((el,i)=>el.insertAdjacentHTML('beforeend',`<span>${labels[i]}</span>`));
  const mini=document.createElement('div');mini.id='tv-mobile-mini';mini.setAttribute('aria-label','Now playing');
  mini.innerHTML=`<button class="tm-mini-open" data-tm-action="expand" aria-label="Open Now Playing"><img alt=""/><span class="tm-mini-copy"><strong></strong><small class="tm-live-dot">Live television</small></span></button>${action('save','Save channel to your library','plus')}${action('play','Play or pause live TV','play')}<div class="tm-mini-live" aria-hidden="true"></div>`;
  const now=document.createElement('section');now.id='tv-now-playing';now.setAttribute('aria-label','Now Playing');now.setAttribute('role','dialog');now.setAttribute('aria-modal','true');now.hidden=true;
  now.innerHTML=`<div class="tm-now-head">${action('collapse','Close Now Playing','down')}<div><small>PLAYING FROM LIVE TV</small><strong id="tm-now-group">Your television, everywhere</strong></div>${action('more','Now Playing options','more')}</div><div class="tm-now-spacer" aria-hidden="true"></div><div class="tm-now-copy"><div><h2 id="tm-now-title"></h2><p id="tm-now-subtitle"></p></div>${action('save','Save channel to your library','plus')}</div><div class="tm-timeline"><div class="tm-timeline-track" aria-hidden="true"></div><div class="tm-timeline-label"><span>Live broadcast</span><span class="tm-live-dot">LIVE</span></div></div><div class="tm-now-controls">${action('mute','Mute or unmute TV','screen')}${action('previous','Previous channel','prev')}${action('play','Play or pause live TV','play','tm-big-play')}${action('next','Next channel','next')}${action('fullscreen','Fullscreen video','screen')}</div><div class="tm-now-footer"><span>Watching on this device</span><div>${action('share','Share channel','share')}${action('queue','Open channel queue','queue')}</div></div>`;
  const sheet=document.createElement('div');sheet.id='tv-mobile-sheet';sheet.setAttribute('role','dialog');sheet.setAttribute('aria-label','TV features');sheet.setAttribute('aria-modal','true');
  document.body.append(nav,mini,now,sheet);
  let view='home',expanded=false,scheduled=false,returnFocus=null,lastLandscape=innerWidth>innerHeight;
  const activeTheme=()=>currentTheme==='spotify'||currentTheme==='youtube';
  const isMobile=()=>mobile.matches && activeTheme();
  const artworkCache=new Map();
  function artwork(ch) {
    if(ch.logo?.startsWith('https://') && !ch.logo.includes('unsplash.com'))return ch.logo;
    if(artworkCache.has(ch.url))return artworkCache.get(ch.url);
    const hue=[...ch.name].reduce((n,c)=>n+c.charCodeAt(0)*7,0)%360;
    const initials=ch.name.split(/\s+/).slice(0,2).map(w=>w[0]).join('');
    const svg=`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 320 320"><defs><linearGradient id="a" x2="1" y2="1"><stop stop-color="hsl(${hue},55%,48%)"/><stop offset="1" stop-color="hsl(${hue},60%,17%)"/></linearGradient></defs><path fill="url(#a)" d="M0 0h320v320H0z"/><g fill="none" stroke="#ffffff" opacity=".12"><circle cx="265" cy="70" r="135" stroke-width="45"/><circle cx="265" cy="70" r="190" stroke-width="2"/><path d="M0 220 320 80M0 270 320 130" stroke-width="2"/></g><text x="22" y="38" fill="white" font-family="Arial,sans-serif" font-size="13" font-weight="700" letter-spacing="2">${escapeHtml((ch.group||'LIVE TV').toUpperCase().slice(0,24))}</text><text x="18" y="207" fill="white" font-family="Arial,sans-serif" font-size="112" font-weight="900" letter-spacing="-9">${escapeHtml(initials)}</text><rect x="22" y="248" width="36" height="5" fill="#1ed760"/><text x="22" y="284" fill="white" font-family="Arial,sans-serif" font-size="19" font-weight="700">${escapeHtml(ch.name.slice(0,24))}</text></svg>`;
    const uri='data:image/svg+xml;utf8,'+encodeURIComponent(svg);artworkCache.set(ch.url,uri);return uri;
  }
  const channelButton=(ch,quick=false)=>`<button type="button" class="${quick?'':'tm-album'}" data-tm-channel="${escapeAttr(ch.url)}" aria-label="Watch ${escapeAttr(ch.name)}">${quick?`<img src="${escapeAttr(artwork(ch))}" alt="" loading="lazy"/><span>${escapeHtml(ch.name)}</span>`:`<div class="tm-cover"><img src="${escapeAttr(artwork(ch))}" alt="" loading="lazy"/><span class="tm-live">LIVE TV</span></div><strong>${escapeHtml(ch.name)}</strong><small>${escapeHtml(ch.group || 'Television')} · ${escapeHtml(ch.lang || 'Global')}</small>`}</button>`;
  function shelf(title,pool,filter) {
    if(!pool.length)return '';
    return `<section><div class="tm-section-title"><h2>${title}</h2><button data-tm-category="${filter}">Show all</button></div><div class="tm-shelf">${pool.slice(0,10).map(ch=>channelButton(ch)).join('')}</div></section>`;
  }
  function renderHome() {
    browse.innerHTML=['News','Sports','Movies','Music','Documentary','Entertainment','Kids & Anime','Devotional'].map(cat=>`<button data-tm-category="${cat}"><strong>${escapeHtml(cat)}</strong><span aria-hidden="true">${icon('screen')}</span></button>`).join('');
    const recent=[...recentHistory,...VERIFIED_STREAMS].filter((ch,i,a)=>a.findIndex(c=>c.url===ch.url)===i);
    home.innerHTML=`<div class="tm-quick">${recent.slice(0,mobile.matches&&innerWidth>innerHeight?8:6).map(ch=>channelButton(ch,true)).join('')}</div>${shelf('Jump back in',recent,'recent')}${shelf('Made for your screen',VERIFIED_STREAMS,'verified')}<section><div class="tm-section-title"><h2>Find your next favourite</h2></div><div class="tm-shelf">${['News','Sports','Movies','Music','Documentary'].map(cat=>`<button class="tm-mix" data-tm-category="${cat}"><strong>${cat}</strong><small>Live TV collection</small></button>`).join('')}</div></section>${shelf('Live from India',allChannels.filter(ch=>ch.isIndian||INDIAN_LANG_IDS.includes(ch.lang)),'indian')}${shelf('Your saved channels',favorites,'favs')}`;
  }
  function sync() {
    scheduled=false;
    const enabled=isMobile();
    const stage=document.getElementById('player-stage');
    if(stage)stage.inert=enabled&&currentTheme==='spotify'&&!expanded;
    home.inert=shell.inert=enabled&&expanded&&currentTheme==='spotify';
    if(lastLandscape!==(innerWidth>innerHeight)){lastLandscape=innerWidth>innerHeight;renderHome();}
    document.body.classList.toggle('tm-catalog-view',enabled&&view!=='home');
    document.body.classList.toggle('tm-browse-search',enabled&&currentTheme==='spotify'&&view==='search'&&!searchInputEl.value.trim());
    document.body.classList.toggle('tm-now-open',enabled&&expanded&&currentTheme==='spotify');
    now.hidden=!(enabled&&expanded&&currentTheme==='spotify');
    if(!enabled) { now.hidden=true; return; }
    shell.querySelectorAll('.tm-theme button').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.tmAction===currentTheme)));
    nav.querySelectorAll('button').forEach(b=>{const selected=b.dataset.tmAction===view;b.toggleAttribute('aria-current',selected);if(selected)b.setAttribute('aria-current','page');});
    shell.querySelectorAll('.tm-tabs button,.tm-context button').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.tmAction===view)));
    if(activeChannel) {
      mini.querySelector('img').src=artwork(activeChannel);
      mini.querySelector('strong').textContent=activeChannel.name;
      document.getElementById('tm-now-title').textContent=activeChannel.name;
      document.getElementById('tm-now-subtitle').textContent=`${activeChannel.group||'Live television'} · ${activeChannel.lang||'Global'}`;
      document.getElementById('tm-now-group').textContent=activeChannel.group||'Your live channels';
      const saved=favorites.some(c=>c.url===activeChannel.url);
      [mini,now].forEach(el=>{const b=el.querySelector('[data-tm-action="save"]');b.innerHTML=icon(saved?'check':'plus');b.setAttribute('aria-label',saved?'Remove channel from your library':'Save channel to your library');b.setAttribute('aria-pressed',String(saved));});
    }
    nav.querySelector('[data-tm-action="library"] span').textContent=currentTheme==='youtube'?'Subscriptions':'Your Library';
    nav.querySelector('[data-tm-action="more"] span').textContent=currentTheme==='youtube'?'You':'More';
    const paused=document.getElementById('video-player').paused;
    [mini,now].forEach(el=>{const b=el.querySelector('[data-tm-action="play"]');b.innerHTML=icon(paused?'play':'pause');b.setAttribute('aria-label',paused?'Play live TV':'Pause live TV');});
  }
  function schedule() { if(!scheduled){scheduled=true;requestAnimationFrame(sync);} }
  function closeSheet() { sheet.classList.remove('tm-sheet-open');returnFocus?.focus(); }
  function setExpanded(value) {
    expanded=value;sync();
    if(value){returnFocus=document.activeElement;now.querySelector('button').focus();}
    else mini.querySelector('button').focus();
  }
  function showSheet(kind='more') {
    returnFocus=document.activeElement;
    let title='Your TV, your way';
    let items=[['sources','Playlist sources','library'],['startup','Default startup channel','screen'],['indian','Indian TV & languages','globe'],['filters','Languages & categories','search'],['queue','Up next & programme guide','queue'],['equalizer','Sound equalizer','settings'],['sleep','Sleep timer','clock'],['quality','Quality, captions & fit','screen'],['share','Share current channel','share'],['pip','Picture in picture','screen'],['multiview','Four-channel multiview','screen'],['tools','All TV tools & themes','settings']];
    if(['filters','equalizer','quality','epg'].includes(kind)) {
      title={filters:'Languages & categories',equalizer:'Sound equalizer',quality:'Playback settings',epg:'Programme guide'}[kind];
      let content='';
      if(kind==='filters') content=`<label class="tm-filter-label">Language<select data-tm-filter="language">${document.getElementById('catalog-lang-select').innerHTML}</select></label><label class="tm-filter-label">Category<select data-tm-filter="category">${document.getElementById('catalog-genre-select').innerHTML}</select></label>`;
      if(kind==='equalizer') content=Object.entries(EQ_PRESETS).map(([key,preset])=>`<button data-tm-eq="${key}">${icon('settings')}<span>${escapeHtml(preset.label)}</span></button>`).join('');
      if(kind==='quality') content=`${action('fit','Change video fit','screen','tm-feature').replace('</button>','<span>Fit / Fill / Zoom</span></button>')}<button data-tm-quality="-1">${icon('check')}<span>Automatic quality</span></button>${(hls?.levels||[]).map((level,i)=>`<button data-tm-quality="${i}">${icon('screen')}<span>${level.height||'Auto'}p · ${Math.round((level.bitrate||0)/1000)} kbps</span></button>`).join('')}<p class="tm-feature-note">${hasNativeHls(document.getElementById('video-player'))&&!hls?'Your browser manages stream quality automatically.':'Quality options depend on the current broadcast.'}</p>`;
      if(kind==='epg') content=activeChannel?`<p class="tm-feature-note">Guide preview · programme times are illustrative.</p>${getChannelEpgSchedule(activeChannel).map(item=>`<div class="tm-guide-row"><strong>${escapeHtml(item.title||'Live broadcast')}</strong><small>${escapeHtml(item.time||item.start||'Live TV')}</small></div>`).join('')}`:'<p>Select a channel to view its guide.</p>';
      sheet.innerHTML=`<div class="tm-sheet-content"><div class="tm-sheet-handle"></div><div class="tm-sheet-title"><h2>${title}</h2>${action('close-sheet','Close TV features','close')}</div>${content}</div>`;
    } else if(kind==='queue') {
      title='Up next';
      sheet.innerHTML=`<div class="tm-sheet-content"><div class="tm-sheet-handle"></div><div class="tm-sheet-title"><h2>${title}</h2>${action('close-sheet','Close queue','close')}</div>${computeRightRailPool().slice(0,20).map(ch=>`<button data-tm-channel="${escapeAttr(ch.url)}">${icon('screen')}<span>${escapeHtml(ch.name)}</span></button>`).join('')}${action('epg','Programme guide','queue')}</div>`;
    } else {
      sheet.innerHTML=`<div class="tm-sheet-content"><div class="tm-sheet-handle"></div><div class="tm-sheet-title"><h2>${title}</h2>${action('close-sheet','Close TV features','close')}</div>${items.map(([name,label,glyph])=>action(name,label,glyph,'tm-feature').replace('</button>',`<span>${label}</span></button>`)).join('')}</div>`;
    }
    sheet.classList.add('tm-sheet-open');sheet.querySelector('button')?.focus();
  }
  function catalog(next,scope='all',category='All') {
    view=next;expanded=false;catalogScope=scope;catalogCategory=category;catalogLanguage='All';
    searchInputEl.value='';document.getElementById('tm-search-input').value='';
    renderCatalogControls();renderViewport();sync();
    const heading=document.createElement('h2');heading.className='tm-feed-heading';heading.textContent=next==='library'?'Your Library':next==='search'?'Search':next==='history'?'Recently watched':category!=='All'?category:'Live television';
    document.querySelector('.tm-feed-heading')?.remove();if(currentTheme==='spotify'&&next!=='home')document.getElementById('catalog-section').prepend(heading);
    document.getElementById('main-scroll').scrollTo({top:0,behavior:'instant'});
  }
  function run(name) {
    switch(name) {
      case 'spotify': case 'youtube': expanded=false;switchTheme(name);renderHome();sync();break;
      case 'home': catalog('home');shell.querySelector('.tm-search').hidden=true;renderHome();break;
      case 'live': catalog('live','verified');break;
      case 'library': catalog('library','favs');break;
      case 'history': catalog('history','recently_played');break;
      case 'news': catalog('news','all','News');break;
      case 'sports': catalog('sports','all','Sports');break;
      case 'indian': catalog('live','indian_hub');activateIndianSection();break;
      case 'search': catalog('search');shell.querySelector('.tm-search').hidden=false;document.getElementById('tm-search-input').focus();break;
      case 'close-search': shell.querySelector('.tm-search').hidden=true;run('home');break;
      case 'expand': setExpanded(true);break;
      case 'collapse': setExpanded(false);break;
      case 'play': { const video=document.getElementById('video-player'); if(video.paused && video.readyState===0)resumeTvPlayback();else togglePlayPause();schedule();break; }
      case 'save': toggleSubscribeAndFavorite();renderHome();sync();break;
      case 'next': playNextChannel();schedule();break;
      case 'previous': playPrevChannel();schedule();break;
      case 'mute': toggleMuteAudio();break;
      case 'fullscreen': toggleFullscreenPlayer();break;
      case 'share': copyStreamShareLink();break;
      case 'more': showSheet();break;
      case 'queue': showSheet('queue');break;
      case 'close-sheet': closeSheet();break;
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
  document.addEventListener('click',event=>{
    const button=event.target.closest('[data-tm-action],[data-tm-channel],[data-tm-category],[data-tm-eq],[data-tm-quality]');
    if(!button){if(isMobile()&&event.target.closest('.channel-grid-card')){if(currentTheme==='spotify')setExpanded(true);else{view='home';sync();document.getElementById('main-scroll').scrollTo({top:0,behavior:'smooth'});}}return;}
    if(button.dataset.tmEq){applyAudioPreset(button.dataset.tmEq);closeSheet();return;}
    if(button.dataset.tmQuality){setHlsQualityLevel(Number(button.dataset.tmQuality));closeSheet();return;}
    if(button.dataset.tmChannel){const ch=allChannels.find(c=>c.url===button.dataset.tmChannel)||recentHistory.find(c=>c.url===button.dataset.tmChannel)||favorites.find(c=>c.url===button.dataset.tmChannel);if(ch){playStream(ch,0);closeSheet();if(currentTheme==='spotify')setExpanded(true);else{view='home';sync();document.getElementById('main-scroll').scrollTo({top:0,behavior:'smooth'});}}return;}
    if(button.dataset.tmCategory){const cat=button.dataset.tmCategory;if(cat==='recent')run('history');else if(cat==='verified')run('live');else if(cat==='indian')run('indian');else if(cat==='favs')run('library');else catalog('live','all',cat);return;}
    run(button.dataset.tmAction);
  });
  sheet.addEventListener('change',async e=>{if(e.target.dataset.tmFilter==='language'){view='live';await handleCatalogLanguageChange(e.target.value);closeSheet();schedule();}else if(e.target.dataset.tmFilter==='category'){view='live';handleCatalogGenreChange(e.target.value);closeSheet();schedule();}});
  sheet.addEventListener('click',e=>{if(e.target===sheet)closeSheet();});
  document.getElementById('tm-search-input').addEventListener('input',e=>{searchInputEl.value=e.target.value;renderViewport();closeSearchDropdown();});
  document.getElementById('tm-search-input').addEventListener('keydown',e=>{if(e.key==='Enter'){triggerSearchNow();if(currentTheme==='spotify')setExpanded(true);e.target.blur();}});
  document.addEventListener('keydown',e=>{
    if(e.key==='Escape'){if(sheet.classList.contains('tm-sheet-open'))closeSheet();else if(expanded)setExpanded(false);}
    if(e.key==='Tab' && isMobile()){
      const scope=sheet.classList.contains('tm-sheet-open')?sheet:expanded&&currentTheme==='spotify'?now:null;
      if(!scope)return;const focusable=[...scope.querySelectorAll('button,input')].filter(el=>el.getClientRects().length);const first=focusable[0],last=focusable[focusable.length-1];if(e.shiftKey&&document.activeElement===first){last.focus();e.preventDefault();}else if(!e.shiftKey&&document.activeElement===last){first.focus();e.preventDefault();}
    }
  });
  const originalTheme=switchTheme;switchTheme=function(...args){originalTheme(...args);schedule();};
  const originalPlay=playStream;playStream=function(...args){originalPlay(...args);if(isMobile()&&(!args[1]))renderHome();schedule();};
  const originalBatch=renderNextBatch;renderNextBatch=function(...args){originalBatch(...args);if(isMobile()&&currentTheme==='spotify'){[...document.querySelectorAll('#yt-studio-grid>.channel-grid-card')].forEach((card,i)=>{const ch=filteredChannels[i];if(ch)card.querySelectorAll('img').forEach(img=>{img.src=artwork(ch);img.onerror=()=>{img.onerror=null;img.src=artwork({...ch,logo:''});};});});}};
  const originalRender=renderViewport;renderViewport=function(...args){originalRender(...args);if(isMobile())renderHome();schedule();};
  const originalDock=dockVideoToTopLeftMini;dockVideoToTopLeftMini=function(value){if(isMobile())return;originalDock(value);};
  const video=document.getElementById('video-player');['play','pause','playing','error','loadedmetadata'].forEach(event=>video.addEventListener(event,schedule));
  mobile.addEventListener('change',()=>{if(!mobile.matches){expanded=false;closeSheet();}renderHome();schedule();});
  window.addEventListener('resize',schedule);
  home.addEventListener('error',event=>{const img=event.target;if(img.tagName!=='IMG')return;const ch=allChannels.find(c=>c.url===img.closest('[data-tm-channel]')?.dataset.tmChannel);if(ch)img.src=artwork({...ch,logo:''});},true);
  mini.querySelector('img').addEventListener('error',event=>{if(activeChannel)event.target.src=artwork({...activeChannel,logo:''});});
  renderHome();sync();
})();
