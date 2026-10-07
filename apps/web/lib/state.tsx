'use client';

import { Fragment, createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { createClient, createDemoClient, createDemoSnapshot, type MiloSnapshot } from '@milo/contracts';
import type { Connection, Conversation, DataRecord, Message, MiloActions, MiloData, MiloState } from './types';

type Session = { state: MiloState; actions: MiloActions; messages: Record<string, Message[]>;
  authenticate(credential: string, nonce: string): Promise<void>; backendConfigured: boolean;
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
  const [backendConfigured, setBackendConfigured] = useState(false);
  const [composers, setComposers] = useState<Record<string, string>>({});
  const epoch = useRef(0);
  const liveSessionEnabled = useRef(false);
  const snapshotSequence = useRef(0);
  const loadingCursor = useRef<string | null>(null);
  const stateRef = useRef(state); stateRef.current = state;
  const csrf = useRef('');
  const client = useMemo(() => createClient({ baseUrl: '/api', headers: async () => ({ 'X-CSRF-Token': csrf.current }),
    onUnauthorized: () => { if (stateRef.current.mode === 'live' || liveSessionEnabled.current) { liveSessionEnabled.current = false; epoch.current++; snapshotSequence.current++; setComposers({}); setState(previous => ({ ...initial.current.state, online: previous.online,
      error: 'Your session expired. Sign in again to load your private workspace.' })); setMessages(initial.current.messages); csrf.current = ''; } } }), []);

  const apply = useCallback((snapshot: MiloSnapshot, mode: 'demo' | 'live') => {
    const normalized = normalize(snapshot, mode);
    setState(previous => ({ ...normalized.state, selectedConversationId: previous.selectedConversationId,
      online: previous.online, pausePending: previous.pausePending }));
    setMessages(normalized.messages);
    if (mode === 'live') setComposers(previous => Object.fromEntries(Object.entries(previous).filter(([id]) => normalized.state.data.conversations.some(chat => chat.id === id))));
  }, []);
  const refreshLive = useCallback(async () => {
    if (!liveSessionEnabled.current) return;
    const capturedEpoch = epoch.current;
    const sequence = ++snapshotSequence.current;
    const snapshot = await client.request<MiloSnapshot>('/ui/bootstrap');
    if (!liveSessionEnabled.current || capturedEpoch !== epoch.current || sequence !== snapshotSequence.current) return;
    apply(snapshot, 'live');
  }, [apply, client]);
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
      const token = await client.request<{ csrf_token: string }>('/auth/csrf');
      if (!alive || capturedEpoch !== epoch.current) return;
      csrf.current = token.csrf_token;
      liveSessionEnabled.current = true;
      if (alive) await refreshLive();
    }).catch(() => undefined);
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

  const request = useCallback(async <T,>(method: string, path: string, body?: unknown): Promise<T> => {
    const current = stateRef.current;
    const capturedEpoch = epoch.current;
    if (current.mode === 'demo') {
      const result = await demo.current!.request<T>(path, { method, body });
      if (capturedEpoch === epoch.current && stateRef.current.mode === 'demo') apply(demo.current!.snapshot(), 'demo');
      return result;
    }
    if (!current.online && method !== 'GET') throw new Error('Offline: the server has not confirmed this request. Automation may still be active.');
    if (method !== 'GET') snapshotSequence.current++;
    const result = await client.request<T>(path, { method, body, idempotencyKey: method === 'POST' ? crypto.randomUUID() : undefined });
    if (capturedEpoch !== epoch.current) throw new Error('The session changed. Reload the current owner’s scope before continuing.');
    if (method !== 'GET') snapshotSequence.current++;
    return result;
  }, [apply, client]);
  const actions: MiloActions = useMemo(() => ({ request, refresh, navigate: path => router.push(path),
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
      liveSessionEnabled.current = false;
      epoch.current++; snapshotSequence.current++; setComposers({});
      const wasLive = stateRef.current.mode === 'live';
      setMessages(initial.current.messages); setState(initial.current.state);
      if (wasLive) await client.request('/auth/logout', { method: 'POST' });
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
  }), [client, refresh, request, router]);
  const authenticate = useCallback(async (credential: string, nonce: string) => {
    liveSessionEnabled.current = false;
    const capturedEpoch = ++epoch.current; snapshotSequence.current++; setComposers({});
    setState(initial.current.state); setMessages(initial.current.messages);
    const result = await client.request<{ csrf_token: string }>('/auth/google', { method: 'POST', body: { credential, nonce }, headers: { 'X-CSRF-Token': nonce } });
    if (capturedEpoch !== epoch.current) throw new Error('Sign-in was superseded by another session change.');
    csrf.current = result.csrf_token;
    liveSessionEnabled.current = true;
    await refreshLive(); router.push('/');
  }, [client, refreshLive, router]);
  return <Context.Provider value={{ state, actions, messages, authenticate, backendConfigured, composers,
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
