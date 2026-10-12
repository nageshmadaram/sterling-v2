import { useState, useCallback, type Dispatch, type SetStateAction } from 'react';
/** A UI selection cached locally and mirrored to the authenticated preference DB. */
export function useSavedSelection<T extends string | boolean>(key: string | undefined, fallback: T): [T, Dispatch<SetStateAction<T>>] {
 const [value,setValue]=useState<T>(()=>{if(!key)return fallback;try{const parsed=JSON.parse(localStorage.getItem(`sterling.selection.${key}`)??'null');return typeof parsed===typeof fallback?parsed:fallback;}catch{return fallback;}});
 const update=useCallback<Dispatch<SetStateAction<T>>>(next=>setValue(previous=>{const resolved=typeof next==='function'?next(previous):next;if(key)try{localStorage.setItem(`sterling.selection.${key}`,JSON.stringify(resolved));}catch{/* in-memory still usable */}return resolved;}),[key]);
 return [value,update];
}
