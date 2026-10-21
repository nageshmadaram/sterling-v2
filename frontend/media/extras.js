/* Sterling media extras, shared by Radio and TV: keyboard shortcuts with a help panel, Surprise me,
   sleep timer with fade-out, wake-up alarm, listening stats, backup and restore, screen wake lock and
   lock-screen controls. Each app passes an adapter; features it does not ask for stay off. */
(()=>{
 const DAY=86400000;
 const el=(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text!=null)n.textContent=text;return n;};
 const dayKey=t=>{const d=new Date(t);return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;};
 const clock=ms=>{const s=Math.max(0,Math.round(ms/1000));return `${Math.floor(s/60)}:${String(s%60).padStart(2,'0')}`;};
 const minutes=s=>s<60?`${Math.round(s)} s`:s<3600?`${Math.round(s/60)} min`:`${(s/3600).toFixed(1)} h`;
 const typing=t=>t&&(t.isContentEditable||/^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName));

 function mount(a){
  const f={sleep:false,alarm:false,stats:false,wakeLock:false,mediaSession:false,...a.features};
  const noun=a.noun||'station',Noun=noun[0].toUpperCase()+noun.slice(1);
  let saved={};try{saved=JSON.parse(a.storage?.get(a.storageKey)||'{}')||{};}catch(_){saved={};}
  if(typeof saved!=='object'||Array.isArray(saved))saved={};
  saved.stats=saved.stats&&typeof saved.stats==='object'?saved.stats:{};
  let saveTimer=0;const persist=()=>{clearTimeout(saveTimer);saveTimer=setTimeout(()=>{try{a.storage?.set(a.storageKey,JSON.stringify(saved).slice(0,48000));}catch(_){}} ,800);};

  // ── Panel ───────────────────────────────────────────────────────────────
  const dialog=el('dialog','mx-dialog');dialog.setAttribute('aria-label',`${a.kind==='tv'?'TV':'Radio'} extras`);
  dialog.innerHTML=`<header><div><small>MORE WAYS TO ${a.kind==='tv'?'WATCH':'LISTEN'}</small><h2>Extras</h2></div><button type="button" data-mx="close" aria-label="Close extras">✕</button></header>
  <section class="mx-quick"><button type="button" data-mx="surprise"><b>🎲</b><span>Surprise me<small>Play a random ${noun} from this list</small></span></button></section>
  <section data-mx-part="sleep" hidden><h3>Sleep timer</h3><p class="mx-note">${a.kind==='tv'?'Video':'Sound'} fades out over the last 30 seconds, then stops.</p><div class="mx-chips" data-mx-sleep></div><p class="mx-status" data-mx-sleep-status role="status"></p></section>
  <section data-mx-part="alarm" hidden><h3>Wake-up alarm</h3><p class="mx-note">Plays a ${noun} at a set time and fades in. This page must stay open.</p>
   <div class="mx-row"><label>Time<input type="time" data-mx-alarm-time></label><label class="mx-toggle"><input type="checkbox" data-mx-alarm-on> On</label></div>
   <p class="mx-status" data-mx-alarm-status role="status"></p><button type="button" data-mx="alarm-use" class="mx-secondary">Use the ${noun} playing now</button></section>
  <section data-mx-part="stats" hidden><h3>Your ${a.kind==='tv'?'watching':'listening'}</h3><div class="mx-stats" data-mx-stats></div><ol class="mx-top" data-mx-top></ol></section>
  <section><h3>Backup</h3><p class="mx-note">Save your liked ${noun}s, sources and settings to a file, or restore them.</p><div class="mx-row"><button type="button" data-mx="export" class="mx-secondary">Export backup</button><button type="button" data-mx="import" class="mx-secondary">Restore from file</button><input type="file" accept="application/json,.json" data-mx-file hidden></div><p class="mx-status" data-mx-backup-status role="status"></p></section>
  <section data-mx-part="keys"><h3>Keyboard shortcuts</h3><dl class="mx-keys" data-mx-keys></dl></section>`;
  document.body.append(dialog);
  const $=s=>dialog.querySelector(s);
  for(const part of ['sleep','alarm','stats'])$(`[data-mx-part="${part}"]`).hidden=!f[part];
  let returnFocus=null;
  function open(section){returnFocus=document.activeElement;render();if(!dialog.open)dialog.showModal();dialog.scrollTop=0;if(section)$(`[data-mx-part="${section}"]`)?.scrollIntoView({block:'start'});}
  function close(){dialog.close();returnFocus?.focus?.({preventScroll:true});}
  dialog.addEventListener('cancel',e=>{e.preventDefault();close();});
  dialog.addEventListener('click',e=>{if(e.target===dialog)close();});
  dialog.addEventListener('keydown',e=>e.stopPropagation());

  // ── Surprise me ─────────────────────────────────────────────────────────
  function surprise(){
   const pool=(a.pool?.()||[]).filter(item=>item&&item.id!==a.current?.()?.id);
   if(!pool.length){a.toast?.(`No other ${noun}s in this list`);return;}
   const pick=pool[Math.floor(Math.random()*pool.length)];a.play(pick.id);a.toast?.(`Surprise: ${pick.title}`);
  }

  // ── Sleep timer ─────────────────────────────────────────────────────────
  let sleepEnd=0,sleepTick=0,sleepBase=null,sleepMin=0;
  const chip=el('button','mx-sleep-chip');chip.type='button';chip.hidden=true;chip.addEventListener('click',()=>open('sleep'));document.body.append(chip);
  function setSleep(min){
   clearInterval(sleepTick);if(sleepBase!=null)a.setVolume?.(sleepBase);sleepBase=null;sleepMin=min;sleepEnd=min?Date.now()+min*60000:0;chip.hidden=!sleepEnd;
   if(sleepEnd){sleepTick=setInterval(stepSleep,1000);stepSleep();a.toast?.(`Sleep timer: ${min} min`);}else a.toast?.('Sleep timer off');
   render();
  }
  function stepSleep(){
   const left=sleepEnd-Date.now();chip.textContent=`☾ ${clock(left)}`;chip.setAttribute('aria-label',`Sleep timer, ${clock(left)} left`);
   if(left<=30000&&a.setVolume&&a.getVolume){if(sleepBase==null)sleepBase=a.getVolume();a.setVolume(sleepBase*Math.max(0,left)/30000);}
   if(left<=0){const base=sleepBase;sleepBase=null;clearInterval(sleepTick);sleepEnd=0;sleepMin=0;chip.hidden=true;if(a.isPlaying?.())a.toggle();if(base!=null)setTimeout(()=>a.setVolume?.(base),400);a.toast?.('Sleep timer finished');}
   if(dialog.open)$('[data-mx-sleep-status]').textContent=sleepEnd?`Stops in ${clock(sleepEnd-Date.now())}`:'';
  }

  // ── Alarm ───────────────────────────────────────────────────────────────
  saved.alarm=saved.alarm&&typeof saved.alarm==='object'?saved.alarm:{on:false,time:'07:00',id:'',title:''};
  function nextAlarm(){const [h,m]=String(saved.alarm.time||'07:00').split(':').map(Number);const d=new Date();d.setHours(h||0,m||0,0,0);if(d.getTime()<=Date.now())d.setTime(d.getTime()+DAY);return d;}
  function checkAlarm(){
   if(!f.alarm||!saved.alarm.on||!saved.alarm.id)return;const now=new Date(),[h,m]=String(saved.alarm.time).split(':').map(Number);
   if(now.getHours()===h&&now.getMinutes()===m&&saved.alarm.fired!==dayKey(now)){
    saved.alarm.fired=dayKey(now);persist();const target=a.getVolume?.()??1;a.setVolume?.(Math.min(.1,target));a.play(saved.alarm.id);a.toast?.(`Good morning: ${saved.alarm.title||Noun}`);
    let step=0;const ramp=setInterval(()=>{step++;a.setVolume?.(Math.min(target,.1+(target-.1)*step/30));if(step>=30)clearInterval(ramp);},1000);
   }
  }
  if(f.alarm)setInterval(checkAlarm,15000);

  // ── Stats ───────────────────────────────────────────────────────────────
  if(f.stats)setInterval(()=>{
   if(document.hidden&&!a.isPlaying?.())return;if(!a.isPlaying?.())return;const cur=a.current?.();if(!cur?.id)return;
   const day=dayKey(Date.now()),bucket=saved.stats[day]||(saved.stats[day]={}),row=bucket[cur.id]||(bucket[cur.id]={t:0,n:cur.title});row.t+=10;row.n=cur.title;
   const keep=new Set(Array.from({length:30},(_,i)=>dayKey(Date.now()-i*DAY)));for(const k of Object.keys(saved.stats))if(!keep.has(k))delete saved.stats[k];
   persist();if(dialog.open)renderStats();
  },10000);
  function renderStats(){
   const today=saved.stats[dayKey(Date.now())]||{},week={};let todayT=0,weekT=0;
   for(const v of Object.values(today))todayT+=v.t;
   for(let i=0;i<7;i++){for(const [id,v] of Object.entries(saved.stats[dayKey(Date.now()-i*DAY)]||{})){weekT+=v.t;(week[id]||(week[id]={t:0,n:v.n})).t+=v.t;}}
   $('[data-mx-stats]').innerHTML='';
   for(const [label,value] of [['Today',minutes(todayT)],['Last 7 days',minutes(weekT)],[`${Noun}s`,String(Object.keys(week).length)]]){const box=el('div','mx-stat');box.append(el('strong','',value),el('small','',label));$('[data-mx-stats]').append(box);}
   const top=$('[data-mx-top]');top.replaceChildren();
   const rows=Object.entries(week).sort((x,y)=>y[1].t-x[1].t).slice(0,5);
   if(!rows.length)top.append(el('li','mx-empty',`Play a ${noun} and your top ${noun}s appear here.`));
   for(const [id,v] of rows){const li=el('li'),b=el('button');b.type='button';b.append(el('span','',v.n||Noun),el('small','',minutes(v.t)));b.addEventListener('click',()=>{a.play(id);close();});li.append(b);top.append(li);}
  }

  // ── Backup ──────────────────────────────────────────────────────────────
  async function exportBackup(){
   const data={app:`sterling-${a.kind}`,version:1,exported:new Date().toISOString(),data:a.exportData?.()||{}},text=JSON.stringify(data,null,1),status=$('[data-mx-backup-status]');
   try{const url=URL.createObjectURL(new Blob([text],{type:'application/json'})),link=el('a');link.href=url;link.download=`sterling-${a.kind}-backup-${dayKey(Date.now())}.json`;document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),4000);status.textContent='Backup file saved.';}
   catch(_){try{await navigator.clipboard.writeText(text);status.textContent='Download blocked here, so the backup was copied to your clipboard.';}catch(__){status.textContent='Could not save the backup in this browser.';}}
  }
  $('[data-mx-file]').addEventListener('change',async e=>{
   const file=e.target.files?.[0],status=$('[data-mx-backup-status]');e.target.value='';if(!file)return;
   if(file.size>2_000_000){status.textContent='That file is too large to be a Sterling backup.';return;}
   try{const parsed=JSON.parse(await file.text());if(parsed?.app!==`sterling-${a.kind}`||typeof parsed.data!=='object')throw Error('wrong');const count=a.importData?.(parsed.data);status.textContent=`Restored${Number.isFinite(count)?` ${count} items`:''}.`;a.toast?.('Backup restored');}
   catch(_){status.textContent=`This is not a Sterling ${a.kind==='tv'?'TV':'Radio'} backup file.`;}
  });

  // ── Keyboard ────────────────────────────────────────────────────────────
  const own=[['Space / K','Play or pause',()=>a.toggle()],['N','Next '+noun,()=>a.next?.()],['B','Previous '+noun,()=>a.prev?.()],['R','Surprise me',surprise],['L','Like or save',()=>a.like?.()],['S','Sleep timer',()=>open('sleep')],['?','Extras and shortcuts',()=>open('keys')]];
  if(a.mute)own.push(['M','Mute',()=>a.mute()]);if(a.focusSearch)own.push(['/','Search',()=>a.focusSearch()]);
  const handled=new Set((a.handledKeys||[]).map(k=>k.toLowerCase()));
  document.addEventListener('keydown',e=>{
   if(e.defaultPrevented||e.ctrlKey||e.metaKey||e.altKey||typing(e.target)||dialog.open)return;
   const key=e.key===' '?' ':e.key.toLowerCase();if(handled.has(key))return;
   if((key===' '||key==='k')&&e.target.closest?.('button,[role="button"],a,summary'))return;
   const run={' ':own[0][2],k:own[0][2],n:own[1][2],b:own[2][2],r:own[3][2],l:own[4][2],s:f.sleep?own[5][2]:null,'?':own[6][2],m:a.mute,'/':a.focusSearch}[key];
   if(!run)return;e.preventDefault();run();
  });

  // ── Wake lock and lock-screen controls (TV) ─────────────────────────────
  let lock=null;
  async function holdScreen(on){try{if(on&&!lock&&navigator.wakeLock){lock=await navigator.wakeLock.request('screen');lock.addEventListener?.('release',()=>{lock=null;});}else if(!on&&lock){await lock.release();lock=null;}}catch(_){lock=null;}}
  if(f.wakeLock){const v=a.video?.();if(v){v.addEventListener('playing',()=>holdScreen(true));for(const t of ['pause','ended','emptied'])v.addEventListener(t,()=>holdScreen(false));}
   document.addEventListener('visibilitychange',()=>{if(!document.hidden&&a.isPlaying?.())holdScreen(true);});}
  let sessionFor='';
  if(f.mediaSession&&navigator.mediaSession){
   try{navigator.mediaSession.setActionHandler('nexttrack',()=>a.next?.());navigator.mediaSession.setActionHandler('previoustrack',()=>a.prev?.());}catch(_){}
   setInterval(()=>{const cur=a.current?.();if(!cur||cur.id===sessionFor)return;sessionFor=cur.id;
    try{navigator.mediaSession.metadata=new MediaMetadata({title:cur.title,artist:cur.subtitle||'Sterling TV',album:'Sterling',artwork:cur.art?[{src:cur.art,sizes:'512x512'}]:[]});}catch(_){}},1500);
  }

  // ── Render ──────────────────────────────────────────────────────────────
  function render(){
   const chips=$('[data-mx-sleep]');chips.replaceChildren();
   for(const min of [0,15,30,45,60,90]){const b=el('button','',min?`${min} min`:'Off');b.type='button';b.setAttribute('aria-pressed',String(min===sleepMin));b.addEventListener('click',()=>setSleep(min));chips.append(b);}
   $('[data-mx-sleep-status]').textContent=sleepEnd?`Stops in ${clock(sleepEnd-Date.now())}`:'Off';
   $('[data-mx-alarm-time]').value=saved.alarm.time||'07:00';$('[data-mx-alarm-on]').checked=Boolean(saved.alarm.on);
   $('[data-mx-alarm-status]').textContent=saved.alarm.id?(saved.alarm.on?`${saved.alarm.title} · next ${nextAlarm().toLocaleString([], {weekday:'short',hour:'2-digit',minute:'2-digit'})}`:`${saved.alarm.title} · off`):`Choose a ${noun}: play one, then tap below.`;
   if(f.stats)renderStats();
   const keys=$('[data-mx-keys]');keys.replaceChildren();
   for(const [k,label] of [...own.filter(([k])=>k!=='S'||f.sleep).map(([k,l])=>[k,l]),...(a.extraShortcuts||[])].filter(([k])=>!handled.has(k.toLowerCase())||(a.extraShortcuts||[]).some(([x])=>x===k))){keys.append(el('dt','',k),el('dd','',label));}
  }
  $('[data-mx-alarm-time]').addEventListener('change',e=>{saved.alarm.time=e.target.value||'07:00';saved.alarm.fired='';persist();render();});
  $('[data-mx-alarm-on]').addEventListener('change',e=>{saved.alarm.on=e.target.checked;if(saved.alarm.on&&!saved.alarm.id){const cur=a.current?.();if(cur){saved.alarm.id=cur.id;saved.alarm.title=cur.title;}}persist();render();});
  dialog.addEventListener('click',e=>{const action=e.target.closest('[data-mx]')?.dataset.mx;
   if(action==='close')close();else if(action==='surprise'){surprise();close();}else if(action==='export')exportBackup();else if(action==='import')$('[data-mx-file]').click();
   else if(action==='alarm-use'){const cur=a.current?.();if(cur){saved.alarm.id=cur.id;saved.alarm.title=cur.title;saved.alarm.on=true;persist();render();}}});
  return Object.freeze({open,close,surprise,setSleep});
 }
 window.SterlingMediaExtras=Object.freeze({mount});
})();
