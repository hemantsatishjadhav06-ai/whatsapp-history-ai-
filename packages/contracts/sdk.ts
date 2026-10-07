export type RequestOptions = {method?: string; body?: unknown; headers?: HeadersInit; signal?: AbortSignal; idempotencyKey?: string};
export type ApiClient = {request<T = unknown>(path: string, options?: RequestOptions): Promise<T>};
export class ApiError extends Error {
  constructor(readonly status: number, readonly reasonCode: string | null, message: string) { super(message); this.name = 'ApiError'; }
}
export function createClient(config: {baseUrl: string; fetchImpl?: typeof fetch;
  headers?: HeadersInit | (() => Promise<HeadersInit>); onUnauthorized?: () => void}): ApiClient {
  const fetcher = config.fetchImpl ?? fetch;
  if (config.baseUrl.startsWith('http')) {
    const origin = new URL(config.baseUrl);
    if (origin.username || origin.password || origin.search || origin.hash
        || (origin.protocol !== 'https:' && !(origin.protocol === 'http:' && ['localhost','127.0.0.1','::1','[::1]'].includes(origin.hostname)))) {
      throw new Error('The API requires HTTPS or an explicit loopback development origin');
    }
  } else if (!config.baseUrl.startsWith('/') || config.baseUrl.startsWith('//')) throw new Error('Invalid API origin');
  return {async request<T>(path: string, options: RequestOptions = {}): Promise<T> {
    let pathname = path.split('?')[0];
    try { for (let n=0;n<3;n++) {const decoded=decodeURIComponent(pathname); if(decoded===pathname) break; pathname=decoded;} }
    catch {throw new Error('Invalid API path encoding');}
    if (!path.startsWith('/') || path.startsWith('//') || path.includes('://') || /[\\\x00-\x20]/.test(pathname)
        || pathname.startsWith('//') || pathname.split('/').some(segment => segment === '.' || segment === '..')
        || /^\/(v1\/)?internal(\/|$)/.test(pathname) || /%[0-9a-f]{2}/i.test(pathname)) throw new Error('API paths must stay inside the public configured origin');
    const headers = new Headers(typeof config.headers === 'function' ? await config.headers() : config.headers);
    new Headers(options.headers).forEach((value, key) => headers.set(key, value));
    if (options.body !== undefined) headers.set('Content-Type', 'application/json');
    if (options.idempotencyKey) headers.set('Idempotency-Key', options.idempotencyKey);
    // Single explicit submission. In particular, uncertainty is never retried.
    const response = await fetcher(config.baseUrl.replace(/\/$/, '') + path, {method: options.method ?? 'GET', headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body), signal: options.signal,
      credentials: 'include', redirect: 'error'});
    if (response.status === 401) config.onUnauthorized?.();
    if (response.status === 204) return undefined as T;
    const payload: unknown = await response.json().catch(() => null);
    if (!response.ok) {
      const detail = payload && typeof payload === 'object' && 'detail' in payload ? (payload as {detail: unknown}).detail : null;
      const reason = detail && typeof detail === 'object' && ('code' in detail||'reason_code' in detail)
        ? String((detail as {code?:unknown;reason_code?:unknown}).code??(detail as {reason_code?:unknown}).reason_code) : null;
      const message = typeof detail === 'string' ? detail : detail && typeof detail === 'object' && 'message' in detail
        ? String((detail as {message: unknown}).message) : reason?reason.replaceAll('_',' '):`Request failed (${response.status})`;
      throw new ApiError(response.status, reason, message);
    }
    return payload as T;
  }};
}
