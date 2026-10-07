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

test('a server-scoped owner answer survives its model-budget mutation but never a source/permission/owner change',()=>{
  const before={...createDemoSnapshot(),snapshot_version:'before-model'};
  const chat=before.conversations[0];chat.control_epoch=1;chat.revision=10;
  chat.permissions={read:true,retain:true,learn:true,draft:true,send:false,share:false,version:4,expires_at:null};
  const connection=before.connections.find(row=>row.id===chat.connector_id)!;connection.fence=2;
  before.workspace.paused=false;
  const answer={conversation_id:chat.id,audience:'owner_only',external_actions:false,text:'Owner answer',
    authorization_context:{schema_version:1,owner_id:before.user.id,workspace_id:before.workspace.id,
      conversation_id:chat.id,connector_id:connection.id,conversation_revision:10,control_epoch:1,
      control_state:chat.control_state,permission_version:4,permissions:chat.permissions,permission_expires_at:null,
      pause_generation:before.workspace.pause_generation,connector_fence:2,connector_status:connection.status,
      memory_versions:{},expires_at:new Date(Date.now()+60000).toISOString()}};
  const fresh={...before,snapshot_version:'after-model',budget:{id:'budget',usage:{token_units:2048}}};
  assert.equal(reconcilePrivateResponse(answer,before,fresh),answer);
  assert.equal(currentPrivateResult({value:answer,version:snapshotVersion(before)},fresh),answer);
  const edited={...fresh,conversations:fresh.conversations.map(row=>({...row,revision:row.revision+1}))};
  assert.equal(reconcilePrivateResponse(answer,before,edited),null);
  assert.equal(currentPrivateResult({value:answer,version:snapshotVersion(before)},edited),null);
  const changedOwner={...fresh,user:{...fresh.user,id:'new-owner'}};
  assert.equal(reconcilePrivateResponse(answer,before,changedOwner),null);
  assert.equal(currentPrivateResult({value:answer,version:snapshotVersion(before)},null),null);
  // The narrower fence cannot bless an ordinary digest from an old generation.
  assert.equal(reconcilePrivateResponse({summary:'Ordinary private digest'},before,fresh),null);
});
