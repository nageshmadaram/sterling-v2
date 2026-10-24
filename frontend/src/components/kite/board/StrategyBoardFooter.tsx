import React, { useCallback, useRef, useState, type ReactNode, type ReactElement } from 'react';
import { createPortal } from 'react-dom';
import { useMobileBackLayer } from '../../../hooks/useMobileBackLayer';
import { useFocusTrap, useScrollLock } from '../replay/primitives/useFocusTrap';

/** Keep strategy status compact; Gamma history remains reachable from its board. */
export function StrategyBoardFooter({ name, children, onReset }: {
  name: string; children: ReactNode; onReset?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const panelRef = useRef<HTMLElement>(null);
  const close = useCallback(() => setOpen(false), []);
  useMobileBackLayer(open, close, 'strategy-info');
  useFocusTrap(panelRef, open, { onEscape: close });
  useScrollLock(open);
  const items = React.Children.toArray(children);
  const first = items[0] as ReactElement<{ children?: ReactNode }>;
  const controls = React.isValidElement(first) ? React.Children.toArray(first.props.children) : [];
  const secondary = controls.filter(c => !(React.isValidElement(c) && c.type === 'button'));
  if (name === 'SuperTrend') return <section className="sb-strategy-footer" aria-label={`${name} controls and information`}>{children}</section>;

  const interactive = (c: ReactNode): boolean => {
    if (!React.isValidElement<{ className?: string; children?: ReactNode; onClick?: unknown }>(c)) return false;
    if (typeof c.type === 'string' && ['button', 'a', 'select', 'input', 'label', 'details', 'summary'].includes(c.type)) return true;
    if (c.props.onClick || /help|scan-modes/.test(c.props.className ?? '')) return true;
    return React.Children.toArray(c.props.children).some(interactive);
  };
  const heavy = (c: ReactNode) => {
    if (!React.isValidElement<{ className?: string }>(c)) return true;
    const t = c.type as { name?: string; displayName?: string } | string;
    const typeName = typeof t === 'string' ? t : (t.displayName || t.name || '');
    return /History|Timeline|Ledger|ScanCost/.test(typeName) || (typeName === 'details' && /strategy-help/.test(c.props.className ?? ''));
  };
  const notes = secondary.filter(c => !interactive(c));
  notes.push(...items.slice(1).filter(c => !heavy(c) && (!interactive(c)
    || (React.isValidElement<{ role?: string }>(c) && c.props.role === 'alert'))));
  const hasHistory = name === 'Gamma Unwind';
  return <>
    {(notes.length > 0 || hasHistory) && <section className="sb-strategy-footer sb-strategy-notes" aria-label={`${name} status`} style={{ display: 'flex', flexWrap: hasHistory ? 'wrap' : 'nowrap', gap: 10, alignItems: 'center', padding: '4px 12px', fontSize: 11, color: 'var(--k-dim)', whiteSpace: hasHistory ? 'normal' : 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', maxWidth: '100%' }}>
      {hasHistory && <button type="button" className="sb-tool" aria-label="Gamma history and coverage" aria-expanded={open} onClick={() => setOpen(true)} style={{ flexShrink: 0 }}>History</button>}
      {!open && notes}
    </section>}
    {open && createPortal(<div className="strategy-options-backdrop" onClick={e => { if (e.target === e.currentTarget) close(); }}>
      <section ref={panelRef} role="dialog" aria-modal="true" tabIndex={-1} aria-label="Gamma history and coverage" className="strategy-options-panel">
        <header><strong>Gamma history &amp; coverage</strong>
          {onReset && <button type="button" aria-label="Reset strategy view" title="Show the current session; keep configuration unchanged" onClick={onReset}>↺</button>}
          <button type="button" aria-label="Close strategy options" onClick={close}>×</button>
        </header>
        <div className="strategy-options-body">
          <div className="strategy-option-controls">{secondary}</div>
          {items.slice(1)}
        </div>
      </section>
    </div>, document.body)}
  </>;
}
