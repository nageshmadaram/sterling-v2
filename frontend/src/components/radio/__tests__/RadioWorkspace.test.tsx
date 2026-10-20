import React from 'react';
import { describe, expect, it } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { RadioWorkspace } from '../RadioWorkspace';

describe('Radio workspace isolation', () => {
  it('keeps the radio iframe off the authenticated Sterling origin', () => {
    render(<RadioWorkspace />);
    const frame = screen.getByTitle('Sterling Radio player');
    expect(frame).toHaveAttribute('src','/radio/index.html');
    expect(frame).toHaveAttribute('referrerpolicy','no-referrer');
    expect(frame.getAttribute('sandbox')).not.toContain('allow-same-origin');
    expect(frame.getAttribute('sandbox')).not.toContain('allow-top-navigation');
  });
  it('refreshes on the shared Radio header command', () => {
    render(<RadioWorkspace />);
    const oldFrame = screen.getByTitle('Sterling Radio player');
    fireEvent(window,new Event('sterling-radio-reload'));
    expect(screen.getByTitle('Sterling Radio player')).not.toBe(oldFrame);
  });
});
