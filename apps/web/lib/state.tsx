'use client';

import { Fragment, createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { ApiError, createClient, createDemoClient, createDemoSnapshot, isOwnerAnswer, ownerAnswerIsCurrent, ownerAnswerExpiresAt, type MiloSnapshot } from '@milo/contracts';
import type { Connection, Conversation, DataRecord, Message, MiloActions, MiloData, MiloState } from './types';

type Session = { state: MiloState; actions: MiloActions; messages: Record<string, Message[]>;
  authorizedSnapshot: MiloSnapshot | null;
  authenticate(credential: string, nonce: string): Promise<void>; authenticateWithAccessCode(code: string): Promise<void>; backendConfigured: boolean;
  browserSessionPresent: boolean; browserSessionChecking: boolean; resumeBrowserSession(): Promise<void>;
  composers: Record<string, string>; setComposer(id: string, text: string): void };
const Context = createContext<Session | null>(null);

function seedDemo(): MiloSnapshot {
  const snapshot = createDemoSnapshot();
  const today = new Date();
  const base = Date.parse('2026-10-06T00:00:00Z');
  const midnight = Date.UTC(today.getUTCFullYear(), today.getUTCMonth(), today.getUTCDate());
  const shift = (value: unknown): unknown => {
    if (typeof value === 'string' && /^2026-10-\d\dT/.test(value)) return new Date(Date.parse(value) + midnight - base).toISOString();
    if (Array.isArray(value)) return value.map(shift);
    if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, shift(item)]));
    return value;
  };
  const data = shift(snapshot) as MiloSnapshot;
  const mayaLatest = data.messages.find(message => message.conversation_id === 'chat_maya');
  if (mayaLatest) { mayaLatest.text = 'Same little place?'; mayaLatest.provider_timestamp = new Date(midnight + 4.9 * 3600000).toISOString(); }
  data.actions[0].text = '8 works :) see you there';
  data.messages.push({ id: 'demo-message-maya-reply', conversation_id: 'chat_maya', text: '8 works :) see you there',
    direction: 'outbound', author_kind: 'assistant', provider_timestamp: new Date(midnight + 5 * 3600000 + 11 * 60000).toISOString(), revision: 1 });
  data.messages.unshift({ id: 'demo-message-maya-dinner', conversation_id: 'chat_maya', text: 'Dinner at 8? Same place as last time 🍝',
    direction: 'inbound', author_kind: 'contact_human', provider_timestamp: new Date(midnight + 5 * 3600000).toISOString(), revision: 1 });
  data.tasks.push({ id: 'demo-launch-reminder', title: 'Check the launch copy.', due_at: new Date(midnight + 86400000 + 3.5 * 3600000).toISOString(), timezone: 'Asia/Kolkata',
    status: 'pending', version: 1, conversation_id: null, workspace_id: data.workspace.id });
  data.generated_at = today.toISOString();
  return data;
}

function normalize(snapshot: MiloSnapshot, mode: 'demo' | 'live'): { state: MiloState; messages: Record<string, Message[]> } {
  const workspace = snapshot.workspace ?? { id: '', timezone: 'Asia/Kolkata', paused: false };
  const pagination = (snapshot as MiloSnapshot & { pagination?: { conversation_next_cursor: string | null; has_more_conversations: boolean } }).pagination;
  const messages: Record<string, Message[]> = {};
  for (const message of snapshot.messages ?? []) (messages[message.conversation_id] ??= []).push(message);
  const data: MiloData = {
    conversations: snapshot.conversations.map((chat, i) => ({ ...chat, conversation_id: chat.id,
      account_label: chat.account_label ?? snapshot.connections.find(c => c.id === chat.connector_id)?.label ?? 'WhatsApp',
      preview: chat.preview ?? '', timestamp: chat.timestamp ?? '', unread: chat.unread ?? 0,
      pending_drafts: chat.pending_drafts ?? 0, pending_tasks: chat.pending_tasks ?? 0,
      avatar_color: chat.avatar_color ?? ['#EAE0FD', '#F6C7AA', '#DDEFE6'][i % 3] })) as Conversation[],
    connections: snapshot.connections.map(connection => ({ ...connection, label: connection.label ?? connection.account_id })) as Connection[],
    actions: snapshot.actions ?? [], jobs: snapshot.jobs ?? [], memories: snapshot.memories ?? [], styles: snapshot.styles ?? [],
    grants: snapshot.grants ?? [], routes: snapshot.routes ?? [], contacts: snapshot.contacts ?? [], budget: snapshot.budget,
    activity: snapshot.activity ?? [], tasks: snapshot.tasks ?? [], drafts: snapshot.drafts ?? [],
  };
  return { state: { mode, workspaceId: workspace.id, selectedConversationId: null, loading: false, error: null,
    paused: workspace.paused, pausePending: false, online: true, user: snapshot.user, data, lastUpdated: snapshot.generated_at, timezone: workspace.timezone,
    authorizationVersion: (snapshot as MiloSnapshot & { snapshot_version?: string }).snapshot_version,
    nextCursor: pagination?.conversation_next_cursor ?? null, hasMore: pagination?.has_more_conversations ?? false },
    messages };
}

