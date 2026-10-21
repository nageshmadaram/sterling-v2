export type MediaSuggestion = {id:string;label:string;subtitle:string;artwork:string};
/** Frame-sourced suggestions are display data, never authority or markup. */
export function readMediaSuggestions(value:unknown):MediaSuggestion[] {
  if(!Array.isArray(value))return [];
  return value.slice(0,8).flatMap(item=>{
    if(!item||typeof item!=='object'||typeof item.id!=='string'||item.id.length>4096||typeof item.label!=='string')return [];
    let artwork=typeof item.artwork==='string'&&item.artwork.length<=2048&&/^https:\/\//i.test(item.artwork)&&!/[<>"'\x00-\x20]/.test(item.artwork)?item.artwork:'';
    try{const parsed=new URL(artwork);if(parsed.username||parsed.password)artwork='';}catch{artwork='';}
    return [{id:item.id,label:item.label.slice(0,200),subtitle:typeof item.subtitle==='string'?item.subtitle.slice(0,200):'',artwork}];
  });
}
