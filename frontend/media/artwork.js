/* Broadcaster identity from public IPTV metadata; no guessed movie/album covers. */
(()=>{
  const safe=url=>{
    if(typeof url!=='string'||url.length>2048||!/^https:\/\//i.test(url)||/[<>"'\x00-\x20]/.test(url))return false;
    try{const parsed=new URL(url);return !parsed.username&&!parsed.password;}catch{return false;}
  };
  const normalize=value=>String(value||'').normalize('NFKD').replace(/[\u0300-\u036f]/g,'').toLowerCase().replace(/\b(live|hd|full hd|global|us|india)\b/g,'').replace(/[^\p{L}\p{N}]/gu,'');
  let names=new Map(),logos=new Map(),pending=null,nextFetch=0,failures=0;
  const ready=new Set(),loading=new Set(),checked=new Map(),queue=[];let inflight=0,paintTimer;
  const announce=()=>{clearTimeout(paintTimer);paintTimer=setTimeout(()=>window.dispatchEvent(new Event('sterling-artwork-update')),100);};
  function pump(){
    while(inflight<2&&queue.length){const url=queue.shift();inflight++;const image=new Image();
      const timer=setTimeout(()=>finish(false),7000);let complete=false;
      const finish=ok=>{if(complete)return;complete=true;clearTimeout(timer);image.onload=image.onerror=null;if(ok){ready.add(url);if(ready.size>256)ready.delete(ready.values().next().value);announce();}loading.delete(url);inflight--;pump();};
      image.onload=()=>finish(true);image.onerror=()=>finish(false);image.src=url;
    }
  }
  function usable(url){
    if(!url)return '';if(ready.has(url))return url;
    if(!loading.has(url)&&Date.now()-(checked.get(url)||0)>5*60*1000&&queue.length<64){checked.set(url,Date.now());if(checked.size>256)checked.delete(checked.keys().next().value);loading.add(url);queue.push(url);pump();}
    return '';
  }
  async function load(){
    if(pending)return pending;if(Date.now()<nextFetch)return;
    nextFetch=Date.now()+30*60*1000;const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),8000);
    pending=Promise.all(['channels','logos'].map(async kind=>{const res=await fetch(`https://iptv-org.github.io/api/${kind}.json`,{signal:controller.signal,cache:'default'});if(!res.ok)throw Error('Artwork metadata unavailable');return res.json();})).then(([channels,images])=>{
      if(!Array.isArray(channels)||!Array.isArray(images))throw Error('Invalid artwork metadata');
      const nextNames=new Map(),nextLogos=new Map();
      for(const row of channels){if(!row||row.is_nsfw||typeof row.id!=='string'||typeof row.name!=='string')continue;for(const name of [row.name,...(Array.isArray(row.alt_names)?row.alt_names:[])]){const key=normalize(name);if(key)nextNames.set(key,nextNames.has(key)&&nextNames.get(key)!==row.id?null:row.id);}}
      for(const row of images){if(!row||typeof row.channel!=='string'||!safe(row.url)||row.in_use===false)continue;if(!nextLogos.has(row.channel)||(Array.isArray(row.tags)&&row.tags.includes('square')))nextLogos.set(row.channel,row.url);}
      names=nextNames;logos=nextLogos;failures=0;window.dispatchEvent(new Event('sterling-artwork-update'));
    }).catch(()=>{controller.abort();failures=Math.min(failures+1,5);nextFetch=Date.now()+Math.min(5*60*1000,30000*2**(failures-1));}).finally(()=>{clearTimeout(timer);pending=null;});return pending;
  }
  window.SterlingArtwork=Object.freeze({resolveTv(channel){void load();const aliases={'redbulltvglobalsports':'Red Bull TV','bbcearthus':'BBC Earth','aajtak': 'Aaj Tak','tv9telugu':'TV9 Telugu'};
    const alias=aliases[String(channel.name||'').toLowerCase().replace(/[^a-z0-9]/g,'')];
    const id=channel.channelId?.split('@')[0]||names.get(normalize(alias||channel.name));return usable(logos.get(id));},refresh:load});
  window.addEventListener('online',()=>{checked.clear();announce();if(!names.size){nextFetch=0;void load();}});
  document.addEventListener('visibilitychange',()=>{if(!document.hidden&&names.size)void load();});
})();
