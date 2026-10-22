/* Radio bands: Shortwave, Medium wave (AM), Longwave and Ham. Each station gets a band and, when its
   source or name says so, a frequency in kHz. The tuner uses the band's own range, step and markers. */
(()=>{
 const BANDS={
  sw:{id:'sw',category:'shortwave',label:'Shortwave',short:'SW',title:'World band receiver',min:2300,max:26100,step:5,start:9410,
   marks:[['120m',2300,2495],['90m',3200,3400],['75m',3900,4000],['60m',4750,5060],['49m',5900,6200],['41m',7200,7450],['31m',9400,9900],['25m',11600,12100],['22m',13570,13870],['19m',15100,15800],['16m',17480,17900],['15m',18900,19020],['13m',21450,21850],['11m',25670,26100]]},
  mw:{id:'mw',category:'mediumwave',label:'Medium wave (AM)',short:'AM',title:'AM broadcast tuner',min:530,max:1710,step:9,start:1008,
   marks:[['Low',530,800],['Mid',800,1200],['High',1200,1710]]},
  lw:{id:'lw',category:'longwave',label:'Longwave',short:'LW',title:'Longwave tuner',min:148,max:283,step:9,start:198,
   marks:[['LW',148,283]]},
  ham:{id:'ham',category:'ham',label:'Ham radio',short:'HAM',title:'Amateur band receiver',min:1800,max:29700,step:1,start:14200,
   marks:[['160m',1800,2000],['80m',3500,4000],['40m',7000,7300],['30m',10100,10150],['20m',14000,14350],['17m',18068,18168],['15m',21000,21450],['12m',24890,24990],['10m',28000,29700]]},
 };
 const BY_CATEGORY=Object.fromEntries(Object.values(BANDS).map(b=>[b.category,b]));
 // Source hints from the built-in library (media/radio-sources.js).
 const SOURCE_BAND={'tag-am':'mw','tag-am-english':'mw','tag-mw':'mw','tag-mediumwave':'mw','tag-shortwave':'sw','tag-shortwave-broad':'sw','tag-sw':'sw','intl-shortwave':'sw','tag-world-radio':'sw','tag-longwave':'lw','tag-ham':'ham'};
 const TAG_BAND=[[/^(ham|ham radio|amateur radio|amateur)$/,'ham'],[/^(longwave|long wave|lw)$/,'lw'],[/^(shortwave|short wave|sw|world band|hf)$/,'sw'],[/^(am|mw|mediumwave|medium wave|am radio)$/,'mw']];
 function frequency(text){
  const t=String(text||'');let m;
  if((m=t.match(/(\d{1,2}[.,]\d{1,3})\s*MHz/i))){const khz=Math.round(parseFloat(m[1].replace(',','.'))*1000);if(khz>=1600&&khz<=30000)return khz;}
  if((m=t.match(/(\d{3,5})\s*kHz/i)))return Number(m[1]);
  if((m=t.match(/\b(?:AM|MW|LW|SW)\s*-?\s*(\d{3,5})\b/i)))return Number(m[1]);
  if((m=t.match(/\b(\d{3,4})\s*(?:AM|MW)\b/i)))return Number(m[1]);
  return null;
 }
 const fromFrequency=khz=>!khz?null:khz>=148&&khz<=283?'lw':khz>=520&&khz<=1710?'mw':khz>1710&&khz<=30000?'sw':null;
 /** {band, khz} for a station. Order: a stated frequency, the source, then the station's tags. */
 function classify(station,source){
  let khz=Number(station.freqKhz)||frequency(station.title)||frequency(station.name)||null;
  if(khz&&(khz<148||khz>30000))khz=null;
  let band=SOURCE_BAND[source?.id]||null;
  const tags=station.tags||[];
  if(band===null)for(const tag of tags){const hit=TAG_BAND.find(([re])=>re.test(tag));if(hit){band=hit[1];break;}}
  const byFreq=fromFrequency(khz);
  // A stated frequency outranks the source label (an "AM" list also carries some SW and LW stations),
  // except for ham, whose bands sit inside the shortwave range.
  if(byFreq&&band!=='ham')band=byFreq;
  return {band,khz};
 }
 function meterBand(band,khz){const b=BANDS[band];if(!b||!khz)return '';const hit=b.marks.find(([,from,to])=>khz>=from&&khz<=to);return hit?hit[0]:'';}
 window.SterlingRadioBands=Object.freeze({BANDS,BY_CATEGORY,classify,frequency,meterBand,list:()=>Object.values(BANDS)});
})();
