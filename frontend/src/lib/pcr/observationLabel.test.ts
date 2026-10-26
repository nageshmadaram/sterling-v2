import { expect, it } from 'vitest';
import { buildGrid, slotObservationLabel } from './slots';

const mark = (hhmm: string) => ({ hhmm, pcr: 0.9, volumePcr: 1, changeOiPcr: 0, indexClose: 22000 });
it.each([
  [12 * 60 + 5, '12:15', 'Forming until 12:15 · observed 12:05'],
  [12 * 60 + 15, '12:15', 'Slot 12:15 · observed 12:15'],
  [16 * 60, '12:15', 'Slot 12:15 · observed 12:15'],
  [13 * 60 + 5, '13:15', 'No PCR observation'],
] as const)('discloses forming, boundary, closed and stale-source timing at minute %s', (now, time, label) => {
  const slots = buildGrid([mark('12:00'), mark('12:15')], mark('12:05'), now);
  const slot = slots.find(s => s.hhmm === time)!;
  expect(slotObservationLabel(slot)).toBe(label);
});
