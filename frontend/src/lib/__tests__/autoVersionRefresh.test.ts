import { describe, expect, it } from 'vitest';
import { shouldAutoReload } from '../autoVersionRefresh';

const idle = {
  quietForMs: 130_000,
  isVisible: true,
  hasFocusedEditor: false,
  hasDialog: false,
  hasPlayingMedia: false,
  hasMediaFrame: false,
};

describe('device update availability', () => {
  it('refreshes a background-idle, visible trading UI only after the grace period', () => {
    expect(shouldAutoReload(idle)).toBe(true);
    expect(shouldAutoReload({ ...idle, quietForMs: 5_000 })).toBe(false);
    expect(shouldAutoReload({ ...idle, isVisible: false })).toBe(false);
  });
  it('never interrupts editing, broker dialogs, or TV/Radio playback', () => {
    for (const key of ['hasFocusedEditor', 'hasDialog',
                       'hasPlayingMedia', 'hasMediaFrame'] as const) {
      expect(shouldAutoReload({ ...idle, [key]: true })).toBe(false);
    }
  });
  it('never refreshes immediately when user is interacting with signals', () => {
    expect(shouldAutoReload({ ...idle, quietForMs: 10 })).toBe(false);
  });
});
