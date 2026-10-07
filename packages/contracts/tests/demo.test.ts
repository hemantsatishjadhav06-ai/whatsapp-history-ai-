import assert from 'node:assert/strict';
import {test} from 'node:test';
import {createDemoClient, createDemoSnapshot} from '../demo';
import {ApiError} from '../sdk';
import type {RecordEntity} from '../domain';

const future = () => new Date(Date.now() + 86400000).toISOString();
const later = () => new Date(Date.now() + 2 * 86400000).toISOString();
const reason = (code: string) => (error: unknown) => error instanceof ApiError && error.reasonCode === code;
const jobBody = () => ({workspace_id: 'workspace_demo', conversation_id: 'chat_maya', idempotency_key: 'stable-owner-job-key',
  action_kind: 'SEND_TEXT', purpose: 'An exact owner follow-up', content: 'See you tomorrow.', due_at: future(), expires_at: later(),
  timezone: 'Asia/Kolkata', recurrence: 'none', max_runs: 1});

test('fixture has eight scopes, six configured Auto chats and verified synthetic human voice examples', () => {
  const data = createDemoSnapshot();
  assert.equal(data.conversations.length, 8);
  assert.equal(data.grants.filter(row => row.enabled).length, 6);
  for (const profile of data.styles) for (const source of profile.evidence_message_ids as string[]) {
    const message = data.messages.find(row => row.id === source);
    assert.equal(message?.author_kind, 'human_owner');
    assert.equal(message?.conversation_id, profile.conversation_id);
  }
  assert.equal(data.actions.find(row => row.status === 'uncertain')?.transport, 'simulation_only');
});

test('snapshots and response objects are independent copies', async () => {
  const client = createDemoClient();
  const snapshot = client.snapshot(); snapshot.conversations[0].title = 'Mutated externally';
  assert.equal(client.snapshot().conversations[0].title, 'Maya');
  const bootstrap = await client.request<ReturnType<typeof createDemoSnapshot>>('/ui/bootstrap');
  bootstrap.memories[0].text = 'Private caller mutation';
  assert.notEqual(client.snapshot().memories[0].text, 'Private caller mutation');
});

test('selected-chat Catch me up does not disclose another chat or global resources', async () => {
  const client = createDemoClient();
  const scoped = await client.request<{result:{conversations:RecordEntity[]}}>('/assistant/commands',{method:'POST',body:{command:'catch_me_up',conversation_id:'chat_maya'}});
  assert.deepEqual(scoped.result.conversations.map(row => row.id),['chat_maya']);
  assert.equal('tasks' in scoped.result,false); assert.equal('jobs' in scoped.result,false);
  const global = await client.request<{result:{conversations:RecordEntity[]}}>('/assistant/commands',{method:'POST',body:{command:'catch_me_up'}});
  assert.equal(global.result.conversations.length,8);
  await assert.rejects(client.request('/assistant/commands',{method:'POST',body:{command:'catch_me_up',conversation_id:'foreign-chat'}}),reason('SCOPE_DENIED'));
});

test('workspace IDs embedded in resource paths cannot bypass demo scope', async () => {
  const client = createDemoClient(); const before = client.snapshot().budget;
  await assert.rejects(client.request('/workspaces/another-owner/budget'),reason('SCOPE_DENIED'));
  await assert.rejects(client.request('/workspaces/another-owner/budget',{method:'PUT',body:{expected_version:1,max_actions_per_day:0}}),reason('SCOPE_DENIED'));
  assert.deepEqual(client.snapshot().budget,before);
});

test('explicit Kolkata local time resolves without using the device timezone', async () => {
  const client = createDemoClient();
  const result = await client.request<RecordEntity>('/schedules/resolve-time', {method: 'POST', body: {local_datetime: '2026-10-07T09:00', timezone: 'Asia/Kolkata'}});
  assert.equal(result.due_at, '2026-10-07T03:30:00.000Z');
  assert.equal(result.utc_offset, '+05:30');
});

test('DST gaps and ambiguous times require an explicit decision', async () => {
  const client = createDemoClient();
  await assert.rejects(client.request('/schedules/resolve-time', {method:'POST',body:{local_datetime:'2026-03-08T02:30',timezone:'America/New_York'}}),reason('DST_GAP'));
  await assert.rejects(client.request('/schedules/resolve-time', {method:'POST',body:{local_datetime:'2026-11-01T01:30',timezone:'America/New_York'}}),reason('DST_AMBIGUOUS'));
  const early = await client.request<RecordEntity>('/schedules/resolve-time',{method:'POST',body:{local_datetime:'2026-11-01T01:30',timezone:'America/New_York',ambiguity_policy:'earlier'}});
  const late = await client.request<RecordEntity>('/schedules/resolve-time',{method:'POST',body:{local_datetime:'2026-11-01T01:30',timezone:'America/New_York',ambiguity_policy:'later'}});
  assert.equal(Date.parse(String(late.due_at)) - Date.parse(String(early.due_at)),3600000);
});

