import test from 'node:test';
import assert from 'node:assert/strict';
import {createDemoSnapshot} from '../demo';
import {ownerAnswerIsCurrent,ownerAnswerExpiresAt,type OwnerAnswer} from '../owner-answer';

function currentAnswer(){
  const snapshot=createDemoSnapshot();
  const chat=snapshot.conversations[0];
  chat.control_epoch=4;chat.revision=7;
  chat.permissions={read:true,retain:true,learn:true,draft:false,send:false,share:false,version:3,expires_at:null};
  const connection=snapshot.connections.find(row=>row.id===chat.connector_id)!;
  connection.fence=2;
  snapshot.workspace.paused=false;
  const answer:OwnerAnswer={conversation_id:chat.id,audience:'owner_only',external_actions:false,
    text:'Current owner-only answer',evidence_message_ids:[],missing_facts:[],authorization_context:{
      schema_version:1,owner_id:snapshot.user.id,workspace_id:snapshot.workspace.id,conversation_id:chat.id,
      connector_id:connection.id,conversation_revision:chat.revision,control_epoch:4,control_state:chat.control_state,
      permission_version:3,permissions:{read:true,retain:true,learn:true,draft:false,send:false,share:false},
      permission_expires_at:null,pause_generation:snapshot.workspace.pause_generation,connector_fence:2,
      connector_status:connection.status,memory_versions:{},expires_at:new Date(Date.now()+60000).toISOString()}};
  return {snapshot,answer,chat,connection};
}

test('owner answer survives unrelated usage or activity changes using its exact audience authority',()=>{
  const {snapshot,answer}=currentAnswer();
  assert.equal(ownerAnswerIsCurrent(answer,snapshot),true);
  snapshot.budget={id:'budget',usage:{token_units:1900}};snapshot.activity=[];
  assert.equal(ownerAnswerIsCurrent(answer,snapshot),true);
  assert.equal(ownerAnswerExpiresAt(answer),Date.parse(answer.authorization_context.expires_at));
});

for(const mutation of ['owner','workspace','paused','pauseGeneration','revision','takeover','controlEpoch','connectorFence',
  'connectorStatus','permissionVersion','read','learn','retain','missingChat','missingConnection'] as const){
  test(`owner answer cannot survive ${mutation} authority change`,()=>{
    const {snapshot,answer,chat,connection}=currentAnswer();
    switch(mutation){
      case 'owner':snapshot.user.id='other-owner';break;
      case 'workspace':snapshot.workspace.id='other-workspace';break;
      case 'paused':snapshot.workspace.paused=true;break;
      case 'pauseGeneration':snapshot.workspace.pause_generation++;break;
      case 'revision':chat.revision++;break;
      case 'takeover':chat.control_state='HUMAN_TAKEOVER';break;
      case 'controlEpoch':chat.control_epoch=5;break;
      case 'connectorFence':connection.fence=3;break;
      case 'connectorStatus':connection.status='disconnected';break;
      case 'permissionVersion':(chat.permissions as {version:number}).version=4;break;
      case 'read':(chat.permissions as {read:boolean}).read=false;break;
      case 'learn':(chat.permissions as {learn:boolean}).learn=false;break;
      case 'retain':(chat.permissions as {retain:boolean}).retain=false;break;
      case 'missingChat':snapshot.conversations=[];break;
      case 'missingConnection':snapshot.connections=[];break;
    }
    assert.equal(ownerAnswerIsCurrent(answer,snapshot),false);
  });
}

test('owner answer expires without another server poll or explicit user action',()=>{
  const {snapshot,answer}=currentAnswer();
  const expires=Date.parse(answer.authorization_context.expires_at);
  assert.equal(ownerAnswerIsCurrent(answer,snapshot,expires-1),true);
  assert.equal(ownerAnswerIsCurrent(answer,snapshot,expires),false);
  answer.authorization_context.expires_at='invalid-time';
  assert.equal(ownerAnswerIsCurrent(answer,snapshot),false);
});

test('a matching permission can silently expire without a version mutation',()=>{
  const {snapshot,answer,chat}=currentAnswer();
  const expiry=new Date(Date.now()+10000).toISOString();
  (chat.permissions as {expires_at:string|null}).expires_at=expiry;
  answer.authorization_context.permission_expires_at=expiry;
  assert.equal(ownerAnswerIsCurrent(answer,snapshot,Date.parse(expiry)-1),true);
  assert.equal(ownerAnswerIsCurrent(answer,snapshot,Date.parse(expiry)),false);
});

test('selected owner-confirmed memories must remain available, current and unexpired',()=>{
  const {snapshot,answer,chat}=currentAnswer();
  const expiry=new Date(Date.now()+30000).toISOString();
  const memory={id:'scoped-memory',conversation_id:chat.id,text:'Owner confirmed fact',status:'confirmed',
    version:2,suppression_version:0,expires_at:expiry};
  snapshot.memories.push(memory);
  answer.authorization_context.memory_versions={[memory.id]:[2,0,expiry]};
  assert.equal(ownerAnswerIsCurrent(answer,snapshot),true);
  memory.version=3;assert.equal(ownerAnswerIsCurrent(answer,snapshot),false);
  memory.version=2;assert.equal(ownerAnswerIsCurrent(answer,snapshot,Date.parse(expiry)),false);
  memory.conversation_id='another-person';assert.equal(ownerAnswerIsCurrent(answer,snapshot),false);
  snapshot.memories=[];assert.equal(ownerAnswerIsCurrent(answer,snapshot),false);
});

test('malformed metadata, unknown audiences and action-bearing responses cannot acquire an answer fence',()=>{
  const {snapshot,answer}=currentAnswer();
  assert.equal(ownerAnswerIsCurrent({...answer,external_actions:true},snapshot),false);
  assert.equal(ownerAnswerIsCurrent({...answer,audience:'group'},snapshot),false);
  assert.equal(ownerAnswerIsCurrent({...answer,conversation_id:'another-chat'},snapshot),false);
  assert.equal(ownerAnswerIsCurrent({...answer,authorization_context:null},snapshot),false);
  assert.equal(ownerAnswerIsCurrent({...answer,authorization_context:{...answer.authorization_context,memory_versions:[]}},snapshot),false);
  assert.equal(ownerAnswerIsCurrent(answer,null),false);
});
