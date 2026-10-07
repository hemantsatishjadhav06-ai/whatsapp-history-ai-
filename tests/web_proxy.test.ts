import assert from 'node:assert/strict';
import test from 'node:test';
import type { TestContext } from 'node:test';
import type { NextRequest } from 'next/server';
import { GET, POST } from '../apps/web/app/api/[...path]/route.ts';

function setup(t: TestContext, origin: string | null = 'https://milo.example.test') {
  const previous = { backend: process.env.BACKEND_URL, origin: process.env.PUBLIC_APP_ORIGIN, fetch: globalThis.fetch };
  process.env.BACKEND_URL = 'http://private-api.railway.internal:8000';
  if (origin === null) delete process.env.PUBLIC_APP_ORIGIN; else process.env.PUBLIC_APP_ORIGIN = origin;
  const sent: { url: string; init: RequestInit | undefined }[] = [];
  globalThis.fetch = async (input, init) => {
    sent.push({ url: String(input), init });
    return new Response('{"confirmed":true}', { status: 200, headers: { 'Content-Type': 'application/json',
      'Set-Cookie': 'synthetic_nonce=local; Path=/v1/auth; Secure; HttpOnly; SameSite=Lax' } });
  };
  t.after(() => {
    if (previous.backend === undefined) delete process.env.BACKEND_URL; else process.env.BACKEND_URL = previous.backend;
    if (previous.origin === undefined) delete process.env.PUBLIC_APP_ORIGIN; else process.env.PUBLIC_APP_ORIGIN = previous.origin;
    globalThis.fetch = previous.fetch;
  });
  return sent;
}
function request(path: string, options: RequestInit = {}, url = 'http://internal-service:3000'): NextRequest {
  const target = new URL('/api/' + path, url);
  const value = new Request(target, options);
  Object.defineProperty(value, 'nextUrl', { value: target });
  return value as NextRequest;
}
function context(path: string) { return { params: Promise.resolve({ path: path.split('/') }) }; }

test('configured public HTTPS origin survives private HTTP reverse-proxy routing', async t => {
  const sent = setup(t);
  const response = await POST(request('pause-all?workspace_id=synthetic', { method: 'POST', body: '{}', headers: {
    Origin: 'https://milo.example.test', Host: 'private-host:3000', 'X-Forwarded-Host': 'milo.example.test',
    'X-Forwarded-Proto': 'https', 'X-CSRF-Token': 'synthetic-csrf', Cookie: 'session=synthetic' } }), context('pause-all'));
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
    Origin: 'http://localhost:3100', Host: 'localhost:3100' } }, 'http://localhost:3100'), context('pause-all'));
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
    Origin: 'https://milo.example.test', 'Content-Length': String(13 * 1024 * 1024) } }), context('imports'));
  assert.equal(response.status, 413);
  assert.equal(sent.length, 0);
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
    headers: { Origin: 'https://milo.example.test' } } as RequestInit), context('imports'));
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
    headers: { Cookie: 'session=synthetic' } }), context('ui/resolve'));
  assert.equal(response.status, 200);
  assert.equal(sent[0]?.url, 'http://private-api.railway.internal:8000/v1/ui/resolve?kind=conversation&id=synthetic-source');
});
