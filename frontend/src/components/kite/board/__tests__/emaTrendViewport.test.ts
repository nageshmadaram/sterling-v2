import { describe, expect, it } from 'vitest';
import { selectEmaViewport } from '../emaTrendViewport';
import { emaTrendCampaignsToBoard, emaTrendSetupsToBoard } from '../emaTrendAdapter';
import { emaCampaign, emaSetup } from '../../../../test/emaTrendFixtures';

describe('EMA progressive rendering', () => {
  it('shows only the newest page and never mutates signal order', () => {
    const rows = Array.from({length: 500}, (_,i) => emaTrendSetupsToBoard([{
      ...emaSetup(i % 2 ? 'scalping' : 'swing'), id: `row-${i}`,
      close_ms: 1700000000000 + i * 60000,
    }])[0]);
    const original = [...rows];
    const result = selectEmaViewport(rows, rows, 80);
    expect(result).toHaveLength(80);
    expect(result[0].id).toBe('row-499');
    expect(result[79].id).toBe('row-420');
    expect(rows).toEqual(original);
    expect(selectEmaViewport(rows, rows, 160)).toHaveLength(160);
  });
  it('reserves room for older swing signals behind newer ultra scalp records', () => {
    const recent = Array.from({length: 300}, (_,i) => emaTrendSetupsToBoard([{
      ...emaSetup('ultra_scalping'), id: `ultra-${i}`,
      close_ms: 1700000000000 + i * 60000,
    }])[0]);
    const older = Array.from({length: 30}, (_,i) => emaTrendSetupsToBoard([{
      ...emaSetup('swing'), id: `swing-${i}`, close_ms: 1690000000000 + i * 60000,
    }])[0]);
    const result = selectEmaViewport([...older, ...recent], [...older, ...recent], 80);
    expect(result).toHaveLength(80);
    expect(result.some(row => row.emaTrend?.mode === 'swing')).toBe(true);
    expect(result.some(row => row.emaTrend?.mode === 'ultra_scalping')).toBe(true);
  });
  it('always shows a filtered-out recovery campaign, even below the page', () => {
    const rows = Array.from({length: 200}, (_,i) => emaTrendSetupsToBoard([{
      ...emaSetup(), id: `row-${i}`, close_ms: 1700000000000 + i * 60000,
    }])[0]);
    const campaign = emaTrendCampaignsToBoard([{
      ...emaCampaign(), id: 'held', state: 'RECOVERY_REQUIRED', last_close_ms: 1000,
    }])[0];
    const result = selectEmaViewport(rows, [...rows, campaign], 80);
    expect(result).toHaveLength(81);
    expect(result.filter(row => row.id === campaign.id)).toHaveLength(1);
  });
});
