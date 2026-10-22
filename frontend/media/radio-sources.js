/* Built-in Radio source library: grouped presets, two curated playlists and the
   default on/off state. Users toggle, edit, delete and restore these in the
   Station sources dialog. Checked 2026-10-22; empty Radio Browser tags start off. */
(()=>{
 const RB='https://de1.api.radio-browser.info/m3u/stations/';
 const rb=(path)=>RB+path+(path.includes('?')?'&':'?')+'hidebroken=true';
 const groups=[
  ['world','World directory'],['akashvani','Akashvani · Prasar Bharati'],['india','India'],['languages','Indian languages'],
  ['indian-genres','Indian genres'],['bands','Shortwave · AM · MW · LW'],['top','Top stations worldwide'],['mixes','Curated mixes'],
  ['telugu-247','Telugu 24/7'],['retro','Retro: SPB, Ilaiyaraaja & classics'],['rahman','A.R. Rahman'],['lofi','Lo-fi & chill'],['desi-chill','Desi chill & classical'],
 ];
 // [id, name, url, group, enabled, category, note]
 const rows=[
  ['radio-browser','Radio Browser · World directory','https://de1.api.radio-browser.info/json/stations','world',true,'','Every working station, loaded page by page (45,000+).'],
  ['rb-everything','Radio Browser · Entire database (single file)',RB.replace(/\/$/,'')+'?hidebroken=true','world',false,'','About 12 MB in one file. The World directory above already covers it.'],
  ['akashvani-all','Akashvani · All stations (daily list)','https://raw.githubusercontent.com/codito/akashvani/master/stations.json','akashvani',true,'akashvani','277 national and state stations, updated daily by github.com/codito/akashvani.'],
  ['akashvani-pbaudio','Akashvani · National (pbaudio)','builtin:akashvani-pbaudio','akashvani',true,'akashvani','News 24x7, FM Rainbow, FM Gold, Raagam and Odia.'],
  ['akashvani-search','Akashvani · Radio Browser search',rb('search?name=akashvani'),'akashvani',true,'akashvani',''],
  ['air-search','All India Radio · Radio Browser search',rb('search?name=all%20india%20radio'),'akashvani',true,'akashvani',''],
  ['india-all','India · All stations',rb('bycountrycodeexact/IN'),'india',true,'',''],
  ['india-classical','India · Classical',rb('search?countrycode=IN&tag=classical&order=clickcount&reverse=true'),'indian-genres',true,'',''],
  ['india-news','India · News & talk',rb('search?countrycode=IN&tag=news&order=clickcount&reverse=true'),'indian-genres',true,'',''],
  ['bollywood','Bollywood · Worldwide',rb('search?tag=bollywood&order=clickcount&reverse=true'),'indian-genres',true,'',''],
  ['junguler-india','India mix (junguler playlists)','https://raw.githubusercontent.com/junguler/m3u-radio-music-playlists/main/india.m3u','indian-genres',false,'',''],
  ...['hindi','telugu','tamil','malayalam','kannada','marathi','bengali','punjabi','urdu','gujarati','odia'].map(lang=>
   [`lang-${lang}`,`${lang[0].toUpperCase()+lang.slice(1)}`,rb(`bylanguageexact/${lang}`),'languages',['hindi','telugu','tamil'].includes(lang),'','']),
  ['intl-shortwave','International broadcasters (shortwave)','builtin:intl-shortwave','bands',true,'shortwave','BBC WS, RNZ Pacific, WRMI, RRI, RFI, DW, NHK World, ABC Radio Australia.'],
  ['tag-am','AM stations',rb('bytagexact/am'),'bands',true,'shortwave',''],
  ['tag-am-english','English news on AM',rb('search?language=english&tag=am&order=clickcount&reverse=true'),'bands',false,'shortwave',''],
  ['tag-shortwave','Shortwave (exact tag)',rb('bytagexact/shortwave'),'bands',true,'shortwave',''],
  ['tag-shortwave-broad','Shortwave (broad match)',rb('bytag/shortwave'),'bands',true,'shortwave',''],
  ['tag-sw','SW / HF tag',rb('bytagexact/sw'),'bands',false,'shortwave','No stations carry this tag today.'],
  ['tag-mw','Medium wave (MW)',rb('bytagexact/mw'),'bands',false,'shortwave','No stations carry this tag today.'],
  ['tag-mediumwave','Mediumwave',rb('bytagexact/mediumwave'),'bands',false,'shortwave','No stations carry this tag today.'],
  ['tag-longwave','Longwave (LW)',rb('bytagexact/longwave'),'bands',false,'shortwave','No stations carry this tag today.'],
  ['tag-ham','Ham radio',rb('bytag/ham%20radio'),'bands',true,'shortwave',''],
  ['tag-world-radio','World radio',rb('bytag/world%20radio'),'bands',true,'shortwave',''],
  ['top-click','Top 1,000 · Most played',rb('topclick/1000'),'top',true,'',''],
  ['top-vote','Top 1,000 · Most voted',rb('topvote/1000'),'top',true,'',''],
  ['hifi-320','High quality · 320 kbps+',rb('search?bitrateMin=320&order=clickcount&reverse=true'),'top',false,'',''],
  ['junguler-talk','Talk mix (junguler playlists)','https://raw.githubusercontent.com/junguler/m3u-radio-music-playlists/main/talk.m3u','mixes',false,'',''],
  ['junguler-world','World mix (junguler playlists)','https://raw.githubusercontent.com/junguler/m3u-radio-music-playlists/main/world.m3u','mixes',false,'',''],
  // Playlists on the Radio home page. Each group is one playlist; several searches can feed it.
  ['telugu-247','Telugu 24/7 melodies','builtin:telugu-247','telugu-247',true,'','Melody Radio Telugu, Radio Ala, London Telugu Radio, TORI, AIR Hyderabad A.'],
  ['desi-chill','Desi chill & classical','builtin:desi-chill','desi-chill',true,'','SomaFM Suburbs of Goa and Akashvani Raagam.'],
  ['rb-spb-tag','SPB · Radio Browser tag',rb('search?tag=spb'),'retro',true,'',''],
  ['rb-spb','SPB · Radio Browser name',rb('byname/spb'),'retro',true,'',''],
  ['rb-ilaiyaraaja','Ilaiyaraaja · Radio Browser name',rb('byname/ilaiyaraaja'),'retro',true,'','Few stations carry this exact spelling today.'],
  ['rb-ilayaraja','Ilayaraja · Radio Browser search',rb('search?name=ilayaraja'),'retro',true,'',''],
  ['rb-india-retro','Indian retro',rb('search?tag=retro&countrycode=IN'),'retro',true,'',''],
  ['rb-rahman','A.R. Rahman · Radio Browser name',rb('byname/rahman'),'rahman',true,'',''],
  ['rb-rahman-tag','A.R. Rahman · Radio Browser tag',rb('search?tag=rahman'),'rahman',true,'',''],
  ['rb-lofi-tag','Lo-fi · tag',rb('search?tag=lofi&order=clickcount&reverse=true'),'lofi',true,'',''],
  ['rb-lofi-name','Lo-fi · name',rb('search?name=lofi&order=clickcount&reverse=true'),'lofi',true,'',''],
  ['rb-bollywood-lofi','Bollywood lo-fi',rb('byname/bollywood%20lofi'),'lofi',true,'','Few stations carry this exact name today.'],
  ['junguler-oldies','Oldies mix (junguler playlists)','https://raw.githubusercontent.com/junguler/m3u-radio-music-playlists/main/oldies.m3u','mixes',false,'',''],
 ];
 const kindOf=url=>url.startsWith('builtin:')?'builtin':/\/json\/stations$/.test(url)?'directory':/\.json(?:\?|$)/.test(url)?'json':'m3u';
 const sources=rows.map(([id,name,url,group,enabled,category,note])=>({id,name,url,kind:kindOf(url),group,enabled,category,note}));
 const PB='https://airhlspush.pc.cdn.bitgravity.com/httppush/hlspbaudio';
 const pb=(n,name,lang,group)=>`#EXTINF:-1 tvg-country="IN" tvg-language="${lang}" group-title="${group}",${name}\n${PB}${n}/hlspbaudio${n}_Auto.m3u8`;
 const playlists={
  'akashvani-pbaudio':['#EXTM3U',
   pb('002','Akashvani News 24x7','Hindi;English','Akashvani News'),
   pb('004','Akashvani FM Rainbow Delhi','Hindi;English','Akashvani FM'),
   pb('005','Akashvani FM Gold Delhi','Hindi;English','Akashvani FM'),
   pb('139','Akashvani Raagam Classical','Classical','Akashvani Classical'),
   pb('137','Akashvani Odia','Odia','Akashvani Regional')].join('\n'),
  // From the user's list, 2026-10-22. Zeno (401), Virijallu (403) and both Hungama streams (no
  // response) were down when checked and are left out; TORI timed out but is kept.
  'telugu-247':['#EXTM3U',
   ...[['Melody Radio Telugu','https://a1.asurahosting.com:9580/radio.mp3'],
    ['Radio Ala 90.8 FM Kakinada','https://mahi.radioca.st/stream'],
    ['TORI · Telugu One Radio','https://stream.teluguoneradio.com:9700/;stream/1'],
    ['London Telugu Radio','https://c8.radioboss.fm/stream/33'],
    ['AIR Hyderabad A (738 kHz MW)','https://air.pc.cdn.bitgravity.com/air/live/pbaudio032/playlist.m3u8'],
   ].map(([name,url])=>`#EXTINF:-1 tvg-country="IN" tvg-language="Telugu" group-title="Telugu 24/7",${name}\n${url}`)].join('\n'),
  'desi-chill':['#EXTM3U',
   '#EXTINF:-1 tvg-country="US" tvg-language="English" group-title="Desi Chill",SomaFM · Suburbs of Goa\nhttps://ice1.somafm.com/suburbsofgoa-128-mp3',
   '#EXTINF:-1 tvg-country="IN" tvg-language="Classical" group-title="Desi Chill",Akashvani Raagam (24/7 classical)\nhttps://air.pc.cdn.bitgravity.com/air/live/pbaudio139/playlist.m3u8'].join('\n'),
  'intl-shortwave':['#EXTM3U',
   ...[['GB','English','BBC World Service','https://stream.live.vc.bbcmedia.co.uk/bbc_world_service'],
    ['NZ','English','RNZ Pacific','http://radionz-ice.streamguys.com/international.mp3'],
    ['US','English;Spanish','WRMI Radio Miami International (9955 kHz)','http://wrmi.listen.creek.fm/stream'],
    ['RO','English;Romanian','Radio Romania International','https://stream4.srr.ro:8443/romania-rri3'],
    ['FR','French','Radio France Internationale','http://live02.rfi.fr/rfimonde-64.mp3'],
    ['DE','English','Deutsche Welle Radio','https://dw.audiostream.io/dw/1027/mp3/64/dw08'],
    ['JP','Japanese;English','NHK World Radio','https://nhkworld-radio.nhkworld.jp/hls/live/nhkworld-radio-media2/index.m3u8'],
    ['AU','English','ABC Radio Australia','https://mediaserviceslive.akamaized.net/hls/live/2038266/ramulti/masterhq.m3u8'],
   ].map(([country,lang,name,url])=>`#EXTINF:-1 tvg-country="${country}" tvg-language="${lang}" group-title="International Shortwave",${name}\n${url}`)].join('\n'),
 };
 window.SterlingRadioPresets=Object.freeze({version:3,homePlaylists:['telugu-247','retro','rahman','lofi','desi-chill'],groups:groups.map(([id,label])=>({id,label})),sources:()=>sources.map(row=>({...row})),playlists,
  groupLabel:id=>(groups.find(row=>row[0]===id)||[])[1]||id||'My sources'});
})();
