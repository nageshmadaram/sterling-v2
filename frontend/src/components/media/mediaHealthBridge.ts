import {api} from '../../utils/api';
import type {MediaKind} from './mediaBridge';
const inflight=new WeakMap<Window,number>();
/** Caller must first verify event.source is its frame and origin is opaque. */
export function handleMediaHealth(kind:MediaKind,message:unknown,frame:Window):boolean {
  if(!message||typeof message!=='object')return false;
  const data=message as Record<string,unknown>;
  if(data.type!=='sterling.media.health')return false;
  if(data.kind!==kind||typeof data.requestId!=='string'||!/^health-[0-9]+$/.test(data.requestId)||!['list','sync','action'].includes(String(data.op)))return true;
  if((inflight.get(frame)||0)>=4)return true;
  let size:number;try{size=JSON.stringify(data).length;}catch{return true;}
  if(size>512000)return true;
  inflight.set(frame,(inflight.get(frame)||0)+1);
  const path=`/api/v1/media/${kind}/health`;
  const job=data.op==='list'?api.get(path):api.post(path+(data.op==='sync'?'/catalogue':''),data.body);
  void job.then(result=>frame.postMessage({type:'sterling.media.health.result',requestId:data.requestId,result},'*'))
    .catch(error=>frame.postMessage({type:'sterling.media.health.result',requestId:data.requestId,error:error.message||'Stream checks could not be reached.'},'*'))
    .finally(()=>inflight.set(frame,Math.max(0,(inflight.get(frame)||1)-1)));
  return true;
}
