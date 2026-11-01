import { useQuery } from '@tanstack/react-query';
import { api } from '../../utils/api';
import { useView } from '../../store/useViewStore';

type ToolMetadata = {
  key: string;
  label: string;
  category: 'analytics' | 'charting' | 'strategy' | 'feed';
  method: 'GET';
  api_path: string;
  backend_module: string;
  requires_kite_feature: true;
  permits_order_write: false;
  launches_runtime: false;
};

type Catalog = {
  version: '0.1.0';
  execution_authorized: false;
  tools_ui_mounted: false;
  tools: ToolMetadata[];
};

// The backend is the sole source of tool metadata. Do not create a second
// frontend command registry or interpret a returned path as a callable action.
const KNOWN_READ_TOOLS = new Set([
  'pcr_session', 'pcr_instrument', 'candles', 'strategy_modes', 'ticker_health',
]);
const CATEGORIES = new Set(['analytics', 'charting', 'strategy', 'feed']);

export function safeToolCatalog(value: unknown): Catalog | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const raw = value as Partial<Catalog>;
  if (
    raw.version !== '0.1.0' || raw.execution_authorized !== false
    || raw.tools_ui_mounted !== false || !Array.isArray(raw.tools)
    || raw.tools.length > KNOWN_READ_TOOLS.size
  ) return null;
  const seen = new Set<string>();
  for (const candidate of raw.tools) {
    if (!candidate || typeof candidate !== 'object') return null;
    const item = candidate as Partial<ToolMetadata>;
    if (
      typeof item.key !== 'string' || !KNOWN_READ_TOOLS.has(item.key)
      || seen.has(item.key)
      || typeof item.label !== 'string' || !item.label.trim() || item.label.length > 90
      || !CATEGORIES.has(item.category ?? '')
      || item.method !== 'GET'
      || typeof item.api_path !== 'string' || !item.api_path.startsWith('/api/v1/')
      || typeof item.backend_module !== 'string'
      || !item.backend_module.startsWith('app.api.v1.endpoints.')
      || item.requires_kite_feature !== true
      || item.permits_order_write !== false || item.launches_runtime !== false
    ) return null;
    seen.add(item.key);
  }
  return raw as Catalog;
}

export function PlatformToolsPane() {
  const view = useView();
  // Both views use the existing authenticated API, with the canonical
  // Simulation header supplied by api.get. Never cross-cache tenant views.
  const query = useQuery({
    queryKey: ['kite', 'platform-tools-readonly', view],
    queryFn: () => api.get<unknown>('/api/v1/platform/tools'),
    retry: false,
    staleTime: 0,
  });
  const catalog = query.isSuccess ? safeToolCatalog(query.data) : null;

  return <section aria-label="Read-only tools catalog" style={{
    padding: 16, color: 'var(--k-ink-2)', background: 'var(--k-bg)',
    maxWidth: '100%', overflowY: 'auto',
  }}>
    <h2 style={{ fontSize: 16, margin: '0 0 8px' }}>Tools</h2>
    <p style={{ margin: '0 0 14px', fontSize: 12, color: 'var(--k-dim)' }}>
      Existing Sterling data tools. This catalog does not run a strategy,
      submit orders, create market feeds, or grant additional permissions.
    </p>
    {query.isPending ? <p role="status">Loading tools from your permitted workspace…</p>
      : !catalog ? <div role="alert">The tools catalog is unavailable or its safety contract could not be verified. No tools were opened.</div>
      : <ul aria-label="Available read-only tools" style={{
        listStyle: 'none', margin: 0, padding: 0, display: 'grid', gap: 8,
      }}>
        {catalog.tools.map(tool => <li key={tool.key} data-testid={`platform-tool-${tool.key}`}
          style={{ border: '1px solid var(--k-border)', borderRadius: 6, padding: '10px 12px' }}>
          <strong style={{ fontSize: 13 }}>{tool.label}</strong>
          <span style={{ fontSize: 11, marginLeft: 8, color: 'var(--k-dim)' }}>
            {tool.category} · read only
          </span>
          <div style={{ marginTop: 5, fontSize: 11, overflowWrap: 'anywhere' }}>
            <code>{tool.method} {tool.api_path}</code>
          </div>
        </li>)}
      </ul>}
  </section>;
}
export default PlatformToolsPane;
