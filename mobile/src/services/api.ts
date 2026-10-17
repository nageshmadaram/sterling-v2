import { useStore } from '../store/useStore';

let refreshInFlight: { host: string; token: string; promise: Promise<boolean> } | null = null;

async function renewSession(host: string, rejectedToken: string): Promise<boolean> {
  const session = useStore.getState();
  if (session.apiHost !== host) return false;
  if (session.authToken !== rejectedToken) return !!session.authToken;
  const refresh = session.refreshToken;
  if (!refresh) { session.setSession('', ''); return false; }
  if (refreshInFlight?.host === host && refreshInFlight.token === refresh) return refreshInFlight.promise;
  const promise = (async () => {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 20_000);
    try {
      const response = await fetch(`${host}/api/v1/auth/refresh`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ refresh_token: refresh }), signal: controller.signal,
      });
      const current = () => useStore.getState().apiHost === host && useStore.getState().refreshToken === refresh;
      if (response.status === 401 || response.status === 403) {
        if (current()) useStore.getState().setSession('', '');
        return false;
      }
      if (!response.ok) return false;
      const tokens = await response.json();
      if (!current() || typeof tokens.access_token !== 'string' || !tokens.access_token ||
          typeof tokens.refresh_token !== 'string' || !tokens.refresh_token) return false;
      useStore.getState().setSession(tokens.access_token, tokens.refresh_token);
      return true;
    } catch { return false; }
    finally { clearTimeout(timeout); }
  })();
  refreshInFlight = { host, token: refresh, promise };
  try { return await promise; }
  finally { if (refreshInFlight?.promise === promise) refreshInFlight = null; }
}

async function request<T>(path: string, options?: RequestInit, retried = false): Promise<T> {
  const { apiHost: host, authToken: token } = useStore.getState();
  if (!token) throw new Error('Sign in to Sterling to continue.');
  const headers = new Headers(options?.headers);
  headers.set('Authorization', `Bearer ${token}`);
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 20_000);
  try {
    let response: Response;
    try {
      response = await fetch(`${host}${path}`, { ...options, headers, signal: controller.signal });
    } catch {
      throw new Error(options?.method && options.method !== 'GET'
        ? 'The result could not be confirmed. Check orders before trying again.'
        : 'Could not reach Sterling. Check your connection and server address.');
    }
    if (response.status === 401 && !retried) {
      const renewed = await renewSession(host, token);
      if (renewed && (!options?.method || options.method === 'GET')) return request<T>(path, options, true);
      if (renewed) throw new Error('Session renewed. Review the order status before submitting again.');
      throw new Error(useStore.getState().authToken
        ? 'Session renewal is temporarily unavailable. Try again when connected.'
        : 'Your session expired. Sign in again.');
    }
    if (!response.ok) {
      const body = await response.json().catch(() => ({ detail: response.statusText }));
      const detail = body?.detail;
      throw new Error(typeof detail === 'string' ? detail : `Request failed (HTTP ${response.status}).`);
    }
    if (response.status === 204) return undefined as T;
    try { return await response.json() as T; }
    catch {
      throw new Error(options?.method && options.method !== 'GET'
        ? 'The result could not be confirmed. Check orders before trying again.'
        : 'Sterling returned an unreadable response. Refresh when connected.');
    }
  } finally { clearTimeout(timeout); }
}

const json = (body: unknown) => ({
  headers: { 'Content-Type': 'application/json' },
  body: body !== undefined ? JSON.stringify(body) : undefined,
});

export const api = {
  get: <T>(path: string) => request<T>(path),
  post: <T>(path: string, body?: unknown) => request<T>(path, { method: 'POST', ...json(body) }),
  put: <T>(path: string, body?: unknown) => request<T>(path, { method: 'PUT', ...json(body) }),
  patch: <T>(path: string, body?: unknown) => request<T>(path, { method: 'PATCH', ...json(body) }),
  delete: <T>(path: string, body?: unknown) => request<T>(path, { method: 'DELETE', ...(body !== undefined ? json(body) : {}) }),
};
