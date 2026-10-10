/**
 * jsdom does not lay anything out, so the responsive contract is held against the stylesheet itself:
 * the Journal answers to the width of the room it is given (a narrow dock or a phone), and below it a
 * table becomes a stack of cards built from each cell's own label.
 */
import { describe, it, expect } from 'vitest';
import { render } from '@testing-library/react';
import { JOURNAL_CSS, JournalStyles } from '../ui';

const block = (query: string): string => {
  const start = JOURNAL_CSS.indexOf(query);
  expect(start, `${query} not found`).toBeGreaterThan(-1);
  let depth = 0;
  for (let i = JOURNAL_CSS.indexOf('{', start); i < JOURNAL_CSS.length; i += 1) {
    if (JOURNAL_CSS[i] === '{') depth += 1;
    if (JOURNAL_CSS[i] === '}' && (depth -= 1) === 0) return JOURNAL_CSS.slice(start, i + 1);
  }
  throw new Error('unbalanced');
};

describe('Journal responsive contract', () => {
  it('measures its own container, not the window', () => {
    expect(JOURNAL_CSS).toMatch(/\.jr-root \{ container-type: inline-size; container-name: jr; \}/);
    expect(JOURNAL_CSS).not.toMatch(/@media \(max-width/);
  });

  it('turns every table into cards before it could be clipped (under 760px, which includes phones under 640px)', () => {
    const cards = block('@container jr (max-width: 759px)');
    expect(cards).toMatch(/\.jr-rt tr \{[^}]*border-radius/);
    expect(cards).toMatch(/content: attr\(data-label\)/);
    expect(cards).toMatch(/\.jr-rt thead \{ position: absolute/);
    expect(cards).toMatch(/\.jr-trades \{ min-width: 0/);
    expect(cards).toMatch(/td\.jr-hide-narrow \{ display: none; \}/);
  });

  it('stacks the story columns on a phone and trims the trades table on a tablet', () => {
    expect(block('@container jr (max-width: 639px)')).toMatch(/\.jr-two \{ grid-template-columns: 1fr; \}/);
    expect(block('@container jr (max-width: 959px)')).toMatch(/\.jr-rt \.jr-hide-medium \{ display: none; \}/);
  });

  it('puts the styles on the page when the tab renders', () => {
    render(<JournalStyles />);
    expect(document.querySelector('style')?.textContent).toContain('container-type: inline-size');
  });
});
