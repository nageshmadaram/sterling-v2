import React from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { EmaTrendSettings } from '../../src/components/EmaTrendSettings';
import { EmaTrendBoard } from '../../src/components/kite/board/EmaTrendBoard';
import { BoardDetailPane } from '../../src/components/kite/board/BoardDetailPane';
import { EmaTrendChart } from '../../src/components/charts/EmaTrendChart';
import type { BoardSignal } from '../../src/components/kite/board/boardTypes';
import type { EmaTrendMode } from '../../src/types/emaTrend';
import '../../src/styles/globals.css';

const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
function Harness() {
  const [page, setPage] = React.useState<'settings' | 'signals' | 'detail' | 'chart'>('settings');
  const [signal, setSignal] = React.useState<BoardSignal | null>(null);
  const [chart, setChart] = React.useState<{ symbol: string; mode: EmaTrendMode; signalId?: string; configRevision?: number }>({ symbol: 'NSE:TCS', mode: 'swing' });
  React.useEffect(() => {
    const open = () => setPage('settings');
    window.addEventListener('kite-connect-section', open);
    return () => window.removeEventListener('kite-connect-section', open);
  }, []);
  return <main data-kite style={{ padding: 12, background: 'var(--k-bg)', color: 'var(--k-text)', minHeight: '100vh' }}>
    <nav style={{ display: 'flex', gap: 12, marginBottom: 12 }}><button onClick={() => setPage('settings')}>Connect settings</button><button onClick={() => setPage('signals')}>Signals workspace</button></nav>
    <h1 style={{ fontSize: 16 }}>EMA Trend paper workflow</h1>
    {page === 'settings' && <div className="kite-settings-content-wrapper"><EmaTrendSettings /></div>}
    {page === 'signals' && <div style={{ height: 650 }}><EmaTrendBoard onOpenDetail={row => { setSignal(row); setPage('detail'); }} onOpenChart={(symbol, mode, row) => { setChart({ symbol, mode, signalId: row.emaTrend?.signalId, configRevision: row.emaTrend?.configRevision }); setPage('chart'); }} /></div>}
    {page === 'detail' && signal && <BoardDetailPane signal={signal} onClose={() => setPage('signals')} />}
    {page === 'chart' && <EmaTrendChart {...chart} />}
  </main>;
}
createRoot(document.getElementById('root')!).render(<QueryClientProvider client={client}><Harness /></QueryClientProvider>);
