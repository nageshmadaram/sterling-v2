import { filterKitePayload } from './kiteExchanges';
import { currentOfflineMfaProof, clearOfflineMfaProof } from './offlineMfa';
import { API_BASE_URL, AUTH_REQUEST_TIMEOUT_MS, bearerHeaders, getRefreshToken, refreshSterlingAccess } from './auth';
import { notifyAuthExpired, notifyWriteSettled } from './liveSync';
import { VIEW_HEADER, getView } from '../store/useViewStore';

/**
 * Every non-GET request funnels through here, so this is where "a write
 * happened" is observed. It fires whether the request succeeded or failed: a
 * timed-out order POST may have reached the broker, and the screen must re-read
 * the book rather than assume nothing changed.
 */
async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const method = (options?.method || 'GET').toUpperCase();
  try {
    return await perform<T>(path, options);
  } finally {
    if (method !== 'GET') notifyWriteSettled(method, path);
  }
}

let sterlingSessionProbe: Promise<boolean | null> | null = null;

async function sterlingSessionIsValid(): Promise<boolean | null> {
  if (sterlingSessionProbe) return sterlingSessionProbe;
  sterlingSessionProbe = (async () => {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), AUTH_REQUEST_TIMEOUT_MS);
    try {
      const response = await fetch(`${API_BASE_URL}/api/v1/auth/me`, {
        method: 'GET',
        headers: bearerHeaders(),
        signal: controller.signal,
      });
      if (response.ok) return true;
      if (response.status === 401) return false;
      // Backend/network errors are not proof that the operator session expired.
      return null;
    } catch {
      return null;
    } finally {
      clearTimeout(timeout);
    }
  })();
  try {
    return await sterlingSessionProbe;
  } finally {
    sterlingSessionProbe = null;
  }
}

function isKitePath(path: string): boolean {
  return path === '/api/v1/kite' || path.startsWith('/api/v1/kite/');
}

async function perform<T>(path: string, options?: RequestInit): Promise<T> {
  // The view is fixed when the request begins, so a retry after a token refresh asks the same
  // tenant the first try did even if the operator flips the switch in between.
  const view = getView();
  // Sign-in, refresh and /auth/me say who the operator is, not which book is open. Scoping them
  // would make a non-owner who inherits a remembered Simulation view unable to sign in at all.
  const identityPath = path.startsWith('/api/v1/auth/') || path.startsWith('/api/v1/settings/') || path.startsWith('/api/v1/admin/') || path.startsWith('/api/v1/media/');
  const scoped = view === 'simulation' && !identityPath;
  const send = async () => {
    const headers = bearerHeaders(options?.headers);
    if (scoped) headers.set(VIEW_HEADER, 'simulation');
    // Phase 10 proof is in-memory, scoped only to offline evidence endpoints.
    // Never attach it to broker/order/auth/other application requests.
    if (path.startsWith('/api/v1/offline-validation/')) {
      const proof = currentOfflineMfaProof();
      if (proof) headers.set('X-Sterling-Offline-MFA', proof);
    }
    // A suspended mobile request can otherwise keep a query fetching forever.
    // Bound reads only; an order timeout must not imply the broker rejected it.
    const controller = !options?.signal && (!options?.method || options.method.toUpperCase() === 'GET') ? new AbortController() : null;
    const timer = controller ? setTimeout(() => controller.abort(), 20_000) : null;
    try {
      return await fetch(`${API_BASE_URL}${path}`, { ...options, headers, ...(controller ? { signal: controller.signal } : {}) });
    } finally { if (timer != null) clearTimeout(timer); }
  };

  let resp = await send();
  let refreshDeferred = false;
  const authPath = path.split('?')[0];
  const credentialEndpoint = authPath === '/api/v1/auth/login' || authPath === '/api/v1/auth/refresh';
  if (resp.status === 401 && !credentialEndpoint) {
    // /auth/me is the startup session check and needs renewal too. Excluding it
    // leaves an expired mobile access token stuck behind the reconnect screen.
    // Kite uses its own broker session. A Kite token rejection must never be
    // interpreted as an expired Sterling operator JWT. Prove the operator
    // session independently before refreshing/clearing Sterling credentials.
    //
    // For non-Kite endpoints a 401 already means the Sterling bearer was
    // rejected, so the existing refresh path remains unchanged.
    const operatorStillValid = isKitePath(path)
      ? await sterlingSessionIsValid()
      : false;

    if (operatorStillValid !== true) {
      const refreshed = await refreshSterlingAccess();
      if (refreshed) resp = await send();
      else refreshDeferred = Boolean(getRefreshToken());
      // Still unauthorised after a refresh attempt. Tell the app only when the
      // session is genuinely over. If /auth/me could not be reached, preserve
      // the tokens rather than turning a network problem into a forced logout.
      if (
        resp.status === 401
        && operatorStillValid !== null
        && !refreshDeferred
      ) {
        notifyAuthExpired();
      }
    }
  }

  if (resp.status === 401 && !isKitePath(path) && refreshDeferred) {
    const failure = new Error('Sterling is reconnecting to renew your saved session.') as Error & {status?:number}; failure.status=503; throw failure;
  }
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({ detail: resp.statusText }));
    let msg = `HTTP ${resp.status}`;
    if (err && typeof err === 'object' && 'detail' in err) {
      if (typeof err.detail === 'string') {
        msg = err.detail;
      } else if (typeof err.detail === 'object' && err.detail !== null) {
        const detail = err.detail;
        const description = detail.reason_text || detail.message;
        msg = Array.isArray(description) ? description.join('; ')
          : typeof description === 'string' && description ? description : JSON.stringify(detail);
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
