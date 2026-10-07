import assert from 'node:assert/strict';
import test from 'node:test';
import type { TestContext } from 'node:test';
import { GET } from '../apps/web/app/readyz/route.ts';

const COMMIT = 'a'.repeat(40);
let originSequence = 0;

function setup(t: TestContext) {
  const previous = {backend:process.env.BACKEND_URL, sha:process.env.RENDER_GIT_COMMIT,
    railwaySha:process.env.RAILWAY_GIT_COMMIT_SHA,
    fetch:globalThis.fetch, now:Date.now};
  const origin = `http://synthetic-api-${++originSequence}.internal:8000`;
  process.env.BACKEND_URL = origin;
  process.env.RENDER_GIT_COMMIT = COMMIT;
  delete process.env.RAILWAY_GIT_COMMIT_SHA;
  let now = 100_000;
  Date.now = () => now;
  const calls: {url:string;init:RequestInit | undefined}[] = [];
  globalThis.fetch = async (input, init) => {
    calls.push({url:String(input),init});
    return new Response(null,{status:200});
  };
  t.after(() => {
    if (previous.backend === undefined) delete process.env.BACKEND_URL; else process.env.BACKEND_URL = previous.backend;
    if (previous.sha === undefined) delete process.env.RENDER_GIT_COMMIT; else process.env.RENDER_GIT_COMMIT = previous.sha;
    if (previous.railwaySha === undefined) delete process.env.RAILWAY_GIT_COMMIT_SHA; else process.env.RAILWAY_GIT_COMMIT_SHA = previous.railwaySha;
    globalThis.fetch = previous.fetch;
    Date.now = previous.now;
  });
  return {origin,calls,advance:(ms:number) => {now += ms;}};
}

test('readiness exposes only coarse dependency status and an exact immutable release', async t => {
  const state = setup(t);
  const response = await GET();
  assert.equal(response.status,200);
  assert.equal(response.headers.get('cache-control'),'no-store');
  assert.equal(response.headers.get('x-content-type-options'),'nosniff');
  const payload = await response.json();
  assert.deepEqual(payload,{status:'ready',service:'milo-web',release_commit:COMMIT,
    dependency_status_max_age_seconds:5,external_integrations:'not_validated'});
  assert.equal(state.calls[0]?.url,state.origin + '/health/ready');
  const request = state.calls[0]?.init;
  assert.equal(request?.redirect,'manual');
  assert.equal(request?.cache,'no-store');
  assert.ok(request?.signal instanceof AbortSignal);
  assert.equal(new Headers(request?.headers).has('authorization'),false);
  assert.equal(new Headers(request?.headers).has('cookie'),false);
  assert.equal(JSON.stringify(payload).includes(state.origin),false);
});

test('invalid backend origins fail generically before any dependency request', async t => {
  const state = setup(t);
  for (const origin of ['', 'file:///etc/passwd', 'https://owner:synthetic-secret@private.internal',
    'https://api.internal/private', 'https://api.internal?token=synthetic-secret',
    'https://api.internal/#private', 'https://api.internal:notaport']) {
    process.env.BACKEND_URL = origin;
    const response = await GET();
    assert.equal(response.status,503);
    const payload = await response.text();
    assert.equal(payload.includes('synthetic-secret'),false);
    assert.equal(payload.includes('private.internal'),false);
    assert.equal(JSON.parse(payload).status,'unavailable');
  }
  assert.equal(state.calls.length,0);
});

test('dependency redirects and non-200 responses are unavailable without following redirects', async t => {
  const state = setup(t);
  for (const status of [302,401,500,204]) {
    process.env.BACKEND_URL = `http://status-${status}-${++originSequence}.internal`;
    globalThis.fetch = async (input,init) => {
      state.calls.push({url:String(input),init});
      return new Response(null,{status,headers:{Location:'https://attacker.example.test'}});
    };
    assert.equal((await GET()).status,503);
    assert.equal(state.calls.at(-1)?.init?.redirect,'manual');
    assert.equal(state.calls.at(-1)?.url.includes('attacker'),false);
  }
  assert.equal(state.calls.length,4);
});

test('dependency timeout or thrown transport detail cannot appear in public output', async t => {
  setup(t);
  globalThis.fetch = async () => {throw new Error('private-host synthetic-secret SQL failed');};
  const response = await GET();
  assert.equal(response.status,503);
  const output = await response.text();
  for (const forbidden of ['private-host','synthetic-secret','SQL failed']) assert.equal(output.includes(forbidden),false);
});

