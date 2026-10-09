import assert from 'node:assert/strict';
import test, { type TestContext } from 'node:test';
import type { NextRequest } from 'next/server';
import { createHmac } from 'node:crypto';
import { nativeProxy, whatsappWebhook, nativeGoogleCallback } from '../apps/web/lib/public-ingress.ts';
import { acquireProxyLease } from '../apps/web/lib/proxy-admission.ts';

const token = 'na_synthetic_native_token_for_boundary_test';
const signature = 'sha256=' + 'a'.repeat(64);
function setup(t: TestContext) {
  const keys = ['BACKEND_URL', 'TRUST_PROXY_HOPS', 'BACKEND_PROXY_KEY', 'GOOGLE_NATIVE_APP_REDIRECT_URI'] as const;
  const prior = keys.map(key => [key, process.env[key]] as const);
  const originalFetch = globalThis.fetch;
  process.env.BACKEND_URL = 'http://private-api.railway.internal:8000';
  for (const key of keys.slice(1)) delete process.env[key];
  const sent: { url: string; init?: RequestInit }[] = [];
  const controllers: AbortController[] = [];
  globalThis.fetch = async (url, init) => {
    sent.push({ url: String(url), init });
    return new Response('{"ok":true}', { headers: { 'Content-Type': 'application/json', 'Set-Cookie': 'session=unexpected', 'Access-Control-Allow-Origin': '*' } });
  };
  const request = (path: string, init: RequestInit = {}) => {
    const controller = new AbortController(); controllers.push(controller);
    const url = new URL(path, 'https://milo.example.test');
    const value = new Request(url, { ...init, signal: controller.signal });
    Object.defineProperty(value, 'nextUrl', { value: url });
    return value as NextRequest;
  };
  t.after(() => {
    controllers.forEach(controller => controller.abort()); globalThis.fetch = originalFetch;
    for (const [key, value] of prior) { if (value === undefined) delete process.env[key]; else process.env[key] = value; }
  });
  return { sent, request };
}
const context = (path: string) => ({ params: Promise.resolve({ path: path.split('/') }) });

test('native phone linking routes remain bearer-only and restrict pairing versus mutation methods', async t => {
  const { sent, request } = setup(t); const headers = { Authorization: `Bearer ${token}` };
  for (const suffix of ['config', 'status', 'pairing', 'chats']) {
    const path = `integrations/whatsapp/personal/${suffix}`;
    const result = await nativeProxy(request('/native-api/v1/' + path, { headers }), context('v1/' + path));
    assert.equal(result.status, 200); await result.text();
    assert.equal(result.headers.get('cache-control'), 'no-store');
    assert.equal((await nativeProxy(request('/native-api/v1/' + path, { method: 'POST', body: '{}', headers }), context('v1/' + path))).status, 405);
  }
  for (const suffix of ['start', 'chats/authorize', 'authorship/confirm', 'disconnect']) {
    const path = `integrations/whatsapp/personal/${suffix}`;
    const result = await nativeProxy(request('/native-api/v1/' + path, { method: 'POST', body: '{}', headers }), context('v1/' + path));
    assert.equal(result.status, 200); await result.text();
    assert.equal((await nativeProxy(request('/native-api/v1/' + path, { headers }), context('v1/' + path))).status, 405);
  }
  const path = 'conversations/synthetic-chat/automatic-drafts';
  const result = await nativeProxy(request('/native-api/v1/' + path, { method: 'PUT', body: '{"enabled":false,"expected_version":1}', headers }), context('v1/' + path));
  assert.equal(result.status, 200); await result.text();
  for (const outgoing of sent) { const value = new Headers(outgoing.init?.headers); assert.equal(value.get('authorization'), headers.Authorization); assert.equal(value.has('cookie'), false); }
});

test('native ingress forwards only native bearer authority with exact private route and no response cookies/CORS', async t => {
  const { sent, request } = setup(t);
  const response = await nativeProxy(request('/native-api/v1/me?owner=current', { headers: { Authorization: `Bearer ${token}`, 'X-Internal-Token': 'forged', 'X-CSRF-Token': 'forged', 'X-Milo-Rate-Source': 'forged' } }), context('v1/me'));
  assert.equal(response.status, 200); await response.text();
  assert.equal(sent[0]?.url, 'http://private-api.railway.internal:8000/v1/me?owner=current');
  const headers = new Headers(sent[0]?.init?.headers);
  assert.equal(headers.get('authorization'), `Bearer ${token}`);
  for (const name of ['cookie', 'x-internal-token', 'x-csrf-token', 'x-milo-rate-source']) assert.equal(headers.has(name), false);
  assert.equal(sent[0]?.init?.credentials, 'omit'); assert.equal(sent[0]?.init?.redirect, 'manual');
  assert.equal(response.headers.has('set-cookie'), false); assert.equal(response.headers.has('access-control-allow-origin'), false);
});

