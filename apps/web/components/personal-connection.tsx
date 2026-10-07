'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import qrcode from 'qrcode-generator';
import { emptyPersonalConsent, personalConsentFor, personalQRDrawing, usablePersonalQR, validPersonalConsent,
  type PersonalChats, type PersonalConfig, type PersonalConsent, type PersonalPairing, type PersonalStatus } from '@milo/contracts';
import type { MiloActions, MiloState } from '../lib/types';
import { currentToolResult, toolAuthorizationVersion, type PrivateToolResult } from '../lib/tool-privacy';
import styles from './tools.module.css';
import { AutomaticDrafts } from './automatic-drafts';
import { PersonalAuthorship } from './personal-authorship';

const permissions: [keyof PersonalConsent, string][] = [
  ['read', 'Read messages in this conversation'], ['retain', 'Keep this conversation under my retention policy'],
  ['learn', 'Learn from my verified writing in this conversation'], ['draft', 'Prepare unsent replies for my review'],
  ['send', 'Allow sending under my separate rules'], ['recipient_opted_in', 'This person has agreed to receive my replies'],
];
const route = '/integrations/whatsapp/personal';

/** Converts the pairing value directly to drawing commands; no image service sees it. */
function PairingQR({ value }: { value: string }) {
  const drawing = useMemo(() => {
    try {
      const code = qrcode(0, 'M'); code.addData(value, 'Byte'); code.make();
      return personalQRDrawing(code);
    } catch { return null; }
  }, [value]);
  if (!drawing) return <p role="alert">The pairing code could not be displayed. Refresh linking to request the current code.</p>;
  return <svg role="img" aria-label="Temporary WhatsApp linking QR code" viewBox={`0 0 ${drawing.size} ${drawing.size}`} width="256" height="256" style={{ display: 'block', maxWidth: '100%', height: 'auto', background: 'white' }} shapeRendering="crispEdges"><rect width={drawing.size} height={drawing.size} fill="white"/><path d={drawing.path} fill="black"/></svg>;
}

