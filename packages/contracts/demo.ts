import type {MiloSnapshot, RecordEntity, Conversation} from './domain';
import type {ApiClient, RequestOptions} from './sdk';
import {ApiError} from './sdk';

export function createDemoSnapshot(): MiloSnapshot {
  const workspace = {id: 'workspace_demo', name: 'Your Milo', timezone: 'Asia/Kolkata', paused: false, pause_generation: 1};
  const connection = {id: 'conn_whatsapp', provider: 'mock', account_id: 'synthetic-owner', status: 'connected', label: 'WhatsApp · synthetic',
    capabilities: {send_text: 'supported', quoted_reply: 'supported', emoji_reaction: 'supported', native_forward: 'supported',
      phone_continuity: 'unknown', pairing_code: 'unsupported', phone_os_contact_write: 'unsupported'}};
  const rows = [
    ['maya', 'Maya', 'contact', 'AUTO_ENABLED', 'Auto', 'Thanks, that works for me!', '#EAE0FD'],
    ['dev', 'Dev', 'contact', 'AUTO_ENABLED', 'Auto', 'Sharing the plan for Saturday.', '#F6C7AA'],
    ['weekend', 'Weekend Plans', 'group', 'AUTO_ENABLED', 'Auto', 'Dev’s plan was forwarded here.', '#DDEFE6'],
    ['ravi', 'Ravi', 'contact', 'AUTO_ENABLED', 'Auto', 'What time should we meet?', '#FEEDD1'],
    ['neha', 'Neha', 'contact', 'HUMAN_TAKEOVER', 'Auto', 'You replied on your phone. Milo is waiting.', '#FBE4E7'],
    ['sana', 'Sana', 'contact', 'AUTO_ENABLED', 'Auto', 'A previous submission is uncertain.', '#EAE0FD'],
    ['mom', 'Mom', 'contact', 'DRAFT_MODE', 'draft', 'Can you call this evening?', '#F6C7AA'],
    ['team', 'Studio Team', 'group', 'READ_ONLY', 'read-only', 'Next chapter discussion on Sunday.', '#DDEFE6'],
  ];
  const conversations = rows.map(([id,title,kind,control_state,mode,preview,avatar_color], i) => ({id: `chat_${id}`,
    conversation_id: `chat_${id}`, title, kind, control_state, mode: mode as 'Auto'|'draft'|'read-only',
    connector_id: connection.id, provider_chat_id: `synthetic-${id}`, account_label: connection.label,
    revision: 4, preview, avatar_color, timestamp: `2026-10-06T0${8-i}:30:00Z`, unread: id === 'ravi' ? 1 : 0,
    pending_drafts: id === 'mom' ? 1 : 0, pending_tasks: id === 'neha' ? 1 : 0}));
  const messages = conversations.map((chat, i) => ({id: `demo-message-${i}`, conversation_id: chat.id, text: chat.preview,
    direction: 'inbound', author_kind: 'contact_human', provider_timestamp: chat.timestamp, revision: 1, origin: 'live'}));
  for (const [i, chat] of conversations.entries()) if (i !== 7) messages.push({id: `demo-human-${i}`, conversation_id: chat.id, text: i === 0 ? 'Sure :) see you at 8.' : 'Got it, thanks for the update.', direction: 'outbound', author_kind: 'human_owner', provider_timestamp: '2026-10-05T08:30:00Z', revision: 1, origin: 'history'});
  const actions = [
    {id: 'demo-action-maya', conversation_id: 'chat_maya', destination_conversation_id: 'chat_maya', kind: 'reply',
      status: 'accepted', text: 'That works for me, thanks!', recipient_id: 'synthetic-maya', reason_code: null,
      evidence_message_ids: ['demo-message-0'], payload_hash: 'synthetic-payload-hash', provider_message_id: 'mock:maya', payload: {text: 'That works for me, thanks!'}, transport: 'simulation_only', intent: 'acknowledgement', created_at: '2026-10-06T08:20:00Z'},
    {id: 'demo-action-forward', conversation_id: 'chat_dev', destination_conversation_id: 'chat_weekend', kind: 'forward',
      status: 'accepted', recipient_id: 'synthetic-weekend', target_message_id: 'demo-message-1',
      route_id: 'demo-route-weekend', native_record_id: 'synthetic-native-original', provider_message_id: 'mock:forward', payload: {target_message_id: 'demo-message-1', native_record_ref: 'synthetic-native-original', route_id: 'demo-route-weekend'}, evidence_message_ids: ['demo-message-1'], transport: 'simulation_only', intent: 'forwarding', created_at: '2026-10-06T08:21:00Z'},
    {id: 'demo-action-ravi', conversation_id: 'chat_ravi', destination_conversation_id: 'chat_ravi', kind: 'reply',
      status: 'blocked', text: '', reason_code: 'MISSING_FACTS', missing_facts: ['Your preferred meeting time'], payload: {text: ''}, transport: 'simulation_only', created_at: '2026-10-06T08:22:00Z'},
    {id: 'demo-action-uncertain', conversation_id: 'chat_sana', destination_conversation_id: 'chat_sana', kind: 'send',
      status: 'uncertain', reason_code: 'DELIVERY_UNCERTAIN', text: 'See you soon', recipient_id: 'synthetic-sana', payload: {text: 'See you soon'}, transport: 'simulation_only', created_at: '2026-10-06T08:23:00Z'},
  ];
  return {user: {id: 'owner_hemant', display_name: 'Hemant', email: 'demo@example.test'}, workspaces: [workspace], workspace,
    connections: [connection], conversations, messages, actions, drafts: [{id: 'demo-draft-mom', conversation_id: 'chat_mom',
      text: 'I’ll call you this evening. The grocery list is at example.com/list, and I’ll pay the ₹500 tomorrow.', status: 'needs_approval',
      content_hash: 'synthetic-exact-hash', missing_facts: ['Exact time'], evidence_message_ids: ['demo-message-6'],
      risk_flags: ['link', 'payment', 'commitment']}],
    tasks: [{id: 'demo-task-neha', workspace_id: workspace.id, conversation_id: 'chat_neha', title: 'Call Neha about the weekend',
      due_at: '2026-10-06T13:00:00Z', timezone: 'Asia/Kolkata', status: 'pending', version: 1}], jobs: [{id: 'demo-job-reminder', workspace_id: workspace.id, conversation_id: null, action_kind: 'REMINDER', purpose: 'An owner reminder', content: 'Review the weekend plan.', due_at: '2026-10-07T03:30:00Z', expires_at: '2026-10-08T03:30:00Z', timezone: 'Asia/Kolkata', recurrence: 'none', max_runs: 1, version: 1, status: 'scheduled', simulation: true}],
    memories: [{id: 'demo-memory-maya', conversation_id: 'chat_maya', text: 'Maya prefers brief, friendly replies.',
      status: 'confirmed', version: 2, source_message_ids: ['demo-message-0'], source_revision: {'demo-message-0': 1},
      expires_at: null, visibility: 'conversation', created_by: 'owner'}],
    styles: conversations.map((chat, i) => ({id: `demo-style-${i}`, conversation_id: chat.id, version: 1,
      sample_count: i === 7 ? 0 : 1, sufficiency: 'provisional', owner_rules: ['Keep replies clear and friendly'],
      features: {median_words: 9, greeting_rate: 0.25}, evidence_message_ids: i === 7 ? [] : [`demo-human-${i}`]})),
    grants: conversations.filter(chat => chat.mode === 'Auto').map(chat => ({id: `demo-grant-${chat.id}`, conversation_id: chat.id,
      enabled: true, version: 1, allowed_actions: ['SEND_TEXT', 'QUOTE', 'REACTION'], allowed_intents: ['acknowledgement', 'allowed_clarification'],
      max_outgoing_per_hour: 6, expires_at: '2026-10-20T13:00:00Z', quiet_start: '21:00', quiet_end: '09:00', timezone: 'Asia/Kolkata', reaction_palette: ['👍', '❤️'], forward_route_ids: [], max_trigger_age_seconds: 300, require_grounded_facts: true})),
    routes: [{id: 'demo-route-weekend', source_conversation_id: 'chat_dev', destination_conversation_id: 'chat_weekend',
      enabled: true, version: 1, audience: 'group', categories: ['owner_selected_text'], expires_at: '2026-10-20T13:00:00Z'}],
    contacts: [{id: 'demo-contact-maya', display_name: 'Maya', provider_identity: 'synthetic-maya', saved_destination: 'assistant_local', external_write: false}],
    activity: actions.map((action, i) => ({id: `demo-activity-${i}`, action: `${action.kind}.${action.status}`,
      resource_id: action.id, created_at: `2026-10-06T08:${20+i}:00Z`})),
    budget: {id: 'demo-budget', version: 1, max_actions_per_day: 50, max_tokens_per_day: 100000,
      max_cost_microusd_per_day: null, usage: {action_units: 4, token_units: 0, cost_microusd: 0}, window_timezone: 'UTC'},
    retention: {id: 'demo-retention', workspace_id: workspace.id, version: 1, raw_days: 30, derived_days: 90, audit_days: 90, backup_status: 'separate_lifecycle_not_verified'},
    generated_at: '2026-10-06T08:35:00Z', simulation: true};
}