test('native ingress rejects browser session mixing, invalid bearer tokens and internal/encoded paths before forwarding', async t => {
  const { sent, request } = setup(t);
  for (const extra of [{ Origin: 'https://milo.example.test' }, { Cookie: 'session=browser' }, { 'Sec-Fetch-Site': 'same-origin' }]) {
    assert.equal((await nativeProxy(request('/native-api/me', { headers: { Authorization: `Bearer ${token}`, ...extra } }), context('me'))).status, 403);
  }
  for (const authorization of ['', 'Bearer browser_secret', 'Bearer na_short', 'Basic owner']) {
    assert.equal((await nativeProxy(request('/native-api/me', { headers: { Authorization: authorization } }), context('me'))).status, 401);
  }
  for (const path of ['internal/connector-events', 'auth/dev', 'auth/google', 'auth/native/google/callback', 'v1/v1/me', '%2e%2e/me', 'conversations/chat%2fsecret/messages', 'conversations/../messages']) {
    assert.equal((await nativeProxy(request('/native-api/me', { headers: { Authorization: `Bearer ${token}` } }), context(path))).status, 404);
  }
  assert.equal(sent.length, 0);
});

test('native bootstrap/login/refresh/revocation endpoints are public only with their expected method', async t => {
  const { sent, request } = setup(t);
  const config = await nativeProxy(request('/native-api/auth/config'), context('auth/config')); assert.equal(config.status, 200); await config.text();
  for (const path of ['auth/native/google/start', 'auth/native/google/exchange', 'auth/native/refresh', 'auth/native/revoke']) {
    const result = await nativeProxy(request('/native-api/' + path, { method: 'POST', body: '{}', headers: { 'Content-Type': 'application/json' } }), context(path));
    assert.equal(result.status, 200); await result.text();
    assert.equal((await nativeProxy(request('/native-api/' + path), context(path))).status, 405);
  }
  assert.equal(sent.length, 5);
});

test('native controls retain reserved admission under ordinary saturation', async t => {
  const { request } = setup(t);
  const leases = Array.from({ length: 28 }, () => acquireProxyLease(false, false)!);
  t.after(() => leases.forEach(lease => lease.release()));
  assert.equal((await nativeProxy(request('/native-api/me', { headers: { Authorization: `Bearer ${token}` } }), context('me'))).status, 503);
  for (const [path, method] of [['conversations/chat/takeover', 'POST'], ['conversations/chat/resume', 'POST'], ['conversations/chat/permissions', 'PUT'], ['auth/sessions/session_id', 'DELETE'], ['auth/native/revoke', 'POST']]) {
    const result = await nativeProxy(request('/native-api/' + path, { method, body: '{}', headers: { Authorization: `Bearer ${token}` } }), context(path!));
    assert.equal(result.status, 200); await result.text();
  }
});

test('bearer-less native session routes are refused before any body read or lease', async t => {
  const { sent, request } = setup(t); let reads = 0;
  for (let attempt = 0; attempt < 40; attempt++) {
    const body = new ReadableStream<Uint8Array>({ pull() { reads++; } }, { highWaterMark: 0 });
    const path = attempt % 2 ? 'assistant/commands' : 'imports';
    assert.equal((await nativeProxy(request('/native-api/' + path, { method: 'POST', body, duplex: 'half' } as RequestInit), context(path))).status, 401);
  }
  assert.equal(reads, 0); assert.equal(sent.length, 0);
  const leases = [...Array.from({ length: 28 }, () => acquireProxyLease(false, false)), ...Array.from({ length: 4 }, () => acquireProxyLease(true, false)),
    ...Array.from({ length: 6 }, () => acquireProxyLease(false, false, true))];
  try { assert.ok(leases.every(Boolean)); } finally { leases.forEach(lease => lease?.release()); }
});

