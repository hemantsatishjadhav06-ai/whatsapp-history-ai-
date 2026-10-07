import type { NextRequest } from 'next/server';

export const dynamic = 'force-dynamic';
const ALLOWED = /^(?:auth\/(?:config|nonce|google|csrf|logout|sessions(?:\/[^/]+)?)|me|ui\/(?:bootstrap|updates|resolve)|(?:workspaces|connectors|conversations|drafts|messages|imports|inbox|tasks|memories|forward-routes|actions|jobs|schedules|scheduled-intents|contacts|people|integrations)(?:\/[A-Za-z0-9_.:@+-]+){0,3}|automation\/grants(?:\/[^/]+)?|assistant\/(?:commands|digest)|privacy\/(?:retention(?:\/sweep)?|model-processing)|pause-all|resume-all|activity|data-export|account-data)$/;
const MAX_BODY_BYTES = 12 * 1024 * 1024;

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

async function boundedBody(request: NextRequest): Promise<ArrayBuffer | undefined> {
  const declared = request.headers.get('content-length');
  if (declared && (!/^\d+$/.test(declared) || Number(declared) > MAX_BODY_BYTES)) throw new RangeError('Upload is too large');
  if (!request.body) return undefined;
  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  try {
    while (true) {
      const chunk = await reader.read();
      if (chunk.done) break;
      size += chunk.value.byteLength;
      if (size > MAX_BODY_BYTES) {
        await reader.cancel();
        throw new RangeError('Upload is too large');
      }
      chunks.push(chunk.value);
    }
  } finally { reader.releaseLock(); }
  const result = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) { result.set(chunk, offset); offset += chunk.byteLength; }
  return result.buffer;
}

async function proxy(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  const route = path.join('/');
  if (!ALLOWED.test(route) || path.some(segment => segment.includes('..') || segment.includes('\\'))) {
    return Response.json({ detail: 'This service route is not available to the browser' }, { status: 404 });
  }
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
  const headers = new Headers();
  for (const key of ['cookie', 'content-type', 'x-csrf-token', 'idempotency-key', 'origin', 'sec-fetch-site']) {
    const value = request.headers.get(key); if (value) headers.set(key, value);
  }
  let upstream: URL;
  try { upstream = new URL(`/v1/${route}`, configured); }
  catch { return Response.json({ detail: 'Backend configuration is unavailable' }, { status: 503 }); }
  upstream.search = request.nextUrl.search;
  let body: ArrayBuffer | undefined;
  try { body = unsafe ? await boundedBody(request) : undefined; }
  catch (error) {
    return Response.json({ detail: error instanceof RangeError ? 'Upload is too large' : 'Invalid upload body' },
      { status: error instanceof RangeError ? 413 : 400 });
  }
  try {
    const response = await fetch(upstream, { method: request.method, headers, body, cache: 'no-store', redirect: 'manual', signal: AbortSignal.timeout(25000) });
    const returned = new Headers({ 'Content-Type': response.headers.get('content-type') ?? 'application/json', 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff' });
    for (const cookie of response.headers.getSetCookie()) {
      returned.append('set-cookie', cookie.replace(/Path=\/(?:v1\/)?auth(?=;|$)/i, 'Path=/api/auth'));
    }
    return new Response(response.body, { status: response.status, headers: returned });
  } catch {
    return Response.json({ detail: 'The server did not confirm this request. Check current state before retrying an action.' }, { status: 504 });
  }
}
export { proxy as GET, proxy as POST, proxy as PUT, proxy as PATCH, proxy as DELETE };
