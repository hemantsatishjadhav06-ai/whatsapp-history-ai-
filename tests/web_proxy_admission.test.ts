import assert from 'node:assert/strict';
import test, {type TestContext} from 'node:test';
import type {NextRequest} from 'next/server';
import {GET,POST} from '../apps/web/app/api/[...path]/route.ts';
import {acquireProxyLease,leaseProxyBody} from '../apps/web/lib/proxy-admission.ts';
import {boundedProxyBody} from '../apps/web/lib/proxy-body.ts';

function setup(t:TestContext) {
  const previous={fetch:globalThis.fetch,backend:process.env.BACKEND_URL,origin:process.env.PUBLIC_APP_ORIGIN,
    hops:process.env.TRUST_PROXY_HOPS,key:process.env.BACKEND_PROXY_KEY};
  process.env.BACKEND_URL='http://private-api.invalid';process.env.PUBLIC_APP_ORIGIN='https://milo.example.test';
  delete process.env.TRUST_PROXY_HOPS;delete process.env.BACKEND_PROXY_KEY;
  const controllers:AbortController[]=[];
  t.after(()=>{
    controllers.forEach(controller=>controller.abort());globalThis.fetch=previous.fetch;
    for(const [name,value] of [['BACKEND_URL',previous.backend],['PUBLIC_APP_ORIGIN',previous.origin],['TRUST_PROXY_HOPS',previous.hops],['BACKEND_PROXY_KEY',previous.key]]) {
      if(value===undefined)delete process.env[name!];else process.env[name!]=value;
    }
  });
  const request=(path:string,method='GET',body?:ReadableStream<Uint8Array>|string,cookie='session_token=synthetic-owner')=>{
    const controller=new AbortController();controllers.push(controller);
    const value=new Request('https://milo.example.test/api/'+path,{method,body,signal:controller.signal,
      headers:{Origin:'https://milo.example.test',...(cookie?{Cookie:cookie}:{})},...(body instanceof ReadableStream?{duplex:'half'}:{})} as RequestInit);
    Object.defineProperty(value,'nextUrl',{value:new URL(value.url)});
    return {value:value as NextRequest,controller};
  };
  return request;
}
const context=(path:string)=>({params:Promise.resolve({path:path.split('/')})});
const tick=()=>new Promise<void>(resolve=>setTimeout(resolve,0));

test('28 held normal requests reject a new body before reading or fetching, preserve four controls, then recover',async t=>{
  const request=setup(t);const release:Array<()=>void>=[];
  globalThis.fetch=async()=>new Promise<Response>(resolve=>{release.push(()=>resolve(new Response('{}')));});
  const pending=Array.from({length:28},()=>GET(request('me').value,context('me')));
  await tick();assert.equal(release.length,28);
  let reads=0;
  const input=new ReadableStream<Uint8Array>({pull(){reads++;}},{highWaterMark:0});
  const denied=await POST(request('assistant/commands','POST',input).value,context('assistant/commands'));
  assert.equal(denied.status,503);assert.equal(reads,0);assert.equal(release.length,28);
  const controls=Array.from({length:4},()=>POST(request('pause-all','POST','{}').value,context('pause-all')));
  await tick();assert.equal(release.length,32);
  assert.equal((await POST(request('resume-all','POST','{}').value,context('resume-all'))).status,503);
  release.forEach(resolve=>resolve());
  const responses=await Promise.all([...pending,...controls]);
  await Promise.all(responses.map(response=>response.text()));
  globalThis.fetch=async()=>new Response('{}');
  const recovered=await GET(request('me').value,context('me'));
  assert.equal(recovered.status,200);await recovered.text();
});

test('only two imports may buffer or reach the backend, while an ordinary request still works',async t=>{
  const request=setup(t);const release:Array<()=>void>=[];
  globalThis.fetch=async()=>new Promise<Response>(resolve=>{release.push(()=>resolve(new Response('{}')));});
  const pending=[POST(request('imports','POST','{}').value,context('imports')),
    POST(request('imports/preview','POST','{}').value,context('imports/preview'))];
  await tick();assert.equal(release.length,2);
  let reads=0;const input=new ReadableStream<Uint8Array>({pull(){reads++;}},{highWaterMark:0});
  assert.equal((await POST(request('imports','POST',input).value,context('imports'))).status,503);
  assert.equal(reads,0);assert.equal(release.length,2);
  const ordinary=GET(request('me').value,context('me'));await tick();assert.equal(release.length,3);
  release.forEach(resolve=>resolve());
  await Promise.all((await Promise.all([...pending,ordinary])).map(response=>response.text()));
});

