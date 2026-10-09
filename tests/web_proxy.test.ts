import assert from 'node:assert/strict';
import test from 'node:test';
import type { TestContext } from 'node:test';
import type { NextRequest } from 'next/server';
import { createHmac } from 'node:crypto';
import { GET, POST, PUT } from '../apps/web/app/api/[...path]/route.ts';
import { boundedProxyBody, UploadTimeoutError } from '../apps/web/lib/proxy-body.ts';
import { acquireProxyLease } from '../apps/web/lib/proxy-admission.ts';
const controllers = new Set<AbortController>();

function setup(t: TestContext, origin: string | null = 'https://milo.example.test') {
  const previous = { backend: process.env.BACKEND_URL, origin: process.env.PUBLIC_APP_ORIGIN, proxyKey:process.env.BACKEND_PROXY_KEY, hops:process.env.TRUST_PROXY_HOPS, header:process.env.TRUST_PROXY_HEADER, fetch: globalThis.fetch };
  process.env.BACKEND_URL = 'http://private-api.railway.internal:8000';
  if (origin === null) delete process.env.PUBLIC_APP_ORIGIN; else process.env.PUBLIC_APP_ORIGIN = origin;
  delete process.env.BACKEND_PROXY_KEY; delete process.env.TRUST_PROXY_HOPS; delete process.env.TRUST_PROXY_HEADER;
  const sent: { url: string; init: RequestInit | undefined }[] = [];
  globalThis.fetch = async (input, init) => {
    sent.push({ url: String(input), init });
    return new Response('{"confirmed":true}', { status: 200, headers: { 'Content-Type': 'application/json',
      'Set-Cookie': 'synthetic_nonce=local; Path=/v1/auth; Secure; HttpOnly; SameSite=Lax' } });
  };
  t.after(() => {
    for (const controller of controllers) controller.abort();
    controllers.clear();
    if (previous.backend === undefined) delete process.env.BACKEND_URL; else process.env.BACKEND_URL = previous.backend;
    if (previous.origin === undefined) delete process.env.PUBLIC_APP_ORIGIN; else process.env.PUBLIC_APP_ORIGIN = previous.origin;
    if (previous.proxyKey === undefined) delete process.env.BACKEND_PROXY_KEY; else process.env.BACKEND_PROXY_KEY = previous.proxyKey;
    if (previous.hops === undefined) delete process.env.TRUST_PROXY_HOPS; else process.env.TRUST_PROXY_HOPS = previous.hops;
    if (previous.header === undefined) delete process.env.TRUST_PROXY_HEADER; else process.env.TRUST_PROXY_HEADER = previous.header;
    globalThis.fetch = previous.fetch;
  });
  return sent;
}
function request(path: string, options: RequestInit = {}, url = 'http://internal-service:3000'): NextRequest {
  const target = new URL('/api/' + path, url);
  const controller = new AbortController();
  if (!options.signal) controllers.add(controller);
  const value = new Request(target, {...options,signal:options.signal || controller.signal});
  Object.defineProperty(value, 'nextUrl', { value: target });
  return value as NextRequest;
}
function context(path: string) { return { params: Promise.resolve({ path: path.split('/') }) }; }
const owner = 'session_token=synthetic-owner';

test('phone linking routes preserve owner cookies, no-store and expected public methods', async t => {
  const sent = setup(t);
  for (const suffix of ['config', 'status', 'pairing', 'chats']) {
    const path = `integrations/whatsapp/personal/${suffix}`;
    const result = await GET(request(path, { headers: { Cookie: owner } }), context(path));
    assert.equal(result.status, 200); await result.text();
    assert.equal(result.headers.get('cache-control'), 'no-store');
    assert.equal(new Headers(sent.at(-1)?.init?.headers).get('cookie'), owner);
    assert.equal((await POST(request(path, { method: 'POST', body: '{}' }), context(path))).status, 405);
  }
  for (const suffix of ['start', 'chats/authorize', 'authorship/confirm', 'disconnect']) {
    const path = `integrations/whatsapp/personal/${suffix}`;
    const result = await POST(request(path, { method: 'POST', body: '{}', headers: { Origin: 'https://milo.example.test', 'X-CSRF-Token': 'synthetic-csrf', Cookie: owner } }), context(path));
    assert.equal(result.status, 200); await result.text();
    assert.equal((await GET(request(path), context(path))).status, 405);
  }
  const path = 'conversations/synthetic-chat/automatic-drafts';
  const result = await PUT(request(path, { method: 'PUT', body: '{"enabled":false,"expected_version":1}', headers: { Origin: 'https://milo.example.test', Cookie: owner } }), context(path));
  assert.equal(result.status, 200); await result.text();
  assert.equal(sent.at(-1)?.url, 'http://private-api.railway.internal:8000/v1/' + path);
});

