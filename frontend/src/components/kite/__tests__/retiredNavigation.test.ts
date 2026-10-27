import {NAV_OPTIONS} from '../../../store/useExperienceOptions';
import { describe, expect, it } from 'vitest';
import simpleTerminal from '../../../pages/SimpleTerminal.tsx?raw';
import kiteTab from '../KiteTab.tsx?raw';
import kiteLayout from '../KiteLayout.tsx?raw';
import activityRail from '../KiteActivityRail.tsx?raw';
import palette from '../KiteCommandPalette.tsx?raw';
import supertrendPane from '../SterlingKiteEnginePane.tsx?raw';

/** Retired strategies (Adaptive Edge, Intraday, Opening Volume, Navigator) have no release surface. */
describe('release UI carries no retired strategy destination', () => {
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

  it('keeps Navigator out of the SuperTrend runtime controls', () => {
    expect(supertrendPane).not.toMatch(/useNavigatorConfig|useRunNavigatorScan/);
    expect(supertrendPane).not.toMatch(/Navigator only|Where both agree|Show Navigator setups/);
    expect(supertrendPane).toMatch(/row\.source !== 'navigator'/);
  });

  it('keeps the destinations that stay', () => {
    const ids=NAV_OPTIONS.map(([id])=>id);expect(ids).toContain('pcr');expect(ids).toContain('orders');
  });
});
