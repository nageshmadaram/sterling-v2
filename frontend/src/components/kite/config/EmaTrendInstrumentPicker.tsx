import React from 'react';
import { useEmaTrendInstruments } from '../../../hooks/useEmaTrend';
import { Field, ChoiceRow, CheckOption } from '../kiteSettingsPrimitives';
import { ConfigNote } from './ConfigPrimitives';
import type { EmaTrendMode, EmaTrendInstrumentChoice } from '../../../types/emaTrend';

/** Exact broker contracts; filters never substitute one instrument for another. */
export function EmaTrendInstrumentPicker({ symbols, mode, onChange }: {
  symbols: string[]; mode: EmaTrendMode; onChange: (symbols: string[]) => void;
}) {
  const [text, setText] = React.useState('');
  const [query, setQuery] = React.useState('');
  const [exchange, setExchange] = React.useState('ALL');
  const [kind, setKind] = React.useState('ALL');
  const [known, setKnown] = React.useState<Record<string, EmaTrendInstrumentChoice>>({});
  React.useEffect(() => { const id = setTimeout(() => setQuery(text.trim()), 250); return () => clearTimeout(id); }, [text]);
  const search = useEmaTrendInstruments(query, mode);
  React.useEffect(() => { if (search.data?.items) setKnown(previous => ({ ...previous, ...Object.fromEntries(search.data.items.map(item => [item.symbol, item])) })); }, [search.data]);
  const results = (search.data?.items || []).filter(item => (exchange === 'ALL' || item.exchange === exchange) && (kind === 'ALL' || item.instrument_type === kind));
  return <>
    <Field label="Search instruments" hint="Search stocks, index futures/options or commodities by underlying or exact trading symbol. Uses the connected broker master." wide>
      <input aria-label="EMA Trend instrument search" value={text} onChange={event => setText(event.target.value)} placeholder="RELIANCE, NIFTY, SENSEX, GOLD…" style={{ width: '100%' }} />
    </Field>
    <Field label="Exchange" wide><ChoiceRow value={exchange} onChange={setExchange} options={['ALL','NSE','BSE','NFO','BFO','MCX'].map(value => ({ value, label: value === 'ALL' ? 'All exchanges' : value }))} /></Field>
    <Field label="Contract type" wide><ChoiceRow value={kind} onChange={setKind} options={[
      { value: 'ALL', label: 'All contracts' }, { value: 'EQ', label: 'Cash equity' }, { value: 'FUT', label: 'Futures' }, { value: 'CE', label: 'Calls' }, { value: 'PE', label: 'Puts' },
    ]} /></Field>
    {query.length < 2 && <ConfigNote>Type at least two characters to load actual listed instruments.</ConfigNote>}
    {search.isFetching && <p role="status">Loading broker instruments…</p>}
    {search.error && <p role="alert">Instrument search unavailable: {(search.error as Error).message}</p>}
    {query.length >= 2 && search.data && !results.length && <ConfigNote>No matching instruments in these filters.</ConfigNote>}
    <div role="group" aria-label="EMA Trend broker instruments" style={{ display: 'grid', gap: 6, maxHeight: 360, overflow: 'auto' }}>
      {results.map(item => <div key={item.symbol} style={{ padding: 6, border: '1px solid var(--k-border)' }}>
        <CheckOption label={item.symbol} checked={symbols.includes(item.symbol)} disabled={!item.execution_supported && !symbols.includes(item.symbol) || symbols.length >= 30 && !symbols.includes(item.symbol)} onChange={() => onChange(symbols.includes(item.symbol) ? symbols.filter(symbol => symbol !== item.symbol) : [...symbols, item.symbol])} />
        <div style={{ fontSize: 11 }}>{item.name} · {item.instrument_type} · lot {item.lot_size} · tick {item.tick_size}{item.expiry ? ` · expiry ${item.expiry}` : ''}</div>
        {item.blocker && <div style={{ fontSize: 11, color: 'var(--k-amber)' }}>{item.blocker.replace(/_/g, ' ')}</div>}
      </div>)}
    </div>
    <Field label="Selected instruments and contracts" hint={`${symbols.length}/30 selected. Changes take effect only after Apply.`} wide>
      <div style={{ display: 'grid', gap: 5 }}>{symbols.map(symbol => <div key={symbol} style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
        <strong>{symbol}</strong><span>{known[symbol]?.contract ? `${known[symbol].instrument_type} · ${mode === 'swing' ? known[symbol].instrument_type === 'EQ' ? 'CNC' : 'NRML' : 'MIS'} · lot ${known[symbol].lot_size}${known[symbol].expiry ? ` · ${known[symbol].expiry}` : ''}` : 'Metadata verified by the server during scan and planning.'}</span>
        <button type="button" className="sb-tool" aria-label={`Remove ${symbol}`} onClick={() => onChange(symbols.filter(value => value !== symbol))}>Remove</button>
      </div>)}</div>
    </Field>
    <ConfigNote>Indices require an actual listed future or long CE/PE contract. Signals use the selected contract’s own candles. Contracts remain fixed through a campaign; there is no automatic rollover. New staged derivative positions require at least four complete lots.</ConfigNote>
    <ConfigNote>MCX execution is currently blocked: the shared virtual broker still needs commodity multipliers, calendar, fees and accounting support. Selecting a cash index does not create a tradable position.</ConfigNote>
  </>;
}