export function createDemoData(_now?: Date | string) {
  const snapshot = createDemoSnapshot();
  const messages = Object.fromEntries(snapshot.conversations.map(chat => [chat.id,
    snapshot.messages.filter(message => message.conversation_id === chat.id)]));
  return {...snapshot, messages};
}

const string = (row: RecordEntity | Record<string, unknown>, key: string, fallback = '') => typeof row[key] === 'string' ? String(row[key]) : fallback;
const strings = (value: unknown): string[] => Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : [];
const copied = <T>(value: T): T => structuredClone(value);
const failed = (status: number, reason: string, message: string): never => { throw new ApiError(status, reason, message); };

/** The synthetic adapter resolves explicit wall time without using the device zone. */
function resolveTime(body: Record<string, unknown>) {
  const local = String(body.local_datetime || '');
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?$/.exec(local);
  if (!match) failed(422, 'INVALID_LOCAL_TIME', 'Use an exact local date and time.');
  const parts = match!.slice(1).map(value => Number(value || 0));
  const [year, month, day, hour, minute, second] = parts;
  const naive = Date.UTC(year, month - 1, day, hour, minute, second);
  const check = new Date(naive);
  if (check.getUTCFullYear() !== year || check.getUTCMonth() + 1 !== month || check.getUTCDate() !== day || hour > 23 || minute > 59 || second > 59) failed(422, 'INVALID_LOCAL_TIME', 'The local date or time is invalid.');
  const timezone = String(body.timezone || '');
  let formatter: Intl.DateTimeFormat;
  try { formatter = new Intl.DateTimeFormat('en-CA', {timeZone: timezone, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23'}); }
  catch { return failed(422, 'INVALID_TIMEZONE', 'Choose a valid IANA timezone.'); }
  const candidates: {instant: number; offset: number}[] = [];
  for (let offset = -840; offset <= 840; offset++) {
    const instant = naive - offset * 60000;
    const rendered = Object.fromEntries(formatter.formatToParts(instant).map(item => [item.type, item.value]));
    if (Number(rendered.year) === year && Number(rendered.month) === month && Number(rendered.day) === day && Number(rendered.hour) === hour && Number(rendered.minute) === minute && Number(rendered.second) === second) candidates.push({instant, offset});
  }
  candidates.sort((a,b) => a.instant - b.instant);
  if (!candidates.length) return failed(422, 'DST_GAP', 'This local time does not exist. Choose a different time.');
  const policy = String(body.ambiguity_policy || 'reject');
  if (candidates.length > 1 && policy === 'reject') return failed(422, 'DST_AMBIGUOUS', 'This time occurs twice. Choose an explicit earlier or later occurrence.');
  if (!['reject', 'earlier', 'later'].includes(policy)) return failed(422, 'INVALID_PROPOSAL', 'Unknown ambiguity policy.');
  const chosen = policy === 'later' ? candidates[candidates.length - 1] : candidates[0];
  const sign = chosen.offset < 0 ? '-' : '+'; const absolute = Math.abs(chosen.offset);
  return {due_at: new Date(chosen.instant).toISOString(), local_datetime: local, timezone, utc_offset: `${sign}${String(Math.floor(absolute / 60)).padStart(2, '0')}:${String(absolute % 60).padStart(2, '0')}`, simulation: true};
}

/** Isolated, explicit synthetic state. It never calls an external transport. */
export function createDemoClient(seed = createDemoSnapshot()): ApiClient & {snapshot(): MiloSnapshot} {
  const data = copied(seed);
  let sequence = 0;
  const suppressed = new Set<string>();
  const imports = new Map<string, RecordEntity>();
  const sessions: RecordEntity[] = [{id: 'demo-session-current', current: true, created_at: data.generated_at, expires_at: '2026-10-31T00:00:00Z', session_kind: 'synthetic_browser', label: 'This browser · synthetic'}];
  const id = (kind: string) => `demo-${kind}-${++sequence}`;
  const stamp = () => new Date().toISOString();
  const chatFor = (chatId: unknown): Conversation => data.conversations.find(row => row.id === chatId) ?? failed(404, 'SCOPE_DENIED', 'This conversation is unavailable in the demo workspace.');
  const find = (rows: RecordEntity[], rowId: string, label: string): RecordEntity => rows.find(row => row.id === rowId) ?? failed(404, 'SOURCE_MISSING', `${label} is no longer available.`);
  const cas = (row: RecordEntity | null | undefined, body: Record<string, unknown>, optional = false) => {
    if (optional && body.expected_version == null) return;
    if (body.expected_version !== Number(row?.version || 0)) failed(409, 'CONTEXT_STALE', 'This item changed. Refresh the current version before saving.');
  };
  const scopedSources = (chatId: string, sourceIds: unknown) => strings(sourceIds).map(sourceId => {
    const source = data.messages.find(row => row.id === sourceId && row.conversation_id === chatId && !suppressed.has(row.id));
    return source ?? failed(409, 'SOURCE_MISSING', 'A source is unavailable in this exact chat.');
  });
  const invalidate = (chatId: string, reason: string) => {
    for (const action of data.actions) if ((action.conversation_id === chatId || action.destination_conversation_id === chatId) && ['ready', 'held', 'prepared'].includes(String(action.status))) Object.assign(action, {status: 'canceled', reason_code: reason});
  };
  const audit = (name: string, row: RecordEntity) => data.activity.unshift({id: id('activity'), action: name, resource_id: row.id, created_at: stamp(), simulation: true});
  const pause = (paused: boolean) => {
    data.workspace.paused = paused; data.workspace.pause_generation++;
    for (const action of data.actions) if (paused && ['ready', 'prepared'].includes(String(action.status))) Object.assign(action, {status: 'canceled', reason_code: 'GLOBAL_PAUSE'});
    for (const job of data.jobs) {
      if (job.action_kind === 'REMINDER') continue;
      if (paused && ['scheduled', 'held'].includes(String(job.status)) && job.hold_reason !== 'OWNER_HOLD') Object.assign(job, {status: 'held', hold_reason: 'GLOBAL_PAUSE'});
      else if (!paused && job.status === 'held' && job.hold_reason === 'GLOBAL_PAUSE') {
        const expires = Date.parse(String(job.expires_at)); const due = Date.parse(String(job.due_at));
        Object.assign(job, {status: expires <= Date.now() || due + Number(job.max_lateness_seconds || 3600) * 1000 <= Date.now() ? 'expired' : 'scheduled', hold_reason: null});
      }
    }
    audit(paused ? 'workspace.paused' : 'workspace.resumed', data.workspace);
    return {paused, pause_generation: data.workspace.pause_generation, simulation: true};
  };
  return {snapshot: () => copied(data), async request<T>(rawPath: string, options: RequestOptions = {}): Promise<T> {
    const uri = new URL(rawPath.replace(/^\/v1(?=\/|$)/, ''), 'https://synthetic.invalid');
    const path = uri.pathname; const segments = path.split('/').filter(Boolean);
    const method = (options.method || 'GET').toUpperCase();
    const body = (options.body && typeof options.body === 'object' && !Array.isArray(options.body) ? options.body : {}) as Record<string, unknown>;
    const workspaceId = body.workspace_id || uri.searchParams.get('workspace_id');
    if (workspaceId && workspaceId !== data.workspace.id) failed(404, 'SCOPE_DENIED', 'The requested workspace is unavailable.');
    if (segments[0] === 'workspaces' && segments[1] !== data.workspace.id) failed(404, 'SCOPE_DENIED', 'The requested workspace is unavailable.');
    let result: unknown;
    if (path === '/ui/bootstrap') result = data;
    else if (path === '/me') result = data.user;
    else if (path === '/auth/config') result = {google_configured: false, native_google_configured: false, simulation: true};
    else if (path === '/auth/csrf') result = {csrf_token: 'synthetic-session-only'};
    else if (path === '/auth/sessions' && method === 'GET') result = {sessions};
    else if (path.startsWith('/auth/sessions/') && method === 'DELETE') { const index = sessions.findIndex(row => row.id === segments[2]); if (index < 0) failed(404, 'SOURCE_MISSING', 'This app session is no longer available.'); sessions.splice(index, 1); result = {revoked: true, simulation: true}; }
    else if (['/auth/logout', '/auth/native/logout'].includes(path)) result = {revoked: true, simulation: true};
    else if (path === '/schedules/resolve-time') result = resolveTime(body);
    else if (path === '/assistant/commands') {
      if (body.command === 'pause' || body.command === 'resume') {
        if (body.conversation_id) {
          const chat = chatFor(body.conversation_id); chat.control_state = body.command === 'pause' ? 'HUMAN_TAKEOVER' : 'DRAFT_MODE'; chat.revision++;
          invalidate(chat.id, 'HUMAN_TAKEOVER'); result = {command: body.command, result: {conversation_id: chat.id, control_state: chat.control_state}};
        } else result = {command: body.command, result: pause(body.command === 'pause')};
      } else if (body.command === 'write_with_me') {
        const chat = chatFor(body.conversation_id); const draft: RecordEntity = {id: id('draft'), conversation_id: chat.id, text: 'Could you share a little more detail?', status: 'needs_approval', content_hash: id('hash'), missing_facts: ['Owner intent'], evidence_message_ids: [], simulation: true};
        data.drafts.unshift(draft); result = {command: body.command, result: draft};
      } else if (body.command === 'teach_me') {
        const chat = chatFor(body.conversation_id); scopedSources(chat.id, body.source_message_ids);
        if (!strings(body.source_message_ids).length) failed(422, 'SOURCE_MISSING', 'Choose a permitted source for the memory.');
        const memory: RecordEntity = {id: id('memory'), conversation_id: chat.id, text: String(body.text || ''), status: body.status || 'candidate', version: 1, source_message_ids: strings(body.source_message_ids), simulation: true};
        data.memories.unshift(memory); result = {command: body.command, result: memory};
      } else if (body.command === 'catch_me_up') result = {command: 'catch_me_up', result: {conversations: body.conversation_id ? [chatFor(body.conversation_id)] : data.conversations, summary_kind: 'synthetic_source_preview', generated_by_model: false}};
      else failed(422, 'INVALID_PROPOSAL', 'Choose a supported assistant command.');
    } else if (['/pause-all', '/control/pause'].includes(path)) result = pause(true);
    else if (['/resume-all', '/control/resume'].includes(path)) result = pause(false);
    else if (path === '/conversations' && method === 'GET') result = data.conversations;
    else if (segments[0] === 'conversations') {
      const chat = chatFor(segments[1]);
      if (segments[2] === 'messages' && method === 'GET') result = data.messages.filter(row => row.conversation_id === chat.id);
      else if (segments[2] === 'style-profile') {
        let profile = data.styles.find(row => row.conversation_id === chat.id);
        if (method === 'GET') result = profile || {id: '', conversation_id: chat.id, version: 0, sample_count: 0, sufficiency: 'provisional', owner_rules: []};
        else { cas(profile, body, true); if (!profile) { profile = {id: id('style'), conversation_id: chat.id, version: 0, sample_count: 0, sufficiency: 'provisional'}; data.styles.push(profile); } const {expected_version: _, reviewed, ...changes} = body; Object.assign(profile, changes, {version: Number(profile.version || 0) + 1, features: {...(profile.features as Record<string, unknown> || {}), ...(typeof reviewed === 'boolean' ? {owner_reviewed: reviewed} : {})}}); invalidate(chat.id, 'CONTEXT_STALE'); audit('style.corrected', profile); result = profile; }
      } else if (segments[2] === 'resume' || segments[2] === 'takeover') { chat.control_state = segments[2] === 'takeover' ? 'HUMAN_TAKEOVER' : 'DRAFT_MODE'; chat.revision++; invalidate(chat.id, 'HUMAN_TAKEOVER'); result = chat; }
      else if (segments[2] === 'permissions') {
        if (method === 'PUT') { const {expected_version: _, ...permissions} = body; chat.permissions = {...permissions, version: Number((chat.permissions as Record<string, unknown> | undefined)?.version || 0) + 1}; chat.control_state = body.draft ? 'DRAFT_MODE' : body.read ? 'READ_ONLY' : 'AI_OFF'; chat.mode = body.draft ? 'draft' : body.read ? 'read-only' : 'disabled'; chat.revision++; invalidate(chat.id, 'SCOPE_DENIED'); }
        result = chat.permissions || {read: true, retain: true, learn: true, draft: true, send: true, share: true, version: 1};
      } else if (['owner-drafts', 'drafts'].includes(segments[2]) && method === 'POST') {
        if (data.workspace.paused || chat.control_state === 'HUMAN_TAKEOVER') failed(409, 'HUMAN_TAKEOVER', 'Resume the selected conversation before preparing an outward draft.');
        const content = String(body.text || 'Could you share a little more detail?').trim(); if (!content) failed(422, 'INVALID_PROPOSAL', 'Write a message for this exact recipient.');
        const draft: RecordEntity = {id: id('draft'), conversation_id: chat.id, recipient_id: chat.provider_chat_id, text: content, status: 'needs_approval', content_hash: id('hash'), evidence_message_ids: [], missing_facts: [], simulation: true}; data.drafts.unshift(draft); result = draft;
      } else if (segments[2] === 'data' && method === 'DELETE') {
        for (const key of imports.keys()) if (JSON.parse(key)[0] === chat.id) imports.delete(key);
        data.messages = data.messages.filter(row => row.conversation_id !== chat.id); data.drafts = data.drafts.filter(row => row.conversation_id !== chat.id); data.memories = data.memories.filter(row => row.conversation_id !== chat.id); data.styles = data.styles.filter(row => row.conversation_id !== chat.id); data.jobs = data.jobs.filter(row => row.conversation_id !== chat.id); data.tasks = data.tasks.filter(row => row.conversation_id !== chat.id);
        invalidate(chat.id, 'SCOPE_DENIED');
        for (const action of data.actions) if (action.conversation_id === chat.id || action.destination_conversation_id === chat.id) { action.payload = {}; action.text = ''; action.evidence_message_ids = []; }
        for (const grant of data.grants) if (grant.conversation_id === chat.id) grant.enabled = false;
        for (const route of data.routes) if (route.source_conversation_id === chat.id || route.destination_conversation_id === chat.id) route.enabled = false;
        chat.preview = ''; chat.control_state = 'AI_OFF'; chat.mode = 'disabled'; result = {status:'completed',simulation:true};
      }
      else if (segments[2] === 'memories' && method === 'POST') { scopedSources(chat.id, body.source_message_ids); const memory = {id: id('memory'), ...body, conversation_id: chat.id, version: 1}; data.memories.unshift(memory); result = memory; }
      else failed(409, 'CAPABILITY_UNAVAILABLE', 'This synthetic conversation operation is unavailable.');
    } else if (segments[0] === 'drafts') {
      const draft = find(data.drafts, segments[1], 'Draft'); const chat = chatFor(draft.conversation_id);
      if (method === 'GET') result = draft;
      else if (segments[2] === 'reject') { draft.status = 'rejected'; result = draft; }
      else if (segments[2] === 'approve') { if (body.content_hash !== draft.content_hash) failed(409, 'CONTEXT_STALE', 'Exact draft content changed.'); draft.status = 'approved'; result = draft; }
      else if (segments[2] === 'dispatch') {
        const existing = data.actions.find(row => row.draft_id === draft.id); if (existing) result = existing;
        else { if (draft.status !== 'approved' || data.workspace.paused || chat.control_state === 'HUMAN_TAKEOVER') failed(409, 'CONTEXT_STALE', 'Current permission and exact draft approval are required.'); const action: RecordEntity = {id: id('action'), draft_id: draft.id, conversation_id: chat.id, destination_conversation_id: chat.id, recipient_id: chat.provider_chat_id, kind: 'SEND_TEXT', intent: 'owner_authored', payload: {text: draft.text}, status: 'accepted', transport: 'simulation_only', created_at: stamp(), simulation: true}; data.actions.unshift(action); draft.status = 'accepted'; data.messages.push({id: id('message'), conversation_id: chat.id, text: String(draft.text), direction: 'outbound', author_kind: 'assistant', provider_timestamp: stamp(), revision: 1, origin: 'live'}); audit('action.accepted.synthetic', action); result = action; }
      } else if (method === 'PATCH') { draft.text = String(body.text || ''); draft.content_hash = id('hash'); draft.status = 'needs_approval'; delete draft.risk_flags; result = draft; } // Only the server screens edited text.
      else failed(409, 'CAPABILITY_UNAVAILABLE', 'This draft operation is unavailable.');
    } else if (path === '/memories' && method === 'GET') result = data.memories;
    else if (segments[0] === 'memories') {
      const row = find(data.memories, segments[1], 'Memory');
      if (method === 'DELETE') { data.memories = data.memories.filter(item => item.id !== row.id); for (const source of strings(row.source_message_ids)) suppressed.add(source); invalidate(String(row.conversation_id), 'SOURCE_MISSING'); audit('memory.forgotten', row); result = null; }
      else if (method === 'GET') result = row;
      else { cas(row, body, true); scopedSources(String(row.conversation_id), body.source_message_ids || row.source_message_ids); const {expected_version: _, ...changes} = body; Object.assign(row, changes, {version: Number(row.version || 0) + 1}); invalidate(String(row.conversation_id), 'CONTEXT_STALE'); audit('memory.corrected', row); result = row; }
    } else if (path === '/actions' && method === 'GET') result = data.actions;
    else if (segments[0] === 'actions') {
      const row = find(data.actions, segments[1], 'Action');
      if (segments[2] === 'cancel') { if (!['ready', 'prepared', 'held'].includes(String(row.status))) failed(409, 'CONTEXT_STALE', 'The action already started and cannot be canceled.'); row.status = 'canceled'; row.reason_code = 'OWNER_CANCELED'; audit('action.canceled', row); result = row; }
      else if (segments[2] === 'evidence') result = {action_id: row.id, sources: data.messages.filter(message => strings(row.evidence_message_ids).includes(message.id) && !suppressed.has(message.id)).map(message => ({message_id: message.id, revision: message.revision, text: message.text}))};
      else if (method === 'GET') result = row;
      else failed(409, 'CAPABILITY_UNAVAILABLE', 'The synthetic activity adapter cannot retry or dispatch an action.');
    } else if (path === '/jobs') {
      if (method === 'GET') result = data.jobs;
      else {
        const key = String(body.idempotency_key || options.idempotencyKey || ''); if (key.length < 8) failed(422, 'INVALID_PROPOSAL', 'A stable idempotency key is required.');
        const existing = data.jobs.find(row => row.idempotency_key === key);
        if (existing) { if (existing.request_body !== JSON.stringify(body)) failed(409, 'CONTEXT_STALE', 'This idempotency key already belongs to another schedule.'); result = existing; }
        else {
          const kind = String(body.action_kind); const due = Date.parse(String(body.due_at)); const expiry = Date.parse(String(body.expires_at));
          if (!Number.isFinite(due) || due <= Date.now() || !Number.isFinite(expiry) || expiry <= due || expiry > Date.now() + 90 * 86400000) failed(422, 'EXPIRED', 'Choose a future due time and a bounded expiry after it.');
          if (!['REMINDER', 'SEND_TEXT', 'QUOTE', 'REACTION', 'FORWARD'].includes(kind)) failed(422, 'INVALID_PROPOSAL', 'Choose a supported action kind.');
          if (kind === 'REMINDER') { if (body.conversation_id || body.target_message_id || body.route_id) failed(422, 'SCOPE_DENIED', 'An owner reminder has no external recipient.'); }
          else { const chat = chatFor(body.conversation_id); scopedSources(chat.id, body.evidence_message_ids); if (body.target_message_id) scopedSources(chat.id, [body.target_message_id]); }
          if (!String(body.purpose || '').trim() || (['REMINDER', 'SEND_TEXT', 'QUOTE'].includes(kind) && !String(body.content || '').trim())) failed(422, 'INVALID_PROPOSAL', 'Purpose and exact content are required.');
          const job: RecordEntity = {id: id('job'), ...body, status: data.workspace.paused && kind !== 'REMINDER' ? 'held' : 'scheduled', hold_reason: data.workspace.paused && kind !== 'REMINDER' ? 'GLOBAL_PAUSE' : null, version: 1, max_lateness_seconds: body.max_lateness_seconds || 3600, request_body: JSON.stringify(body), simulation: true}; data.jobs.unshift(job); audit('job.scheduled.synthetic', job); result = job;
        }
      }
    } else if (segments[0] === 'jobs') {
      const row = find(data.jobs, segments[1], 'Scheduled action');
      if (segments[2] === 'runs') result = [];
      else if (method === 'GET') result = row;
      else { cas(row, body); const operation = String(body.operation); if (!['cancel', 'hold', 'resume'].includes(operation)) failed(422, 'INVALID_PROPOSAL', 'Unknown schedule control.'); if (['accepted', 'uncertain', 'completed', 'expired', 'canceled'].includes(String(row.status))) failed(409, 'CONTEXT_STALE', 'This schedule has reached a terminal or uncertain state.'); if (operation === 'resume' && (Date.parse(String(row.expires_at)) <= Date.now() || data.workspace.paused)) failed(409, 'EXPIRED', 'Resume requires a current, unpaused schedule.'); Object.assign(row, {status: operation === 'cancel' ? 'canceled' : operation === 'hold' ? 'held' : 'scheduled', hold_reason: operation === 'hold' ? 'OWNER_HOLD' : null, version: Number(row.version || 0) + 1}); audit(`job.${operation}`, row); result = row; }
    } else if (path === '/automation/grants') {
      if (method === 'GET') result = data.grants;
      else { const chat = chatFor(body.conversation_id); let row = data.grants.find(item => item.conversation_id === chat.id); cas(row, body, !row && body.expected_version == null); const allowed = strings(body.allowed_actions); if (!allowed.length || allowed.some(kind => !['SEND_TEXT', 'QUOTE', 'REACTION', 'FORWARD'].includes(kind))) failed(422, 'INVALID_PROPOSAL', 'Choose supported action families.'); if (body.enabled && chat.control_state === 'HUMAN_TAKEOVER') failed(409, 'HUMAN_TAKEOVER', 'Resume this chat explicitly before enabling Auto.'); if (allowed.includes('FORWARD')) for (const routeId of strings(body.forward_route_ids)) { const route = find(data.routes, routeId, 'Sharing route'); if (route.source_conversation_id !== chat.id || route.enabled !== true) failed(403, 'ROUTE_DENIED', 'The route does not authorize this source chat.'); } if (allowed.includes('FORWARD') && !strings(body.forward_route_ids).length) failed(422, 'ROUTE_DENIED', 'Choose a precise forwarding route.'); const expiry = Date.parse(String(body.expires_at)); if (!Number.isFinite(expiry) || expiry <= Date.now() || expiry > Date.now() + 30 * 86400000 + 1000) failed(422, 'EXPIRED', 'Auto grants need an expiry within 30 days.'); if (!row) { row = {id: id('grant'), conversation_id: chat.id, version: 0}; data.grants.push(row); } const {expected_version: _, ...changes} = body; Object.assign(row, changes, {version: Number(row.version || 0) + 1, connector_id: chat.connector_id, mode: 'AUTO'}); chat.mode = body.enabled ? 'Auto' : 'draft'; if (chat.control_state !== 'HUMAN_TAKEOVER') chat.control_state = body.enabled ? 'AUTO_ENABLED' : 'DRAFT_MODE'; invalidate(chat.id, 'POLICY_CHANGED'); audit('automation.grant_saved', row); result = row; }
    } else if (segments[0] === 'automation' && segments[1] === 'grants' && method === 'DELETE') { const row = find(data.grants, segments[2], 'Auto grant'); row.enabled = false; row.version = Number(row.version || 0) + 1; const chat = chatFor(row.conversation_id); if (chat.control_state === 'AUTO_ENABLED') chat.control_state = 'DRAFT_MODE'; chat.mode = 'draft'; invalidate(chat.id, 'POLICY_CHANGED'); audit('automation.grant_revoked', row); result = row; }
    else if (path === '/forward-routes') {
      if (method === 'GET') result = data.routes;
      else { const source = chatFor(body.source_conversation_id); const destination = chatFor(body.destination_conversation_id); if (source.id === destination.id || source.connector_id !== destination.connector_id || destination.kind !== body.audience) failed(403, 'ROUTE_DENIED', 'Choose exact distinct source and destination in the same account.'); const route = {id: id('route'), ...body, connector_id: source.connector_id, enabled: true, version: 1, simulation: true}; data.routes.unshift(route); audit('forward_route.granted', route); result = route; }
    } else if (segments[0] === 'forward-routes') { const route = find(data.routes, segments[1], 'Sharing route'); cas(route, body); route.enabled = body.enabled === true; route.version = Number(route.version || 0) + 1; invalidate(String(route.source_conversation_id), 'ROUTE_DENIED'); audit('forward_route.changed', route); result = route; }
    else if (path === '/connectors' || path === '/connections') result = data.connections;
    else if (segments[0] === 'connectors' || segments[0] === 'connections') { const connection = data.connections.find(row => row.id === segments[1]) ?? failed(404, 'SCOPE_DENIED', 'The account is unavailable.'); if (method === 'DELETE') { connection.status = 'disconnected'; for (const chat of data.conversations.filter(row => row.connector_id === connection.id)) { chat.control_state = 'RECONNECT_REVIEW'; invalidate(chat.id, 'CAPABILITY_UNAVAILABLE'); } audit('connection.disconnected.synthetic', connection); result = {status: 'disconnected', provider_revocation: 'not_requested', simulation: true}; } else if (segments[2] === 'capabilities') result = {connector_id: connection.id, adapter_version: 'synthetic-v1', capabilities: connection.capabilities, evidence_date: data.generated_at, test_reference: 'synthetic_fixture'}; else if (method === 'GET') result = connection; else failed(409, 'CAPABILITY_UNAVAILABLE', 'Personal pairing and provider verification are unavailable in the demo.'); }
    else if (path === '/imports' || path === '/imports/preview') {
      const chat = chatFor(body.conversation_id); const content = String(body.text || ''); const owner = String(body.owner_sender_label || '').trim();
      if (!content.trim() || new TextEncoder().encode(content).length > 2000000) failed(422, 'INVALID_IMPORT', 'Choose a text export up to 2 MB.');
      if (!owner) failed(422, 'INVALID_IMPORT', 'Specify the exact owner label.');
      if (!['DMY','MDY','YMD'].includes(String(body.date_order))) failed(422, 'INVALID_IMPORT', 'Choose the export date order explicitly.');
      const rows: {sender:string;text:string;timestamp:string}[] = [];
      for (const line of content.replaceAll('\u200e','').split(/\r?\n/)) {
        const header = /^\[?(\d{1,4})[/.\-](\d{1,2})[/.\-](\d{1,4}),?\s+(\d{1,2}):(\d{2})(?::(\d{2}))?\s*(AM|PM)?\]?\s*(?:-\s*)?([^:]+):\s?(.*)$/i.exec(line);
        if (!header) { if (rows.length && line.trim()) rows[rows.length - 1].text += '\n' + line; continue; }
        if (rows.length >= 500) failed(422, 'INVALID_IMPORT', 'This synthetic parser accepts up to 500 records. Real imports use the backend parser.');
        const values = header.slice(1,4).map(Number); const [day,month,rawYear] = body.date_order === 'DMY' ? values : body.date_order === 'MDY' ? [values[1],values[0],values[2]] : [values[2],values[1],values[0]];
        const year = rawYear < 100 ? 2000 + rawYear : rawYear; let hour = Number(header[4]);
        if (header[7]) { if (hour < 1 || hour > 12) failed(422,'INVALID_IMPORT','The export contains an invalid 12-hour clock.'); hour = hour % 12 + (header[7].toUpperCase() === 'PM' ? 12 : 0); }
        const local = [year,String(month).padStart(2,'0'),String(day).padStart(2,'0')].join('-') + 'T' + [String(hour).padStart(2,'0'),header[5],header[6] || '00'].join(':');
        const resolved = resolveTime({local_datetime:local,timezone:body.timezone,ambiguity_policy:'reject'});
        rows.push({sender:header[8].trim(),text:header[9],timestamp:resolved.due_at});
      }
      if (!rows.length) failed(422, 'INVALID_IMPORT', 'No supported export message records were detected.');
      if (!rows.some(row => row.sender === owner)) failed(422, 'INVALID_IMPORT', 'The exact owner label must match an observed sender.');
      const key = JSON.stringify([chat.id,content,owner,body.date_order,body.timezone]); const existing = imports.get(key);
      if (path === '/imports' && existing) result = {...existing,replayed:true};
      else {
        const timestamps = rows.map(row => row.timestamp).sort();
        const preview: RecordEntity = {id:id('import'),record_count:rows.length,message_count:rows.length,status:path === '/imports/preview' ? 'preview' : 'completed',oldest:timestamps[0],newest:timestamps.at(-1),senders:[...new Set(rows.map(row => row.sender))],warnings:['Synthetic parsing of supported text exports. Real imports use the validated backend parser.'],history_completeness:'unknown',automatic_reply:false,simulation:true,replayed:false};
        if (path === '/imports') {
          data.messages.push(...rows.map(row => ({id:id('history-message'),conversation_id:chat.id,text:row.text,direction:row.sender === owner ? 'outbound' : 'inbound',author_kind:row.sender === owner ? 'human_owner' : 'contact_human',provider_timestamp:row.timestamp,revision:1,origin:'history',import_id:preview.id,simulation:true})));
          imports.set(key,preview); audit('history.imported.synthetic',preview);
        }
        result = preview;
      }
    }
    else if (path === '/tasks' && method === 'POST') { const row = {id: id('task'), ...body, status: 'pending', version: 1}; data.tasks.push(row); audit('task.created', row); result = row; }
    else if (segments[0] === 'tasks' && method === 'PATCH') { const row = find(data.tasks, segments[1], 'Task'); cas(row, body); const {expected_version: _, ...changes} = body; Object.assign(row, changes, {version: Number(row.version || 0) + 1}); result = row; }
    else if (path === '/contacts' || path === '/people') { if (method === 'GET') result = data.contacts; else { if (body.destination && body.destination !== 'assistant_local') failed(409, 'CAPABILITY_UNAVAILABLE', 'Phone, Google, and WhatsApp contact writes are unavailable.'); const chat = chatFor(body.conversation_id); const source = scopedSources(chat.id, [body.source_message_id])[0]; if (!source) failed(409, 'SOURCE_MISSING', 'Choose a verified source sender.'); const row = {id: id('contact'), ...body, provider_identity: chat.provider_chat_id, display_name: body.display_name || chat.title, saved_destination: 'assistant_local', external_write: false, simulation: true}; data.contacts.push(row); audit('contact.saved.assistant_local', row); result = row; } }
    else if (path === '/privacy/retention') { if (method === 'GET') result = data.retention; else { cas(data.retention, body, true); for (const key of ['raw_days', 'derived_days', 'audit_days']) if (!Number.isInteger(body[key]) || Number(body[key]) < 1 || Number(body[key]) > 3650) failed(422, 'INVALID_PROPOSAL', 'Retention durations must be 1–3650 days.'); const {expected_version: _, ...changes} = body; data.retention = {id: data.retention?.id || id('retention'), ...changes, version: Number(data.retention?.version || 0) + 1, backup_status: 'separate_lifecycle_not_verified', simulation: true}; audit('retention.changed', data.retention); result = data.retention; } }
    else if (path.endsWith('/budget')) { if (method === 'GET') result = data.budget; else { cas(data.budget, body); const {expected_version: _, ...changes} = body; if (Object.values(changes).some(value => value !== null && (!Number.isInteger(value) || Number(value) < 0))) failed(422, 'INVALID_PROPOSAL', 'Budgets must be nonnegative integer units or unset.'); data.budget = {...(data.budget || {id: id('budget')}), ...changes, version: Number(data.budget?.version || 0) + 1}; audit('budget.changed', data.budget); result = data.budget; } }
    else if (path.endsWith('/usage')) result = {entries: [], simulation: true};
    else if (path === '/data-export') result = {workspace_id: data.workspace.id, conversations: data.conversations.map(chat => ({conversation: chat, messages: data.messages.filter(message => message.conversation_id === chat.id), memories: data.memories.filter(memory => memory.conversation_id === chat.id)})), local_reminders: data.jobs.filter(job => job.action_kind === 'REMINDER'), format_version: 2, simulation: true};
    else if (path === '/account-data' && method === 'DELETE') { pause(true); data.messages = []; data.drafts = []; data.memories = []; data.styles = []; data.contacts = []; data.jobs = []; data.tasks = []; imports.clear(); for (const action of data.actions) { action.payload = {}; action.text = ''; action.evidence_message_ids = []; } for (const chat of data.conversations) { chat.preview = ''; chat.control_state = 'AI_OFF'; } for (const connection of data.connections) connection.status = 'disconnected'; for (const grant of data.grants) grant.enabled = false; for (const route of data.routes) route.enabled = false; result = {status: 'completed', scope: 'synthetic_application_content', provider_revocation: 'not_requested', simulation: true}; }
    else if (path === '/activity') result = data.activity;
    else if (path === '/assistant/digest') result = {conversations: data.conversations, generated_at: data.generated_at, generated_by_model: false, simulation: true};
    else failed(409, 'CAPABILITY_UNAVAILABLE', 'This synthetic operation has no implemented adapter. No external action occurred.');
    if (method !== 'GET') data.generated_at = stamp();
    return copied(result) as T;
  }};
}