test('schedule creation is stable by idempotency key and never creates an incoming event', async () => {
  const client = createDemoClient(); const body = jobBody(); const before = client.snapshot().messages.length;
  const first = await client.request<RecordEntity>('/jobs',{method:'POST',body});
  const again = await client.request<RecordEntity>('/jobs',{method:'POST',body});
  assert.equal(first.id,again.id); assert.equal(first.status,'scheduled'); assert.equal(first.simulation,true);
  assert.equal(client.snapshot().messages.length,before);
  await assert.rejects(client.request('/jobs',{method:'POST',body:{...body,content:'Changed payload'}}),reason('CONTEXT_STALE'));
});

test('schedule controls enforce version conflicts and terminal state', async () => {
  const client = createDemoClient(); const job = await client.request<RecordEntity>('/jobs',{method:'POST',body:jobBody()});
  await assert.rejects(client.request(`/jobs/${job.id}`,{method:'PATCH',body:{expected_version:999,operation:'cancel'}}),reason('CONTEXT_STALE'));
  const held = await client.request<RecordEntity>(`/jobs/${job.id}`,{method:'PATCH',body:{expected_version:1,operation:'hold'}});
  assert.equal(held.hold_reason,'OWNER_HOLD');
  const canceled = await client.request<RecordEntity>(`/jobs/${job.id}`,{method:'PATCH',body:{expected_version:2,operation:'cancel'}});
  assert.equal(canceled.status,'canceled');
  await assert.rejects(client.request(`/jobs/${job.id}`,{method:'PATCH',body:{expected_version:3,operation:'resume'}}),reason('CONTEXT_STALE'));
});

test('global pause holds external schedules and leaves independent owner reminders', async () => {
  const client = createDemoClient();
  const external = await client.request<RecordEntity>('/jobs',{method:'POST',body:jobBody()});
  const reminder = await client.request<RecordEntity>('/jobs',{method:'POST',body:{...jobBody(),conversation_id:null,action_kind:'REMINDER',idempotency_key:'owner-reminder-key'}});
  await client.request('/pause-all?workspace_id=workspace_demo',{method:'POST'});
  assert.equal(client.snapshot().jobs.find(row => row.id === external.id)?.status,'held');
  assert.equal(client.snapshot().jobs.find(row => row.id === reminder.id)?.status,'scheduled');
  await client.request('/resume-all?workspace_id=workspace_demo',{method:'POST'});
  assert.equal(client.snapshot().jobs.find(row => row.id === external.id)?.status,'scheduled');
});

test('exact grant scopes use the submitted conversation and retain takeover separately', async () => {
  const client = createDemoClient(); const row = client.snapshot().grants.find(row => row.conversation_id === 'chat_maya')!;
  const grant = await client.request<RecordEntity>('/automation/grants',{method:'PUT',body:{...row,expected_version:row.version,allowed_actions:['SEND_TEXT'],allowed_intents:['acknowledgement'],expires_at:future()}});
  assert.equal(grant.conversation_id,'chat_maya'); assert.equal(grant.version,2);
  await assert.rejects(client.request('/automation/grants',{method:'PUT',body:{...row,expected_version:1,expires_at:future()}}),reason('CONTEXT_STALE'));
  const neha = client.snapshot().grants.find(row => row.conversation_id === 'chat_neha')!;
  await assert.rejects(client.request('/automation/grants',{method:'PUT',body:{...neha,expected_version:1,expires_at:future()}}),reason('HUMAN_TAKEOVER'));
  assert.equal(client.snapshot().conversations.find(row => row.id === 'chat_neha')?.mode,'Auto');
});