test('native sign-in, provider webhook and callback share a public pool that cannot block bearer sessions', async t => {
  const { sent, request } = setup(t);
  const held = Array.from({ length: 6 }, () => acquireProxyLease(false, false, true)!);
  t.after(() => held.forEach(lease => lease.release()));
  assert.equal((await nativeProxy(request('/native-api/auth/native/refresh', { method: 'POST', body: '{}' }), context('auth/native/refresh'))).status, 503);
  assert.equal((await nativeProxy(request('/native-api/auth/config'), context('auth/config'))).status, 503);
  assert.equal((await whatsappWebhook(request('/api/webhooks/whatsapp', { method: 'POST', body: '{}', headers: { 'Content-Type': 'application/json', 'X-Hub-Signature-256': signature } }))).status, 503);
  assert.equal((await nativeGoogleCallback(request('/api/auth/native/google/callback?state=state&code=code'))).status, 503);
  assert.equal(sent.length, 0);
  for (const [path, method] of [['me', 'GET'], ['pause-all', 'POST']] as const) {
    const result = await nativeProxy(request('/native-api/' + path, { method, ...(method === 'POST' ? { body: '{}' } : {}), headers: { Authorization: `Bearer ${token}` } }), context(path));
    assert.equal(result.status, 200); await result.text();
  }
  assert.equal(sent.length, 2);
});

test('public native and webhook uploads expire after five seconds; bearer imports keep ten', async t => {
  const { sent, request } = setup(t); t.mock.timers.enable({ apis: ['setTimeout'] });
  const flush = () => new Promise<void>(resolve => setImmediate(resolve));
  const stalled = () => new ReadableStream<Uint8Array>({ pull() {} });
  const login = nativeProxy(request('/native-api/auth/native/login', { method: 'POST', body: stalled(), duplex: 'half' } as RequestInit), context('auth/native/login'));
  const webhook = whatsappWebhook(request('/api/webhooks/whatsapp', { method: 'POST', body: stalled(), duplex: 'half', headers: { 'Content-Type': 'application/json', 'X-Hub-Signature-256': signature } } as RequestInit));
  let imported = false;
  const history = nativeProxy(request('/native-api/imports', { method: 'POST', body: stalled(), duplex: 'half', headers: { Authorization: `Bearer ${token}` } } as RequestInit), context('imports'));
  void history.finally(() => { imported = true; });
  await flush(); t.mock.timers.tick(4_999); await flush();
  t.mock.timers.tick(1);
  assert.deepEqual([(await login).status, (await webhook).status], [408, 408]);
  await flush(); assert.equal(imported, false);
  t.mock.timers.tick(5_000); assert.equal((await history).status, 408); assert.equal(sent.length, 0);
});

test('WhatsApp ingress preserves signed raw bytes and strips all owner/service credentials', async t => {
  const { sent, request } = setup(t);
  const body = '{ "object": "whatsapp_business_account", "text": "hello 🌍" }\n';
  const result = await whatsappWebhook(request('/api/webhooks/whatsapp?owner=forged', { method: 'POST', body, headers: {
    'Content-Type': 'application/json; charset=utf-8', 'X-Hub-Signature-256': signature, Cookie: 'session=owner', Authorization: `Bearer ${token}`, 'X-Internal-Token': 'forged' } }));
  assert.equal(result.status, 200); await result.text();
  assert.equal(sent[0]?.url, 'http://private-api.railway.internal:8000/v1/webhooks/whatsapp');
  assert.equal(new TextDecoder().decode(sent[0]?.init?.body as ArrayBuffer), body);
  const headers = new Headers(sent[0]?.init?.headers); assert.equal(headers.get('x-hub-signature-256'), signature);
  for (const key of ['authorization', 'cookie', 'x-internal-token']) assert.equal(headers.has(key), false);
});

