import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { HelpPane } from '../HelpPane';

describe('HelpPane', () => {
  it('covers the three live strategies and the safety controls', () => {
    render(<HelpPane />);
    for (const title of ['SuperTrend', 'Snapback', 'Gamma Unwind', 'Safety controls', 'Arming automatic entry']) {
      expect(screen.getByText(title)).toBeInTheDocument();
    }
  });

  it('states that Sterling trades a real account', () => {
    render(<HelpPane />);
    expect(screen.getByText(/real money/i)).toBeInTheDocument();
  });

  it('documents the canonical number names', () => {
    render(<HelpPane />);
    for (const label of ['Entry', 'LTP', 'SL / TSL', 'Qty', 'Risk', 'Capital', 'Status']) {
      expect(screen.getAllByText(label).length).toBeGreaterThan(0);
    }
  });

  it('does not mention retired strategies or simulation', () => {
    const { container } = render(<HelpPane />);
    const text = container.textContent ?? '';
    for (const word of [/adaptive edge/i, /navigator/i, /opening/i, /paper/i, /simulat/i, /shadow/i]) {
      expect(text).not.toMatch(word);
    }
  });
});