test('sharing routes pin distinct audiences and preserve revision conflicts', async () => {
  const client = createDemoClient(); const body = {source_conversation_id:'chat_dev',destination_conversation_id:'chat_weekend',audience:'group',categories:['owner_selected_text'],expires_at:future()};
  const route = await client.request<RecordEntity>('/forward-routes',{method:'POST',body});
  assert.equal(route.destination_conversation_id,'chat_weekend');
  await assert.rejects(client.request('/forward-routes',{method:'POST',body:{...body,destination_conversation_id:'chat_dev'}}),reason('ROUTE_DENIED'));
  await assert.rejects(client.request(`/forward-routes/${route.id}`,{method:'PATCH',body:{enabled:false,expected_version:7}}),reason('CONTEXT_STALE'));
  assert.equal((await client.request<RecordEntity>(`/forward-routes/${route.id}`,{method:'PATCH',body:{enabled:false,expected_version:1}})).enabled,false);
});

test('memory correction rejects stale edits, forgetting preserves original messages', async () => {
  const client = createDemoClient(); const memory = client.snapshot().memories[0];
  await assert.rejects(client.request(`/memories/${memory.id}`,{method:'PATCH',body:{text:'Overwrite',expected_version:1}}),reason('CONTEXT_STALE'));
  const corrected = await client.request<RecordEntity>(`/memories/${memory.id}`,{method:'PATCH',body:{text:'Use warm, concise wording.',expected_version:2}});
  assert.equal(corrected.version,3); const count = client.snapshot().messages.length;
  await client.request(`/memories/${memory.id}`,{method:'DELETE'});
  assert.equal(client.snapshot().memories.length,0); assert.equal(client.snapshot().messages.length,count);
  const evidence = await client.request<{sources:unknown[]}>('/actions/demo-action-maya/evidence');
  assert.deepEqual(evidence.sources,[]);
});

test('voice corrections and budget edits follow expected versions', async () => {
  const client = createDemoClient();
  const style = await client.request<RecordEntity>('/conversations/chat_maya/style-profile',{method:'PATCH',body:{expected_version:1,owner_rules:['Keep mixed Marathi and English natural.'],reviewed:true}});
  assert.equal(style.version,2);
  assert.equal((style.features as Record<string,unknown>).owner_reviewed,true);
  assert.equal('reviewed' in style,false);
  await assert.rejects(client.request('/conversations/chat_maya/style-profile',{method:'PATCH',body:{expected_version:1,owner_rules:['Silently overwrite']}}),reason('CONTEXT_STALE'));
  assert.equal((await client.request<RecordEntity>('/workspaces/workspace_demo/budget',{method:'PUT',body:{expected_version:1,max_actions_per_day:0,max_tokens_per_day:null,max_cost_microusd_per_day:null}})).max_actions_per_day,0);
  await assert.rejects(client.request('/workspaces/workspace_demo/budget',{method:'PUT',body:{expected_version:1,max_actions_per_day:100}}),reason('CONTEXT_STALE'));
});

test('retention is inspectable, mutable, and cannot hide version conflicts', async () => {
  const client = createDemoClient(); const current = await client.request<RecordEntity>('/privacy/retention?workspace_id=workspace_demo');
  assert.equal(current.raw_days,30);
  const saved = await client.request<RecordEntity>('/privacy/retention',{method:'PUT',body:{workspace_id:'workspace_demo',expected_version:1,raw_days:14,derived_days:30,audit_days:90}});
  assert.equal(saved.raw_days,14); assert.equal(saved.version,2);
  await assert.rejects(client.request('/privacy/retention',{method:'PUT',body:{workspace_id:'workspace_demo',expected_version:1,raw_days:99,derived_days:30,audit_days:90}}),reason('CONTEXT_STALE'));
});

test('uncertain actions expose evidence but cannot be canceled or retried', async () => {
  const client = createDemoClient();
  assert.equal((await client.request<RecordEntity>('/actions/demo-action-uncertain')).status,'uncertain');
  await assert.rejects(client.request('/actions/demo-action-uncertain/cancel',{method:'POST'}),reason('CONTEXT_STALE'));
  await assert.rejects(client.request('/actions/demo-action-uncertain/dispatch',{method:'POST'}),reason('CAPABILITY_UNAVAILABLE'));
});

test('explicit owner draft approval simulates once and never trains as human', async () => {
  const client = createDemoClient(); const draft = await client.request<RecordEntity>('/conversations/chat_maya/owner-drafts',{method:'POST',body:{text:'An exact owner message'}});
  await assert.rejects(client.request(`/drafts/${draft.id}/approve`,{method:'POST',body:{content_hash:'wrong'}}),reason('CONTEXT_STALE'));
  await client.request(`/drafts/${draft.id}/approve`,{method:'POST',body:{content_hash:draft.content_hash}});
  const first = await client.request<RecordEntity>(`/drafts/${draft.id}/dispatch`,{method:'POST'});
  const again = await client.request<RecordEntity>(`/drafts/${draft.id}/dispatch`,{method:'POST'});
  assert.equal(first.id,again.id); assert.equal(first.transport,'simulation_only');
  assert.equal(client.snapshot().messages.filter(row => row.text === 'An exact owner message').length,1);
  assert.equal(client.snapshot().messages.find(row => row.text === 'An exact owner message')?.author_kind,'assistant');
});

