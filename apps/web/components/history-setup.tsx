'use client';

import { useEffect, useRef, useState, type Dispatch, type FormEvent, type SetStateAction } from 'react';
import type { Conversation, DataRecord, MiloActions, MiloState } from '../lib/types';
import { rememberHistoryImport } from '../lib/history-confirmation';
import styles from './tools.module.css';

type HistoryProps = { state: MiloState; actions: MiloActions };
type CreatedChat = { id: string; title: string; learn: boolean };

/** A collection of exported files is an application record, not a linked phone. */
export function ExportChatSetup({ state, actions, onCreated }: HistoryProps & { onCreated(chat: CreatedChat): void }) {
  const [collection] = useState(() => `export-${crypto.randomUUID()}`);
  const [chatIdentity, setChatIdentity] = useState('');
  const [title, setTitle] = useState('');
  const [kind, setKind] = useState('contact');
  const [ownerLabel, setOwnerLabel] = useState('');
  const [read, setRead] = useState(false);
  const [retain, setRetain] = useState(false);
  const [draft, setDraft] = useState(false);
  const [learn, setLearn] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  // Preserve server identifiers when the permission step must be retried.
  const created = useRef<{ connectorId?: string; chatId?: string; chatTitle?: string }>({});
  const active = useRef(true);
  const ownerScope = `${state.user.id}:${state.workspaceId}`;
  const currentScope = useRef(ownerScope); currentScope.current = ownerScope;
  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);

  async function create(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (busy || !state.workspaceId || state.mode !== 'live') return;
    setBusy(true); setError('');
    const current = () => active.current && currentScope.current === ownerScope;
    try {
      if (!created.current.connectorId) {
        const connector = await actions.request<DataRecord>('POST', '/connectors', {
          workspace_id: state.workspaceId, provider: 'export_only', account_id: collection.trim(),
          owner_sender_id: ownerLabel.trim(),
        });
        if (!current()) return;
        if (!connector.id) throw new Error('The server did not confirm a history collection identifier.');
        created.current.connectorId = connector.id;
      }
      if (!created.current.chatId) {
        const chat = await actions.request<DataRecord>('POST', '/conversations', {
          connector_id: created.current.connectorId, provider_chat_id: chatIdentity.trim(),
          title: title.trim(), kind, recipient_opted_in: false, group_send_allowed: false,
        });
        if (!current()) return;
        if (!chat.id) throw new Error('The server did not confirm a conversation identifier.');
        created.current.chatId = chat.id;
        created.current.chatTitle = typeof chat.title === 'string' ? chat.title : title.trim();
      }
      await actions.request('PUT', `/conversations/${encodeURIComponent(created.current.chatId)}/permissions`, {
        read, retain, draft, learn, send: false, share: false,
      });
      if (!current()) return;
      await actions.refresh();
      if (!current()) return;
      await actions.ensureConversation(created.current.chatId);
      if (current()) onCreated({ id: created.current.chatId, title: created.current.chatTitle || title.trim(), learn });
    } catch (failure) {
      if (current()) setError(failure instanceof Error ? failure.message : 'History setup was not confirmed. Retry to finish this same chat.');
    } finally { if (current()) setBusy(false); }
  }

  return <form onSubmit={create} aria-label="Set up an export-only chat">
    <p className={styles.bodyMuted}>Create a private history collection for one WhatsApp text export. This does not connect your phone, sync new messages, or enable automatic replies.</p>
    {error && <p className={styles.warning} role="alert">{error}{created.current.connectorId && ' The existing setup will be reused when you retry.'}</p>}
    <fieldset className={styles.fieldset} disabled={busy || !!created.current.connectorId}>
      <legend>Owner identity in the export</legend>
      <label className={styles.field}><span>Exact owner sender label in the export</span><input value={ownerLabel} onChange={event => setOwnerLabel(event.target.value)} required maxLength={120} placeholder="Your name as written in the file"/></label>
    </fieldset>
    <fieldset className={styles.fieldset} disabled={busy || !!created.current.chatId}>
      <legend>Selected conversation</legend>
      <label className={styles.field}><span>Chat title</span><input value={title} onChange={event => setTitle(event.target.value)} required maxLength={160}/></label>
      <div className={styles.formGrid}>
        <label className={styles.field}><span>Chat audience</span><select value={kind} onChange={event => setKind(event.target.value)}><option value="contact">One contact</option><option value="group">Group</option></select></label>
        <label className={styles.field}><span>Chat identifier for this export</span><input value={chatIdentity} onChange={event => setChatIdentity(event.target.value)} required maxLength={160} placeholder="A unique name for this chat"/></label>
      </div>
      <p className={styles.meta}>This identifier keeps exports in the same conversation. A text file does not prove a live provider chat identifier or complete history.</p>
    </fieldset>
    <fieldset className={styles.fieldset} disabled={busy}>
      <legend>Only this chat’s permissions</legend>
      <label className={styles.checkbox}><input type="checkbox" checked={read} onChange={event => setRead(event.target.checked)} required/><span>Read this selected chat history</span></label>
      <label className={styles.checkbox}><input type="checkbox" checked={retain} onChange={event => setRetain(event.target.checked)} required/><span>Retain this history under my workspace retention policy</span></label>
      <label className={styles.checkbox}><input type="checkbox" checked={draft} onChange={event => setDraft(event.target.checked)}/><span>Prepare unsent drafts for my review</span></label>
      <label className={styles.checkbox}><input type="checkbox" checked={learn} onChange={event => setLearn(event.target.checked)}/><span>Learn voice and memory from permitted examples in this chat</span></label>
    </fieldset>
    <p className={styles.meta}>Reading and retention are required to import. Learning is optional. Sending and sharing remain disabled, and historical records never trigger outgoing actions.</p>
    <div className={styles.formFoot}><button className="button" disabled={busy || !read || !retain || !collection.trim() || !ownerLabel.trim() || !title.trim() || !chatIdentity.trim()}>{busy ? 'Creating history scope…' : created.current.chatId ? 'Finish chat permissions' : 'Create export-only chat'}</button></div>
  </form>;
}

