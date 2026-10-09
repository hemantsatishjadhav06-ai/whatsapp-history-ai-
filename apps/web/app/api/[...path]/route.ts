import type { NextRequest } from 'next/server';
import { boundedProxyBody, IMPORT_UPLOAD_DEADLINE_MS, JSON_UPLOAD_DEADLINE_MS, UploadTimeoutError } from '../../../lib/proxy-body';
import { acquireProxyLease, leaseProxyBody } from '../../../lib/proxy-admission';
import { signRateSource } from '../../../lib/rate-source';

export const dynamic = 'force-dynamic';
const ALLOWED = /^(?:auth\/(?:config|nonce|google|access-code|csrf|logout|sessions(?:\/[^/]+)?)|me|ui\/(?:bootstrap|updates|resolve)|(?:workspaces|connectors|conversations|drafts|messages|imports|inbox|tasks|memories|forward-routes|actions|jobs|schedules|scheduled-intents|contacts|people|integrations)(?:\/[A-Za-z0-9_.:@+-]+){0,3}|integrations\/whatsapp\/personal\/(?:config|status|start|pairing|chats(?:\/authorize)?|disconnect|authorship\/confirm)|automation\/grants(?:\/[^/]+)?|assistant\/(?:commands|digest)|privacy\/(?:retention(?:\/sweep)?|model-processing)|pause-all|resume-all|activity|data-export|account-data)$/;
const MAX_JSON_BODY_BYTES = 64 * 1024;
const MAX_IMPORT_BODY_BYTES = 12 * 1024 * 1024;
const CONTROL_ROUTES = /^(?:pause-all|resume-all|auth\/logout|conversations\/[^/]+\/(?:control|takeover|resume)|actions\/[^/]+\/cancel|integrations\/whatsapp\/personal\/disconnect)$/;
// The only private API routes that accept a browser request without a session cookie.
const PUBLIC_ROUTES = new Map([['auth/config', 'GET'], ['auth/nonce', 'GET'], ['auth/google', 'POST'], ['auth/access-code', 'POST']]);

function loopback(hostname: string) {
  return hostname === 'localhost' || hostname === '127.0.0.1' || hostname === '[::1]' || hostname === '::1';
}

function applicationOrigin(request: NextRequest): string | null {
  const configured = process.env.PUBLIC_APP_ORIGIN;
  if (configured) {
    try {
      const origin = new URL(configured);
      if (!['http:', 'https:'].includes(origin.protocol) || origin.username || origin.password || origin.search || origin.hash
          || origin.pathname !== '/' || (origin.protocol === 'http:' && !loopback(origin.hostname))) return null;
      return origin.origin;
    } catch { return null; }
  }
  // Local preview can use its actual Host. Public deployments require an explicit
  // origin; forwarded headers alone cannot define a trusted application domain.
  try {
    const local = new URL(`${request.nextUrl.protocol}//${request.headers.get('host') ?? request.nextUrl.host}`);
    return loopback(local.hostname) && !local.username && !local.password ? local.origin : null;
  } catch { return null; }
}

