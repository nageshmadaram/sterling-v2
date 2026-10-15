import { useState, useCallback, type Dispatch, type SetStateAction } from 'react';
/** Only the current page survives refresh; transient filters/search stay in memory. */
export function useSavedSelection<T extends string | boolean>(key: string | undefined, fallback: T): [T, Dispatch<SetStateAction<T>>] {
 const navigation=Boolean(key?.startsWith('workspace.'));
 const [value,setValue]=useState<T>(()=>{if(!navigation||!key)return fallback;try{const parsed=JSON.parse(sessionStorage.getItem(`sterling.page.${key}`)??'null');return typeof parsed===typeof fallback?parsed:fallback;}catch{return fallback;}});
 const update=useCallback<Dispatch<SetStateAction<T>>>(next=>setValue(previous=>{const resolved=typeof next==='function'?next(previous):next;if(navigation&&key)try{sessionStorage.setItem(`sterling.page.${key}`,JSON.stringify(resolved));}catch{}return resolved;}),[key,navigation]);
 return [value,update];
}
