import assert from 'node:assert/strict';
import test from 'node:test';
import { authorizedToolRequest, currentToolResult, toolAuthorizationVersion } from '../apps/web/lib/tool-privacy.ts';
import type { MiloState } from '../apps/web/lib/types.ts';

function state(): MiloState {
  return { mode:'live', user:{id:'owner',display_name:'Owner',email:'synthetic@example.test'}, workspaceId:'workspace',
    selectedConversationId:'chat', loading:false,error:null,paused:false,pausePending:false,online:true,
    lastUpdated:'2026-10-07T00:00:00Z',timezone:'UTC',nextCursor:null,hasMore:false,
    data:{ conversations:[{id:'chat',conversation_id:'chat',title:'Private chat',kind:'contact',control_state:'DRAFT_MODE',
      connector_id:'account',account_label:'WhatsApp',provider_chat_id:'private-chat',revision:1,preview:'',timestamp:'',
      unread:0,avatar_color:'',pending_drafts:0,pending_tasks:0,permissions:{read:true,retain:true,learn:true,version:1}}],
    connections:[{id:'account',account_id:'private-account',label:'WhatsApp',provider:'mock',status:'connected',capabilities:{quote:'mocked'}}],
    memories:[{id:'memory',conversation_id:'chat',text:'Private fact',version:1,source_message_ids:['source']}],
    actions:[],jobs:[],styles:[],grants:[{id:'grant',conversation_id:'chat',version:1,enabled:true}],routes:[],
    contacts:[],budget:null,activity:[],tasks:[],drafts:[] } };
}

test('unchanged authorized polling preserves an open edit and its private result', () => {
  const before=state(); const after=structuredClone(before);
  after.lastUpdated='2026-10-07T00:00:30Z';after.online=false;
  const version=toolAuthorizationVersion(before);
  assert.equal(toolAuthorizationVersion(after),version);
  assert.equal(currentToolResult({value:before.data.memories[0],authorizationVersion:version},toolAuthorizationVersion(after))?.text,'Private fact');
});

test('same-owner memory correction and source forgetting remove prior dialog text synchronously', () => {
  const before=state();const version=toolAuthorizationVersion(before);
  for (const operation of ['correct','forget'] as const) {
    const after=structuredClone(before);
    if(operation==='correct')after.data.memories[0]={...after.data.memories[0],text:'Corrected',version:2,source_message_ids:[]};
    else after.data.memories=[];
    assert.equal(after.user.id,before.user.id);
    assert.equal(currentToolResult({value:before.data.memories[0],authorizationVersion:version},toolAuthorizationVersion(after)),null);
  }
});

test('same-owner read revocation, grant revision and removed capability invalidate old private details', () => {
  const before=state();const version=toolAuthorizationVersion(before);
  for(const operation of ['read','grant','capability'] as const) {
    const after=structuredClone(before);
    if(operation==='read')after.data.conversations=[];
    if(operation==='grant')after.data.grants[0]={...after.data.grants[0],version:2,enabled:false};
    if(operation==='capability')after.data.connections[0].capabilities={quote:'unavailable'};
    assert.notEqual(toolAuthorizationVersion(after),version);
    assert.equal(currentToolResult({value:{text:'Previously readable evidence'},authorizationVersion:version},toolAuthorizationVersion(after)),null);
  }
});

test('authoritative server content versions are bound to owner, workspace and mode', () => {
  const before={...state(),authorizationVersion:'authorized-v1'};const version=toolAuthorizationVersion(before);
  assert.equal(toolAuthorizationVersion({...before,lastUpdated:'later'}),version);
  for(const after of [{...before,authorizationVersion:'authorized-v2'},{...before,workspaceId:'other'},
    {...before,user:{...before.user,id:'other'}},{...before,mode:'demo' as const}]) {
    assert.equal(currentToolResult({value:'private',authorizationVersion:version},toolAuthorizationVersion(after)),null);
  }
});

test('late evidence GET and object resolver cannot reopen a same-owner forgotten detail', async () => {
  let latest=state();const before=toolAuthorizationVersion(latest);
  let finish!:(value:{object:{id:string;text:string}})=>void;
  const request=new Promise<{object:{id:string;text:string}}>(resolve=>{finish=resolve;});
  const result=authorizedToolRequest(request,before,()=>toolAuthorizationVersion(latest));
  latest={...latest,data:{...latest.data,memories:[]}};
  finish({object:{id:'memory',text:'Forgotten private fact'}});
  assert.equal(await result,null);
});

test('a late rejected GET is discarded while a current rejection remains recoverable', async () => {
  let latest='v1';let reject!:(error:Error)=>void;
  const pending=authorizedToolRequest(new Promise<void>((_,fail)=>{reject=fail;}),'v1',()=>latest);
  latest='v2';reject(new Error('Old scope denied'));
  assert.equal(await pending,null);
  await assert.rejects(authorizedToolRequest(Promise.reject(new Error('Current request failed')),'v2',()=>latest),/Current request failed/);
});

test('a fresh authorized detail is available without relabeling old evidence', async () => {
  const before=state(); const version=toolAuthorizationVersion(before);
  const entry=await authorizedToolRequest(Promise.resolve({sources:[{id:'source',text:'Current evidence'}]}),version,()=>version);
  assert.deepEqual(currentToolResult(entry,version),{sources:[{id:'source',text:'Current evidence'}]});
});
