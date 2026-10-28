/** Detect published frontend changes on *this* Sterling gateway.
 *
 * The host serves local and Cloudflare from one atomically switched
 * release. Do not confuse GitHub main HEAD with what this device can load.
 * A hidden/returned, idle app may reload; active trading, media playback,
 * dialogs and unsaved settings always require a deliberate tap.
 */
import { useEffect, useRef, useState } from 'react';
import { hasUnsavedDraft } from './kite/config/unsavedDraftGuard';

const RELEASE_RE = /^[a-f0-9]{40}$/i;
const PERIOD_MS = 45_000;
const RETURNED_AFTER_MS = 15_000;

export function isNewPublishedRelease(current: string, offered: unknown): offered is string {
  return RELEASE_RE.test(current) && typeof offered === 'string'
    && RELEASE_RE.test(offered) && current.toLowerCase() !== offered.toLowerCase();
}

export function canAutoRefreshPublishedRelease(
  path: string,
  unsaved: boolean,
  blocked: boolean,
): boolean {
  // Never interrupt a TV programme, Radio audio, a trading draft or a modal.
  return !unsaved && !blocked && path !== '/tv' && path !== '/radio';
}

function interactionBlocksReload(): boolean {
  const focused = document.activeElement;
  const editing = focused instanceof HTMLElement &&
    (focused.matches('input,textarea,select,[contenteditable="true"]')
      || focused.closest('[contenteditable="true"]') !== null);
  const modalOrBusy = document.querySelector(
    'dialog[open], [role="dialog"][aria-modal="true"], [aria-busy="true"], [data-operation-pending="true"]',
  ) !== null;
  return editing || modalOrBusy;
}

export function PublishedReleaseWatcher() {
  const built = String(import.meta.env.VITE_BUILD_SHA || '');
  const [available, setAvailable] = useState('');
  const availableRef = useRef('');
  const dismissedRef = useRef('');
  const lastHiddenAt = useRef<number | null>(null);

  useEffect(() => {
    if (import.meta.env.DEV || !RELEASE_RE.test(built)) return;
    let cancelled = false;
    let checking = false;
    async function check(reopened = false) {
      if (checking) return;
      checking = true;
      try {
        const response = await fetch('/__sterling_web_health', {
          cache: 'no-store', credentials: 'same-origin',
        });
        if (!response.ok) return;
        const data: unknown = await response.json();
        if (!data || typeof data !== 'object') return;
        const value = data as {
          service?: unknown; ready?: unknown; release_commit?: unknown;
        };
        if (value.service !== 'sterling-web' || value.ready !== true
            || !isNewPublishedRelease(built, value.release_commit)) return;
        if (cancelled || dismissedRef.current === value.release_commit) return;
        availableRef.current = value.release_commit;
        setAvailable(value.release_commit);
        if (reopened && canAutoRefreshPublishedRelease(
          window.location.pathname, hasUnsavedDraft(), interactionBlocksReload(),
        )) {
          // Return from the background to a safely idle app: load the current
          // published frontend without making the user refresh every device.
          window.location.reload();
        }
      } catch {
        // Loss of tunnel connectivity is NOT evidence of a new frontend.
      } finally {
        checking = false;
      }
    }

    const interval = window.setInterval(() => {
      if (!document.hidden) void check();
    }, PERIOD_MS);
    const onVisible = () => {
      if (document.hidden) {
        lastHiddenAt.current = Date.now();
        return;
      }
      const wasAway = lastHiddenAt.current !== null
        && Date.now() - lastHiddenAt.current >= RETURNED_AFTER_MS;
      lastHiddenAt.current = null;
      void check(wasAway);
    };
    document.addEventListener('visibilitychange', onVisible);
    window.addEventListener('online', onVisible);
    void check();
    return () => {
      cancelled = true;
      window.clearInterval(interval);
      document.removeEventListener('visibilitychange', onVisible);
      window.removeEventListener('online', onVisible);
    };
  }, [built]);

  if (!available || !availableRef.current) return null;
  return <aside data-testid="sterling-release-update"
    role="status" aria-live="polite"
    className="sterling-release-update">
    <span>New Sterling version ready</span>
    <button type="button" onClick={() => {
      if (hasUnsavedDraft()) {
        if (!window.confirm('You have unsaved settings changes. Reload Sterling and discard them?')) return;
      }
      window.location.reload();
    }}>Update</button>
    <button type="button" aria-label="Dismiss update notice" onClick={() => {
      dismissedRef.current = available;
      setAvailable('');
    }}>×</button>
  </aside>;
}
