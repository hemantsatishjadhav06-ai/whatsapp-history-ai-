'use client';

import { useEffect, useRef, useState, type FormEvent } from 'react';
import type { Message, MiloActions, MiloState } from '../lib/types';
import { currentToolResult, toolAuthorizationVersion, type PrivateToolResult } from '../lib/tool-privacy';
import styles from './tools.module.css';

type BusinessConnector = {
  id: string; account_id: string; status: string; lease_valid: boolean;
  lease_expires_at?: string | null;
  capabilities: Record<string, unknown>; authorized_contacts: number;
  received_messages: number; delivered_messages: number; live_delivery_verified: boolean;
};
type BusinessStatus = {
  configured: boolean; owner_authorized: boolean; external_sends_enabled: boolean;
  webhook_configured: boolean; missing_requirements: string[]; connectors: BusinessConnector[];
};
type Consent = { read: boolean; retain: boolean; learn: boolean; draft: boolean; send: boolean; recipient_opted_in: boolean };
const emptyConsent = (): Consent => ({ read: false, retain: false, learn: false, draft: false, send: false, recipient_opted_in: false });
const choices: [keyof Consent, string][] = [
  ['read', 'Read new messages from this contact'], ['retain', 'Retain this contact’s messages under my retention policy'],
  ['learn', 'Learn this contact’s voice and evidence-linked context'], ['draft', 'Prepare unsent replies for my review'],
  ['send', 'Permit sending to this contact, subject to my rules'], ['recipient_opted_in', 'This contact has opted in to receive Business messages'],
];
const requirements: Record<string, string> = {
  authorized_google_owner: 'The deployment operator must authorize your Google account for this Business number.',
  business_phone_number: 'The deployment operator must configure an eligible WhatsApp Business number.',
  business_access_token: 'The deployment operator must connect the Business account securely on the server.',
  webhook_app_secret: 'The deployment operator must enable signed incoming WhatsApp events.',
  webhook_verify_token: 'The deployment operator must register the public WhatsApp webhook with Meta.',
};

