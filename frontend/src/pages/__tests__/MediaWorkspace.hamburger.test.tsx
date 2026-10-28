import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, render, screen, waitFor } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import MediaWorkspace from '../MediaWorkspace';

vi.mock('../../components/tv/TvWorkspace', () => ({
  TvWorkspace: () => <div>TV mock player</div>,
}));
vi.mock('../../components/radio/RadioWorkspace', () => ({
  RadioWorkspace: () => <div>Radio mock player</div>,
}));

afterEach(() => {
  cleanup();
  document.getElementById('workspace-toolbar')?.remove();
  document.getElementById('sterling-media-account-settings')?.parentElement?.remove();
});

describe('media hamburger appearance host', () => {
  it.each(['tv', 'radio'] as const)(
    'mounts %s settings even when the dialog host commits after the media page',
    async feature => {
      render(<MediaWorkspace feature={feature} />);
      // The media route can commit before its outer, asynchronously loaded
      // AccountMenu. The settings must recover when the host is added later.
      const toolbar = document.createElement('div');
      toolbar.id = 'workspace-toolbar';
      document.body.appendChild(toolbar);
      const dialog = document.createElement('div');
      const portal = document.createElement('div');
      portal.id = 'sterling-media-account-settings';
      dialog.appendChild(portal);
      document.body.appendChild(dialog);
      act(() => window.dispatchEvent(new Event('sterling-media-menu-open')));
      await waitFor(() => expect(portal.querySelector('.media-account-settings')).not.toBeNull());
      expect(screen.getByRole('group', { name: 'Player interface' })).toBeInTheDocument();
      expect(screen.getByRole('button', {
        name: feature === 'tv' ? 'Search TV channels' : 'Search radio stations',
      })).toBeInTheDocument();
      expect(screen.queryByRole('button', {
        name: feature === 'tv' ? 'TV display settings' : 'Radio display settings',
      })).toBeNull();
    },
  );
});
