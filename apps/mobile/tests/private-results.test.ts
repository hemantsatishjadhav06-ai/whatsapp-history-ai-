import test from 'node:test';
import assert from 'node:assert/strict';
import {createDemoSnapshot} from '@milo/contracts';
import {currentPrivateResult,reconcilePrivateResponse,snapshotVersion} from '../lib/private-results';

test('same-owner same-workspace read revocation hides a previously permitted private command result immediately',()=>{
  const before=createDemoSnapshot();const entry={value:{summary:'Synthetic Maya private digest'},version:snapshotVersion(before)};
  const revoked={...before,conversations:before.conversations.filter(row=>row.id!=='chat_maya')};
  assert.equal(currentPrivateResult(entry,revoked),null);
  assert.equal(reconcilePrivateResponse(entry.value,before,revoked),null);
});

test('memory forget and permission edits clear prior evidence even when owner, workspace and conversation remain unchanged',()=>{
  const before=createDemoSnapshot();const entry={value:{evidence:'Synthetic private learned preference'},version:snapshotVersion(before)};
  const forgotten={...before,memories:[]};
  assert.equal(currentPrivateResult(entry,forgotten),null);
  assert.equal(reconcilePrivateResponse(entry.value,before,forgotten),null);
  const permissionsChanged={...before,conversations:before.conversations.map(row=>({...row,permissions:{read:true,learn:false,retain:false,version:99}}))};
  assert.equal(currentPrivateResult(entry,permissionsChanged),null);
});

test('a private response cannot be blessed with a newer server authorization generation',()=>{
  const before={...createDemoSnapshot(),snapshot_version:'authority-before'};
  const fresh={...before,snapshot_version:'authority-after'};
  assert.equal(reconcilePrivateResponse({summary:'Old permitted body'},before,fresh),null);
  assert.equal(currentPrivateResult({value:'Old body',version:snapshotVersion(before)},fresh),null);
});

test('an object changed by an owner mutation is re-read from the fresh authorized collection instead of displaying its old response body',()=>{
  const before=createDemoSnapshot();
  const old={id:'new-draft',conversation_id:'chat_maya',text:'Old response text',status:'needs_approval'};
  const current={...old,text:'Fresh authorized object text',content_hash:'fresh-hash'};
  const fresh={...before,drafts:[...before.drafts,current]};
  assert.deepEqual(reconcilePrivateResponse(old,before,fresh),current);
  assert.equal(reconcilePrivateResponse({...old,id:'revoked-draft'},before,fresh),null);
});

test('a no-change snapshot poll preserves a result while sign-out removes it',()=>{
  const before=createDemoSnapshot();const response={summary:'Current synthetic permitted body'};
  const entry={value:response,version:snapshotVersion(before)};
  const fresh={...before,generated_at:'2026-10-07T12:00:00Z'};
  assert.deepEqual(currentPrivateResult(entry,fresh),response);
  assert.deepEqual(reconcilePrivateResponse(response,before,fresh),response);
  assert.equal(currentPrivateResult(entry,null),null);
});
