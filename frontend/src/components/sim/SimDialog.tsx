import { useEffect, useId, useRef, useState, type KeyboardEvent, type ReactNode } from 'react';
import { SIM, TAP, button } from './simStyle';

/**
 * A confirmation that cannot be mistaken for something else: it names what will happen and
 * whose money it is, puts focus on the safe choice, closes on Escape, and keeps Tab inside.
 * `typedPhrase` makes the confirm button wait until the person has typed the word themselves.
 */
export interface SimDialogProps {
  title: string;
  accent: string;
  children: ReactNode;
  confirmLabel: string;
  busy?: boolean;
  typedPhrase?: string;
  onConfirm: () => void;
  onCancel: () => void;
}

const FOCUSABLE = 'button:not([disabled]), input:not([disabled]), [href], select, textarea, [tabindex]:not([tabindex="-1"])';

export function SimDialog({
  title, accent, children, confirmLabel, busy = false, typedPhrase, onConfirm, onCancel,
}: SimDialogProps) {
  const titleId = useId();
  const [typed, setTyped] = useState('');
  const root = useRef<HTMLDivElement>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const typedOk = !typedPhrase || typed.trim().toUpperCase() === typedPhrase.toUpperCase();
  const canConfirm = typedOk && !busy;

  // Focus lands on the safe choice (or on the field to type in); focus returns to the opener.
  useEffect(() => {
    const opener = document.activeElement as HTMLElement | null;
    (inputRef.current ?? cancelRef.current)?.focus();
    return () => { try { opener?.focus?.(); } catch { /* the opener is gone */ } };
  }, []);

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.key === 'Escape') {
      e.stopPropagation();
      if (!busy) onCancel();
      return;
    }
    if (e.key !== 'Tab' || !root.current) return;
    const items = Array.from(root.current.querySelectorAll<HTMLElement>(FOCUSABLE));
    if (items.length === 0) return;
    const first = items[0];
    const last = items[items.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
  };

  return (
    <div
      onClick={(e) => { if (e.target === e.currentTarget && !busy) onCancel(); }}
      style={{
        position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.5)', zIndex: 3000,
        display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 16,
      }}
    >
      <div
        ref={root}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        onKeyDown={onKeyDown}
        style={{
          width: 'min(440px, 100%)', maxHeight: '100%', overflowY: 'auto', boxSizing: 'border-box',
          background: SIM.surface, color: SIM.text, border: `1px solid ${SIM.border}`,
          borderTop: `4px solid ${accent}`, borderRadius: 10, padding: '20px 22px',
          boxShadow: '0 16px 40px rgba(0,0,0,.25)',
        }}
      >
        <div id={titleId} style={{ fontSize: 15, fontWeight: 800, color: accent, marginBottom: 10 }}>{title}</div>
        <div style={{ fontSize: 13, lineHeight: 1.6, marginBottom: 16 }}>{children}</div>
        {typedPhrase && (
          <label style={{ display: 'block', marginBottom: 16, fontSize: 12 }}>
            <span style={{ display: 'block', marginBottom: 6, color: SIM.muted }}>
              Type <strong style={{ color: SIM.text }}>{typedPhrase}</strong> to confirm
            </span>
            <input
              ref={inputRef}
              value={typed}
              onChange={(e) => setTyped(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter' && canConfirm) onConfirm(); }}
              aria-label={`Type ${typedPhrase} to confirm`}
              autoComplete="off"
              autoCapitalize="characters"
              spellCheck={false}
              style={{
                width: '100%', boxSizing: 'border-box', minHeight: TAP, padding: '0 10px', borderRadius: 7,
                border: `1px solid ${SIM.border}`, background: SIM.surface, color: SIM.text,
                fontFamily: 'inherit', fontSize: 14, letterSpacing: 1,
              }}
            />
          </label>
        )}
        <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end', flexWrap: 'wrap' }}>
          <button ref={cancelRef} type="button" onClick={onCancel} disabled={busy}
            style={{ ...button(SIM.muted), cursor: busy ? 'wait' : 'pointer' }}>
            Cancel
          </button>
          <button type="button" onClick={onConfirm} disabled={!canConfirm}
            style={{ ...button(accent, true), opacity: canConfirm ? 1 : 0.5, cursor: canConfirm ? 'pointer' : 'not-allowed' }}>
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
