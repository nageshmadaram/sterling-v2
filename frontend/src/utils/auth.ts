export const API_BASE_URL: string =
  (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? 'http://localhost:8000';

const ACCESS_KEY = 'sterling.auth.access';
const REFRESH_KEY = 'sterling.auth.refresh';

export interface SterlingTokens {
  access_token: string;
  refresh_token: string;
  token_type?: string;
}

function storage(): Storage | null {
  try {
    return typeof window !== 'undefined' ? window.sessionStorage : null;
  } catch {
    return null;
  }
}

export function getAccessToken(): string {
  return storage()?.getItem(ACCESS_KEY) || '';
}

export function getRefreshToken(): string {
  return storage()?.getItem(REFRESH_KEY) || '';
}

export function setAuthTokens(tokens: SterlingTokens): void {
  const s = storage();
  if (!s) return;
  s.setItem(ACCESS_KEY, tokens.access_token);
  s.setItem(REFRESH_KEY, tokens.refresh_token);
}

export function clearAuthTokens(): void {
  const s = storage();
  if (!s) return;
  s.removeItem(ACCESS_KEY);
  s.removeItem(REFRESH_KEY);
}

export function bearerHeaders(input?: HeadersInit): Headers {
  const headers = new Headers(input);
  const token = getAccessToken();
  if (token) headers.set('Authorization', `Bearer ${token}`);
  return headers;
}

export function authenticatedFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  return fetch(input, { ...init, headers: bearerHeaders(init?.headers) });
}

export async function loginSterling(username: string, password: string): Promise<SterlingTokens> {
  const body = new URLSearchParams({ username, password });
  const response = await fetch(`${API_BASE_URL}/api/v1/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body,
  });
  if (!response.ok) throw new Error('Invalid username or password');
  const tokens = await response.json() as SterlingTokens;
  setAuthTokens(tokens);
  return tokens;
}

export async function refreshSterlingAccess(): Promise<boolean> {
  const refresh = getRefreshToken();
  if (!refresh) return false;
  try {
    const response = await fetch(`${API_BASE_URL}/api/v1/auth/refresh`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: refresh }),
    });
    if (!response.ok) {
      clearAuthTokens();
      return false;
    }
    setAuthTokens(await response.json() as SterlingTokens);
    return true;
  } catch {
    return false;
  }
}

export function authenticatedWebSocketUrl(url: string): string {
  const token = getAccessToken();
  if (!token) return url;
  const join = url.includes('?') ? '&' : '?';
  return `${url}${join}token=${encodeURIComponent(token)}`;
}