test('provider verification is bounded, duplicate query/signature and oversized bodies never reach backend', async t => {
  const { sent, request } = setup(t);
  globalThis.fetch = async (url, init) => { sent.push({ url: String(url), init }); return new Response('123456', { headers: { 'Content-Type': 'text/plain' } }); };
  const verification = await whatsappWebhook(request('/api/webhooks/whatsapp?hub.mode=subscribe&hub.challenge=123456&hub.verify_token=synthetic-verification&workspace_id=forged'));
  assert.equal(await verification.text(), '123456');
  assert.ok(sent[0]?.url.includes('hub.verify_token=synthetic-verification')); assert.equal(sent[0]?.url.includes('workspace'), false);
  assert.equal((await whatsappWebhook(request('/api/webhooks/whatsapp?hub.verify_token=a&hub.verify_token=b'))).status, 400);
  for (const value of ['', 'sha256=short']) {
    assert.equal((await whatsappWebhook(request('/api/webhooks/whatsapp', { method: 'POST', body: '{}', headers: { 'Content-Type': 'application/json', 'X-Hub-Signature-256': value } }))).status, 401);
  }
  assert.equal((await whatsappWebhook(request('/api/webhooks/whatsapp', { method: 'POST', body: '{}', headers: { 'Content-Type': 'application/json', 'X-Hub-Signature-256': signature, 'Content-Length': String(3 * 1024 * 1024) } }))).status, 413);
  assert.equal(sent.length, 1);
});

test('Google callback permits only configured opaque app handoff; no callback cookies or arbitrary redirect', async t => {
  const { sent, request } = setup(t); const handoff = 'h'.repeat(64);
  globalThis.fetch = async (url, init) => { sent.push({ url: String(url), init }); return new Response(null, { status: 303, headers: { Location: `milo://oauth?handoff=${handoff}`, 'Set-Cookie': 'session=unexpected' } }); };
  const result = await nativeGoogleCallback(request('/api/auth/native/google/callback?state=synthetic-state&code=single-use-code&next=https://attacker.invalid', { headers: { Cookie: 'session=browser', Authorization: 'Bearer forged' } }));
  assert.equal(result.status, 303); assert.equal(result.headers.get('location'), `milo://oauth?handoff=${handoff}`);
  assert.equal(result.headers.has('set-cookie'), false); assert.equal(result.headers.get('referrer-policy'), 'no-referrer');
  assert.equal(sent[0]?.url.includes('next='), false); assert.equal(new Headers(sent[0]?.init?.headers).has('cookie'), false);
  for (const location of [`https://attacker.invalid?handoff=${handoff}`, `milo://other?handoff=${handoff}`, `milo://oauth?access_token=${handoff}`, `milo://oauth?handoff=${handoff}&owner=secret`]) {
    globalThis.fetch = async () => new Response(null, { status: 303, headers: { Location: location } });
    assert.equal((await nativeGoogleCallback(request('/api/auth/native/google/callback?state=state&code=code'))).status, 502);
  }
  assert.equal((await nativeGoogleCallback(request('/api/auth/native/google/callback?state=a&state=b'))).status, 400);
});

test('only explicitly configured trusted hop is signed and misconfiguration fails closed', async t => {
  const { sent, request } = setup(t); const key = 'synthetic-rate-signing-key-with-at-least-32-bytes';
  process.env.TRUST_PROXY_HOPS = '1';
  assert.equal((await nativeProxy(request('/native-api/auth/config'), context('auth/config'))).status, 503);
  process.env.BACKEND_PROXY_KEY = key;
  const result = await nativeProxy(request('/native-api/auth/config', { headers: { 'X-Forwarded-For': 'forged-prefix, 203.0.113.8', 'X-Milo-Rate-Signature': 'forged' } }), context('auth/config'));
  assert.equal(result.status, 200); await result.text();
  const headers = new Headers(sent[0]?.init?.headers); const timestamp = headers.get('x-milo-rate-timestamp');
  assert.equal(headers.get('x-milo-rate-source'), '203.0.113.8');
  assert.equal(headers.get('x-milo-rate-signature'), createHmac('sha256', key).update(`${timestamp}.203.0.113.8`).digest('hex'));
  assert.equal(headers.has('x-forwarded-for'), false);
});

test('native upstream redirects and backend origin injection fail closed', async t => {
  const { sent, request } = setup(t);
  for (const value of ['https://user:password@private.invalid', 'file:///data', 'https://private.invalid/v1', 'https://private.invalid?path=internal']) {
    process.env.BACKEND_URL = value; assert.equal((await nativeProxy(request('/native-api/auth/config'), context('auth/config'))).status, 503);
  }
  assert.equal(sent.length, 0);
  process.env.BACKEND_URL = 'http://private-api.railway.internal:8000'; globalThis.fetch = async () => new Response(null, { status: 302, headers: { Location: 'https://attacker.invalid' } });
  assert.equal((await nativeProxy(request('/native-api/auth/config'), context('auth/config'))).status, 502);
});
