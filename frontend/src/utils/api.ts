import { filterKitePayload } from './kiteExchanges';
import { API_BASE_URL, bearerHeaders, refreshSterlingAccess } from './auth';

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const send = () => fetch(`${API_BASE_URL}${path}`, {
    ...options,
    headers: bearerHeaders(options?.headers),
  });

  let resp = await send();
  if (resp.status === 401 && !path.startsWith('/api/v1/auth/')) {
    if (await refreshSterlingAccess()) resp = await send();
  }

  if (!resp.ok) {
    const err = await resp.json().catch(() => ({ detail: resp.statusText }));
    let msg = `HTTP ${resp.status}`;
    if (err && typeof err === 'object' && 'detail' in err) {
      if (typeof err.detail === 'string') {
        msg = err.detail;
      } else if (typeof err.detail === 'object' && err.detail !== null) {
        msg = JSON.stringify(err.detail);
      }
    }
    const failure = new Error(msg) as Error & { status?: number };
    failure.status = resp.status;
    throw failure;
  }
  if (resp.status === 204) return undefined as T;
  const payload = await resp.json() as T;
  return filterKitePayload(path, payload);
}

const json = (body: unknown) => ({
  headers: { 'Content-Type': 'application/json' },
  body: body !== undefined ? JSON.stringify(body) : undefined,
});

export const api = {
  get: <T>(path: string) => request<T>(path),
  // `keepalive: true` lets a POST outlive a page unload (tab close / refresh /
  // navigation) — a normal fetch is cancelled by the unload and the body never
  // leaves. Used by the chart-state unload flush. Routing it through the same
  // request() keeps the URL and headers identical to a regular save.
  post: <T>(path: string, body?: unknown, opts?: { keepalive?: boolean }) =>
    request<T>(path, { method: 'POST', ...json(body), ...(opts?.keepalive ? { keepalive: true } : {}) }),
  put: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'PUT', ...json(body) }),
  patch: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'PATCH', ...json(body) }),
  delete: <T>(path: string, body?: unknown) =>
    request<T>(path, { method: 'DELETE', ...(body !== undefined ? json(body) : {}) }),
};
