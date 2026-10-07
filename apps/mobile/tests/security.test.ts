import test from 'node:test';
import assert from 'node:assert/strict';
import {recoverableSession,safeApiOrigin,sessionKey,sessionRecord,validStoredSession} from '../lib/security';
import {draftScopeKey,SessionGuard} from '../lib/session-guard';
import {nativeClient} from '../lib/native-api';

const now=Date.parse('2026-10-07T10:00:00Z');
const origin='https://api.milo.example';
const access={access_token:'na_synthetic_access_example_1234567890',expires_at:'2026-10-07T11:00:00Z',session_id:'session-a'};
const stored=sessionRecord(access,origin,'production');
function deferred(){let resolve!:()=>void;const promise=new Promise<void>(done=>{resolve=done;});return{promise,resolve};}

test('production API origins cannot send bearer material to credentials, HTTP or path-derived targets',()=>{
  assert.equal(safeApiOrigin(origin),origin);
  for(const target of ['http://api.milo.example','https://owner:secret@api.milo.example','https://api.milo.example/v1','https://api.milo.example?redirect=elsewhere','https://api.milo.example#token','file:///private','not-a-url']){
    assert.equal(safeApiOrigin(target),null);
  }
  assert.equal(safeApiOrigin('http://127.0.0.1:8000',true),'http://127.0.0.1:8000');
  assert.equal(safeApiOrigin('http://192.168.1.2:8000',true),null);
  assert.equal(safeApiOrigin('http://localhost:8000',false),null);
});

test('secure session records strip profile/chat payloads and stay origin/environment bound',()=>{
  const response={...access,user:{email:'synthetic@example.test'},messages:['Private synthetic text'],whatsapp_keys:'must-not-persist'};
  assert.deepEqual(sessionRecord(response,origin,'production'),stored);
  assert.equal(validStoredSession(stored,origin,'production',now),true);
  assert.equal(validStoredSession(stored,'https://other.example','production',now),false);
  assert.equal(validStoredSession(stored,origin,'preview',now),false);
  assert.equal(validStoredSession({...stored,expires_at:'2026-10-07T10:00:04Z'},origin,'production',now),false);
  assert.equal(validStoredSession({...stored,expires_at:'invalid'},origin,'production',now),false);
  assert.notEqual(sessionKey(origin,'production'),sessionKey(origin,'preview'));
  assert.notEqual(sessionKey(origin,'production'),sessionKey('https://other.example','production'));
});

test('expired access can recover only with a bounded current refresh credential in the same deployment',()=>{
  const expired={...stored,expires_at:'2026-10-07T09:00:00Z'};
  assert.equal(recoverableSession(expired,origin,'production',now),false);
  const renewable={...expired,refresh_token:'nr_synthetic_refresh_example_1234567890',refresh_expires_at:'2026-11-06T10:00:00Z'};
  assert.equal(recoverableSession(renewable,origin,'production',now),true);
  assert.equal(validStoredSession(renewable,origin,'production',now),false);
  for(const invalid of [{...renewable,refresh_expires_at:'2026-10-07T09:00:00Z'},{...renewable,refresh_token:'browser-cookie'},{...renewable,environment:'preview'},{...renewable,origin:'https://other.example'}]){
    assert.equal(recoverableSession(invalid,origin,'production',now),false);
  }
});

test('native API requests capture one bearer and omit ambient browser cookies',async()=>{
  const requests:RequestInit[]=[];
  const fakeFetch:typeof fetch=async(_url,options)=>{requests.push(options??{});return new Response(JSON.stringify({owned:true}),{status:200});};
  let selectedToken=access.access_token;
  const client=nativeClient(origin,selectedToken,fakeFetch);
  selectedToken='na_different_owner_access_123456789012345';
  await client.request('/v1/ui/bootstrap');
  assert.equal(new Headers(requests[0].headers).get('Authorization'),`Bearer ${access.access_token}`);
  assert.equal(requests[0].credentials,'omit');
  assert.equal(requests[0].redirect,'error');
});

