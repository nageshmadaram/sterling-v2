import { useEffect, useRef } from 'react';
import { createPortal } from 'react-dom';

export type Workspace = 'kite' | 'radio' | 'tv';
export const WORKSPACE_NAMES = { kite: 'Kite', radio: 'Echo', tv: 'Space' };
const options: Array<{ id: Workspace; icon: string; detail: string }> = [
  { id: 'kite', icon: '↗', detail: 'Markets, portfolios and trading' },
  { id: 'radio', icon: '◉', detail: 'Radio, music and live stations' },
  { id: 'tv', icon: '▣', detail: 'Live television and your library' },
];
export function WorkspaceChooser({ features, visibility, administrator, onSelect, onAdmin }: {
  features: Record<Workspace, boolean>; visibility?: Record<Workspace, boolean>; administrator: boolean; onSelect: (id: Workspace) => void; onAdmin: () => void;
}) {
  return <div className="workspace-chooser">
    <div className="chooser-intro"><h1>Choose your workspace</h1></div>
    <div className="chooser-options">{options.filter(option => visibility?.[option.id] !== false).map(option => <button type="button" key={option.id} className={`chooser-option chooser-${option.id}`}
      onClick={() => !features[option.id] && administrator ? onAdmin() : onSelect(option.id)} aria-label={`${features[option.id] ? 'Open' : administrator ? 'Manage access to' : 'Request access to'} ${WORKSPACE_NAMES[option.id]}`}>
      <span className="chooser-icon" aria-hidden><svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">{option.id==='kite'?<><path d="M3 17l6-6 4 3 8-10"/><path d="M15 4h6v6"/></>:option.id==='radio'?<><rect x="3" y="8" width="18" height="13" rx="3"/><path d="M5 8l13-5M7 13h3"/><circle cx="16" cy="15" r="2.5"/></>:<><rect x="3" y="6" width="18" height="14" rx="3"/><path d="M8 2l4 4 4-4M9 23h6"/></>}</svg></span><span className="chooser-copy"><strong>{WORKSPACE_NAMES[option.id]}</strong>
        {!features[option.id]&&<span className="chooser-status">{administrator?'Manage access':'Request access'}</span>}</span><span className="chooser-arrow" aria-hidden>↗</span>
    </button>)}</div>
    {administrator && <button type="button" className="chooser-management" onClick={onAdmin}><span><strong>Administration</strong></span><span aria-hidden>↗</span></button>}
  </div>;
}
export function WorkspaceDialog({ open, onClose, children }: { open: boolean; onClose: () => void; children: React.ReactNode }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const focusOrigin = useRef<HTMLElement | null>(null);
  useEffect(() => {
    if (open && !dialog.current?.open) {
      focusOrigin.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
      dialog.current?.showModal();
    } else if (!open) {
      if (dialog.current?.open) dialog.current.close();
      const target = focusOrigin.current;
      focusOrigin.current = null;
      if (target?.isConnected) target.focus();
    }
  }, [open]);
  if (typeof document === 'undefined') return null;
  return createPortal(<dialog className="workspace-dialog" ref={dialog} aria-label="Choose your workspace" onCancel={onClose} onClose={onClose}
    onClick={event => { if (event.target === event.currentTarget) { const bounds = event.currentTarget.getBoundingClientRect(); if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) onClose(); } }}>
    <button type="button" className="chooser-close" aria-label="Close workspace selector" onClick={onClose}>✕</button>{children}
  </dialog>, document.body);
}
