/* Sterling Space/Echo touch policy.
 * Loaded only by the isolated TV and Radio media documents. Never attach to
 * the parent Sterling app: other workspaces retain browser zoom/accessibility.
 * A single finger still scrolls/swipes; controls retain normal tap handling.
 */
(() => {
  'use strict';
  const isMobile = () => typeof window.matchMedia === 'function'
    && window.matchMedia('(max-width: 1023px)').matches;

  function suppressPinch(event) {
    if (isMobile() && event.cancelable && event.touches?.length > 1) {
      event.preventDefault();
    }
  }

  function suppressGesture(event) {
    if (isMobile() && event.cancelable) event.preventDefault();
  }

  // iOS Safari emits gesture*; Android Chrome uses touch* for pinch.
  // The capture phase is intentional: playback overlays may stop bubbling.
  document.addEventListener('touchstart', suppressPinch, { capture: true, passive: false });
  document.addEventListener('touchmove', suppressPinch, { capture: true, passive: false });
  for (const type of ['gesturestart', 'gesturechange', 'gestureend']) {
    document.addEventListener(type, suppressGesture, { capture: true, passive: false });
  }

  // A double-tap can still dispatch to the media player's existing click
  // handler (e.g. fullscreen); only prevent the browser's default zoom.
  document.addEventListener('dblclick', suppressGesture, { capture: true, passive: false });

  // Trackpad pinch maps to Ctrl+wheel in some mobile webviews/emulators.
  document.addEventListener('wheel', (event) => {
    if (isMobile() && event.ctrlKey && event.cancelable) event.preventDefault();
  }, { capture: true, passive: false });
})();
