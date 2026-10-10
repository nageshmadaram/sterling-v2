import React from 'react';
import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';

vi.mock('../SimpleTerminal', () => ({
  SimpleTerminal: () => <div data-testid="kite-workspace">Kite workspace</div>,
}));

import { Terminal } from '../Terminal';

describe('Terminal', () => {
  it('keeps a persisted pro-mode session on the usable Kite workspace', () => {
    render(<Terminal />);

    expect(screen.getByTestId('kite-workspace')).toBeInTheDocument();
    expect(screen.queryByText(/coming soon/i)).not.toBeInTheDocument();
  });
});