/** Provider secrets stay on the server. A verified identity is not a delivery receipt. */
export function WhatsAppConnection({ state, actions }: { state: MiloState; actions: MiloActions }) {
  const version = toolAuthorizationVersion(state);
  const latest = useRef(version); latest.current = version;
  const active = useRef(true);
  const actionRef = useRef(actions); actionRef.current = actions;
  const flight = useRef(false);
  const statusSequence = useRef(0);
  const [statusEntry, setStatusEntry] = useState<PrivateToolResult<BusinessStatus> | null>(null);
  const [errorEntry, setErrorEntry] = useState<PrivateToolResult<string> | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const [connectorId, setConnectorId] = useState('');
  const [phone, setPhone] = useState('');
  const [title, setTitle] = useState('');
  const [consent, setConsent] = useState<Consent>(emptyConsent);
  const [historyConsent, setHistoryConsent] = useState(false);
  const [reviewChatId, setReviewChatId] = useState('');
  const [reviewEntry, setReviewEntry] = useState<PrivateToolResult<Message[]> & { chatId: string } | null>(null);
  const [selection, setSelection] = useState<{ key: string; ids: string[] }>({ key: '', ids: [] });
  const status = currentToolResult(statusEntry, version);
  const error = currentToolResult(errorEntry, version);
  const connector = status?.connectors.find(row => row.id === connectorId) ?? status?.connectors[0];
  const authorized = Boolean(status?.owner_authorized && status.configured);
  const prerequisites = !(consent.learn || consent.draft || consent.send) || (consent.read && consent.retain);
  const validConsent = prerequisites && (!consent.send || consent.recipient_opted_in);
  const currentLease = Boolean(connector?.lease_valid && (!connector.lease_expires_at || Date.parse(connector.lease_expires_at) > Date.now()));
  const historyEligible = Boolean(connector?.status === 'connected' && currentLease &&
    connector.capabilities.business_app_coexistence === 'supported' && status?.webhook_configured && connector.authorized_contacts > 0);
  const priorHistoryRequest = connector?.capabilities.business_history_request;
  const historyRequestStatus = priorHistoryRequest && typeof priorHistoryRequest === 'object' && 'status' in priorHistoryRequest ? String(priorHistoryRequest.status) : '';
  const reviewKey = `${version}:${reviewChatId}`;
  const currentReview = useRef(reviewKey); currentReview.current = reviewKey;
  const examples = reviewEntry?.chatId === reviewChatId ? currentToolResult(reviewEntry, version) : null;
  const selectedIds = selection.key === reviewKey ? selection.ids : [];
  const reviewChats = state.data.conversations.filter(chat => chat.connector_id === connector?.id && chat.kind === 'contact');

  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  useEffect(() => {
    if (state.mode !== 'live' || !state.workspaceId) { setLoading(false); return; }
    let valid = true;
    const load = () => {
      const sequence = ++statusSequence.current;
      const current = () => valid && active.current && latest.current === version && sequence === statusSequence.current;
      setLoading(true);
      void actionRef.current.request<BusinessStatus>('GET', `/integrations/whatsapp/status?workspace_id=${encodeURIComponent(state.workspaceId)}`)
        .then(value => { if (current()) { setStatusEntry({ value, authorizationVersion: version }); setErrorEntry(null); } })
        .catch(failure => { if (current()) setErrorEntry({ value: failure instanceof Error ? failure.message : 'Business connection status is unavailable. Try Refresh connection status.', authorizationVersion: version }); })
        .finally(() => { if (current()) setLoading(false); });
    };
    load();
    const timer = window.setInterval(() => { if (document.visibilityState === 'visible' && !flight.current) load(); }, 30_000);
    return () => { valid = false; window.clearInterval(timer); };
  }, [state.mode, state.workspaceId, version]);

  async function refreshStatus() {
    if (flight.current || state.mode !== 'live') return;
    const started = version;
    const sequence = ++statusSequence.current;
    setLoading(true); setErrorEntry(null);
    try {
      const value = await actions.request<BusinessStatus>('GET', `/integrations/whatsapp/status?workspace_id=${encodeURIComponent(state.workspaceId)}`);
      if (active.current && latest.current === started && sequence === statusSequence.current) setStatusEntry({ value, authorizationVersion: started });
    } catch (failure) {
      if (active.current && latest.current === started && sequence === statusSequence.current) setErrorEntry({ value: failure instanceof Error ? failure.message : 'Connection status was not confirmed.', authorizationVersion: started });
    } finally { if (active.current && latest.current === started && sequence === statusSequence.current) setLoading(false); }
  }

  async function change(path: string, body?: unknown, onSaved?: () => void) {
    if (flight.current || !state.online || !authorized) return;
    const started = version;
    statusSequence.current++;
    flight.current = true; setBusy(true); setLoading(false); setErrorEntry(null); setNotice('');
    try {
      await actions.request('POST', path, body);
      if (!active.current || latest.current !== started) return;
      onSaved?.();
      setNotice('Request acknowledged. Review the refreshed connection evidence below. No automatic reply was enabled.');
      await actions.refresh();
      // The snapshot may be unchanged (e.g. provider verification); refresh separately.
      if (active.current && latest.current === started) {
        const value = await actions.request<BusinessStatus>('GET', `/integrations/whatsapp/status?workspace_id=${encodeURIComponent(state.workspaceId)}`);
        if (active.current && latest.current === started) setStatusEntry({ value, authorizationVersion: started });
      }
    } catch (failure) {
      if (active.current && latest.current === started) setErrorEntry({ value: failure instanceof Error ? failure.message : 'The change was not confirmed. Review current status before explicitly retrying.', authorizationVersion: started });
    } finally { flight.current = false; if (active.current) setBusy(false); }
  }

  async function loadReview() {
    if (flight.current || !reviewChatId || !authorized || !state.online) return;
    const started = reviewKey;
    flight.current = true; setBusy(true); setReviewEntry(null); setSelection({ key: started, ids: [] }); setErrorEntry(null);
    try {
      const rows = await actions.request<Message[]>('GET', `/conversations/${encodeURIComponent(reviewChatId)}/messages?limit=30`);
      if (active.current && currentReview.current === started) setReviewEntry({ value: rows.filter(row => row.direction === 'outbound' && row.author_kind === 'unknown_owner_outgoing' && row.deleted !== true && row.text.trim()), authorizationVersion: version, chatId: reviewChatId });
    } catch (failure) {
      if (active.current && currentReview.current === started) setErrorEntry({ value: failure instanceof Error ? failure.message : 'Message examples are unavailable in this contact’s current scope.', authorizationVersion: version });
    } finally { flight.current = false; if (active.current) setBusy(false); }
  }

  function saveContact(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!connector || !validConsent || !phone.trim() || !title.trim()) return;
    void change('/integrations/whatsapp/contacts', { connector_id: connector.id, phone_number: phone.trim(), title: title.trim(), ...consent }, () => {
      setPhone(''); setTitle(''); setConsent(emptyConsent());
    });
  }

  if (state.mode !== 'live') return <section className={styles.panel} aria-label="WhatsApp Business connection"><h2>Connect WhatsApp Business</h2><p className={styles.bodyMuted}>You are exploring a synthetic demo. Sign in with Google to set up your eligible Business number and choose each contact’s permissions.</p></section>;
  if (!state.workspaceId) return null;
  return <section className={styles.panel} aria-label="WhatsApp Business connection">
    <div className={styles.panelHeader}><div><span className={styles.eyebrow}>Live account setup</span><h2>Connect WhatsApp Business</h2></div><button className="button secondary" disabled={busy || loading} onClick={() => void refreshStatus()}>Refresh connection status</button></div>
    <p className={styles.bodyMuted}>Connect an eligible Business number, then choose individual contacts. Phone linking is configured separately in the linked-device pilot. Live group access is unavailable. Connecting does not grant access to every chat.</p>
    {loading && <p className={styles.meta} role="status">Checking current Business connection…</p>}
    {error && <p className={styles.warning} role="alert">{error}</p>}
    {notice && <p className={styles.success} role="status">{notice}</p>}
    {status && <>
      {!!status.missing_requirements.length && <div className={styles.coverage}><div><strong>Business setup needs attention</strong><ul>{status.missing_requirements.map(item => <li key={item} className={styles.meta}>{requirements[item] ?? 'The deployment operator must complete the remaining Business setup.'}</li>)}</ul><p>Provider credentials are entered in the deployment settings, never in this app.</p></div></div>}
      <div className={styles.connectionFacts}><div><span>Incoming events</span><strong>{status.webhook_configured ? 'Webhook configured · live receipt still required' : 'Webhook setup required'}</strong></div><div><span>External sending</span><strong>{status.external_sends_enabled ? 'Enabled on the server · contact rules still apply' : 'Disabled on the server'}</strong></div><div><span>History access</span><strong>Eligible Business history or selected exports</strong></div></div>
      {!connector && <button className="button" disabled={!authorized || busy || !state.online} onClick={() => void change('/integrations/whatsapp/connect', { workspace_id: state.workspaceId })}>{busy ? 'Connecting…' : 'Set up my Business number'}</button>}
      {connector && <>
        {status.connectors.length > 1 && <label className={styles.field}><span>Business connection</span><select value={connector.id} disabled={busy} onChange={event => { setConnectorId(event.target.value); setPhone(''); setTitle(''); setConsent(emptyConsent()); setHistoryConsent(false); setReviewChatId(''); setReviewEntry(null); setNotice(''); }}>{status.connectors.map(row => <option key={row.id} value={row.id}>{row.account_id}</option>)}</select></label>}
        <div className={styles.coverage}><div><strong>{connector.status === 'connected' && currentLease ? 'Business identity verified at last check' : 'Business identity needs verification'}</strong><p>{connector.authorized_contacts} contacts with read and retain permission · {connector.received_messages} incoming messages observed · {connector.delivered_messages} deliveries confirmed.</p><p>{connector.live_delivery_verified ? 'A provider delivery receipt has been observed. Each new message still needs its own receipt.' : 'Live message delivery has not been verified. Identity verification alone does not prove replies are working.'}</p></div></div>
        <div className={styles.formFoot}><button className="button secondary" disabled={!authorized || busy || !state.online} onClick={() => void change(`/connectors/${encodeURIComponent(connector.id)}/verify`)}>Verify Business number</button></div>
        <form onSubmit={saveContact} aria-label="Authorize an individual WhatsApp contact">
          <h3>Choose one contact’s scope</h3><p className={styles.meta}>Saving replaces this contact’s permission choices. Every choice starts off. Reading and retention are required for learning, drafting or sending.</p>
          <div className={styles.formGrid}><label className={styles.field}><span>Contact name</span><input required maxLength={160} value={title} disabled={busy || !authorized} onChange={event => setTitle(event.target.value)}/></label><label className={styles.field}><span>Full international WhatsApp number</span><input required type="tel" inputMode="tel" autoComplete="off" maxLength={40} placeholder="Country code and phone number" value={phone} disabled={busy || !authorized} onChange={event => setPhone(event.target.value)}/></label></div>
          <fieldset className={styles.fieldset} disabled={busy || !authorized}><legend>Only this contact’s permissions</legend>{choices.map(([key, label]) => <label key={key} className={styles.checkbox}><input type="checkbox" checked={consent[key]} onChange={event => setConsent(previous => ({ ...previous, [key]: event.target.checked }))}/><span>{label}</span></label>)}</fieldset>
          {!prerequisites && <p className={styles.meta}>Choose read and retain before enabling learning, drafts or sends.</p>}{consent.send && !consent.recipient_opted_in && <p className={styles.meta}>Recipient opt-in is required for sending.</p>}
          <p className={styles.meta}>Sending permission creates no Auto rule or message. Sharing between contacts remains off. Earlier messages are not imported by adding a contact.</p>
          <div className={styles.formFoot}><button className="button" disabled={!authorized || busy || !state.online || !validConsent || !phone.trim() || !title.trim()}>{busy ? 'Saving contact scope…' : 'Save this contact’s scope'}</button></div>
        </form>
        <div className={styles.coverage}><div><strong>Optional eligible Business app history</strong><p>Approved Meta Coexistence onboarding and provider history-sharing permission can recover eligible direct-chat history, up to 180 days. Group history is unsupported. Select read and retain permissions for each intended contact first.</p><p>Only request this during the eligible onboarding window. A request acknowledgement does not prove history arrived; the original request is never blindly repeated.</p>
          {historyRequestStatus ? <p role="status">{historyRequestStatus === 'accepted' ? 'Meta accepted the original history request. History arrival and completeness still need verification.' : historyRequestStatus === 'failed' ? 'The original history request was rejected. Review the eligible setup with your deployment operator; no request is automatically repeated.' : 'The original history request has an unresolved outcome. Review its provider status with your deployment operator before taking further action.'}</p> : <label className={styles.checkbox}><input type="checkbox" checked={historyConsent} disabled={busy} onChange={event => setHistoryConsent(event.target.checked)}/><span>I enabled history sharing during approved Business app onboarding.</span></label>}
          <button className="button secondary" disabled={!authorized || !historyEligible || !historyConsent || !!historyRequestStatus || busy || !state.online} onClick={() => void change('/integrations/whatsapp/history-sync', { connector_id: connector.id })}>{historyRequestStatus ? 'Original history request recorded' : 'Request eligible Business history'}</button></div></div>
        {!!reviewChats.length && <div>
          <h3>Review your own writing examples</h3><p className={styles.meta}>Business messages may be written by several people. Select only messages you personally wrote. Confirmation grants no learning permission; learning remains a separate choice for this contact.</p>
          <label className={styles.field}><span>Contact for writing review</span><select value={reviewChats.some(chat => chat.id === reviewChatId) ? reviewChatId : ''} disabled={busy} onChange={event => { setReviewChatId(event.target.value); setReviewEntry(null); setSelection({ key: '', ids: [] }); }}><option value="">Choose one permitted contact</option>{reviewChats.map(chat => <option key={chat.id} value={chat.id}>{chat.title}</option>)}</select></label>
          <button className="button secondary" disabled={!reviewChatId || !reviewChats.some(chat => chat.id === reviewChatId) || !authorized || busy || !state.online} onClick={() => void loadReview()}>Load reviewable messages</button>
          {examples && <fieldset className={styles.fieldset} disabled={busy}><legend>Personally authored examples · latest 30 messages only</legend>{examples.length ? examples.map(message => <label className={styles.checkbox} key={message.id}><input type="checkbox" checked={selectedIds.includes(message.id)} onChange={event => setSelection({ key: reviewKey, ids: event.target.checked ? [...selectedIds, message.id] : selectedIds.filter(id => id !== message.id) })}/><span>I personally wrote this message:<blockquote className={styles.evidenceQuote}>{message.text}</blockquote></span></label>) : <p className={styles.meta}>No unreviewed outgoing examples are available in this selected batch. This does not prove all history has been reviewed.</p>}</fieldset>}
          {!!examples?.length && <button className="button" disabled={!selectedIds.length || !authorized || busy || !state.online} onClick={() => void change('/integrations/whatsapp/owner-authorship', { conversation_id: reviewChatId, message_ids: selectedIds, confirm_authored_by_owner: true }, () => { setReviewEntry(null); setSelection({ key: '', ids: [] }); })}>Confirm only my selected messages</button>}
        </div>}
      </>}
    </>}
  </section>;
}