test('a stalled upload deadline releases its reservation even if cancellation never settles',async()=>{
  const first=acquireProxyLease(false,true)!;const second=acquireProxyLease(false,true)!;
  assert.equal(acquireProxyLease(false,true),null);
  const input=new ReadableStream<Uint8Array>({pull(){},cancel(){return new Promise<void>(()=>{});}});
  const request=new Request('https://milo.example.test/api/imports',{method:'POST',body:input,duplex:'half'} as RequestInit);
  await assert.rejects(boundedProxyBody(request,1024,10).finally(()=>first.release()),/deadline/);
  const recovered=acquireProxyLease(false,true);assert.ok(recovered);recovered.release();second.release();
});

test('client abort frees a pre-buffer import lease and permits admission after cancellation',async t=>{
  const request=setup(t);let fetched=0;globalThis.fetch=async()=>{fetched++;return new Response('{}');};
  const first=request('imports','POST',new ReadableStream<Uint8Array>({pull(){}},{highWaterMark:0}));
  const second=request('imports','POST',new ReadableStream<Uint8Array>({pull(){}},{highWaterMark:0}));
  const pending=[POST(first.value,context('imports')),POST(second.value,context('imports'))];
  await tick();assert.equal((await POST(request('imports','POST','{}').value,context('imports'))).status,503);
  first.controller.abort();second.controller.abort();
  assert.deepEqual((await Promise.all(pending)).map(response=>response.status),[400,400]);assert.equal(fetched,0);
  const recovered=await POST(request('imports','POST','{}').value,context('imports'));
  assert.equal(recovered.status,200);await recovered.text();
});

test('response consumption, cancellation and deadline release stream leases without hiding upstream errors',async()=>{
  for(const completion of ['consume','cancel','expire','error'] as const) {
    const lease=acquireProxyLease(false,true)!;const other=acquireProxyLease(false,true)!;
    const abort=new AbortController();let upstream:ReadableStreamDefaultController<Uint8Array>;
    const source=new ReadableStream<Uint8Array>({start(controller){upstream=controller;},cancel(){return new Promise<void>(()=>{});}});
    const body=leaseProxyBody(source,lease,abort.signal);
    assert.equal(acquireProxyLease(false,true),null);
    if(completion==='consume') { upstream!.enqueue(new TextEncoder().encode('current'));upstream!.close();assert.equal(await new Response(body).text(),'current'); }
    if(completion==='cancel')await body.cancel();
    if(completion==='expire') { abort.abort(new Error('response deadline'));await assert.rejects(new Response(body).text(),/deadline/); }
    if(completion==='error') { upstream!.error(new Error('upstream failed'));await assert.rejects(new Response(body).text(),/upstream failed/); }
    const recovered=acquireProxyLease(false,true);assert.ok(recovered);recovered.release();other.release();
    lease.release(); // Idempotence must not release another request's reservation.
  }
});

test('six stalled public logins cannot borrow session capacity, and full session capacity cannot block login',async t=>{
  const request=setup(t);let fetched=0;globalThis.fetch=async()=>{fetched++;return new Response('{}');};
  const stalled=Array.from({length:6},()=>request('auth/google','POST',new ReadableStream<Uint8Array>({pull(){}},{highWaterMark:0}),''));
  const pending=stalled.map(entry=>POST(entry.value,context('auth/google')));
  await tick();
  let reads=0;const input=new ReadableStream<Uint8Array>({pull(){reads++;}},{highWaterMark:0});
  const busy=await POST(request('auth/google','POST',input,'').value,context('auth/google'));
  assert.equal(busy.status,503);assert.equal((await busy.json()).reason_code,'PROXY_BUSY');assert.equal(reads,0);
  assert.equal((await GET(request('auth/nonce','GET',undefined,'').value,context('auth/nonce'))).status,503);
  const owner=await GET(request('me').value,context('me'));assert.equal(owner.status,200);await owner.text();
  const control=await POST(request('pause-all','POST','{}').value,context('pause-all'));assert.equal(control.status,200);await control.text();
  stalled.forEach(entry=>entry.controller.abort());
  assert.deepEqual((await Promise.all(pending)).map(response=>response.status),[400,400,400,400,400,400]);
  const held=[...Array.from({length:28},()=>acquireProxyLease(false,false)!),...Array.from({length:4},()=>acquireProxyLease(true,false)!)];
  t.after(()=>held.forEach(lease=>lease.release()));
  assert.equal((await GET(request('me').value,context('me'))).status,503);
  const login=await GET(request('auth/nonce','GET',undefined,'').value,context('auth/nonce'));assert.equal(login.status,200);await login.text();
  assert.equal(fetched,3);
});

test('an upstream transport failure releases a request lease and yields an honest unconfirmed response',async t=>{
  const request=setup(t);globalThis.fetch=async()=>{throw new Error('socket failed');};
  for(let attempt=0;attempt<40;attempt++)assert.equal((await GET(request('me').value,context('me'))).status,504);
  globalThis.fetch=async()=>new Response('{}');const recovered=await GET(request('me').value,context('me'));
  assert.equal(recovered.status,200);await recovered.text();
});
