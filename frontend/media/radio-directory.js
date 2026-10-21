/* Shared source editor and browse facets for hosted and standalone Radio. */
(()=>{
 const genres=['news','pop','rock','jazz','classical','talk','dance','devotional'];
 const labels={news:'News & talk',pop:'Pop',rock:'Rock',jazz:'Jazz',classical:'Classical',talk:'Talk',dance:'Dance',devotional:'Devotional'};
 const match=(station,filters={})=>(!filters.language||(station.languages||[]).includes(filters.language))&&(!filters.country||station.country===filters.country)&&(!filters.tag||(station.tags||[]).includes(filters.tag))&&(!filters.secure||station.streamUrl?.startsWith('https:'));
 function mount({getConfig,onFilter,onSources,catalog,artwork,onPlay}){
  const doc=document,controls=[],dialog=doc.createElement('dialog');dialog.className='radio-source-dialog';dialog.setAttribute('aria-label','Radio sources');
  dialog.innerHTML='<header><div><small>YOUR RADIO, YOUR WAY</small><h2>Station sources</h2></div><button type="button" data-close aria-label="Close radio sources">✕</button></header><p>Add a paged directory, M3U playlist or JSON station list. Settings sync with your Sterling account. Public feeds must allow browser access.</p><div class="radio-source-list"></div><form><label>Source name<input name="name" maxlength="80" required placeholder="My stations"></label><label>Source URL<input name="url" type="url" maxlength="2048" required placeholder="https://example.com/radio.m3u"></label><label>Format<select name="kind"><option value="m3u">M3U / M3U8 playlist</option><option value="json">JSON station list</option><option value="directory">Paged Radio Browser API</option><option value="stream">Single station stream</option></select></label><div class="radio-source-actions"><button type="submit">Save source</button><button type="button" data-cancel-edit hidden>Cancel edit</button></div></form><p class="radio-source-notice" role="status"></p><footer><button type="button" data-reload>Reload sources</button><button type="button" data-defaults>Restore default directory</button></footer>';
  doc.body.append(dialog);let returnFocus=null,editing=null,lastSnapshot=null;
  const close=()=>{dialog.close();if(returnFocus?.isConnected)returnFocus.focus({preventScroll:true});};
  function open(){returnFocus=doc.activeElement;renderSources();if(!dialog.open)dialog.showModal();}
  const configured=()=>window.SterlingRadioCatalog.normalizeSources(getConfig().catalogSources);
  function clearForm(){editing=null;dialog.querySelector('form').reset();dialog.querySelector('[data-cancel-edit]').hidden=true;}
  function save(next){onSources(next);renderSources();}
  function renderSources(){
   const list=dialog.querySelector('.radio-source-list');list.replaceChildren();
   for(const source of configured()){
    const row=doc.createElement('article'),heading=doc.createElement('strong'),address=doc.createElement('small'),status=doc.createElement('p'),actions=doc.createElement('div');
    heading.textContent=source.name;address.textContent=source.url;const state=lastSnapshot?.sources.find(item=>item.id===source.id);
    status.textContent=state?`${state.status} · ${state.count.toLocaleString()} added${state.error?' · '+state.error:''}`:source.enabled?'Ready to load':'Disabled';status.dataset.sourceStatus=source.id;
    for(const [action,label] of [['toggle',source.enabled?'Disable':'Enable'],['edit','Edit'],['delete','Delete']]){const button=doc.createElement('button');button.type='button';button.textContent=label;button.dataset.sourceAction=action;button.dataset.sourceId=source.id;button.setAttribute('aria-label',`${label} ${source.name}`);actions.append(button);}
    row.append(heading,address,status,actions);list.append(row);
   }
   if(!list.children.length)list.textContent='No feeds configured. Your built-in stations are still available.';
  }
  dialog.addEventListener('click',event=>{
   if(event.target.closest('[data-close]'))close();
   const button=event.target.closest('[data-source-action]');
   if(button){const next=configured(),index=next.findIndex(item=>item.id===button.dataset.sourceId);if(index<0)return;
    if(button.dataset.sourceAction==='delete'){next.splice(index,1);clearForm();save(next);}
    else if(button.dataset.sourceAction==='toggle'){next[index].enabled=!next[index].enabled;save(next);}
    else{editing=next[index].id;const form=dialog.querySelector('form');for(const field of ['name','url','kind'])form.elements.namedItem(field).value=next[index][field];dialog.querySelector('[data-cancel-edit]').hidden=false;form.elements.namedItem('name').focus();}
   }
   if(event.target.closest('[data-cancel-edit]'))clearForm();
   if(event.target.closest('[data-reload]'))onSources(configured());
   if(event.target.closest('[data-defaults]')){clearForm();save(window.SterlingRadioCatalog.defaults());}
  });
  dialog.addEventListener('cancel',event=>{event.preventDefault();close();});dialog.addEventListener('keydown',event=>event.stopPropagation());
  dialog.querySelector('input[name=url]').addEventListener('change',event=>{if(editing)return;const value=event.target.value;const kind=/\/json\/stations(?:[?\/]|$)/i.test(value)?'directory':/\.json(?:[?]|$)/i.test(value)?'json':/\.m3u8?(?:[?]|$)/i.test(value)?'m3u':null;if(kind)dialog.querySelector('select[name=kind]').value=kind;});
  dialog.addEventListener('submit',event=>{
   event.preventDefault();const form=dialog.querySelector('form'),raw=Object.fromEntries(new FormData(form));
   const next=configured(),source=window.SterlingRadioCatalog.normalizeSources([{...raw,id:editing||`source-${Date.now().toString(36)}`,enabled:editing?next.find(item=>item.id===editing)?.enabled!==false:true}])[0];
   const notice=dialog.querySelector('.radio-source-notice');
   if(!source){notice.textContent='Use a valid HTTPS feed URL without embedded credentials.';return;}
   if(next.some(item=>item.url===source.url&&item.id!==editing)){notice.textContent='This source URL is already in your list.';return;}
   if(!editing&&next.length>=12){notice.textContent='You can configure up to 12 sources.';return;}
   const index=next.findIndex(item=>item.id===editing);if(index>=0)next[index]=source;else next.push(source);
   clearForm();notice.textContent='Source settings saved. Loading stations…';save(next);
  });
  for(const id of ['sp-cards-grid','ytm-cards-grid']){
   const grid=doc.getElementById(id);if(!grid)continue;
   const block=doc.createElement('div');block.className='radio-directory-tools';
   block.innerHTML='<div class="radio-directory-summary"><span role="status"></span><button type="button" data-sources>Manage sources</button><button type="button" data-clear-filters hidden>Clear filters</button><button type="button" data-pause>Pause loading</button></div><details class="radio-directory-filters"><summary>Filter stations</summary><div class="radio-directory-selects"><label>Language<select data-filter="language" aria-label="Radio language"></select></label><label>Country<select data-filter="country" aria-label="Radio country"></select></label><label>Genre<select data-filter="tag" aria-label="Radio genre"></select></label><label class="radio-secure-filter"><input type="checkbox" data-filter="secure">HTTPS only</label></div></details><div class="radio-genre-chips" aria-label="Radio genres"></div>';
   const chips=block.querySelector('.radio-genre-chips');
   for(const tag of ['',...genres]){const button=doc.createElement('button');button.type='button';button.dataset.genre=tag;chips.append(button);}
   block.addEventListener('click',event=>{if(event.target.closest('[data-sources]'))open();if(event.target.closest('[data-clear-filters]'))onFilter({});if(event.target.closest('[data-pause]')){if(catalog.snapshot().loading)catalog.pause();else void catalog.resume();}const genre=event.target.closest('[data-genre]');if(genre)onFilter({...getConfig().catalogFilters,tag:genre.dataset.genre});});
   block.addEventListener('change',event=>{const control=event.target.closest('[data-filter]');if(control)onFilter({...getConfig().catalogFilters,[control.dataset.filter]:control.type==='checkbox'?control.checked:control.value});});
   grid.before(block);controls.push(block);
  }
  const browse=doc.createElement('section');browse.id='radio-directory-browse';browse.innerHTML='<h2>Find your next favourite</h2><div class="media-browse-grid"></div>';
  doc.getElementById('sp-recents-section')?.after(browse);
  const world=doc.createElement('div');world.id='radio-world-shelves';browse.after(world);
  const worldRows=[];for(const tag of ['news','jazz','pop']){const section=doc.createElement('section');section.className='radio-world-shelf';section.innerHTML='<header><h2></h2><button type="button">Show all</button></header><div></div>';section.querySelector('button').addEventListener('click',()=>onFilter({language:'',country:'',tag,secure:false},true));world.append(section);worldRows.push({tag,section,key:''});}
  const tiles=[];for(const tag of genres){const button=doc.createElement('button');button.type='button';button.dataset.genre=tag;button.innerHTML='<strong></strong><small></small><img alt="" loading="lazy" decoding="async">';button.querySelector('strong').textContent=labels[tag];button.addEventListener('click',()=>onFilter({language:'',country:'',tag,secure:false},true));button.querySelector('img').addEventListener('error',event=>{event.currentTarget.dataset.failedSrc=event.currentTarget.getAttribute('src');event.currentTarget.hidden=true;});browse.lastElementChild.append(button);tiles.push(button);}
  function update(snapshot,basePool){
   lastSnapshot=snapshot;const filters=getConfig().catalogFilters||{},counts={language:new Map(),country:new Map(),tag:new Map()},samples=new Map(),picks=new Map();
   const add=(map,key)=>{if(key)map.set(key,(map.get(key)||0)+1);};
   for(const station of basePool){
    if(match(station,{...filters,language:'',tag:''}))for(const language of station.languages||[])add(counts.language,language);
    if(match(station,{...filters,country:'',tag:''}))add(counts.country,station.country);
    if(match(station,{...filters,tag:''}))for(const tag of new Set(station.tags||[])){add(counts.tag,tag);if(!samples.has(tag))samples.set(tag,station);if(!picks.has(tag))picks.set(tag,[]);if(picks.get(tag).length<8)picks.get(tag).push(station);}
   }
   const filtered=basePool.filter(station=>match(station,filters)),anyFilter=Boolean(filters.language||filters.country||filters.tag||filters.secure);
   doc.documentElement.dataset.radioDirectoryFiltered=String(anyFilter);
   browse.hidden=getConfig().activeCategory!=='all'||anyFilter||doc.documentElement.dataset.radioView!=='home';world.hidden=browse.hidden;
   for(const block of controls){
    block.hidden=getConfig().activeCategory==='podcasts'||doc.documentElement.dataset.radioView==='library';
    block.querySelector('[data-clear-filters]').hidden=!anyFilter;
    block.querySelector('summary').textContent=anyFilter?`Filters · ${[filters.language,filters.country,filters.tag,filters.secure?'HTTPS':''].filter(Boolean).join(' · ')} · ${filtered.length.toLocaleString()} stations`:'Filter stations';
    const status=block.querySelector('[role=status]');
    status.textContent=`${snapshot.stations.length.toLocaleString()} stations loaded${snapshot.loading?' · Loading full directory…':snapshot.complete?' · Sources up to date':' · Loading paused or source needs attention'}${snapshot.reportedTotal?' · Provider: '+snapshot.reportedTotal.toLocaleString()+' entries (includes unavailable)':''}`;
    const pause=block.querySelector('[data-pause]');pause.textContent=snapshot.loading?'Pause loading':'Resume / retry';pause.hidden=snapshot.complete;
    for(const field of ['language','country','tag']){
     const select=block.querySelector(`[data-filter="${field}"]`);if(doc.activeElement===select)continue;
     const options=[['',`All ${field==='tag'?'genres':field==='country'?'countries':'languages'}`],...[...counts[field]].sort((a,b)=>a[0].localeCompare(b[0])).map(([key,count])=>[key,`${key} (${count.toLocaleString()})`])];
     if(filters[field]&&!options.some(([key])=>key===filters[field]))options.push([filters[field],`${filters[field]} (0)`]);
     const signature=JSON.stringify(options);if(select.dataset.options!==signature){select.replaceChildren(...options.map(([value,label])=>new Option(label,value)));select.dataset.options=signature;}select.value=filters[field]||'';
    }
    block.querySelector('[data-filter="secure"]').checked=Boolean(filters.secure);
    for(const button of block.querySelectorAll('[data-genre]')){const tag=button.dataset.genre,count=tag?(counts.tag.get(tag)||0):basePool.filter(station=>match(station,{...filters,tag:''})).length;button.textContent=`${tag?labels[tag]:'All genres'} ${count.toLocaleString()}`;button.setAttribute('aria-pressed',String((filters.tag||'')===tag));}
   }
   for(const button of tiles){const tag=button.dataset.genre,count=counts.tag.get(tag)||0;button.hidden=count===0;button.querySelector('small').textContent=`${count.toLocaleString()} stations`;const image=button.querySelector('img'),sample=samples.get(tag);image.hidden=!sample;if(sample){const src=artwork(sample);image.hidden=image.dataset.failedSrc===src;if(image.getAttribute('src')!==src)image.src=src;}}
   for(const row of worldRows){
    const stations=picks.get(row.tag)||[],count=counts.tag.get(row.tag)||0;row.section.hidden=count===0;row.section.querySelector('h2').textContent=`${labels[row.tag]} · ${count.toLocaleString()} stations`;
    const key=stations.map(station=>station.id).join('|');if(row.key===key)continue;row.key=key;const strip=row.section.lastElementChild;strip.replaceChildren();
    for(const station of stations){const button=doc.createElement('button');button.type='button';button.className='media-station-album';button.setAttribute('aria-label',`Listen to ${station.title}`);const image=doc.createElement('img');image.alt='';image.loading='lazy';image.decoding='async';image.src=artwork(station);image.addEventListener('error',()=>{image.style.visibility='hidden';});const title=doc.createElement('strong'),subtitle=doc.createElement('small');title.textContent=station.title;subtitle.textContent=station.country||station.subtitle;button.append(image,title,subtitle);button.addEventListener('click',()=>onPlay?.(station.id));strip.append(button);}
   }
   if(dialog.open){for(const status of dialog.querySelectorAll('[data-source-status]')){const state=snapshot.sources.find(item=>item.id===status.dataset.sourceStatus);if(state)status.textContent=`${state.status} · ${state.count.toLocaleString()} added${state.error?' · '+state.error:''}`;}}
   return filtered;
  }
  return {update,openSources:open};
 }
 window.SterlingRadioDirectory=Object.freeze({mount,match});
})();