test('configured public HTTPS origin survives private HTTP reverse-proxy routing', async t => {
  const sent = setup(t);
  const response = await POST(request('pause-all?workspace_id=synthetic', { method: 'POST', body: '{}', headers: {
    Origin: 'https://milo.example.test', Host: 'private-host:3000', 'X-Forwarded-Host': 'milo.example.test',
    'X-Forwarded-Proto': 'https', 'X-CSRF-Token': 'synthetic-csrf', Cookie: owner } }), context('pause-all'));
  assert.equal(response.status, 200);
  assert.equal(sent.length, 1);
  assert.equal(sent[0]?.url, 'http://private-api.railway.internal:8000/v1/pause-all?workspace_id=synthetic');
  assert.equal(new Headers(sent[0]?.init?.headers).get('x-csrf-token'), 'synthetic-csrf');
  assert.equal(new Headers(sent[0]?.init?.headers).has('x-forwarded-host'), false);
});

test('cross-site Origin and fetch metadata cannot change configured authority', async t => {
  const sent = setup(t);
  for (const headers of [ { Origin: 'https://attacker.example.test', 'X-Forwarded-Host': 'attacker.example.test' },
    { Origin: 'https://milo.example.test', 'Sec-Fetch-Site': 'cross-site' } ]) {
    assert.equal((await POST(request('pause-all', { method: 'POST', body: '{}', headers }), context('pause-all'))).status, 403);
  }
  assert.equal(sent.length, 0);
});

test('public writes need configured origin; forwarded headers cannot supply it', async t => {
  const sent = setup(t, null);
  const response = await POST(request('pause-all', { method: 'POST', body: '{}', headers: {
    Origin: 'https://milo.example.test', Host: 'private-host:3000', 'X-Forwarded-Host': 'milo.example.test',
    'X-Forwarded-Proto': 'https' } }), context('pause-all'));
  assert.equal(response.status, 503);
  assert.equal(sent.length, 0);
});

test('explicit loopback preview remains usable without public deployment variables', async t => {
  const sent = setup(t, null);
  const response = await POST(request('pause-all', { method: 'POST', body: '{}', headers: {
    Origin: 'http://localhost:3100', Host: 'localhost:3100', Cookie: owner } }, 'http://localhost:3100'), context('pause-all'));
  assert.equal(response.status, 200);
  assert.equal(sent.length, 1);
});

test('configured origins reject plaintext public hosts, userinfo and query substitutions', async t => {
  const sent = setup(t);
  for (const origin of ['http://public.example.test', 'https://secret@public.example.test',
    'https://public.example.test?route=owner', 'https://public.example.test/custom-path']) {
    process.env.PUBLIC_APP_ORIGIN = origin;
    assert.equal((await POST(request('pause-all', { method: 'POST', body: '{}' }), context('pause-all'))).status, 503);
  }
  assert.equal(sent.length, 0);
});

test('declared oversized uploads are rejected before backend calls', async t => {
  const sent = setup(t);
  const response = await POST(request('imports', { method: 'POST', body: '{}', headers: {
    Origin: 'https://milo.example.test', 'Content-Length': String(13 * 1024 * 1024), Cookie: owner } }), context('imports'));
  assert.equal(response.status, 413);
  assert.equal(sent.length, 0);
});

test('ordinary JSON routes cannot allocate the larger history-import buffer', async t => {
  const sent = setup(t);
  const headers = { Origin:'https://milo.example.test', 'Content-Length':String(64 * 1024 + 1), Cookie:owner };
  assert.equal((await POST(request('auth/google',{method:'POST',body:'{}',headers}),context('auth/google'))).status,413);
  assert.equal(sent.length,0);
  for (const path of ['imports','imports/preview']) {
    assert.equal((await POST(request(path,{method:'POST',body:'{}',headers}),context(path))).status,200);
  }
  assert.equal(sent.length,2);
});

