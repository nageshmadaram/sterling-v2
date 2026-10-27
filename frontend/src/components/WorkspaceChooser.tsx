import { useEffect, useRef } from 'react';
import { createPortal } from 'react-dom';
import { SterlingLogo } from './SterlingLogo';
export type Workspace = 'kite' | 'radio' | 'tv';
export const WORKSPACE_NAMES = { kite: 'Kite', radio: 'Echo', tv: 'Space' };
const options: Array<{ id: Workspace; icon: string; detail: string }> = [
  { id: 'kite', icon: '↗', detail: 'Markets, portfolios and trading' },
  { id: 'radio', icon: '◉', detail: 'Radio, music and live stations' },
  { id: 'tv', icon: '▣', detail: 'Live television and your library' },
];
export function WorkspaceChooser({ features, administrator, onSelect, onAdmin }: {
  features: Record<Workspace, boolean>; administrator: boolean; onSelect: (id: Workspace) => void; onAdmin: () => void;
}) {
  return <div className="workspace-chooser"><div className="chooser-brand"><SterlingLogo size={28} /><span>STERLING</span></div>
    <div className="chooser-intro"><p className="workspace-eyebrow">YOUR WORKSPACE</p><h1>Choose your workspace</h1><p>One account. A place for everything.</p></div>
    <div className="chooser-options">{options.map(option => <button type="button" key={option.id} className={`chooser-option chooser-${option.id}`}
      onClick={() => !features[option.id] && administrator ? onAdmin() : onSelect(option.id)} aria-label={`${features[option.id] ? 'Open' : administrator ? 'Manage access to' : 'Request access to'} ${WORKSPACE_NAMES[option.id]}`}>
      <span className="chooser-icon" aria-hidden>{option.icon}</span><span className="chooser-copy"><strong>{WORKSPACE_NAMES[option.id]}</strong><small>{option.detail}</small>
        <span className={`chooser-status ${features[option.id] ? 'is-enabled' : ''}`}>{features[option.id] ? 'Open workspace' : administrator ? 'Manage access' : 'Request access'}</span></span><span className="chooser-arrow" aria-hidden>↗</span>
    </button>)}</div>
    {administrator && <button type="button" className="chooser-management" onClick={onAdmin}><span><strong>Administration</strong><small>Users, access requests and broker assignments</small></span><span aria-hidden>↗</span></button>}
  </div>;
}
export function WorkspaceDialog({ open, onClose, children }: { open: boolean; onClose: () => void; children: React.ReactNode }) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => { if (open && !dialog.current?.open) dialog.current?.showModal(); else if (!open && dialog.current?.open) dialog.current.close(); }, [open]);
  if (typeof document === 'undefined') return null;
  return createPortal(<dialog className="workspace-dialog" ref={dialog} aria-label="Choose your workspace" onCancel={onClose} onClose={onClose}
    onClick={event => { if (event.target === event.currentTarget) { const bounds = event.currentTarget.getBoundingClientRect(); if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) onClose(); } }}>
    <button type="button" className="chooser-close" aria-label="Close workspace selector" onClick={onClose}>✕</button>{children}
  </dialog>, document.body);
}