test('concurrent public readiness calls share one in-flight probe and cache result for five seconds', async t => {
  const state = setup(t);
  let complete!: (value:Response) => void;
  globalThis.fetch = (input,init) => {
    state.calls.push({url:String(input),init});
    return new Promise<Response>(resolve => {complete = resolve;});
  };
  const waiting = Array.from({length:80},() => GET());
  assert.equal(state.calls.length,1);
  complete(new Response(null,{status:200}));
  const responses = await Promise.all(waiting);
  assert.ok(responses.every(response => response.status === 200));
  await GET();
  state.advance(4_999);
  await GET();
  assert.equal(state.calls.length,1);
  globalThis.fetch = async (input,init) => {
    state.calls.push({url:String(input),init});
    return new Response(null,{status:200});
  };
  state.advance(1);
  assert.equal((await GET()).status,200);
  assert.equal(state.calls.length,2);
});

test('unavailable results are cached too so failing backend cannot cause health probe amplification', async t => {
  const state = setup(t);
  globalThis.fetch = async (input,init) => {
    state.calls.push({url:String(input),init});
    throw new Error('synthetic backend outage');
  };
  const responses = await Promise.all(Array.from({length:100},() => GET()));
  assert.ok(responses.every(response => response.status === 503));
  assert.equal(state.calls.length,1);
  state.advance(5_000);
  await GET();
  assert.equal(state.calls.length,2);
});

test('changed private dependency origin invalidates prior ready result immediately', async t => {
  const state = setup(t);
  assert.equal((await GET()).status,200);
  process.env.BACKEND_URL = 'http://different-private-api.internal:8000';
  globalThis.fetch = async (input,init) => {
    state.calls.push({url:String(input),init});
    return new Response(null,{status:503});
  };
  assert.equal((await GET()).status,503);
  assert.equal(state.calls.length,2);
});

test('health discards unneeded response body without awaiting stalled cancellation', async t => {
  setup(t);
  let canceled = false;
  globalThis.fetch = async () => new Response(new ReadableStream({pull() {},cancel() {
    canceled = true;
    return new Promise<void>(() => {});
  }}),{status:200});
  assert.equal((await GET()).status,200);
  assert.equal(canceled,true);
});

test('untrusted, abbreviated, or absent release metadata is null rather than echoed', async t => {
  setup(t);
  for (const commit of ['main','a'.repeat(39),'A'.repeat(40),'synthetic-secret']) {
    process.env.RENDER_GIT_COMMIT = commit;
    assert.equal((await (await GET()).json()).release_commit,null);
  }
  delete process.env.RENDER_GIT_COMMIT;
  assert.equal((await (await GET()).json()).release_commit,null);
});

test('Railway runtime commit takes precedence over metadata for another deployment provider', async t => {
  const state = setup(t);
  const railwayCommit = 'b'.repeat(40);
  process.env.RAILWAY_GIT_COMMIT_SHA = railwayCommit;
  const response = await GET();
  assert.equal(response.status,200);
  assert.deepEqual(await response.json(),{status:'ready',service:'milo-web',release_commit:railwayCommit,
    dependency_status_max_age_seconds:5,external_integrations:'not_validated'});
  assert.equal(state.calls.length,1);
});

test('Railway commit reporting does not make an unavailable private dependency ready', async t => {
  setup(t);
  delete process.env.RENDER_GIT_COMMIT;
  process.env.RAILWAY_GIT_COMMIT_SHA = 'c'.repeat(40);
  globalThis.fetch = async () => new Response(null,{status:503});
  const response = await GET();
  assert.equal(response.status,503);
  assert.equal(response.headers.get('cache-control'),'no-store');
  const payload = await response.json();
  assert.equal(payload.status,'unavailable');
  assert.equal(payload.release_commit,'c'.repeat(40));
});

test('malformed Railway metadata is not echoed or replaced by a different provider commit', async t => {
  setup(t);
  for (const commit of ['', 'main', 'd'.repeat(39), 'D'.repeat(40), 'd'.repeat(40)+'\n',
    'https://owner:synthetic-secret@private.internal']) {
    process.env.RAILWAY_GIT_COMMIT_SHA = commit;
    const output = await (await GET()).text();
    assert.equal(JSON.parse(output).release_commit,null);
    assert.equal(output.includes('synthetic-secret'),false);
    assert.equal(output.includes('private.internal'),false);
  }
});

test('cached dependency readiness does not retain stale provider release metadata', async t => {
  const state = setup(t);
  assert.equal((await (await GET()).json()).release_commit,COMMIT);
  process.env.RAILWAY_GIT_COMMIT_SHA = 'e'.repeat(40);
  assert.equal((await (await GET()).json()).release_commit,'e'.repeat(40));
  delete process.env.RAILWAY_GIT_COMMIT_SHA;
  assert.equal((await (await GET()).json()).release_commit,COMMIT);
  assert.equal(state.calls.length,1);
});
