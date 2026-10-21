import React,{useState,type ReactNode,type ReactElement} from 'react';
import {createPortal} from 'react-dom';
import {useMobileBackLayer} from '../../../hooks/useMobileBackLayer';
/** Primary actions stay light; history, status and evidence open in one dismissible panel. */
export function StrategyBoardFooter({name,children,onReset}:{name:string;children:ReactNode;onReset?:()=>void}){
 const [open,setOpen]=useState(false);
 useMobileBackLayer(open,()=>setOpen(false),'strategy-info');
 const items=React.Children.toArray(children);
 const first=items[0] as ReactElement<{children?:ReactNode}>;
 const controls=React.isValidElement(first)?React.Children.toArray(first.props.children):[];
 const primary=controls.filter(c=>React.isValidElement(c)&&c.type==='button');
 const secondary=controls.filter(c=>!primary.includes(c));
 const modes=secondary.some(c=>React.isValidElement<{className?:string}>(c)&&(c.props.className?.includes('snapback-scan-modes')||c.props.className?.includes('strategy-scan-modes')));
 // SuperTrend's source/exit controls are already compact; keep their original row.
 if(name==='SuperTrend')return <section className="sb-strategy-footer" aria-label={`${name} controls and information`}>{children}</section>;
 // Every other strategy: no scan, stop, modes, history or info buttons on the board.
 // Scans run from the dock's re-scan control; modes use the shared Modes filter.
 // Status and safety text stays, as one quiet line, because it explains what
 // the board can and cannot do right now.
 const notes=secondary.filter(c=>!(React.isValidElement<{className?:string}>(c)&&/scan-modes/.test(c.props.className??'')));
 // Blockers, errors and warnings (plain <p> lines or role=alert/status) stay
 // visible; history ledgers, timelines and help panels do not.
 const heavy=(c:React.ReactNode)=>{
  if(!React.isValidElement<{className?:string}>(c))return true;
  const t=c.type as {name?:string;displayName?:string}|string;
  const typeName=typeof t==='string'?t:(t.displayName||t.name||'');
  return /History|Timeline|Ledger|ScanCost/.test(typeName)||(typeName==='details'&&/strategy-help/.test(c.props.className??''));
 };
 const messages=items.slice(1).filter(c=>!heavy(c));
 notes.push(...messages);
 return notes.length?<section className="sb-strategy-footer sb-strategy-notes" aria-label={`${name} status`} style={{display:'flex',flexWrap:'wrap',gap:'2px 10px',alignItems:'center',padding:'4px 12px',fontSize:11,color:'var(--k-dim)'}}>{notes}</section>:null;
 return <section className="sb-strategy-footer" aria-label={`${name} controls and information`}>
  <div className="strategy-primary-actions">{primary}{modes&&<button type="button" className="sb-tool" onClick={()=>setOpen(true)}>Modes</button>}{name==='Gamma Unwind'&&<button type="button" className="sb-tool" onClick={()=>setOpen(true)}>History</button>}<button type="button" className="strategy-info-trigger" aria-label={`${name} information and options`} aria-expanded={open} onClick={()=>setOpen(true)}>ⓘ</button></div>
  {open&&createPortal(<div className="strategy-options-backdrop" onClick={e=>{if(e.target===e.currentTarget)setOpen(false);}}><section role="dialog" aria-modal="true" aria-label={`${name} options`} className="strategy-options-panel" onKeyDown={e=>{if(e.key==='Escape')setOpen(false);}}><header><strong>{name}</strong>{onReset&&<button type="button" aria-label="Reset strategy view" title="Reset view; keep Config unchanged" onClick={onReset}>↺</button>}<button type="button" autoFocus aria-label="Close strategy options" onClick={()=>setOpen(false)}>×</button></header><div className="strategy-options-body"><div className="strategy-option-controls">{secondary.map((c,i)=>React.isValidElement<{className?:string;open?:boolean}>(c)&&c.props.className?.includes('snapback-scan-modes')?React.cloneElement(c,{open:true,key:i}):c)}</div>{items.slice(1)}</div></section></div>,document.body)}
 </section>;
}
