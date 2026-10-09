import type { NextRequest } from 'next/server';
import { boundedProxyBody, UploadTimeoutError } from './proxy-body';
import { acquireProxyLease, leaseProxyBody } from './proxy-admission';
import { signRateSource } from './rate-source';

// Native clients use bearer sessions. Browser sessions and provider callbacks
// each have a separate boundary; none can reach internal service routes.
const NATIVE_ROUTES = /^(?:auth\/(?:config|native\/(?:nonce|login|refresh|revoke|logout|google\/(?:start|exchange))|sessions(?:\/[A-Za-z0-9_-]+)?)|me|ui\/(?:bootstrap|updates|resolve)|(?:workspaces|connectors|conversations|drafts|messages|imports|inbox|tasks|memories|forward-routes|actions|jobs|schedules|scheduled-intents|contacts|people|integrations)(?:\/[A-Za-z0-9_.:@+-]+){0,3}|integrations\/whatsapp\/personal\/(?:config|status|start|pairing|chats(?:\/authorize)?|disconnect|authorship\/confirm)|automation\/grants(?:\/[^/]+)?|assistant\/(?:commands|digest)|privacy\/(?:retention(?:\/sweep)?|model-processing)|pause-all|resume-all|activity|data-export|account-data)$/;
const PUBLIC_NATIVE_POSTS = new Set(['auth/native/nonce', 'auth/native/login', 'auth/native/refresh', 'auth/native/revoke', 'auth/native/google/start', 'auth/native/google/exchange']);
const CONTROL = /^(?:pause-all|resume-all|auth\/native\/(?:logout|revoke)|conversations\/[^/]+\/(?:takeover|resume|control)|actions\/[^/]+\/cancel|integrations\/whatsapp\/personal\/disconnect)$/;
const SECURITY = { 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff', 'Referrer-Policy': 'no-referrer' };

function unavailable(detail: string, status = 503) {
  return Response.json({ detail }, { status, headers: SECURITY });
}

function backend(route: string, search: string) {
  try {
    const origin = new URL(process.env.BACKEND_URL ?? '');
    if (!['http:', 'https:'].includes(origin.protocol) || origin.username || origin.password || origin.search || origin.hash || origin.pathname !== '/') return null;
    const url = new URL(`/v1/${route}`, origin);
    url.search = search;
    return url;
  } catch { return null; }
}

function nativeRedirect(value: string | null): string | null {
  if (!value) return null;
  try {
    const expected = new URL(process.env.GOOGLE_NATIVE_APP_REDIRECT_URI ?? 'milo://oauth');
    const target = new URL(value);
    if (expected.href !== 'milo://oauth') return null;
    if (target.protocol !== expected.protocol || target.hostname !== expected.hostname || target.port !== expected.port || target.pathname !== expected.pathname || target.username || target.password || target.hash) return null;
    const parameters = [...target.searchParams.entries()];
    if (parameters.length !== 1) return null;
    const [key, content] = parameters[0]!;
    if (key === 'handoff' && /^[A-Za-z0-9_-]{32,256}$/.test(content)) return target.href;
    if (key === 'error' && /^[a-z_]{1,64}$/.test(content)) return target.href;
  } catch { /* An invalid provider redirect is never followed. */ }
  return null;
}

async function forward(request: NextRequest, route: string, search: string, headers: Headers, maxBytes: number, mode: 'native' | 'webhook' | 'callback') {
  const target = backend(route, search);
  if (!target) return unavailable('The private backend is not configured');
  if (!signRateSource(request, headers)) return unavailable('Trusted ingress configuration is unavailable');
  const isImport = mode === 'native' && (route === 'imports' || route === 'imports/preview');
  const isControl = mode === 'native' && ((request.method === 'POST' && CONTROL.test(route))
    || (request.method === 'PUT' && /^conversations\/[^/]+\/permissions$/.test(route))
    || (request.method === 'DELETE' && /^(?:auth\/sessions\/[^/]+|memories\/[^/]+|automation\/grants\/[^/]+|connectors\/[^/]+|account-data)$/.test(route)));
  const lease = acquireProxyLease(isControl, isImport);
  if (!lease) return Response.json({ detail: 'The proxy is busy. This request was not submitted.', reason_code: 'PROXY_BUSY' },
    { status: 503, headers: { ...SECURITY, 'Retry-After': '1' } });
  const aborted = () => lease.release();
  request.signal.addEventListener('abort', aborted, { once: true });
  if (request.signal.aborted) aborted();
  let streaming = false;
  try {
    let body: ArrayBuffer | undefined;
    if (!['GET', 'HEAD'].includes(request.method)) {
      try { body = await boundedProxyBody(request, maxBytes); }
      catch (error) {
        return unavailable(error instanceof UploadTimeoutError ? 'Upload deadline exceeded' : error instanceof RangeError ? 'Upload is too large' : 'Invalid upload',
          error instanceof UploadTimeoutError ? 408 : error instanceof RangeError ? 413 : 400);
      }
    }
    const signal = AbortSignal.any([request.signal, AbortSignal.timeout(25000)]);
    try {
      const response = await fetch(target, { method: request.method, headers, body, signal, cache: 'no-store', redirect: 'manual', credentials: 'omit' });
      if (response.status >= 300 && response.status < 400) {
        const location = mode === 'callback' && response.status === 303 ? nativeRedirect(response.headers.get('location')) : null;
        void response.body?.cancel().catch(() => undefined);
        if (!location) return unavailable('The authentication redirect could not be confirmed', 502);
        return new Response(null, { status: 303, headers: { ...SECURITY, Location: location } });
      }
      const returned = new Headers({ ...SECURITY, 'Content-Type': response.headers.get('content-type') ?? 'application/json' });
      const retry = response.headers.get('retry-after');
      if (retry && /^\d{1,4}$/.test(retry)) returned.set('retry-after', retry);
      // No backend cookies, CORS grants, service headers or arbitrary Location
      // are exposed on these credential-separated public routes.
      const output = new Response(response.body ? leaseProxyBody(response.body, lease, signal) : null,
        { status: response.status, headers: returned });
      streaming = response.body !== null;
      return output;
    } catch {
      return unavailable('The server did not confirm this request. Check current state before retrying.', 504);
    }
  } finally {
    request.signal.removeEventListener('abort', aborted);
    if (!streaming) lease.release();
  }
}

export async function nativeProxy(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  let { path } = await context.params;
  if (path[0] === 'v1') path = path.slice(1);
  if (!path.length || path.some(part => !/^[A-Za-z0-9_.:@+-]+$/.test(part) || part.includes('..'))) return unavailable('This native service route is unavailable', 404);
  const route = path.join('/');
  if (!NATIVE_ROUTES.test(route)) return unavailable('This native service route is unavailable', 404);
  if (request.headers.has('origin') || request.headers.has('sec-fetch-site') || request.headers.has('cookie')) return unavailable('Use the browser session route for browser requests', 403);
  if (route.startsWith('integrations/whatsapp/personal/')) {
    const allowedMethod = /^(?:config|status|pairing|chats)$/.test(route.slice('integrations/whatsapp/personal/'.length)) ? 'GET' : 'POST';
    if (request.method !== allowedMethod) return unavailable('Method unavailable', 405);
  }
  const publicAuth = PUBLIC_NATIVE_POSTS.has(route);
  if ((publicAuth && request.method !== 'POST') || (route === 'auth/config' && request.method !== 'GET')) return unavailable('Method unavailable', 405);
  const authorization = request.headers.get('authorization');
  if (!publicAuth && route !== 'auth/config' && (!authorization || !/^Bearer na_[A-Za-z0-9_-]{20,192}$/.test(authorization))) return unavailable('A native bearer session is required', 401);
  const headers = new Headers();
  if (authorization && !publicAuth) headers.set('authorization', authorization);
  for (const name of ['content-type', 'idempotency-key']) {
    const value = request.headers.get(name); if (value) headers.set(name, value);
  }
  return forward(request, route, request.nextUrl.search, headers,
    route === 'imports' || route === 'imports/preview' ? 12 * 1024 * 1024 : 64 * 1024, 'native');
}

export async function whatsappWebhook(request: NextRequest) {
  if (!['GET', 'POST'].includes(request.method)) return unavailable('Method unavailable', 405);
  const headers = new Headers();
  if (request.method === 'POST') {
    const signature = request.headers.get('x-hub-signature-256');
    if (!signature || !/^sha256=[a-fA-F0-9]{64}$/.test(signature)) return unavailable('A signed WhatsApp webhook is required', 401);
    if (request.headers.get('content-type')?.split(';')[0]?.trim().toLowerCase() !== 'application/json') return unavailable('JSON webhook required', 415);
    headers.set('x-hub-signature-256', signature); headers.set('content-type', 'application/json');
  }
  const search = new URLSearchParams();
  for (const name of ['hub.mode', 'hub.challenge', 'hub.verify_token']) {
    const values = request.nextUrl.searchParams.getAll(name);
    if (values.length > 1 || values.some(value => value.length > 512)) return unavailable('Invalid webhook verification query', 400);
    if (values.length) search.set(name, values[0]!);
  }
  return forward(request, 'webhooks/whatsapp', search.toString(), headers, 1_000_000, 'webhook');
}

export async function nativeGoogleCallback(request: NextRequest) {
  if (request.method !== 'GET') return unavailable('Method unavailable', 405);
  const search = new URLSearchParams();
  for (const name of ['state', 'code', 'error']) {
    const values = request.nextUrl.searchParams.getAll(name);
    if (values.length > 1 || values.some(value => value.length > (name === 'code' ? 4096 : 256) || /[\x00-\x1f\x7f]/.test(value))) return unavailable('Invalid Google callback', 400);
    if (values.length) search.set(name, values[0]!);
  }
  return forward(request, 'auth/native/google/callback', search.toString(), new Headers(), 0, 'callback');
}