test('unqualified streaming uploads cancel at the byte cap without full buffering or forwarding', async t => {
  const sent = setup(t);
  let chunks = 0;
  let canceled = false;
  const body = new ReadableStream<Uint8Array>({ pull(controller) {
    chunks++;
    controller.enqueue(new Uint8Array(1024 * 1024));
    if (chunks === 20) controller.close();
  }, cancel() { canceled = true; } });
  const response = await POST(request('imports', { method: 'POST', body, duplex: 'half',
    headers: { Origin: 'https://milo.example.test', Cookie: owner } } as RequestInit), context('imports'));
  assert.equal(response.status, 413);
  assert.equal(canceled, true);
  assert.ok(chunks <= 14);
  assert.equal(sent.length, 0);
});

test('internal and development login remain inaccessible to browser proxy', async t => {
  const sent = setup(t);
  for (const path of ['internal/connector-events', 'auth/dev', '../internal/dispatch-authority']) {
    assert.equal((await POST(request('pause-all', { method: 'POST' }), context(path))).status, 404);
  }
  assert.equal(sent.length, 0);
});

test('session routes without a session cookie are refused before any body read, lease or backend call', async t => {
  const sent = setup(t); let reads = 0;
  for (let attempt = 0; attempt < 40; attempt++) {
    const path = ['assistant/commands', 'imports', 'auth/logout', 'pause-all'][attempt % 4]!;
    const body = new ReadableStream<Uint8Array>({ pull() { reads++; } }, { highWaterMark: 0 });
    const response = await POST(request(path, { method: 'POST', body, duplex: 'half', headers: { Origin: 'https://milo.example.test',
      'Content-Length': '65536', Cookie: attempt % 2 ? 'session=legacy; xsession_token=forged; session_token=' : '' } } as RequestInit), context(path));
    assert.equal(response.status, 401); assert.deepEqual(await response.json(), { detail: 'Authentication required' });
  }
  for (const path of ['me', 'ui/bootstrap', 'auth/csrf', 'auth/sessions']) assert.equal((await GET(request(path), context(path))).status, 401);
  assert.equal(reads, 0); assert.equal(sent.length, 0);
  const leases = [...Array.from({ length: 28 }, () => acquireProxyLease(false, false)), ...Array.from({ length: 4 }, () => acquireProxyLease(true, false))];
  try { assert.ok(leases.every(Boolean)); } finally { leases.forEach(lease => lease?.release()); }
});

test('owner access-code sign-in is a public same-origin POST forwarded without a session cookie', async t => {
  const sent = setup(t);
  const response = await POST(request('auth/access-code', { method: 'POST', body: '{"code":"synthetic"}', headers: {
    Origin: 'https://milo.example.test', 'Content-Type': 'application/json' } }), context('auth/access-code'));
  assert.equal(response.status, 200); await response.text();
  assert.equal(sent.length, 1); assert.equal(sent[0]?.url, 'http://private-api.railway.internal:8000/v1/auth/access-code');
  assert.equal(new TextDecoder().decode(sent[0]?.init?.body as ArrayBuffer), '{"code":"synthetic"}');
  assert.equal(new Headers(sent[0]?.init?.headers).has('cookie'), false);
  assert.equal(response.headers.getSetCookie()[0], 'synthetic_nonce=local; Path=/api/auth; Secure; HttpOnly; SameSite=Lax');
  assert.equal((await POST(request('auth/access-code', { method: 'POST', body: '{}', headers: { Origin: 'https://attacker.example.test' } }), context('auth/access-code'))).status, 403);
  for (const [path, method] of [['auth/access-code', 'GET'], ['auth/google', 'GET'], ['auth/nonce', 'POST'], ['auth/config', 'POST']] as const) {
    const handler = method === 'GET' ? GET : POST;
    assert.equal((await handler(request(path, { method, ...(method === 'POST' ? { body: '{}' } : {}), headers: { Origin: 'https://milo.example.test' } }), context(path))).status, 405);
  }
  assert.equal(sent.length, 1);
});

