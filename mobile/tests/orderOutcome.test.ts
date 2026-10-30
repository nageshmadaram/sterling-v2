import { expect, it } from 'vitest';
import { requireOrderAcknowledgement } from '../src/services/orderOutcome';

it('reports an acknowledged order ID, not an assumed fill', () => {
  expect(requireOrderAcknowledgement({ order_id: 'PAPER-1', status: 'OPEN' })).toBe('PAPER-1');
  expect(requireOrderAcknowledgement({ order_id: '  PAPER-2  ' })).toBe('PAPER-2');
});

it('does not treat a 200 error, unknown or missing order ID as success', () => {
  for (const outcome of [undefined, null, {}, { order_id: '' },
    { order_id: 12 }, { status: 'blocked', order_id: 'X' },
    { status: 'UNKNOWN', order_id: 'X' },
    { status: 'rejected', order_id: 'X' },
    { status: 'error', order_id: 'X' }]) {
    expect(() => requireOrderAcknowledgement(outcome)).toThrow(/Check Orders/);
  }
});