export function MiloProvider({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const demo = useRef<ReturnType<typeof createDemoClient> | null>(null);
  if (!demo.current) demo.current = createDemoClient(seedDemo());
  const initial = useRef(normalize(demo.current.snapshot(), 'demo'));
  const [state, setState] = useState<MiloState>(initial.current.state);
  const [messages, setMessages] = useState(initial.current.messages);
  const [authorizedSnapshot, setAuthorizedSnapshot] = useState<MiloSnapshot | null>(null);
  const [backendConfigured, setBackendConfigured] = useState(false);
  const [browserSessionPresent, setBrowserSessionPresent] = useState(false);
  const [browserSessionChecking, setBrowserSessionChecking] = useState(true);
  const [composers, setComposers] = useState<Record<string, string>>({});
  const epoch = useRef(0);
  const liveSessionEnabled = useRef(false);
  const snapshotSequence = useRef(0);
  const loadingCursor = useRef<string | null>(null);
  const answerReadScope = useRef<{ conversationId: string; connectorId: string; memoryIds: string[]; expiresAt: number } | null>(null);
  const answerScopeGeneration = useRef(0);
  const clearOwnerAnswerScope = useCallback(() => { answerReadScope.current = null; answerScopeGeneration.current++; }, []);
  const stateRef = useRef(state); stateRef.current = state;
  const csrf = useRef('');
  const client = useMemo(() => createClient({ baseUrl: '/api', headers: async () => ({ 'X-CSRF-Token': csrf.current }),
    onUnauthorized: () => { if (stateRef.current.mode === 'live' || liveSessionEnabled.current) { liveSessionEnabled.current = false; setBrowserSessionPresent(false); setBrowserSessionChecking(false); epoch.current++; snapshotSequence.current++; setComposers({}); setState(previous => ({ ...initial.current.state, online: previous.online,
      error: 'Your session expired. Sign in again to load your private workspace.' })); setMessages(initial.current.messages); setAuthorizedSnapshot(null); clearOwnerAnswerScope(); csrf.current = ''; } } }), []);

  const apply = useCallback((snapshot: MiloSnapshot, mode: 'demo' | 'live') => {
    setAuthorizedSnapshot(mode === 'live' ? snapshot : null);
    const normalized = normalize(snapshot, mode);
    setState(previous => ({ ...normalized.state, selectedConversationId: previous.selectedConversationId,
      online: previous.online, pausePending: previous.pausePending }));
    setMessages(normalized.messages);
    if (mode === 'live') setComposers(previous => Object.fromEntries(Object.entries(previous).filter(([id]) => normalized.state.data.conversations.some(chat => chat.id === id))));
  }, []);
  const readAuthorizedSnapshot = useCallback(async (workspaceId: string, scope: typeof answerReadScope.current) => {
    if (!scope) return client.request<MiloSnapshot>(`/ui/bootstrap?workspace_id=${encodeURIComponent(workspaceId)}`);
    // Resolve only the server-selected evidence memory references (at most20).
    // Cached normalized rows and earlier paginated pages are never authority.
    const [connector, ...memories] = await Promise.all([
      client.request<{ object: Record<string, unknown> }>(`/ui/resolve?kind=connection&id=${encodeURIComponent(scope.connectorId)}`),
      ...scope.memoryIds.map(id => client.request<{ object: Record<string, unknown> }>(`/ui/resolve?kind=memory&id=${encodeURIComponent(id)}`)),
    ]);
    // The final exact-chat read rechecks its permission and revision after the
    // selected memory/connector reads; source edits/Forget change this revision.
    const [snapshot, resolved] = await Promise.all([
      client.request<MiloSnapshot>(`/ui/bootstrap?workspace_id=${encodeURIComponent(workspaceId)}`),
      client.request<{ object: Record<string, unknown> }>(`/ui/resolve?kind=conversation&id=${encodeURIComponent(scope.conversationId)}`),
    ]);
    const chat = resolved.object;
    if (snapshot.workspace?.id !== workspaceId || chat.id !== scope.conversationId || chat.workspace_id !== workspaceId
      || chat.connector_id !== scope.connectorId || connector.object.id !== scope.connectorId || connector.object.workspace_id !== workspaceId
      || memories.some((row, index) => row.object.id !== scope.memoryIds[index] || row.object.conversation_id !== scope.conversationId)) {
      throw new Error('The exact conversation authority could not be confirmed.');
    }
    const memoryRows = memories.map(row => row.object as DataRecord);
    const originalVersion = (snapshot as MiloSnapshot & { snapshot_version?: string }).snapshot_version ?? '';
    const scopedVersion = JSON.stringify([chat, connector.object, memoryRows.map(row => [row.id,row.version,row.suppression_version,row.expires_at,row.status])]);
    return { ...snapshot,
      conversations: [...snapshot.conversations.filter(row => row.id !== scope.conversationId), chat as MiloSnapshot['conversations'][number]],
      connections: [...snapshot.connections.filter(row => row.id !== scope.connectorId), connector.object as MiloSnapshot['connections'][number]],
      memories: [...snapshot.memories.filter(row => !scope.memoryIds.includes(row.id)), ...memoryRows],
      snapshot_version: `${originalVersion}:${scopedVersion}`,
    } as MiloSnapshot;
  }, [client]);
  const refreshLive = useCallback(async () => {
    if (!liveSessionEnabled.current) return;
    const capturedEpoch = epoch.current;
    const sequence = ++snapshotSequence.current;
    const workspaceId = stateRef.current.mode === 'live' ? stateRef.current.workspaceId : '';
    const scope = answerReadScope.current;
    if (scope && scope.expiresAt <= Date.now()) clearOwnerAnswerScope();
    let snapshot: MiloSnapshot;
    try {
      snapshot = workspaceId ? await readAuthorizedSnapshot(workspaceId, answerReadScope.current)
        : await client.request<MiloSnapshot>('/ui/bootstrap');
    } catch (failure) {
      if (capturedEpoch === epoch.current && sequence === snapshotSequence.current && scope) {
        clearOwnerAnswerScope(); setAuthorizedSnapshot(null);
      }
      throw failure;
    }
    if (!liveSessionEnabled.current || capturedEpoch !== epoch.current || sequence !== snapshotSequence.current) return;
    apply(snapshot, 'live');
  }, [apply, client, clearOwnerAnswerScope, readAuthorizedSnapshot]);
  const confirmOwnerAnswer = useCallback(async (value: unknown, stillSelected: () => boolean): Promise<MiloSnapshot> => {
    const current = stateRef.current;
    if (current.mode !== 'live' || !liveSessionEnabled.current || !stillSelected() || !isOwnerAnswer(value)
      || value.authorization_context?.owner_id !== current.user.id || value.authorization_context?.workspace_id !== current.workspaceId
      || value.conversation_id !== value.authorization_context.conversation_id) throw new Error('The answer does not belong to the selected owner and workspace.');
    const context = value.authorization_context;
    const ids = typeof context.memory_versions === 'object' && context.memory_versions !== null && !Array.isArray(context.memory_versions)
      ? Object.keys(context.memory_versions) : null;
    const identifier = (id: unknown) => typeof id === 'string' && /^[A-Za-z0-9_-]{1,36}$/.test(id);
    const expiresAt = ownerAnswerExpiresAt(value);
    if (!ids || ids.length > 20 || ids.some(id => !identifier(id)) || !identifier(value.conversation_id)
      || !identifier(context.connector_id) || !expiresAt || expiresAt <= Date.now()) throw new Error('The answer scope is invalid or expired.');
    clearOwnerAnswerScope();
    const capturedEpoch = epoch.current; const sequence = ++snapshotSequence.current;
    const scopeGeneration = answerScopeGeneration.current;
    const scope = { conversationId: value.conversation_id, connectorId: context.connector_id, memoryIds: ids, expiresAt };
    const snapshot = await readAuthorizedSnapshot(current.workspaceId, scope);
    if (!liveSessionEnabled.current || capturedEpoch !== epoch.current || sequence !== snapshotSequence.current
      || scopeGeneration !== answerScopeGeneration.current || !stillSelected()) throw new Error('The selected conversation changed. This answer was cleared.');
    if (!ownerAnswerIsCurrent(value, snapshot)) {
      apply(snapshot, 'live');
      throw new Error('The answer evidence or permissions changed. Ask again using the current conversation.');
    }
    answerReadScope.current = scope;
    apply(snapshot, 'live');
    return snapshot;
  }, [apply, clearOwnerAnswerScope, readAuthorizedSnapshot]);

  const refresh = useCallback(async () => {
    if (stateRef.current.mode === 'demo') { apply(demo.current!.snapshot(), 'demo'); return; }
    await refreshLive();
  }, [apply, refreshLive]);

  useEffect(() => {
    let alive = true;
    const capturedEpoch = epoch.current;
    void client.request<{ backend_configured?: boolean; google_configured?: boolean }>('/auth/config')
      .then(config => { if (alive) setBackendConfigured(config.backend_configured !== false); }).catch(() => undefined);
    void client.request('/me').then(async () => {
      if (!alive || capturedEpoch !== epoch.current) return;
      const token = await client.request<{ csrf_token: string }>('/auth/csrf');
      if (!alive || capturedEpoch !== epoch.current) return;
      csrf.current = token.csrf_token;
      liveSessionEnabled.current = true;
      setBrowserSessionPresent(true);
      if (alive) await refreshLive();
    }).catch(() => {
      if (alive && capturedEpoch === epoch.current && liveSessionEnabled.current) {
        setState(previous => ({ ...previous,
          error: 'Your sign-in was restored, but your private workspace could not load. Open sign in and continue to your workspace to retry.' }));
      }
    }).finally(() => { if (alive && capturedEpoch === epoch.current) setBrowserSessionChecking(false); });
    const updateOnline = () => { setState(previous => ({ ...previous, online: navigator.onLine })); if (navigator.onLine && stateRef.current.mode === 'live') void refreshLive().catch(() => undefined); };
    const foreground = () => { if (document.visibilityState === 'visible' && stateRef.current.mode === 'live') void refreshLive().catch(() => undefined); };
    window.addEventListener('online', updateOnline); window.addEventListener('offline', updateOnline); updateOnline();
    document.addEventListener('visibilitychange', foreground);
    return () => { alive = false; window.removeEventListener('online', updateOnline); window.removeEventListener('offline', updateOnline); document.removeEventListener('visibilitychange', foreground); };
  }, [client, refreshLive]);

  useEffect(() => {
    if (state.mode !== 'live') return;
    const timer = window.setInterval(() => { void refreshLive().catch(error => setState(previous => ({ ...previous,
      error: `Updates unavailable. Last known state is shown. ${error instanceof Error ? error.message : ''}` }))); }, 30000);
    return () => window.clearInterval(timer);
  }, [state.mode, refreshLive]);

  const request = useCallback(async <T,>(method: string, path: string, body?: unknown, options?: { idempotencyKey?: string }): Promise<T> => {
    const current = stateRef.current;
    const capturedEpoch = epoch.current;
    if (current.mode === 'demo') {
      const result = await demo.current!.request<T>(path, { method, body, idempotencyKey: options?.idempotencyKey });
      if (capturedEpoch === epoch.current && stateRef.current.mode === 'demo') apply(demo.current!.snapshot(), 'demo');
      return result;
    }
    if (!current.online && method !== 'GET') throw new Error('Offline: the server has not confirmed this request. Automation may still be active.');
    if (method !== 'GET') snapshotSequence.current++;
    const result = await client.request<T>(path, { method, body, idempotencyKey: options?.idempotencyKey ?? (method === 'POST' ? crypto.randomUUID() : undefined) });
    if (capturedEpoch !== epoch.current) throw new Error('The session changed. Reload the current owner’s scope before continuing.');
    if (method !== 'GET') snapshotSequence.current++;
    return result;
  }, [apply, client]);
  const actions: MiloActions = useMemo(() => ({ request, refresh, confirmOwnerAnswer, clearOwnerAnswerScope, navigate: path => router.push(path),
    async ensureConversation(id) {
      if (stateRef.current.data.conversations.some(chat => chat.id === id) || stateRef.current.mode !== 'live') return;
      const capturedEpoch = epoch.current; const capturedSequence = snapshotSequence.current;
      const resolved = await client.request<{ conversation: Record<string, unknown> }>(`/ui/resolve?kind=conversation&id=${encodeURIComponent(id)}`);
      if (capturedEpoch !== epoch.current || capturedSequence !== snapshotSequence.current) return;
      const row = resolved.conversation;
      if (row.workspace_id !== stateRef.current.workspaceId) throw new Error('This conversation belongs to a different workspace. Open that workspace first.');
      const connection = stateRef.current.data.connections.find(item => item.id === row.connector_id);
      const chat: Conversation = { ...row, id, conversation_id:id, title:String(row.title),kind:String(row.kind),control_state:String(row.control_state),
        connector_id:String(row.connector_id),account_label:connection?.label ?? 'Selected account', provider_chat_id:String(row.provider_chat_id),
        revision:Number(row.revision),preview:'',timestamp:'',unread:0,avatar_color:'#EAE0FD',pending_drafts:0,pending_tasks:0 };
      setState(previous => ({ ...previous,data:{ ...previous.data,conversations:[...previous.data.conversations,chat] } }));
    },
    async loadMore() {
      const current = stateRef.current;
      if (!current.nextCursor || current.mode !== 'live') return;
      if (loadingCursor.current === current.nextCursor) return;
      loadingCursor.current = current.nextCursor;
      const capturedEpoch = epoch.current;
      const capturedSequence = snapshotSequence.current;
      try {
        const snapshot = await client.request<MiloSnapshot>(`/ui/bootstrap?workspace_id=${encodeURIComponent(current.workspaceId)}&conversation_cursor=${encodeURIComponent(current.nextCursor)}`);
        if (capturedEpoch !== epoch.current || capturedSequence !== snapshotSequence.current || current.workspaceId !== stateRef.current.workspaceId) return;
        const normalized = normalize(snapshot, 'live');
        setState(previous => ({ ...previous, nextCursor: normalized.state.nextCursor, hasMore: normalized.state.hasMore,
          data: { ...previous.data, conversations: [...previous.data.conversations, ...normalized.state.data.conversations.filter(chat => !previous.data.conversations.some(old => old.id === chat.id))] } }));
      } finally { loadingCursor.current = null; }
    },
    selectConversation: id => { setState(previous => ({ ...previous, selectedConversationId: id })); router.push(`/inbox/${encodeURIComponent(id)}`); },
    async logout() {
      setAuthorizedSnapshot(null); clearOwnerAnswerScope();
      // Identity can be valid even when its private bootstrap never loaded.
      // Capture the session before clearing local data, then revoke that cookie.
      const hadBrowserSession = liveSessionEnabled.current || stateRef.current.mode === 'live';
      liveSessionEnabled.current = false;
      setBrowserSessionChecking(hadBrowserSession);
      const capturedEpoch = ++epoch.current; snapshotSequence.current++; setComposers({});
      setMessages(initial.current.messages); setState(initial.current.state);
      try {
        if (hadBrowserSession) {
          // A 401 here confirms there is no active session to revoke; it must
          // not run the private-data client's unrelated expiry callback.
          const logoutClient = createClient({ baseUrl: '/api', headers: { 'X-CSRF-Token': csrf.current } });
          await logoutClient.request('/auth/logout', { method: 'POST' });
        }
      } catch (failure) {
        if (!(failure instanceof ApiError && failure.status === 401)) {
          if (capturedEpoch === epoch.current && hadBrowserSession) {
            liveSessionEnabled.current = true;
            setBrowserSessionPresent(true); setBrowserSessionChecking(false);
            setState(previous => ({ ...previous, error: 'Sign-out was not confirmed. Your local private data was cleared; retry signing out to revoke the browser session.' }));
            router.push('/login');
          }
          throw failure;
        }
      }
      if (capturedEpoch !== epoch.current) return;
      setBrowserSessionPresent(false); setBrowserSessionChecking(false);
      csrf.current = ''; setMessages(initial.current.messages); setState(initial.current.state); router.push('/login');
    },
    async setPaused(paused) {
      setState(previous => ({ ...previous, pausePending: true, error: null }));
      try {
        const current = stateRef.current;
        const result = await request<{ paused: boolean; pause_generation: number }>('POST', `/${paused ? 'pause' : 'resume'}-all?workspace_id=${encodeURIComponent(current.workspaceId)}`);
        setState(previous => ({ ...previous, paused: result.paused, pausePending: false }));
        await refresh();
      } catch (error) {
        setState(previous => ({ ...previous, pausePending: false, error: `${paused ? 'Pause' : 'Resume'} not confirmed; automation may still be active. ${error instanceof Error ? error.message : 'Try again when connected.'}` }));
        throw error;
      }
    },
  }), [client, refresh, request, router, confirmOwnerAnswer, clearOwnerAnswerScope]);
  const establishSession = useCallback(async (path: string, body: Record<string, string>, headers: Record<string, string>, method: string) => {
    setAuthorizedSnapshot(null); clearOwnerAnswerScope();
    liveSessionEnabled.current = false;
    setBrowserSessionPresent(false);
    setBrowserSessionChecking(false);
    const capturedEpoch = ++epoch.current; snapshotSequence.current++; setComposers({});
    setState(initial.current.state); setMessages(initial.current.messages);
    const result = await client.request<{ csrf_token: string }>(path, { method: 'POST', body, headers });
    if (capturedEpoch !== epoch.current) throw new Error('Sign-in was superseded by another session change.');
    csrf.current = result.csrf_token;
    liveSessionEnabled.current = true;
    setBrowserSessionPresent(true);
    try { await refreshLive(); }
    catch (failure) {
      if (!liveSessionEnabled.current || capturedEpoch !== epoch.current) throw failure;
      const message = `${method} succeeded, but your private workspace could not load. Continue to your workspace to retry loading it.`;
      setState(previous => ({ ...previous, error: message }));
      throw new Error(message);
    }
    if (capturedEpoch === epoch.current && liveSessionEnabled.current) router.push('/');
  }, [client, refreshLive, router, clearOwnerAnswerScope]);
  const authenticate = useCallback((credential: string, nonce: string) =>
    establishSession('/auth/google', { credential, nonce }, { 'X-CSRF-Token': nonce }, 'Google sign-in'), [establishSession]);
  const authenticateWithAccessCode = useCallback((code: string) =>
    establishSession('/auth/access-code', { code }, {}, 'Owner sign-in'), [establishSession]);
  const resumeBrowserSession = useCallback(async () => {
    if (!liveSessionEnabled.current) throw new Error('Your session expired. Sign in again.');
    const capturedEpoch = epoch.current;
    await refreshLive();
    if (capturedEpoch === epoch.current && liveSessionEnabled.current) router.push('/');
  }, [refreshLive, router]);
  return <Context.Provider value={{ state, actions, messages, authorizedSnapshot: state.mode === 'live' && authorizedSnapshot?.user.id === state.user.id && authorizedSnapshot?.workspace?.id === state.workspaceId ? authorizedSnapshot : null, authenticate, authenticateWithAccessCode, backendConfigured, browserSessionPresent, browserSessionChecking, resumeBrowserSession, composers,
    setComposer: (id, text) => setComposers(previous => ({ ...previous, [id]: text })) }}>
    <Fragment key={`${state.mode}:${state.user.id}:${state.workspaceId}`}>{children}</Fragment>
  </Context.Provider>;
}
export function useMilo() { const context = useContext(Context); if (!context) throw new Error('Milo provider is missing'); return context; }

export function shortTime(value: unknown, timezone = 'Asia/Kolkata') {
  if (typeof value !== 'string' || !value) return '';
  const date = new Date(value); if (Number.isNaN(date.valueOf())) return '';
  return new Intl.DateTimeFormat('en', { hour: 'numeric', minute: '2-digit', timeZone: timezone }).format(date);
}
export function textValue(value: unknown, fallback = '') { return typeof value === 'string' ? value : fallback; }
export function isRecord(value: unknown): value is DataRecord { return !!value && typeof value === 'object' && 'id' in value; }