async function proxy(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  const route = path.join('/');
  if (!ALLOWED.test(route) || path.some(segment => !/^[A-Za-z0-9_.:@+-]+$/.test(segment) || segment.includes('..'))) {
    return Response.json({ detail: 'This service route is not available to the browser' }, { status: 404 });
  }
  if (route.startsWith('integrations/whatsapp/personal/')) {
    const allowedMethod = /^(?:config|status|pairing|chats)$/.test(route.slice('integrations/whatsapp/personal/'.length)) ? 'GET' : 'POST';
    if (request.method !== allowedMethod) return Response.json({ detail: 'Method unavailable' }, { status: 405, headers: { 'Cache-Control': 'no-store' } });
  }
  const publicMethod = PUBLIC_ROUTES.get(route);
  if (publicMethod && request.method !== publicMethod) return Response.json({ detail: 'Method unavailable' }, { status: 405, headers: { 'Cache-Control': 'no-store' } });
  const configured = process.env.BACKEND_URL;
  if (!configured) {
    if (route === 'auth/config') return Response.json({ google_configured: false, backend_configured: false, client_id: null });
    return Response.json({ detail: 'The private backend is not configured. The synthetic demo remains available.' }, { status: 503 });
  }
  const unsafe = !['GET', 'HEAD'].includes(request.method);
  if (unsafe) {
    const origin = applicationOrigin(request);
    if (!origin) return Response.json({ detail: 'The public application origin is not configured' }, { status: 503 });
    if ((request.headers.get('origin') && request.headers.get('origin') !== origin)
        || request.headers.get('sec-fetch-site') === 'cross-site') {
      return Response.json({ detail: 'Same-origin request required' }, { status: 403 });
    }
  }
  // Session routes cannot reserve capacity or read a body without a session cookie;
  // the private API still validates the session itself.
  if (!publicMethod && !/(?:^|;)\s*session_token=[^;\s]/.test(request.headers.get('cookie') ?? '')) {
    return Response.json({ detail: 'Authentication required' }, { status: 401, headers: { 'Cache-Control': 'no-store' } });
  }
  const headers = new Headers();
  for (const key of ['cookie', 'content-type', 'x-csrf-token', 'idempotency-key', 'origin', 'sec-fetch-site']) {
    const value = request.headers.get(key); if (value) headers.set(key, value);
  }
  if (!signRateSource(request, headers)) return Response.json({detail:'Trusted ingress configuration is unavailable'}, {status:503});
  let upstream: URL;
  try {
    const base = new URL(configured);
    if (!['http:', 'https:'].includes(base.protocol) || base.username || base.password || base.search || base.hash || base.pathname !== '/') throw new Error('Invalid backend origin');
    upstream = new URL(`/v1/${route}`, base);
  }
  catch { return Response.json({ detail: 'Backend configuration is unavailable' }, { status: 503 }); }
  upstream.search = request.nextUrl.search;
  const isImport = route === 'imports' || route === 'imports/preview';
  const isControl = (request.method === 'POST' && CONTROL_ROUTES.test(route))
    || (request.method === 'PUT' && /^conversations\/[^/]+\/permissions$/.test(route))
    || (request.method === 'DELETE' && /^(?:auth\/sessions\/[^/]+|memories\/[^/]+|automation\/grants\/[^/]+|connectors\/[^/]+|account-data)$/.test(route));
  const lease = acquireProxyLease(isControl, isImport, Boolean(publicMethod));
  if (!lease) return Response.json({detail:'The web proxy is busy. This request was not sent to the backend.',reason_code:'PROXY_BUSY'},
    {status:503,headers:{'Retry-After':'1','Cache-Control':'no-store'}});
  const aborted = () => lease.release();
  request.signal.addEventListener('abort',aborted,{once:true});
  if (request.signal.aborted) aborted();
  let streaming = false;
  try {
    let body: ArrayBuffer | undefined;
    const bodyLimit = isImport ? MAX_IMPORT_BODY_BYTES : MAX_JSON_BODY_BYTES;
    try { body = unsafe ? await boundedProxyBody(request, bodyLimit, isImport ? IMPORT_UPLOAD_DEADLINE_MS : JSON_UPLOAD_DEADLINE_MS) : undefined; }
    catch (error) {
      return Response.json({ detail: error instanceof UploadTimeoutError ? 'Upload did not finish within the deadline' : error instanceof RangeError ? 'Upload is too large' : 'Invalid upload body' },
        { status: error instanceof UploadTimeoutError ? 408 : error instanceof RangeError ? 413 : 400 });
    }
    try {
      const signal = AbortSignal.any([request.signal,AbortSignal.timeout(25000)]);
      const response = await fetch(upstream, { method: request.method, headers, body, cache: 'no-store', redirect: 'manual', signal });
      const returned = new Headers({ 'Content-Type': response.headers.get('content-type') ?? 'application/json', 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff' });
      for (const cookie of response.headers.getSetCookie()) {
        returned.append('set-cookie', cookie.replace(/Path=\/(?:v1\/)?auth(?=;|$)/i, 'Path=/api/auth'));
      }
      const output = new Response(response.body ? leaseProxyBody(response.body,lease,signal) : null, { status: response.status, headers: returned });
      streaming = response.body !== null;
      return output;
    } catch {
      return Response.json({ detail: 'The server did not confirm this request. Check current state before retrying an action.' }, { status: 504 });
    }
  } finally {
    request.signal.removeEventListener('abort',aborted);
    if (!streaming) lease.release();
  }
}
export { proxy as GET, proxy as POST, proxy as PUT, proxy as PATCH, proxy as DELETE };
