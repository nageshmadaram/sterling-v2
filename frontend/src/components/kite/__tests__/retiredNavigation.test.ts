import { describe, expect, it } from 'vitest';
import simpleTerminal from '../../../pages/SimpleTerminal.tsx?raw';
import kiteTab from '../KiteTab.tsx?raw';
import kiteLayout from '../KiteLayout.tsx?raw';
import activityRail from '../KiteActivityRail.tsx?raw';
import palette from '../KiteCommandPalette.tsx?raw';

/** Retired strategies (Adaptive Edge, Intraday, Opening Volume, Navigator) have no page to open. */
describe('navigation carries no retired strategy', () => {
  const sources: Array<[string, string]> = [
    ['top navigation', simpleTerminal],
    ['KiteTab', kiteTab],
    ['KiteLayout', kiteLayout],
    ['activity rail', activityRail],
    ['command palette', palette],
  ];

  it.each(sources)('%s has no Opening Leaders or Adaptive Edge destination', (_name, src) => {
    expect(src).not.toMatch(/openingLeaders|OpeningVolumeLeaders|Opening Leaders/);
    expect(src).not.toMatch(/adaptiveEdge|AdaptiveEdgePane|Adaptive Edge Strategy/);
  });

  it('keeps the destinations that stay', () => {
    expect(simpleTerminal).toMatch(/id: 'pcr' as const, label: 'PCR'[\s\S]*?id: 'orders'/);
  });
});