export function PersonalConnection({ state, actions }: { state: MiloState; actions: MiloActions }) {
  const version = toolAuthorizationVersion(state);
  const latest = useRef(version); latest.current = version;
  const actionRef = useRef(actions); actionRef.current = actions;
  const active = useRef(false); const visible = useRef(true); const flight = useRef(false); const sequence = useRef(0);
  const [entry, setEntry] = useState<PrivateToolResult<{ config: PersonalConfig; status: PersonalStatus }> | null>(null);
  const [pairingEntry, setPairingEntry] = useState<PrivateToolResult<PersonalPairing> | null>(null);
  const [chatsEntry, setChatsEntry] = useState<PrivateToolResult<PersonalChats> | null>(null);
  const [errorEntry, setErrorEntry] = useState<PrivateToolResult<string> | null>(null);
  const [notice, setNotice] = useState(''); const [busy, setBusy] = useState(false); const [loading, setLoading] = useState(true);
  const [selectedId, setSelectedId] = useState(''); const [consent, setConsent] = useState(emptyPersonalConsent);
  const [now, setNow] = useState(Date.now()); const [attempt, setAttempt] = useState(0);
  const current = currentToolResult(entry, version); const pairing = currentToolResult(pairingEntry, version);
  const chats = currentToolResult(chatsEntry, version); const error = currentToolResult(errorEntry, version);
  const connector = current?.status.connector;
  const qr = !current?.status.connected && visible.current ? usablePersonalQR(pairing, connector?.id, now) : null;
  const selected = chats?.chats.find(chat => chat.provider_chat_id === selectedId && chat.kind === 'contact');
  const configured = Boolean(current?.config.enabled && current.config.configured && !current.config.simulation && !current.status.simulation);
  const statusRef = useRef(current?.status); statusRef.current = current?.status;
  useEffect(() => { setSelectedId(''); setConsent(emptyPersonalConsent()); setNotice(''); }, [version, connector?.id]);
  useEffect(() => {
    if (!pairing?.qr) return;
    const currentCode = usablePersonalQR(pairing, connector?.id);
    if (!currentCode) { setPairingEntry(null); return; }
    const timer = window.setTimeout(() => { setPairingEntry(null); setNow(Date.now()); }, currentCode.expiresAt - Date.now());
    return () => window.clearTimeout(timer);
  }, [pairing, connector?.id]);

  useEffect(() => {
    active.current = true;
    if (state.mode !== 'live' || !state.workspaceId) { setLoading(false); return () => { active.current = false; }; }
    let valid = true; let timer: number | undefined;
    const currentRequest = (id: number) => valid && active.current && visible.current && latest.current === version && sequence.current === id;
    const load = async () => {
      if (!visible.current) return;
      if (flight.current) { timer = window.setTimeout(() => void load(), 5_000); return; }
      const id = ++sequence.current;
      try {
        const [config, status] = await Promise.all([
          actionRef.current.request<PersonalConfig>('GET', `${route}/config?workspace_id=${encodeURIComponent(state.workspaceId)}`),
          actionRef.current.request<PersonalStatus>('GET', `${route}/status?workspace_id=${encodeURIComponent(state.workspaceId)}`),
        ]);
        if (!currentRequest(id)) return;
        setEntry({ value: { config, status }, authorizationVersion: version }); setErrorEntry(null);
        if (config.enabled && config.configured && status.connector && !status.connected && ['starting', 'pairing', 'reconnecting'].includes(status.status)) {
          const value = await actionRef.current.request<PersonalPairing>('GET', `${route}/pairing?connector_id=${encodeURIComponent(status.connector.id)}`);
          if (currentRequest(id)) setPairingEntry({ value, authorizationVersion: version });
        } else if (currentRequest(id)) setPairingEntry(null);
        if (status.connected && status.connector) {
          const value = await actionRef.current.request<PersonalChats>('GET', `${route}/chats?connector_id=${encodeURIComponent(status.connector.id)}`);
          if (currentRequest(id)) setChatsEntry({ value, authorizationVersion: version });
        } else if (currentRequest(id)) setChatsEntry(null);
      } catch {
        if (currentRequest(id)) { setPairingEntry(null); setChatsEntry(null); setErrorEntry({ value: 'We could not confirm WhatsApp linking. Refresh the connection before trying again.', authorizationVersion: version }); }
      } finally {
        if (currentRequest(id)) setLoading(false);
        if (valid && visible.current) timer = window.setTimeout(() => void load(), statusRef.current?.connector && !statusRef.current.connected && ['starting', 'pairing', 'reconnecting'].includes(statusRef.current.status) ? 5_000 : 30_000);
      }
    };
    const foreground = () => {
      visible.current = document.visibilityState === 'visible'; setPairingEntry(null); setNow(Date.now());
      window.clearTimeout(timer); if (visible.current) void load();
    };
    visible.current = document.visibilityState === 'visible'; setLoading(true); void load();
    document.addEventListener('visibilitychange', foreground);
    const expiry = window.setInterval(() => setNow(Date.now()), 1_000);
    return () => { valid = false; active.current = false; sequence.current++; window.clearTimeout(timer); window.clearInterval(expiry); document.removeEventListener('visibilitychange', foreground); };
  }, [state.mode, state.workspaceId, version, attempt]);

  function reload() {
    if (!flight.current && state.mode === 'live') { setPairingEntry(null); setAttempt(value => value + 1); }
  }

  async function change(action: 'start' | 'disconnect' | 'chats/authorize') {
    if (flight.current || !state.online || !configured || (action !== 'disconnect' && (loading || error))) return;
    if (action !== 'start' && !connector) return;
    if (action === 'chats/authorize' && (!selected || !validPersonalConsent(consent))) return;
    const started = version; flight.current = true; sequence.current++; setBusy(true); setPairingEntry(null); setNotice(''); setErrorEntry(null);
    try {
      const body = action === 'start' ? { workspace_id: state.workspaceId } : action === 'disconnect' ? { connector_id: connector!.id }
        : { connector_id: connector!.id, provider_chat_id: selected!.provider_chat_id, title: selected!.title, ...consent };
      await actions.request('POST', `${route}/${action}`, body);
      if (!active.current || latest.current !== started) return;
      setNotice(action === 'chats/authorize' ? 'Your conversation choices were acknowledged. Automatic replies need a separate rule.' : action === 'disconnect' ? 'Disconnect was acknowledged. Refresh linking to review its current state.' : 'Linking was requested. The current pairing code will appear when WhatsApp supplies it.');
      await actions.refresh();
      if (active.current && latest.current === started) {
        const status = await actions.request<PersonalStatus>('GET', `${route}/status?workspace_id=${encodeURIComponent(state.workspaceId)}`);
        if (active.current && latest.current === started && current) setEntry({ value: { config: current.config, status }, authorizationVersion: started });
      }
    } catch { if (active.current && latest.current === started) setErrorEntry({ value: 'This change was not confirmed. Refresh linking before explicitly retrying. No request was automatically repeated.', authorizationVersion: started }); }
    finally { flight.current = false; if (active.current) { setBusy(false); setAttempt(value => value + 1); } }
  }

  if (state.mode !== 'live') return <section className={styles.panel} aria-label="WhatsApp phone connection"><h2>Link your WhatsApp phone</h2><p className={styles.bodyMuted}>Sign in to review availability for the linked-device pilot. This demo links no phone.</p></section>;
  if (!state.workspaceId) return null;
  return <section className={styles.panel} aria-label="WhatsApp phone connection">
    <div className={styles.panelHeader}><div><span className={styles.eyebrow}>Linked-device pilot</span><h2>Link your WhatsApp phone</h2></div><button className="button secondary" disabled={busy || loading} onClick={reload}>Refresh phone linking</button></div>
    <p className={styles.bodyMuted}>Use WhatsApp’s Linked devices from your phone to scan this screen. This pilot supports individual chats. Groups and native forwarding are unavailable.</p>
    {loading && <p role="status">Checking phone linking…</p>}{error && <p className={styles.warning} role="alert">{error}</p>}{notice && <p className={styles.meta} role="status">{notice}</p>}
    {current && <>
      {!configured ? <p role="status">Phone linking is not available in this deployment yet. You can connect an eligible Business number below or begin with an exported chat.</p>
        : <>
          <p role="status">{error ? 'Current phone linking is unconfirmed. Refresh linking to review its current state.' : current.status.connected ? 'Your phone is linked. Choose one conversation to begin.' : current.status.status === 'pairing' ? 'Waiting for you to scan the current code.' : current.status.status === 'reconnecting' ? 'Reconnecting to WhatsApp. Replies remain subject to current connection checks.' : 'Your phone has not been confirmed as linked.'}</p>
          {!current.status.connected && <button className="button" disabled={busy || loading || !!error || !state.online} onClick={() => void change('start')}>{busy ? 'Requesting linking…' : connector ? 'Resume phone linking' : 'Start phone linking'}</button>}
          {qr && <div className={styles.coverage}><div><PairingQR value={qr.value}/><p>WhatsApp → Settings → Linked devices → Link a device. This code expires shortly; keep this screen open.</p><p className={styles.meta}>Do not share or save this pairing code. On one phone, open Milo on a second screen to scan it.</p></div></div>}
          {!qr && current.status.status === 'pairing' && <p role="status">Waiting for a current pairing code. Expired codes are removed automatically.</p>}
          {connector && <button className="button secondary" disabled={busy || !state.online} onClick={() => void change('disconnect')}>Disconnect this linked device</button>}
          {current.status.connected && chats && <form onSubmit={event => { event.preventDefault(); void change('chats/authorize'); }} aria-label="Choose one linked WhatsApp conversation">
            <h3>Choose one conversation</h3><p className={styles.meta}>New chats start with every choice off. Existing choices are shown for the selected chat. Linking never grants every conversation or enables Auto.</p>
            <label className={styles.field}><span>Individual WhatsApp conversation</span><select value={selected?.provider_chat_id ?? ''} disabled={busy} onChange={event => { const chat = chats.chats.find(row => row.provider_chat_id === event.target.value); setSelectedId(event.target.value); setConsent(chat ? personalConsentFor(chat) : emptyPersonalConsent()); }}><option value="">Choose a person</option>{chats.chats.filter(chat => chat.kind === 'contact').map(chat => <option key={chat.provider_chat_id} value={chat.provider_chat_id}>{chat.title}</option>)}</select></label>
            {!chats.chats.length && <p role="status">No eligible individual conversations have arrived yet. Refresh after WhatsApp syncs this linked device.</p>}
            {chats.has_more && <p className={styles.meta}>This is a limited conversation batch. More conversations may be available.</p>}
            <fieldset className={styles.fieldset} disabled={busy || !selected}><legend>Only this conversation’s permissions</legend>{permissions.map(([key, label]) => <label key={key} className={styles.checkbox}><input type="checkbox" checked={consent[key]} onChange={event => setConsent(value => ({ ...value, [key]: event.target.checked }))}/><span>{label}</span></label>)}</fieldset>
            {!validPersonalConsent(consent) && <p className={styles.meta}>Reading and retention are required for learning, drafts or sends. Sending also requires this person’s agreement.</p>}
            <button className="button" disabled={busy || !state.online || !selected || !validPersonalConsent(consent)}>{busy ? 'Saving choices…' : 'Save this conversation’s choices'}</button>
            {!!selected?.conversation_id && selected.permissions?.read === true && selected.permissions?.retain === true && <PersonalAuthorship key={`writing:${selected.conversation_id}`} state={state} actions={actions} conversationId={selected.conversation_id} title={selected.title}/>}
            {!!selected?.conversation_id && <AutomaticDrafts key={selected.conversation_id} state={state} actions={actions} conversationId={selected.conversation_id} title={selected.title}/>}
            {!!selected?.conversation_id && <button type="button" className="button secondary" disabled={busy} onClick={() => actions.selectConversation(selected.conversation_id!)}>Open this conversation</button>}
          </form>}
        </>}
    </>}
    <p className={styles.meta}>A linked device does not prove complete history or successful replies. Milo works on the server only within your selected permissions and rules.</p>
  </section>;
}
