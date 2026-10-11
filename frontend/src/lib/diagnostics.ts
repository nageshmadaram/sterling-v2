import {useSyncExternalStore} from 'react';
export type Diagnostic = {ts_ms:number; level:string; message:string};
let entries:Diagnostic[]=[];
const listeners=new Set<()=>void>();
let installed=false;
export function redactDiagnostic(text:string):string {
 return text.replace(/bot\d+:[\w-]+/g,'bot[redacted]').replace(/\bBearer\s+[\w.~+\/-]+=*/gi,'Bearer [redacted]').replace(/(["']?(?:password|authorization|api[_-]?key|api[_-]?secret|access[_-]?token|request[_-]?token|refresh[_-]?token|secret|enctoken)["']?\s*[:=]\s*)["']?[^\s,"'&}]+["']?/gi,'$1[redacted]');
}
export function recordDiagnostic(level:string,message:string) {
 entries=[...entries,{ts_ms:Date.now(),level,message:redactDiagnostic(message).slice(0,4000)}].slice(-500);
 listeners.forEach(fn=>fn());
}
export function installDiagnostics() {
 if(installed)return;installed=true;
 for(const method of ['debug','info','log','warn','error'] as const){
  const original=console[method].bind(console);
  console[method]=(...args:unknown[])=>{
   original(...args);
   const message=args.map(value=>{try{return value instanceof Error?`${value.message}\n${value.stack||''}`:typeof value==='string'?value:JSON.stringify(value);}catch{return '[unserializable]';}}).join(' ');
   recordDiagnostic(method==='log'?'INFO':method.toUpperCase(),message);
  };
 }
 window.addEventListener('error',e=>recordDiagnostic('ERROR',e.message||'Resource failed to load'));
 window.addEventListener('unhandledrejection',e=>recordDiagnostic('ERROR',e.reason instanceof Error?e.reason.message:String(e.reason)));
 const originalFetch=window.fetch.bind(window);
 window.fetch=async (...args)=>{
  const start=performance.now();
  let path='request';try{const url=new URL(args[0] instanceof Request?args[0].url:String(args[0]),location.href);path=url.pathname;}catch{}
  try{const response=await originalFetch(...args);recordDiagnostic(response.ok?'DEBUG':'ERROR',`HTTP ${response.status} ${path} (${Math.round(performance.now()-start)} ms)`);return response;}
  catch(error){recordDiagnostic('ERROR',`HTTP ${path}: ${error instanceof Error?error.message:String(error)}`);throw error;}
 };
 recordDiagnostic('INFO','Frontend diagnostics started');
}
const subscribe=(fn:()=>void)=>{listeners.add(fn);return()=>{listeners.delete(fn);};};
export function useFrontendDiagnostics(){return useSyncExternalStore(subscribe,()=>entries);}