test('account disconnect holds affected conversations and pairing remains unavailable', async () => {
  const client = createDemoClient(); const capabilities = await client.request<RecordEntity>('/connectors/conn_whatsapp/capabilities');
  assert.equal((capabilities.capabilities as Record<string,unknown>).pairing_code,'unsupported');
  await client.request('/connectors/conn_whatsapp',{method:'DELETE'});
  assert.ok(client.snapshot().conversations.every(row => row.control_state === 'RECONNECT_REVIEW'));
  await assert.rejects(client.request('/connectors/conn_whatsapp/pair',{method:'POST'}),reason('CAPABILITY_UNAVAILABLE'));
});

test('privacy export is scoped and deleting demo data cannot leave private previews', async () => {
  const client = createDemoClient();
  await assert.rejects(client.request('/data-export?workspace_id=somebody-else',{method:'POST'}),reason('SCOPE_DENIED'));
  const exported = await client.request<RecordEntity>('/data-export?workspace_id=workspace_demo',{method:'POST'});
  assert.equal(exported.simulation,true);
  await client.request('/account-data?workspace_id=workspace_demo',{method:'DELETE'});
  assert.equal(client.snapshot().messages.length,0); assert.equal(client.snapshot().contacts.length,0);
  assert.equal(client.snapshot().drafts.length,0);
  assert.ok(client.snapshot().conversations.every(row => row.preview === ''));
  assert.ok(client.snapshot().grants.every(row => row.enabled === false));
});

test('selected-chat purge clears pending private material and keeps other chats intact', async () => {
  const client = createDemoClient(); await client.request('/conversations/chat_maya/owner-drafts',{method:'POST',body:{text:'Private pending text'}});
  await client.request('/conversations/chat_maya/data',{method:'DELETE'});
  const data = client.snapshot();
  for (const rows of [data.messages,data.drafts,data.memories,data.styles,data.jobs,data.tasks]) assert.ok(rows.every(row => row.conversation_id !== 'chat_maya'));
  assert.ok(data.messages.some(row => row.conversation_id === 'chat_dev'));
  assert.equal(data.conversations.find(row => row.id === 'chat_maya')?.preview,'');
  assert.equal(data.grants.find(row => row.conversation_id === 'chat_maya')?.enabled,false);
  assert.deepEqual(data.actions.find(row => row.conversation_id === 'chat_maya')?.payload,{});
});

test('synthetic history preview is read-only and import retains exact chat history without sends', async () => {
  const client = createDemoClient(); const before = client.snapshot();
  const body = {conversation_id:'chat_maya',text:'07/10/2026, 09:00 - Maya: Shall we meet?\n07/10/2026, 09:01 - Hemant: Let me check.\nI will confirm later.',owner_sender_label:'Hemant',date_order:'DMY',timezone:'Asia/Kolkata'};
  const preview = await client.request<RecordEntity>('/imports/preview',{method:'POST',body});
  assert.equal(preview.record_count,2); assert.equal(preview.oldest,'2026-10-07T03:30:00.000Z');
  assert.equal(client.snapshot().messages.length,before.messages.length);
  await assert.rejects(client.request('/imports/preview',{method:'POST',body:{...body,owner_sender_label:'Not observed'}}),reason('INVALID_IMPORT'));
  const imported = await client.request<RecordEntity>('/imports',{method:'POST',body});
  const repeated = await client.request<RecordEntity>('/imports',{method:'POST',body});
  assert.equal(repeated.id,imported.id); assert.equal(repeated.replayed,true);
  const rows = client.snapshot().messages.filter(row => row.import_id === imported.id);
  assert.equal(rows.length,2); assert.ok(rows.every(row => row.conversation_id === 'chat_maya' && row.origin === 'history'));
  assert.equal(rows[1].text,'Let me check.\nI will confirm later.'); assert.equal(rows[1].author_kind,'human_owner');
  assert.equal(client.snapshot().actions.length,before.actions.length); assert.equal(client.snapshot().jobs.length,before.jobs.length);
});