test('native mutation uncertainty returns once without replaying an outward request',async()=>{
  let calls=0;
  const fakeFetch:typeof fetch=async()=>{calls++;throw new Error('Synthetic connection lost after submission');};
  await assert.rejects(nativeClient(origin,access.access_token,fakeFetch).request('/v1/actions/action-a/dispatch',{method:'POST',idempotencyKey:'native-test-fixed-key'}),/after submission/);
  assert.equal(calls,1);
});

test('native object paths cannot redirect a bearer to an arbitrary origin or internal control route',async()=>{
  let calls=0;const fakeFetch:typeof fetch=async()=>{calls++;return new Response('{}');};
  const client=nativeClient(origin,access.access_token,fakeFetch);
  for(const path of ['https://other.example/v1/ui/bootstrap','//other.example','/v1/internal/actions/prepare','/v1/../internal','/v1/%2e%2e/internal','/v1/%252e%252e/internal','/v1/\\other']){
    await assert.rejects(client.request(path));
  }
  assert.equal(calls,0);
});

test('a delayed secure deletion finishes before saving a newly signed-in session',async()=>{
  const guard=new SessionGuard();const deletion=deferred();let saved='old-owner';const events:string[]=[];
  const remove=guard.enqueueStorage(async()=>{events.push('delete-start');await deletion.promise;saved='';events.push('delete-end');});
  const save=guard.enqueueStorage(async()=>{saved='new-owner';events.push('new-save');},guard.epoch);
  await Promise.resolve();await Promise.resolve();
  assert.deepEqual(events,['delete-start']);
  deletion.resolve();await Promise.all([remove,save]);
  assert.equal(saved,'new-owner');assert.deepEqual(events,['delete-start','delete-end','new-save']);
});

test('sign-out invalidates queued refresh writes while preserving the next owner session',async()=>{
  const guard=new SessionGuard();const slowOperation=deferred();let saved='old-access';
  const blocked=guard.enqueueStorage(()=>slowOperation.promise);
  const oldEpoch=guard.epoch;
  const staleRefresh=guard.enqueueStorage(async()=>{saved='old-owner-renewed';},oldEpoch);
  guard.invalidate();
  const remove=guard.enqueueStorage(async()=>{saved='';});
  const nextLogin=guard.enqueueStorage(async()=>{saved='new-owner-access';},guard.epoch);
  slowOperation.resolve();await Promise.all([blocked,staleRefresh,remove,nextLogin]);
  assert.equal(saved,'new-owner-access');assert.equal(guard.matches(oldEpoch),false);
});

test('a failed secure-storage operation cannot bypass ordering of later cleanup',async()=>{
  const guard=new SessionGuard();let saved='old-access';
  await assert.rejects(guard.enqueueStorage(async()=>{throw new Error('Storage unavailable');}),/Storage unavailable/);
  await guard.enqueueStorage(async()=>{saved='';});
  assert.equal(saved,'');
});

test('late owner responses and old pages cannot reintroduce private data after a session or authorized snapshot change',async()=>{
  const guard=new SessionGuard();const lateOwner=deferred();const ownerEpoch=guard.epoch;let visible='new-owner';
  const oldResponse=lateOwner.promise.then(()=>{if(guard.matches(ownerEpoch))visible='old-owner-secret';});
  guard.invalidate();lateOwner.resolve();await oldResponse;assert.equal(visible,'new-owner');
  const pageEpoch=guard.epoch;const pageSequence=guard.readSequence;
  assert.equal(guard.matches(pageEpoch,pageSequence),true);
  guard.invalidateReads();assert.equal(guard.matches(pageEpoch,pageSequence),false);
  assert.equal(guard.matches(pageEpoch),true);
});

test('unsent drafts remain distinct across owner, workspace, chat and exact recipient even with delimiter characters',()=>{
  const base=draftScopeKey('owner','workspace','chat','recipient');
  for(const parts of [['other','workspace','chat','recipient'],['owner','other','chat','recipient'],['owner','workspace','other','recipient'],['owner','workspace','chat','other']]){
    assert.notEqual(draftScopeKey(...parts as [string,string,string,string]),base);
  }
  assert.notEqual(draftScopeKey('owner|workspace','chat','recipient','x'),draftScopeKey('owner','workspace|chat','recipient','x'));
  assert.equal(decodeURIComponent(draftScopeKey('owner','workspace','chat|exact','recipient').split('|')[2]),'chat|exact');
});
