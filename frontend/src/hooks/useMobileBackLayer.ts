import {useEffect,useRef} from 'react';
type Layer={id:string;close:()=>void;previous:unknown};
const layers:Layer[]=[];
let sequence=0;
if(typeof window!=='undefined')window.addEventListener('popstate',()=>{
 const layer=layers[layers.length-1];if(!layer||history.state?.sterlingLayer===layer.id)return;
 layers.pop();layer.close();
});
/** A mobile Back press dismisses the top UI layer, never submits an action. */
export function useMobileBackLayer(active:boolean,onClose:()=>void,name:string){
 const closeRef=useRef(onClose);closeRef.current=onClose;
 useEffect(()=>{
  if(!active||window.innerWidth>900)return;
  const layer:Layer={id:`${name}:${++sequence}`,close:()=>closeRef.current(),previous:history.state};
  layers.push(layer);history.pushState({...history.state,sterlingLayer:layer.id},'');
  return()=>{const index=layers.indexOf(layer);if(index>=0){const above=layers[index+1];if(above && (above.previous as {sterlingLayer?:string}|null)?.sterlingLayer===layer.id)above.previous=layer.previous;layers.splice(index,1);}if(history.state?.sterlingLayer===layer.id)history.replaceState(layer.previous,'');};
 },[active,name]);
}