test('small JSON bodies expire after five seconds; only session-bearing imports keep ten', async t => {
  const sent = setup(t);
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const flush = () => new Promise<void>(resolve => setImmediate(resolve));
  const stalled = (path: string, cookie?: string) => {
    let settled = false;
    const result = POST(request(path, { method: 'POST', body: new ReadableStream<Uint8Array>({ pull() {} }), duplex: 'half',
      headers: { Origin: 'https://milo.example.test', ...(cookie ? { Cookie: cookie } : {}) } } as RequestInit), context(path));
    void result.finally(() => { settled = true; });
    return { result, settled: () => settled };
  };
  const login = stalled('auth/google'); const command = stalled('assistant/commands', owner); const history = stalled('imports', owner);
  await flush(); t.mock.timers.tick(4_999); await flush();
  assert.deepEqual([login.settled(), command.settled(), history.settled()], [false, false, false]);
  t.mock.timers.tick(1);
  assert.equal((await login.result).status, 408); assert.equal((await command.result).status, 408);
  await flush(); assert.equal(history.settled(), false);
  t.mock.timers.tick(5_000);
  assert.equal((await history.result).status, 408); assert.equal(sent.length, 0);
});

test('nonce cookies keep secure and HttpOnly flags while their path becomes browser-visible', async t => {
  const sent = setup(t);
  const response = await GET(request('auth/nonce'), context('auth/nonce'));
  assert.equal(response.status, 200);
  assert.equal(sent.length, 1);
  assert.equal(response.headers.getSetCookie()[0], 'synthetic_nonce=local; Path=/api/auth; Secure; HttpOnly; SameSite=Lax');
});

test('authenticated object lookup stays in public backend route with exact query refs', async t => {
  const sent = setup(t);
  const response = await GET(request('ui/resolve?kind=conversation&id=synthetic-source', {
    headers: { Cookie: owner } }), context('ui/resolve'));
  assert.equal(response.status, 200);
  assert.equal(sent[0]?.url, 'http://private-api.railway.internal:8000/v1/ui/resolve?kind=conversation&id=synthetic-source');
});

test('encoded separators and URL delimiters cannot normalize a different authorized route', async t => {
  const sent = setup(t);
  const suspicious = ['%2e%2e', '%252e%252e', 'session%2F..%2Fnonce', 'session?x=nonce', 'session#fragment', 'session/nonce', 'session\\nonce'];
  for (const value of suspicious) {
    const route = {params:Promise.resolve({path:['auth','sessions',value]})};
    assert.equal((await GET(request('auth/sessions/opaque'),route)).status,404);
  }
  assert.equal(sent.length,0);
});

test('backend origin credentials, protocols and embedded URL state fail closed without forwarding cookies', async t => {
  const sent = setup(t);
  for (const value of ['https://user:secret@api.example.test', 'file:///private', 'https://api.example.test?next=other', 'https://api.example.test/#secret', 'https://api.example.test/v1']) {
    process.env.BACKEND_URL = value;
    assert.equal((await GET(request('me',{headers:{Cookie:owner}}),context('me'))).status,503);
  }
  assert.equal(sent.length,0);
});

test('proxy forwards only explicit browser-session headers, never supplied backend credentials', async t => {
  const sent = setup(t);
  await GET(request('me',{headers:{Cookie:owner,Authorization:'Bearer attacker-controlled','X-Internal-Token':'forged','X-Connector-Token':'forged'}}),context('me'));
  const headers = new Headers(sent[0]?.init?.headers);
  assert.equal(headers.get('cookie'),owner);
  for (const name of ['authorization','x-internal-token','x-connector-token']) assert.equal(headers.has(name),false);
});

test('a stalled upload expires even when its stream cancellation never finishes', async () => {
  let canceled = false;
  const body = new ReadableStream<Uint8Array>({pull() {},cancel() { canceled = true; return new Promise<void>(() => {}); }});
  const upload = new Request('https://milo.example.test/api/imports',{method:'POST',body,duplex:'half'} as RequestInit);
  await assert.rejects(boundedProxyBody(upload,1024,20),error => error instanceof UploadTimeoutError);
  assert.equal(canceled,true);
});

test('a canceled client upload releases the body reader without an upstream request', async () => {
  const controller = new AbortController(); let canceled = false;
  const body = new ReadableStream<Uint8Array>({pull() {},cancel() {canceled = true;}});
  const upload = new Request('https://milo.example.test/api/imports',{method:'POST',body,duplex:'half',signal:controller.signal} as RequestInit);
  const result = boundedProxyBody(upload,1024,1000); controller.abort();
  await assert.rejects(result,/Upload was canceled/); assert.equal(canceled,true);
});

