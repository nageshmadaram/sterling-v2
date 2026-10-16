export const API_BASE_URL: string =
  (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? '';
export const AUTH_REQUEST_TIMEOUT_MS = 20_000;

const ACCESS_KEY = 'sterling.auth.access';
const REFRESH_KEY = 'sterling.auth.refresh';

export interface SterlingTokens {
  access_token: string;
  refresh_token: string;
  token_type?: string;
}

function sessionStore(): Storage | null {
  try {
    return typeof window !== 'undefined' ? window.sessionStorage : null;
  } catch {
    return null;
  }
}

function localStore(): Storage | null {
  try {
    return typeof window !== 'undefined' ? window.localStorage : null;
  } catch {
    return null;
  }
}

export function getAccessToken(): string {
  const sess = sessionStore()?.getItem(ACCESS_KEY);
  if (sess) return sess;
  const local = localStore()?.getItem(ACCESS_KEY);
  if (local) {
    sessionStore()?.setItem(ACCESS_KEY, local);
    return local;
  }
  return '';
}

export function getRefreshToken(): string {
  const sess = sessionStore()?.getItem(REFRESH_KEY);
  if (sess) return sess;
  const local = localStore()?.getItem(REFRESH_KEY);
  if (local) {
    sessionStore()?.setItem(REFRESH_KEY, local);
    return local;
  }
  return '';
}

export function setAuthTokens(tokens: SterlingTokens): void {
  const s = sessionStore();
  if (s) {
    s.setItem(ACCESS_KEY, tokens.access_token);
    s.setItem(REFRESH_KEY, tokens.refresh_token);
  }
  const ls = localStore();
  if (ls) {
    ls.setItem(ACCESS_KEY, tokens.access_token);
    ls.setItem(REFRESH_KEY, tokens.refresh_token);
  }
}

export function clearAuthTokens(): void {
  const s = sessionStore();
  if (s) {
    s.removeItem(ACCESS_KEY);
    s.removeItem(REFRESH_KEY);
  }
  const ls = localStore();
  if (ls) {
    ls.removeItem(ACCESS_KEY);
    ls.removeItem(REFRESH_KEY);
  }
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

let refreshInFlight: Promise<boolean> | null = null;
export async function refreshSterlingAccess(): Promise<boolean> {
 if(refreshInFlight)return refreshInFlight;
 refreshInFlight=(async()=>{
  const refresh=getRefreshToken();if(!refresh)return false;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), AUTH_REQUEST_TIMEOUT_MS);
  try {
   const response=await fetch(`${API_BASE_URL}/api/v1/auth/refresh`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({refresh_token:refresh}),signal:controller.signal});
   if(!response.ok){if(response.status===401||response.status===403)clearAuthTokens();return false;}
   setAuthTokens(await response.json() as SterlingTokens);return true;
  }catch{return false;}
  finally { clearTimeout(timeout); }
 })();
 try{return await refreshInFlight;}finally{refreshInFlight=null;}
}

export async function rememberSterlingSession(): Promise<void> {
 try {const response=await authenticatedFetch(`${API_BASE_URL}/api/v1/auth/remember`,{method:'POST'});if(response.ok)setAuthTokens(await response.json() as SterlingTokens);}catch{/* Existing sign-in survives an unavailable backend. */}
}

export function authenticatedWebSocketUrl(url: string): string {
  // Kept as a compatibility helper for callers that only need URL resolution.
  // Authentication is sent as the first WebSocket frame; never place bearer
  // tokens in a URL where proxies/access logs may capture them.
  return url;
}
