/**
 * Broker positions Sterling did not open.
 *
 * These get their own red-bordered section and are never merged into a managed
 * count. There are deliberately no Protect or Exit controls: Sterling does not
 * manage these positions, and a button implying otherwise would be a lie the
 * operator acts on. Adoption would need a designed backend workflow, not a
 * button.
 */
import { mono } from './Card';
import { fmtMoney, fmtQty } from '../../../lib/format';
import type { ExternalPosition } from '../../../types/liveOperations';

const price = (value: number | null | undefined) => (value == null ? 'UNKNOWN' : fmtMoney(value));

export function ExternalPositionsTable({ positions }: { positions: ExternalPosition[] }) {
  if (positions.length === 0) return null;

  return (
    <section
      aria-label="Positions not managed by Sterling"
      style={{
        border: '2px solid var(--k-err, #ef4444)',
        borderRadius: 4,
        background: 'rgba(239,68,68,0.06)',
        padding: 10,
        marginTop: 10,
      }}
    >
      <h3
        style={{
          margin: '0 0 6px',
          fontSize: 11,
          letterSpacing: 0.6,
          color: 'var(--k-err, #ef4444)',
        }}
      >
        NOT MANAGED BY STERLING — {positions.length} POSITION
        {positions.length === 1 ? '' : 'S'}
      </h3>
      <p style={{ margin: '0 0 8px', fontSize: 11, color: 'var(--k-muted, #8b93a7)' }}>
        Sterling does not manage these. They have no order, fill or stop here. Act at the broker.
      </p>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 11 }}>
        <thead>
          <tr style={{ color: 'var(--k-muted, #8b93a7)', textAlign: 'left' }}>
            <th scope="col" style={{ padding: '2px 4px' }}>Instrument</th>
            <th scope="col" style={{ padding: '2px 4px' }}>Qty</th>
            <th scope="col" style={{ padding: '2px 4px' }}>Product</th>
            <th scope="col" style={{ padding: '2px 4px' }}>Entry</th>
            <th scope="col" style={{ padding: '2px 4px' }}>LTP</th>
            <th scope="col" style={{ padding: '2px 4px' }}>Account</th>
            <th scope="col" style={{ padding: '2px 4px' }}>Discovered</th>
          </tr>
        </thead>
        <tbody>
          {positions.map((position) => (
            <tr key={`${position.account}:${position.instrument}`}>
              <td style={{ ...mono, padding: '2px 4px' }}>{position.instrument}</td>
              <td style={{ ...mono, padding: '2px 4px' }}>{fmtQty(position.quantity)}</td>
              <td style={{ padding: '2px 4px' }}>{position.product ?? '—'}</td>
              <td style={{ ...mono, padding: '2px 4px' }}>{price(position.broker_avg_price)}</td>
              <td style={{ ...mono, padding: '2px 4px' }}>{price(position.last_price)}</td>
              <td style={{ ...mono, padding: '2px 4px' }}>{position.account}</td>
              <td style={{ padding: '2px 4px', color: 'var(--k-muted, #8b93a7)' }}>
                {position.discovery_reason}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
