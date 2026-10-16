/**
 * The outbound address, where an operator can copy it.
 *
 * Zerodha's API settings ask for addresses to be entered by hand, and an
 * operator reading them off a terminal and typing them into a browser is how a
 * digit gets transposed. So the value is shown here with a copy button.
 *
 * Two things this must not do. It must not imply an address is registered when
 * none is: an unrecorded address reads NOT RECORDED, not blank. And it must not
 * present a detected address as a verified one — Sterling never asks the
 * internet what its own address is, because a safety check that depends on a
 * third party fails when that third party does. What the panel shows is what
 * the deployment recorded, and the command to record it.
 */
import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../utils/api';

export interface EgressStatus {
  expected_ip: string | null;
  observed_ip: string | null;
  observed_at: string | null;
  router_generation: string | null;
  verified: boolean | null;
  reason: string;
  changed_from: string | null;
  record_command: string;
}

export const EGRESS_PATH = '/api/v1/operations/egress';

function CopyButton({ value, label }: { value: string; label: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      aria-label={`copy ${label}`}
      onClick={() => {
        void navigator.clipboard?.writeText(value).then(
          () => {
            setCopied(true);
            window.setTimeout(() => setCopied(false), 1500);
          },
          () => setCopied(false),
        );
      }}
      style={{
        padding: '1px 6px',
        borderRadius: 4,
        border: '1px solid var(--k-border, #d3d6da)',
        background: 'transparent',
        color: copied ? '#59a96a' : '#475569',
        fontSize: 9.5,
        fontWeight: 600,
        cursor: 'pointer',
      }}
    >
      {copied ? 'copied' : 'copy'}
    </button>
  );
}

export function EgressAddressRow() {
  // Unavailability is a value, not a thrown error. This widget sits inside the
  // session card and must never take it down, and a rejected query landing
  // after the card unmounts is a noisy failure with no operator meaning.
  const { data } = useQuery<EgressStatus | null>({
    queryKey: ['operations-egress'],
    queryFn: async () => {
      try {
        return await api.get<EgressStatus>(EGRESS_PATH);
      } catch {
        return null;
      }
    },
    staleTime: 60_000,
    retry: false,
  });

  const isError = data === null;

  const observed = data?.observed_ip ?? null;
  const expected = data?.expected_ip ?? null;
  const verified = data?.verified ?? null;

  const tone =
    verified === true ? '#59a96a' : verified === false ? '#e95420' : '#f4a261';
  const word =
    isError ? 'UNAVAILABLE' : verified === true ? 'VERIFIED' : verified === false ? 'MISMATCH' : 'UNVERIFIED';

  const message = isError ? 'IP check is unavailable.' : verified === true ? 'Kite IP settings are up to date.' : verified === false ? 'Your IP changed. Update your Kite IP settings.' : 'Your IP has not been verified.';
  return <details className="kite-session-disclosure egress-summary" data-testid="egress-address">
    <summary><span style={{ color: tone }} aria-label={`egress ${word}`}>{message}</span></summary>
    <div className="kite-session-disclosure-body">
      <div className="egress-ip-row"><span>Current IP</span><code>{observed || 'Not recorded'}</code>{observed && <CopyButton value={observed} label="observed outbound IP" />}</div>
      <div className="egress-ip-row"><span>Kite IP</span><code>{expected || 'Not recorded'}</code>{expected && <CopyButton value={expected} label="registered outbound IP" />}</div>
    </div>
  </details>;
}

export default EgressAddressRow;
