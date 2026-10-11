import { useSyncExternalStore } from 'react';
export type DiagnosticSource = 'frontend' | 'network';
export type Diagnostic = { id: number; ts_ms: number; source: DiagnosticSource; level: string; message: string;
  method?: string; path?: string; status?: number; duration_ms?: number; correlation_id?: string };
let entries: Diagnostic[] = [];
let sequence = 0;
const listeners = new Set<() => void>();
let installed = false;
export function redactDiagnostic(text: string): string {
  return text.replace(/bot\d+:[\w-]+/g, 'bot[redacted]')
    .replace(/\bBearer\s+[\w.~+\/-]+=*/gi, 'Bearer [redacted]')
    .replace(/(["']?(?:password|authorization|cookie|set-cookie|api[_-]?key|api[_-]?secret|access[_-]?token|request[_-]?token|refresh[_-]?token|secret|enctoken|checksum)["']?\s*[:=]\s*)(?:"[^"\n]*"|'[^'\n]*'|[^\s,"'&}]+)/gi, (match, prefix: string) => {
      const value = match.slice(prefix.length);
      return prefix + (value.startsWith('"') ? '"[redacted]"' : value.startsWith("'") ? "'[redacted]'" : '[redacted]');
    });
}
export function recordDiagnostic(level: string, message: string, meta: Partial<Omit<Diagnostic, 'id' | 'ts_ms' | 'level' | 'message'>> = {}) {
  entries = [...entries, { ...meta, source: meta.source || 'frontend', id: ++sequence, ts_ms: Date.now(),
    level: level === 'WARNING' ? 'WARN' : level, message: redactDiagnostic(message).slice(0, 4000) }].slice(-1000);
  listeners.forEach(fn => fn());
}
export function diagnosticSnapshot() { return entries; }
export function diagnosticBundle(backend: unknown, engine: unknown, backendError: unknown, startedAt: number) {
  // Sanitize values before JSON serialization: regex replacement on encoded
  // JSON can corrupt escaped quotes in an exception message.
  const sensitive = /^(password|authorization|cookie|set-cookie|api[_-]?key|api[_-]?secret|access[_-]?token|request[_-]?token|refresh[_-]?token|secret|enctoken|checksum)$/i;
  const sanitize = (value: unknown): unknown => {
    if (typeof value === 'string') return redactDiagnostic(value);
    if (Array.isArray(value)) return value.map(sanitize);
    if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).map(([key, entry]) => [key, sensitive.test(key) ? '[redacted]' : sanitize(entry)]));
    return value;
  };
  return JSON.stringify(sanitize({ version: 1, started_at: new Date(startedAt).toISOString(),
    exported_at: new Date().toISOString(), sources: { frontend: 'available', network: 'available',
      backend: backendError ? 'unavailable: ' + String(backendError) : backend ? 'available' : 'not received' },
    client: entries.filter(e => e.ts_ms >= startedAt), backend, engine }), null, 2);
}
function pathname(url: string) { try { return redactDiagnostic(new URL(url, location.href).pathname); } catch { return 'request'; } }
export function installDiagnostics() {
  if (installed) return;
  installed = true;
  for (const method of ['debug', 'info', 'log', 'warn', 'error'] as const) {
    const original = console[method].bind(console);
    console[method] = (...args: unknown[]) => {
      original(...args);
      const message = args.map(value => { try { return value instanceof Error ? `${value.message}\n${value.stack || ''}`
        : typeof value === 'string' ? value : JSON.stringify(value); } catch { return '[unserializable]'; } }).join(' ');
      recordDiagnostic(method === 'log' ? 'INFO' : method.toUpperCase(), message);
    };
  }
  window.addEventListener('error', e => {
    if (e instanceof ErrorEvent) recordDiagnostic('ERROR', e.error instanceof Error ? e.error.stack || e.message : e.message);
    else { const resource = (e as Event).target as HTMLScriptElement | HTMLLinkElement;
      recordDiagnostic('ERROR', `Resource failed: ${pathname(('src' in resource ? resource.src : resource.href) || '')}`, { source: 'network' }); }
  }, true);
  window.addEventListener('unhandledrejection', e => recordDiagnostic('ERROR',
    e.reason instanceof Error ? e.reason.stack || e.reason.message : String(e.reason)));
  const originalFetch = window.fetch.bind(window);
  window.fetch = async (...args) => {
    const start = performance.now();
    const request = args[0];
    const path = pathname(request instanceof Request ? request.url : String(request));
    const method = (args[1]?.method || (request instanceof Request ? request.method : 'GET')).toUpperCase();
    // Polling the log ring is deliberately excluded; otherwise observing logs creates logs forever.
    const observer = path.endsWith('/engine/server-logs');
    try {
      const response = await originalFetch(...args);
      const duration_ms = Math.round(performance.now() - start);
      const correlation_id = redactDiagnostic(response.headers.get('x-correlation-id') || '').slice(0, 64);
      if (!observer) recordDiagnostic(response.ok ? 'DEBUG' : response.status >= 500 ? 'ERROR' : 'WARN',
        `${method} ${response.status} ${path} (${duration_ms} ms)${correlation_id ? ` · ${correlation_id}` : ''}`,
        { source: 'network', method, path, status: response.status, duration_ms, correlation_id });
      return response;
    } catch (error) {
      if (!observer) recordDiagnostic('ERROR', `${method} ${path}: ${error instanceof Error ? error.message : String(error)}`,
        { source: 'network', method, path, duration_ms: Math.round(performance.now() - start) });
      throw error;
    }
  };
  if (window.EventSource) {
    const Original = window.EventSource;
    window.EventSource = class extends Original {
      constructor(url: string | URL, options?: EventSourceInit) {
        super(url, options);
        const path = pathname(String(url));
        this.addEventListener('open', () => recordDiagnostic('DEBUG', `Stream connected ${path}`, { source: 'network', path }));
        this.addEventListener('error', () => recordDiagnostic('WARN', `Stream disconnected ${path}`, { source: 'network', path }));
      }
    };
  }
  recordDiagnostic('INFO', 'Diagnostic capture ready');
}
const subscribe = (fn: () => void) => { listeners.add(fn); return () => { listeners.delete(fn); }; };
export function useFrontendDiagnostics() { return useSyncExternalStore(subscribe, () => entries); }
