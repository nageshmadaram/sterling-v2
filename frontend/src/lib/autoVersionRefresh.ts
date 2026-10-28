/**
 * Detect an atomically published frontend update on all devices.
 * Active trading, form edits, dialogs and media playback are never interrupted.
 * Navigation to a freshly loaded page already gets no-store HTML from the
 * Sterling gateway; an already-open tab refreshes only when safely idle.
 */
const SHA = /^[a-f0-9]{40}$/i;
const CHECK_MS = 60_000;
const QUIET_MS = 120_000;

export function shouldAutoReload({
  quietForMs, isVisible, hasFocusedEditor, hasDialog, hasPlayingMedia,
  hasMediaFrame,
}: {
  quietForMs: number; isVisible: boolean; hasFocusedEditor: boolean;
  hasDialog: boolean; hasPlayingMedia: boolean; hasMediaFrame: boolean;
}): boolean {
  return isVisible && quietForMs >= QUIET_MS && !hasFocusedEditor
    && !hasDialog && !hasPlayingMedia && !hasMediaFrame;
}

export function installAutoVersionRefresh() {
  const local = String(import.meta.env.VITE_BUILD_SHA || '');
  if (import.meta.env.DEV || !SHA.test(local)) return;
  let latest: string | null = null;
  let lastInteraction = Date.now();
  let checking = false;
  let banner: HTMLDivElement | null = null;

  const busy = () => {
    const active = document.activeElement;
    const editor = !!active?.matches('input,textarea,select,[contenteditable], [role="textbox"]');
    const modal = !!document.querySelector('dialog[open], [role="dialog"][aria-modal="true"]');
    const media = Array.from(document.querySelectorAll<HTMLMediaElement>('audio,video'))
      .some(el => !el.paused && !el.ended);
    // A sandboxed Space/Echo iframe may play media that its parent cannot
    // inspect. The user can choose Reload without losing the active stream.
    const mediaFrame = !!document.querySelector(
      'iframe[src*="/media/"],iframe[src*="/tv"],iframe[src*="/radio"]',
    ) || /\/(tv|radio)(\/|$)/.test(location.pathname);
    return {
      quietForMs: Date.now() - lastInteraction,
      isVisible: document.visibilityState === 'visible',
      hasFocusedEditor: editor,
      hasDialog: modal,
      hasPlayingMedia: media,
      hasMediaFrame: mediaFrame,
    };
  };

  const showBanner = () => {
    if (banner) return;
    banner = document.createElement('div');
    banner.setAttribute('role', 'status');
    banner.setAttribute('aria-live', 'polite');
    banner.setAttribute('data-testid', 'sterling-update-ready');
    Object.assign(banner.style, {
      position: 'fixed', right: '12px', top: 'calc(12px + env(safe-area-inset-top, 0px))',
      zIndex: '2147483200', display: 'flex', alignItems: 'center', gap: '8px',
      maxWidth: 'calc(100vw - 24px)', boxSizing: 'border-box', padding: '8px 10px',
      border: '1px solid var(--k-border, #64748b)',
      borderRadius: '9px', background: 'var(--k-bg, #fff)',
      color: 'var(--k-text, #111)', boxShadow: '0 3px 14px #0003',
      font: '12px system-ui, sans-serif',
    } as CSSStyleDeclaration);
    const text = document.createElement('span');
    text.textContent = 'Sterling update ready';
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = 'Reload';
    button.title = 'Load the newer frontend. Trading execution stays on the server.';
    button.style.cssText = 'border:1px solid currentColor;border-radius:6px;padding:6px;cursor:pointer;color:inherit;background:transparent;min-height:32px';
    button.addEventListener('click', () => window.location.reload());
    banner.append(text, button);
    document.body.append(banner);
  };

  const refreshIfSafe = () => {
    if (latest && latest !== local) {
      if (shouldAutoReload(busy())) window.location.reload();
      else showBanner();
    }
  };

  const check = async () => {
    if (checking || document.visibilityState !== 'visible') return;
    checking = true;
    try {
      const response = await fetch('/__sterling_web_health', {
        cache: 'no-store', credentials: 'same-origin',
        signal: AbortSignal.timeout(8_000),
      });
      if (!response.ok) return;
      const data: unknown = await response.json();
      if (!data || typeof data !== 'object') return;
      const release = (data as { release_commit?: unknown; ready?: unknown }).release_commit;
      if ((data as {ready?: unknown}).ready !== true ||
          typeof release !== 'string' || !SHA.test(release)) return;
      latest = release;
      refreshIfSafe();
    } catch {
      // Transient tunnel/backend failures must not reload the trading UI.
    } finally { checking = false; }
  };

  for (const event of ['pointerdown', 'keydown', 'touchstart', 'input']) {
    document.addEventListener(event, () => { lastInteraction = Date.now(); }, { passive: true });
  }
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') {
      // Resume long-lived mobile tabs from the latest release.
      void check();
    }
  });
  window.setInterval(() => {
    if (latest) refreshIfSafe();
    void check();
  }, CHECK_MS);
  void check();
}
