/**
 * ConfirmDialog — a beautiful custom confirm overlay that replaces window.confirm().
 *
 * Usage (hook-based, zero prop drilling):
 *   const confirm = useConfirm();
 *   await confirm({ title: 'Remove account?', body: '…', danger: true });
 *
 * Usage (standalone component):
 *   <ConfirmDialog
 *     open={open}
 *     title="Remove account?"
 *     body="This cannot be undone."
 *     confirmLabel="Remove"
 *     danger
 *     onConfirm={() => doDelete()}
 *     onCancel={() => setOpen(false)}
 *   />
 */
import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

/* ── Types ────────────────────────────────────────────────────────────────── */
export interface ConfirmOptions {
  title: string;
  body?: string;
  confirmLabel?: string;
  cancelLabel?: string;
  danger?: boolean;
}

interface ConfirmDialogProps extends ConfirmOptions {
  open: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

/* ── Standalone component ─────────────────────────────────────────────────── */
export function ConfirmDialog({
  open, title, body, confirmLabel = 'Confirm', cancelLabel = 'Cancel',
  danger = false, onConfirm, onCancel,
}: ConfirmDialogProps) {
  const cancelRef = useRef<HTMLButtonElement>(null);
  const confirmRef = useRef<HTMLButtonElement>(null);

  // Focus the cancel button on open (safe default)
  useEffect(() => {
    if (open) cancelRef.current?.focus();
  }, [open]);

  // Close on Escape
  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => { if (e.key === 'Escape') onCancel(); };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [open, onCancel]);

  // Trap focus inside dialog
  useEffect(() => {
    if (!open) return;
    const trap = (e: KeyboardEvent) => {
      if (e.key !== 'Tab') return;
      const els = [cancelRef.current, confirmRef.current].filter(Boolean) as HTMLElement[];
      if (!els.length) return;
      const first = els[0], last = els[els.length - 1];
      if (e.shiftKey ? document.activeElement === first : document.activeElement === last) {
        e.preventDefault();
        (e.shiftKey ? last : first).focus();
      }
    };
    document.addEventListener('keydown', trap);
    return () => document.removeEventListener('keydown', trap);
  }, [open]);

  if (!open) return null;

  return createPortal(
    <div
      className="cdlg-overlay"
      role="presentation"
      onClick={(e) => { if (e.target === e.currentTarget) onCancel(); }}
      aria-modal="true"
    >
      <div
        className={`cdlg-panel${danger ? ' cdlg-panel--danger' : ''}`}
        role="alertdialog"
        aria-labelledby="cdlg-title"
        aria-describedby={body ? 'cdlg-body' : undefined}
      >
        {/* Icon */}
        <div className={`cdlg-icon${danger ? ' cdlg-icon--danger' : ''}`} aria-hidden="true">
          {danger
            ? (
              <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                <path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0Z"/>
                <line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>
              </svg>
            ) : (
              <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/>
              </svg>
            )
          }
        </div>

        {/* Text */}
        <div className="cdlg-content">
          <h2 id="cdlg-title" className="cdlg-title">{title}</h2>
          {body && <p id="cdlg-body" className="cdlg-body">{body}</p>}
        </div>

        {/* Actions */}
        <div className="cdlg-actions">
          <button
            ref={cancelRef}
            className="cdlg-btn cdlg-btn-cancel"
            onClick={onCancel}
          >
            {cancelLabel}
          </button>
          <button
            ref={confirmRef}
            className={`cdlg-btn cdlg-btn-confirm${danger ? ' cdlg-btn-confirm--danger' : ''}`}
            onClick={onConfirm}
          >
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}

/* ── Context-based hook (global singleton, no prop drilling) ──────────────── */
type Resolver = (confirmed: boolean) => void;

interface ConfirmCtx {
  confirm: (opts: ConfirmOptions) => Promise<boolean>;
}

const Ctx = createContext<ConfirmCtx>({ confirm: () => Promise.resolve(false) });

export function useConfirm() {
  return useContext(Ctx).confirm;
}

export function ConfirmProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<(ConfirmOptions & { open: boolean }) | null>(null);
  const resolver = useRef<Resolver | null>(null);

  const confirm = useCallback((opts: ConfirmOptions): Promise<boolean> => {
    return new Promise((resolve) => {
      resolver.current = resolve;
      setState({ ...opts, open: true });
    });
  }, []);

  const handleConfirm = () => {
    setState(null);
    resolver.current?.(true);
  };
  const handleCancel = () => {
    setState(null);
    resolver.current?.(false);
  };

  return (
    <Ctx.Provider value={{ confirm }}>
      {children}
      {state && (
        <ConfirmDialog
          {...state}
          onConfirm={handleConfirm}
          onCancel={handleCancel}
        />
      )}
    </Ctx.Provider>
  );
}
