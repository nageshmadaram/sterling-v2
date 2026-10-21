import React from 'react';
import { useEmaTrendInstruments } from '../../../hooks/useEmaTrend';
import { useStockRegistry } from '../../../hooks/useSterlingKiteEngine';
import { Field, CheckOption, Switch } from '../kiteSettingsPrimitives';
import { ConfigNote } from './ConfigPrimitives';
import { INDEX_OPTIONS } from './registry';
import type { EmaTrendMode } from '../../../types/emaTrend';

const MAX_INSTRUMENTS = 30;

/**
 * Familiar Sterling universe selector:
 * read-only index watch + curated F&O stock underlyings + optional broker
 * instrument search. The UI never substitutes an underlying for an
 * executable derivative contract.
 */
export function EmaTrendInstrumentPicker({
  symbols, mode, onChange, indices = [], onIndicesChange,
  stockScanEnabled = true, onStockScanEnabledChange,
}: {
  symbols: string[]; mode: EmaTrendMode; onChange: (symbols: string[]) => void;
  indices?: string[]; onIndicesChange: (indices: string[]) => void;
  stockScanEnabled?: boolean; onStockScanEnabledChange: (enabled: boolean) => void;
}) {
  const registry = useStockRegistry();
  const [text, setText] = React.useState('');
  const [query, setQuery] = React.useState('');
  const [exchange, setExchange] = React.useState('ALL');
  const [kind, setKind] = React.useState('ALL');
  const [limitWarning, setLimitWarning] = React.useState('');
  React.useEffect(() => {
    const timer = setTimeout(() => setQuery(text.trim()), 250);
    return () => clearTimeout(timer);
  }, [text]);
  const search = useEmaTrendInstruments(query, mode);
  const groups = Array.isArray(registry.data) ? registry.data : [];
  const fnoSymbols = Array.from(new Set(groups.flatMap(group =>
    group.stocks.map(stock => `NSE:${stock.name}`))));
  const selectedCurated = symbols.filter(symbol => fnoSymbols.includes(symbol));
  const custom = symbols.filter(symbol => !fnoSymbols.includes(symbol));
  const allCurated = fnoSymbols.length > 0 && fnoSymbols.every(symbol => symbols.includes(symbol));
  const results = (search.data?.items || []).filter(item =>
    (exchange === 'ALL' || item.exchange === exchange) &&
    (kind === 'ALL' || item.instrument_type === kind));

  const setSymbols = (values: string[]) => {
    const next = [...new Set(values)];
    if (next.length > MAX_INSTRUMENTS) {
      setLimitWarning(`Maximum ${MAX_INSTRUMENTS} selected instruments. Remove some or choose a smaller universe.`);
      return;
    }
    setLimitWarning('');
    onChange(next);
  };
  const toggleSymbol = (symbol: string) => setSymbols(symbols.includes(symbol)
    ? symbols.filter(value => value !== symbol)
    : [...symbols, symbol]);

  return <>
    <Field label="Indices" hint="Index chart signals only. No orders." wide>
      <div role="group" aria-label="EMA index watches" className="sk-config-check-grid"
        style={{ display: 'grid', gridTemplateColumns: 'repeat(4, minmax(0, 1fr))', gap: '6px 12px' }}>
        {INDEX_OPTIONS.map(option => <CheckOption key={option.value} compact
          label={option.label} checked={indices.includes(option.value)}
          onChange={() => onIndicesChange(indices.includes(option.value)
            ? indices.filter(value => value !== option.value)
            : [...indices, option.value])} />)}
      </div>
    </Field>
    <ConfigNote>Indices are watch only. To practise on NIFTY or SENSEX, add a real futures or option contract under Add more.</ConfigNote>

    <Field label="Single-stock underlyings"
      hint="Off: skip stocks but keep your list. Indices still scan.">
      <Switch label="EMA scan single-stock underlyings" checked={stockScanEnabled}
        onChange={() => onStockScanEnabledChange(!stockScanEnabled)} />
    </Field>

    {stockScanEnabled && <>
      <Field label="F&O stocks" hint="Use the standard list, or pick stocks.">
        <Switch label="EMA scan all eligible F&O stocks"
          checked={allCurated}
          onChange={() => setSymbols(allCurated
            ? symbols.filter(symbol => !fnoSymbols.includes(symbol))
            : [...symbols, ...fnoSymbols])}
          disabled={registry.isLoading || registry.isError || fnoSymbols.length === 0} />
      </Field>
      {!allCurated && <Field label="Selected F&O stocks"
        hint={`${selectedCurated.length} selected from the shared Sterling eligible stock list.`} wide>
        {registry.isLoading && <p role="status">Loading eligible stocks…</p>}
        {registry.isError && <p role="alert">Stock list not available. Try again shortly.</p>}
        {groups.map(group => <div key={group.liquidity} style={{ marginBottom: 10 }}>
          <strong style={{ fontSize: 11 }}>{group.liquidity}</strong>
          <div className="sk-config-check-grid" style={{
            display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(110px, 1fr))',
            gap: '4px 8px', marginTop: 4,
          }}>
            {group.stocks.map(stock => <CheckOption key={stock.name}
              compact label={stock.label || stock.name}
              checked={symbols.includes(`NSE:${stock.name}`)}
              onChange={() => toggleSymbol(`NSE:${stock.name}`)} />)}
          </div>
        </div>)}
      </Field>}
    </>}

    {limitWarning && <p role="alert">{limitWarning}</p>}
    <details>
      <summary style={{ cursor: 'pointer', fontSize: 12, fontWeight: 600 }}>
        Add more stocks or exact derivative contracts
        {custom.length ? ` · ${custom.length} selected` : ''}
      </summary>
      <div style={{ display: 'grid', gap: 10, marginTop: 10 }}>
        <Field label="Search broker instruments" hint="Find other stocks, or exact futures and option contracts." wide>
          <input aria-label="EMA Trend instrument search" value={text}
            onChange={event => setText(event.target.value)}
            placeholder="RELIANCE, NIFTY, SENSEX, other stocks…" style={{ width: '100%' }} />
        </Field>
        <Field label="Exchange" wide>
          <select aria-label="EMA extra instrument exchange" value={exchange}
            onChange={event => setExchange(event.target.value)}>
            {['ALL','NSE','BSE','NFO','BFO'].map(value =>
              <option key={value} value={value}>{value === 'ALL' ? 'All supported exchanges' : value}</option>)}
          </select>
        </Field>
        <Field label="Contract" wide>
          <select aria-label="EMA extra instrument type" value={kind}
            onChange={event => setKind(event.target.value)}>
            {[['ALL','All types'],['EQ','Stocks'],['FUT','Futures'],['CE','Calls'],['PE','Puts']]
              .map(([value,label]) => <option key={value} value={value}>{label}</option>)}
          </select>
        </Field>
        {query.length < 2 && <ConfigNote>Type at least 2 letters to search.</ConfigNote>}
        {search.isFetching && <p role="status">Searching broker instruments…</p>}
        {search.error && <p role="alert">Broker search unavailable: {(search.error as Error).message}</p>}
        {query.length >= 2 && search.data && !results.length &&
          <ConfigNote>No contracts match.</ConfigNote>}
        <div role="group" aria-label="EMA additional broker instruments"
          style={{ display: 'grid', gap: 6, maxHeight: 260, overflow: 'auto' }}>
          {results.map(item => <div key={item.symbol} style={{ padding: 7, border: '1px solid var(--k-border)' }}>
            <CheckOption label={item.symbol} checked={symbols.includes(item.symbol)}
              disabled={(!item.execution_supported && !symbols.includes(item.symbol)) ||
                (symbols.length >= MAX_INSTRUMENTS && !symbols.includes(item.symbol))}
              onChange={() => toggleSymbol(item.symbol)} />
            <div style={{ fontSize: 11 }}>{item.name} · {item.instrument_type}
              {item.expiry ? ` · ${item.expiry}` : ''}</div>
            {item.blocker && <div style={{ fontSize: 11, color: 'var(--k-amber)' }}>
              {item.blocker.replace(/_/g, ' ')}</div>}
          </div>)}
        </div>
      </div>
    </details>

    <Field label="Selection summary"
      hint={`${indices.length} indices (watch-only) · ${selectedCurated.length} curated stocks · ${custom.length} extras · ${symbols.length}/${MAX_INSTRUMENTS} instruments`} wide>
      {custom.length > 0 ? <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        {custom.map(symbol => <button key={symbol} type="button" className="sb-tool"
          aria-label={`Remove ${symbol}`} onClick={() => toggleSymbol(symbol)}>
          {symbol} ×
        </button>)}
      </div> : <ConfigNote>Pick indices, F&O stocks or other names, then Apply changes.</ConfigNote>}
    </Field>
    <ConfigNote>Up to 30 names. Each is checked before scanning.</ConfigNote>
  </>;
}