test('default ingress policy ignores forged forwarding and signed-rate headers', async t => {
  const sent = setup(t);
  await GET(request('me',{headers:{Cookie:owner,'X-Forwarded-For':'198.51.100.8','X-Milo-Rate-Source':'198.51.100.8','X-Milo-Rate-Timestamp':'123','X-Milo-Rate-Signature':'forged'}}),context('me'));
  assert.equal(sent.length,1); const headers = new Headers(sent[0]?.init?.headers);
  for (const name of ['x-forwarded-for','x-milo-rate-source','x-milo-rate-timestamp','x-milo-rate-signature']) assert.equal(headers.has(name),false);
});

test('explicit trusted-hop policy signs only its configured XFF suffix with the dedicated server key', async t => {
  const sent = setup(t); const key = 'synthetic-proxy-key-32-bytes-minimum-test';
  process.env.BACKEND_PROXY_KEY = key; process.env.TRUST_PROXY_HOPS = '1';
  await GET(request('me',{headers:{Cookie:owner,'X-Forwarded-For':'attacker-prefix, 203.0.113.20','X-Milo-Rate-Source':'192.0.2.99','X-Milo-Rate-Signature':'forged'}}),context('me'));
  const headers = new Headers(sent[0]?.init?.headers); const timestamp = headers.get('x-milo-rate-timestamp');
  assert.equal(headers.get('x-milo-rate-source'),'203.0.113.20'); assert.ok(timestamp && /^\d+$/.test(timestamp));
  assert.equal(headers.get('x-milo-rate-signature'),createHmac('sha256',key).update(`${timestamp}.203.0.113.20`).digest('hex'));
  assert.equal(headers.has('x-forwarded-for'),false); assert.equal(headers.has('backend-proxy-key'),false);
});

test('edge-set X-Real-IP policy signs that address and ignores forged forwarding chains', async t => {
  const sent = setup(t); const key = 'synthetic-proxy-key-32-bytes-minimum-test';
  process.env.TRUST_PROXY_HEADER = 'x-real-ip';
  assert.equal((await GET(request('me',{headers:{Cookie:owner,'X-Real-IP':'203.0.113.30'}}),context('me'))).status,503);
  process.env.BACKEND_PROXY_KEY = key;
  await GET(request('me',{headers:{Cookie:owner,'X-Real-IP':'203.0.113.30','X-Forwarded-For':'198.51.100.8, 192.0.2.1'}}),context('me'));
  const headers = new Headers(sent.at(-1)?.init?.headers); const timestamp = headers.get('x-milo-rate-timestamp');
  assert.equal(headers.get('x-milo-rate-source'),'203.0.113.30');
  assert.equal(headers.get('x-milo-rate-signature'),createHmac('sha256',key).update(`${timestamp}.203.0.113.30`).digest('hex'));
  for (const value of ['not-an-ip','203.0.113.30, 198.51.100.8']) {
    await GET(request('me',{headers:{Cookie:owner,'X-Real-IP':value}}),context('me'));
    assert.equal(new Headers(sent.at(-1)?.init?.headers).has('x-milo-rate-source'),false);
  }
  process.env.TRUST_PROXY_HEADER = 'x-forwarded-for';
  assert.equal((await GET(request('me',{headers:{Cookie:owner}}),context('me'))).status,503);
});

test('misconfigured ingress trust fails closed and absent or malformed trusted IPs stay unsigned', async t => {
  const sent = setup(t); process.env.TRUST_PROXY_HOPS = '1';
  assert.equal((await GET(request('me',{headers:{Cookie:owner}}),context('me'))).status,503);
  process.env.BACKEND_PROXY_KEY = 'synthetic-proxy-key-32-bytes-minimum-test';
  for (const value of ['not-an-ip','203.0.113.20:8080','fe80::1%eth0']) {
    await GET(request('me',{headers:{Cookie:owner,'X-Forwarded-For':value}}),context('me'));
    assert.equal(new Headers(sent.at(-1)?.init?.headers).has('x-milo-rate-source'),false);
  }
  process.env.TRUST_PROXY_HOPS = '2';
  await GET(request('me',{headers:{Cookie:owner,'X-Forwarded-For':'spoof, 198.51.100.7, 203.0.113.20'}}),context('me'));
  assert.equal(new Headers(sent.at(-1)?.init?.headers).get('x-milo-rate-source'),'198.51.100.7');
  process.env.TRUST_PROXY_HOPS = '-1'; assert.equal((await GET(request('me',{headers:{Cookie:owner}}),context('me'))).status,503);
});