/** A preview belongs to the exact chat, file, owner mapping, dates, and timezone. */
export function HistoryImportForm({ state, actions, chats, initialChatId, busy, setBusy, onImported }: HistoryProps & {
  chats: Conversation[]; initialChatId: string; busy: boolean; setBusy: Dispatch<SetStateAction<boolean>>; onImported(): void;
}) {
  const [chatId, setChatId] = useState(initialChatId);
  const selected = chats.find(chat => chat.id === chatId);
  const connection = state.data.connections.find(item => item.id === selected?.connector_id);
  const [owner, setOwner] = useState(typeof connection?.owner_sender_id === 'string' ? connection.owner_sender_id : '');
  const [dateOrder, setDateOrder] = useState('DMY');
  const [timezone, setTimezone] = useState(state.timezone);
  const [fileText, setFileText] = useState('');
  const [filePending, setFilePending] = useState(false);
  const [preview, setPreview] = useState<{ key: string; result: Record<string, unknown> } | null>(null);
  const [error, setError] = useState('');
  const fileSequence = useRef(0);
  const active = useRef(true);
  useEffect(() => { active.current = true; return () => { active.current = false; fileSequence.current++; }; }, []);
  const payload = { conversation_id: chatId, text: fileText, owner_sender_label: owner, date_order: dateOrder, timezone };
  const key = JSON.stringify(payload);
  const currentKey = useRef(key); currentKey.current = key;
  const confirmed = preview?.key === key ? preview.result : null;
  const learnAllowed = selected?.permissions && typeof selected.permissions === 'object' && (selected.permissions as Record<string, unknown>).learn === true;

  async function loadFile(file?: File) {
    const sequence = ++fileSequence.current;
    setFileText(''); setPreview(null); setError(''); setFilePending(false);
    if (!file) return;
    if (file.size > 2_000_000) { setError('Choose a text export smaller than 2 MB.'); return; }
    setFilePending(true);
    try { const text = await file.text(); if (active.current && sequence === fileSequence.current) setFileText(text); }
    catch { if (active.current && sequence === fileSequence.current) setError('The selected file could not be read.'); }
    finally { if (active.current && sequence === fileSequence.current) setFilePending(false); }
  }

  async function submit(commit: boolean, form: HTMLFormElement) {
    if (busy || filePending || !fileText || !selected || !form.reportValidity() || (commit && !confirmed)) return;
    const submittedKey = key;
    setBusy(true); setError('');
    try {
      const result = await actions.request<Record<string, unknown>>('POST', commit ? '/imports' : '/imports/preview', payload);
      if (!active.current) return;
      if (commit) {
        // The confirmed SQL write changes the snapshot version, which closes this private dialog.
        // Report the committed result before refreshing can unmount its old scope.
        rememberHistoryImport(state);
        onImported();
        await actions.refresh();
      }
      else if (currentKey.current === submittedKey) setPreview({ key: submittedKey, result });
    } catch (failure) { if (active.current) setError(failure instanceof Error ? failure.message : 'The import was not confirmed. Your file and choices are still here.'); }
    finally { if (active.current) setBusy(false); }
  }

  return <form onSubmit={event => { event.preventDefault(); void submit(true, event.currentTarget); }} aria-label="Import selected chat history">
    {error && <p className={styles.warning} role="alert">{error}</p>}
    {!chats.length && <p className={styles.warning}>Create an export-only chat and its read and retention permissions before importing history.</p>}
    <label className={styles.field}><span>Exact conversation</span><select value={chatId} required disabled={busy} onChange={event => {
      setChatId(event.target.value); setPreview(null);
      const next = state.data.connections.find(item => item.id === chats.find(chat => chat.id === event.target.value)?.connector_id);
      setOwner(typeof next?.owner_sender_id === 'string' ? next.owner_sender_id : '');
    }}>{!selected && <option value="">Choose a chat</option>}{chats.map(chat => <option key={chat.id} value={chat.id}>{chat.title} · {chat.account_label}{chat.kind === 'group' ? ' · group' : ''}</option>)}</select></label>
    <label className={styles.field}><span>WhatsApp text export · up to 2 MB</span><input type="file" accept=".txt,text/plain" required disabled={busy} onChange={event => { void loadFile(event.target.files?.[0]); }}/></label>
    <label className={styles.field}><span>Exact owner sender label in the export</span><input value={owner} onChange={event => { setOwner(event.target.value); setPreview(null); }} required maxLength={160} disabled={busy} placeholder="As written in the export"/></label>
    <div className={styles.formGrid}>
      <label className={styles.field}><span>Date order</span><select value={dateOrder} onChange={event => { setDateOrder(event.target.value); setPreview(null); }} disabled={busy}><option value="DMY">Day / month / year</option><option value="MDY">Month / day / year</option><option value="YMD">Year / month / day</option></select></label>
      <label className={styles.field}><span>Export timezone</span><input value={timezone} onChange={event => { setTimezone(event.target.value); setPreview(null); }} required disabled={busy}/></label>
    </div>
    {confirmed && <div className={styles.success} role="status">{Number(confirmed.record_count)} records observed. Lifetime completeness unknown.{Array.isArray(confirmed.warnings) && confirmed.warnings.length > 0 && ` ${confirmed.warnings.map(String).join(' ')}`}</div>}
    {selected && <p className={styles.meta}>{learnAllowed ? 'Voice and memory learning is permitted for eligible examples in this selected chat. Writing examples still need review.' : 'Voice and memory learning is disabled for this selected chat. Importing history does not enable it.'}</p>}
    <p className={styles.meta}>Text exports cannot recreate native quote or forward originals. Importing creates historical records; it never enables Auto or sends a message.</p>
    <div className={styles.formFoot}><button type="button" className="button secondary" disabled={busy || filePending || !fileText || !selected} onClick={event => { const form = event.currentTarget.closest('form'); if (form) void submit(false, form); }}>{busy && !confirmed ? 'Previewing…' : 'Preview import'}</button><button className="button" disabled={busy || filePending || !confirmed || !fileText || !selected}>{busy && confirmed ? 'Importing…' : 'Import selected history'}</button></div>
  </form>;
}
