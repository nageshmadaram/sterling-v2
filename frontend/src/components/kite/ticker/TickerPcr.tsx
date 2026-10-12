import React from 'react';
import { useQuery } from '@tanstack/react-query';
import { api } from '../../../utils/api';
import { fetchPcrDesk } from '../../../lib/pcr/fetchPcr';
import { nowMinutes } from '../pcrWidgets';
import { buildGrid, readPcr, pcrBand, bandTitle } from '../../../lib/pcr/slots';
import { useExperienceOptions } from '../../../store/useExperienceOptions';
import type { PcrIndex, PcrMark, PcrSeries } from '../../../lib/pcr/types';

const aliases: Record<string, PcrIndex> = {
  'NIFTY 50': 'NIFTY',
  'NIFTY': 'NIFTY',
  'NIFTY BANK': 'BANKNIFTY',
  'BANKNIFTY': 'BANKNIFTY',
  'NIFTY FIN SERVICE': 'FINNIFTY',
  'FINNIFTY': 'FINNIFTY',
  'SENSEX': 'SENSEX',
  'NIFTY MIDCAP SELECT': 'MIDCPNIFTY',
  'MIDCPNIFTY': 'MIDCPNIFTY',
};

const SAMPLE_MARKS: PcrMark[] = [
  { hhmm: '14:30', pcr: 1.08, volumePcr: 0.92, changeOiPcr: 1.15, indexClose: 24160 },
  { hhmm: '14:45', pcr: 1.12, volumePcr: 0.94, changeOiPcr: 1.22, indexClose: 24205 },
  { hhmm: '15:00', pcr: 1.15, volumePcr: 0.95, changeOiPcr: 1.28, indexClose: 24198 },
  { hhmm: '15:15', pcr: 1.18, volumePcr: 0.96, changeOiPcr: 1.32, indexClose: 24232 },
];

const SAMPLE_ROW: PcrSeries = {
  id: 'NIFTY',
  expiry: '2026-10-15',
  livePcr: 1.18,
  spot: { ltp: 24231.85, changePer: 0.64, maxPain: 24200, vix: 13.2, timestamp: '15:15:00' },
  marks: SAMPLE_MARKS,
  latest: SAMPLE_MARKS[SAMPLE_MARKS.length - 1],
};

export function TickerPcr({ symbol, preview = false }: { symbol: string; preview?: boolean }) {
  const options = useExperienceOptions((s) => s.options);
  const raw = symbol.includes(':') ? symbol.split(':').slice(1).join(':') : symbol;
  const index = aliases[raw];

  const q = useQuery({
    queryKey: ['ticker-pcr'],
    queryFn: () => fetchPcrDesk(),
    enabled: !!index && !preview,
    refetchInterval: 30000,
    staleTime: 20000,
  });

  const stock = useQuery({
    queryKey: ['ticker-stock-pcr', raw],
    queryFn: () =>
      api.get<{
        ratio: number | null;
        expiry?: string;
        coverage: string;
        marketOpen: boolean;
        observedAt?: string;
        reason?: string;
      }>('/api/v1/pcr/instrument?symbol=' + encodeURIComponent(raw)),
    enabled: !index && options.pcrMetric === 'oi' && !preview,
    refetchInterval: 60000,
    staleTime: 50000,
  });

  const row = preview ? (q.data?.series[index] ?? SAMPLE_ROW) : (index ? q.data?.series[index] : undefined);
  const last = row?.latest;
  const field =
    options.pcrMetric === 'volume'
      ? 'volumePcr'
      : options.pcrMetric === 'changeOi'
      ? 'changeOiPcr'
      : 'pcr';
  const ratio = last?.[field] ?? (field === 'pcr' ? row?.livePcr : null);
  const slots = row
    ? buildGrid(
        row.marks,
        row.latest,
        q.data?.source === 'live' && q.data?.marketOpen !== false ? nowMinutes(new Date()) : null,
        options.pcrMetric
      )
    : [];
  const insight = readPcr(slots, row?.spot.changePer ?? null);
  const old = !preview && (q.data?.source !== 'live' || q.data?.marketOpen === false);

  if (!index && !preview) {
    return (
      <div className="ticker-pcr-line" title={stock.data?.coverage || 'PCR needs a covered options instrument'}>
        {stock.isLoading
          ? 'Loading stock PCR…'
          : stock.data?.ratio != null
          ? `OI PCR ${stock.data.ratio.toFixed(2)} · ${stock.data.expiry?.slice(5)} · ${
              stock.data.marketOpen ? 'broker snapshot' : 'closed / cached'
            }`
          : stock.data?.reason || 'PCR unavailable for this instrument'}
        {options.tickerContent === 'detail' && stock.data?.ratio != null && <small>{stock.data.coverage}</small>}
      </div>
    );
  }

  const band = bandTitle(pcrBand(ratio ?? null));
  const summary = [
    insight.action,
    options.pcrTimestamp && last ? `${last.hhmm} IST` : null,
    band || null,
    options.pcrRatio && ratio != null ? `PCR ${ratio.toFixed(2)}` : null,
  ]
    .filter(Boolean)
    .join(' · ');

  return (
    <div className="ticker-pcr-line" title="PCR desk interpretation; informational, not an order">
      <span>{ratio == null ? 'PCR unavailable' : summary}</span>
      {old && <small>Historical · {q.data?.asOf.slice(0, 10) || 'no current session'}</small>}
      {options.tickerContent === 'detail' && ratio != null && (
        <small>
          {insight.headline} · {insight.conviction}% conviction
        </small>
      )}
    </div>
  );
}
