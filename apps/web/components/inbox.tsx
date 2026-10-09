'use client';

import { useEffect, useRef, useState } from 'react';
import { ApiError, draftRiskWarning, stateLabel } from '@milo/contracts';
import { shortTime, textValue, useMilo } from '@/lib/state';
import type { DataRecord, Message } from '@/lib/types';
import { Icon, Milo } from './icons';

export function Inbox({ selectedId, openAssistant }: { selectedId?: string; openAssistant(contextId: string): void }) {
  const { state, actions, messages, composers, setComposer } = useMilo();
  const [search, setSearch] = useState('');
  const [mode, setMode] = useState('all');
  const [account, setAccount] = useState('all');
  const [thread, setThread] = useState<Message[]>([]);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [review, setReview] = useState<DataRecord | null>(null);
  const [inspector, setInspector] = useState(false);
  const [receipt, setReceipt] = useState('');
  const [editingDraftId, setEditingDraftId] = useState<string | null>(null);
  const threadScope = useRef<string | null>(null);
  const chat = state.data.conversations.find(item => item.id === selectedId);
  const body = selectedId ? composers[selectedId] ?? '' : '';
  const riskWarning = draftRiskWarning(review);
  useEffect(() => {
    if (selectedId && !chat && state.mode === 'live') void actions.ensureConversation(selectedId).catch(failure => setError(failure instanceof Error ? failure.message : 'This conversation is unavailable.'));
  }, [selectedId,chat?.id,state.mode,actions]);
  useEffect(() => {
    setError(''); setReceipt(''); setEditingDraftId(null); setReview(state.data.drafts.find(draft => draft.conversation_id === selectedId && ['needs_approval','approved','ready'].includes(String(draft.status))) ?? null);
    if (!chat) { setThread([]); return; }
    let alive = true;
    if (threadScope.current !== chat.id) { threadScope.current = chat.id; setThread(messages[chat.id] ?? []); }
    const update = () => actions.request<Message[]>('GET', `/conversations/${chat.id}/messages`).then(items => {
      if (alive) setThread(items.map(item => ({ ...item, conversation_id: chat.id })));
    }).catch(failure => { if (alive) { if (failure instanceof ApiError && [401,403,404].includes(failure.status)) setThread([]); setError(`Thread could not refresh. ${failure instanceof Error ? failure.message : ''}`); } });
    void update();
    const timer = state.mode === 'live' ? window.setInterval(() => { void update(); },15000) : null;
    return () => { alive = false; if (timer !== null) window.clearInterval(timer); };
  }, [chat?.id, chat?.revision, state.mode]); // Revalidate content without jumping the owner's reading position.
  useEffect(() => {
    const source = new URLSearchParams(window.location.search).get('message');
    if (source) document.getElementById(`message-${source}`)?.scrollIntoView({ block: 'center' });
  }, [thread.length, selectedId]);
  const filtered = state.data.conversations.filter(item => item.title.toLowerCase().includes(search.toLowerCase())
    && (account === 'all' || item.connector_id === account)
    && (mode === 'all' || (mode === 'group' ? item.kind === 'group' : mode === 'takeover' ? item.control_state === 'HUMAN_TAKEOVER' : item.control_state === mode)));
  async function prepare(event: React.FormEvent) {
    event.preventDefault(); if (!chat || !body.trim()) return;
    setBusy(true); setError(''); setReceipt('');
    try {
      const result = editingDraftId ? await actions.request<DataRecord>('PATCH', `/drafts/${editingDraftId}`, { text:body })
        : await actions.request<DataRecord>('POST', `/conversations/${chat.id}/owner-drafts`,
          { text: body, expected_revision: chat.revision, expected_control_epoch: Number(chat.control_epoch ?? 0) });
      setReview(result); setEditingDraftId(null); await actions.refresh();
    } catch (failure) { setError(failure instanceof Error ? failure.message : 'The draft was not prepared.'); }
    finally { setBusy(false); }
  }
  async function approve() {
    if (!review) return; setBusy(true); setError('');
    try { setReview(await actions.request<DataRecord>('POST', `/drafts/${review.id}/approve`, { content_hash: review.content_hash })); }
    catch (failure) { setError(failure instanceof Error ? failure.message : 'Approval was not confirmed.'); }
    finally { setBusy(false); }
  }
  async function send() {
    if (!review || !chat) return; setBusy(true); setError('');
    try {
      const outcome = await actions.request<DataRecord>('POST', `/drafts/${review.id}/dispatch`);
      setReceipt(outcome.status === 'uncertain' ? 'Submission uncertain. No retry is available until reconciliation.'
        : `${state.mode === 'demo' ? 'Simulated acceptance' : stateLabel(String(outcome.status))}. ${chat.title} · this exact text only.`);
      setComposer(chat.id, ''); setReview(null); await actions.refresh();
    } catch (failure) { setError(failure instanceof Error ? failure.message : 'The server did not confirm submission. Check Activity before retrying.'); }
    finally { setBusy(false); }
  }
  async function discard() {
    if (!review) return;
    try { await actions.request('POST', `/drafts/${review.id}/reject`); setReview(null); }
    catch (failure) { setError(failure instanceof Error ? failure.message : 'Discard was not confirmed.'); }
  }
  async function resume() {
    if (!chat) return; setBusy(true); setError('');
    try { await actions.request('POST', `/conversations/${chat.id}/resume`); await actions.refresh(); }
    catch (failure) { setError(failure instanceof Error ? failure.message : 'Resume was not confirmed.'); }
    finally { setBusy(false); }
  }
  return <div className={`inbox-workspace ${chat ? 'chat-open' : ''} ${inspector ? 'inspector-open' : ''}`}>
    <section className="conversation-list" aria-labelledby="inbox-heading"><div className="inbox-list-header"><h1 id="inbox-heading">Inbox <span>{state.data.conversations.length}</span></h1><p>A person, a group, a little context.</p></div>
      <label className="list-search"><Icon name="search" size={17}/><input aria-label="Search conversations" placeholder="Find a conversation" value={search} onChange={event => setSearch(event.target.value)}/></label>
      <div className="inbox-filters"><label className="sr-only" htmlFor="account-filter">Account filter</label><select id="account-filter" value={account} onChange={event => setAccount(event.target.value)}><option value="all">All accounts</option>{state.data.connections.map(item => <option key={item.id} value={item.id}>{item.label}</option>)}</select><label className="sr-only" htmlFor="mode-filter">Mode filter</label><select id="mode-filter" value={mode} onChange={event => setMode(event.target.value)}><option value="all">All modes</option><option value="AUTO_ENABLED">Auto</option><option value="DRAFT_MODE">Draft</option><option value="group">Groups</option><option value="takeover">Phone takeover</option></select></div>
      <div className="conversation-items">{filtered.map(item => <button className={`conversation-row ${chat?.id === item.id ? 'selected' : ''}`} key={item.id} onClick={() => actions.selectConversation(item.id)} aria-current={chat?.id === item.id ? 'page' : undefined}>
        <span className="person-avatar" style={{ background: item.avatar_color }}>{item.kind === 'group' ? <Icon name="memory" size={20}/> : item.title.slice(0,1)}</span><span className="conversation-description"><span className="conversation-name">{item.title}<time>{shortTime(item.timestamp, state.timezone)}</time></span><span className="conversation-preview">{item.preview || 'No available messages yet'}</span><span className={`mode-tag ${item.control_state === 'HUMAN_TAKEOVER' ? 'takeover' : ''}`}>{stateLabel(item.control_state)}</span></span>{item.unread > 0 && <span className="unread-count">{item.unread}</span>}
      </button>)}{!filtered.length && <div className="empty-list">No conversations match these filters.</div>}</div>
      {state.hasMore && <button className="button secondary load-more" onClick={() => { void actions.loadMore().catch(failure => setError(failure instanceof Error ? failure.message : 'More conversations are unavailable.')); }}>Load more conversations</button>}
      <div className="list-footnote"><Icon name="shield" size={14}/> Selected, readable conversations only</div>
    </section>
    {chat ? <section className="conversation-thread" aria-labelledby="thread-heading"><header className="thread-header"><button className="icon-button thread-back" aria-label="Back to inbox" onClick={() => actions.navigate('/inbox')}><Icon name="back"/></button><span className="person-avatar" style={{ background:chat.avatar_color }}>{chat.title.slice(0,1)}</span><div className="thread-identity"><h2 id="thread-heading">{chat.title}</h2><p><Icon name="whatsapp" size={13}/> {chat.account_label} · {chat.kind === 'group' ? 'Group audience' : 'Direct conversation'}</p></div><span className={`pill ${chat.control_state === 'HUMAN_TAKEOVER' ? 'warning' : ''}`}>{stateLabel(chat.control_state)}</span><button className="icon-button" onClick={() => setInspector(!inspector)} aria-label={inspector ? 'Hide conversation context' : 'Show conversation context'} aria-expanded={inspector}><Icon name="info"/></button></header>
      {(chat.control_state === 'HUMAN_TAKEOVER' || state.paused) && <div className="takeover-banner"><Icon name="pause" size={18}/><div><strong>{state.paused ? 'Replies & actions are paused.' : 'Your phone has the floor.'}</strong><p>{state.paused ? 'Global pause is acknowledged. Already submitted actions have their own receipts.' : `You replied to ${chat.title}. Auto stays paused until you explicitly resume.`}</p></div>{!state.paused && <button className="button secondary" onClick={resume} disabled={busy}>Resume this chat</button>}</div>}
      <div className="thread-messages" role="log" aria-label={`Messages with ${chat.title}`}><div className="thread-day">{state.mode === 'demo' ? 'SYNTHETIC CONVERSATION' : 'CURRENT AVAILABLE HISTORY'}</div>
        {thread.length ? thread.map(message => <div id={`message-${message.id}`} className={`message-row ${message.direction === 'outbound' ? 'outgoing' : ''}`} key={message.id}><div className="message-bubble">{message.reply_to && <div className="quoted-source">Reply to source · {message.reply_to}</div>}<p>{message.text}</p><div className="message-meta"><span>{message.author_kind === 'assistant' ? 'Milo · authorized reply' : message.author_kind === 'human_owner' ? 'You · human' : message.author_kind === 'other_authorized_operator' ? 'Authorized operator' : message.author_kind === 'unknown_owner_outgoing' ? 'Unverified owner activity' : chat.title}</span><time>{shortTime(message.provider_timestamp, state.timezone)}</time><button className="message-source" onClick={() => actions.navigate(`/inbox/${chat.id}?message=${encodeURIComponent(message.id)}`)} aria-label={`Open source message ${message.id}`}>Source</button></div></div></div>) : <div className="thread-empty"><Milo size={75}/><h3>A little context would help.</h3><p>No available messages in this selected conversation. Import history or wait for a verified connector observation.</p></div>}
      </div>
      <div className="chat-assistant-launch"><span><Milo size={27}/> Need a little help with {chat.title}?</span><button className="text-button" onClick={() => openAssistant(chat.id)}>Ask Milo <Icon name="sparkles" size={15}/></button></div>
      <div aria-live="polite">{error && <p className="notice error composer-notice">{error}</p>}{receipt && <p className="notice composer-notice">{receipt}</p>}</div>
      {review && <div className="draft-review"><div className="section-heading"><h3>Review this exact message</h3><span className="pill">{stateLabel(String(review.status))}</span></div><p className="draft-recipient">To {chat.title} · {chat.account_label} · {chat.provider_chat_id}</p><blockquote>{textValue(review.text)}</blockquote>{riskWarning && <p id="draft-risk-warning" className="notice warning"><Icon name="info" size={16}/>{riskWarning}</p>}<div className="review-actions">{review.status === 'approved' ? <button className="button" disabled={busy || state.paused} onClick={send} aria-describedby={riskWarning ? 'draft-risk-warning' : undefined}>{state.mode === 'demo' ? 'Simulate acceptance' : 'Send approved message'}</button> : <button className="button" disabled={busy || state.paused} onClick={approve} aria-describedby={riskWarning ? 'draft-risk-warning' : undefined}>Approve exact text</button>}<button className="button secondary" onClick={() => { setEditingDraftId(review.id); setComposer(chat.id,textValue(review.text)); setReview(null); }}>Edit locally</button><button className="text-button" onClick={discard}>Discard draft</button></div><p className="fine-print">The server rechecks recipient, scope and current context before submission.</p></div>}
      <form className="recipient-composer" onSubmit={prepare}><label htmlFor="recipient-message">Message {chat.title} <span>· {chat.account_label}</span></label><div className="composer-input"><textarea id="recipient-message" aria-label={`Message ${chat.title}`} value={body} onChange={event => setComposer(chat.id,event.target.value)} placeholder={`Write directly to ${chat.title}…`} maxLength={4096} rows={2}/><button className="button" disabled={busy || !body.trim() || chat.control_state === 'READ_ONLY' || chat.control_state === 'AI_OFF'}>{busy ? 'Preparing…' : 'Prepare message'}<Icon name="send" size={17}/></button></div><div className="fine-print">Enter adds a new line. This composer addresses {chat.title}, never Milo.</div></form>
    </section> : <section className="inbox-empty"><Milo size={100}/><h2>A person, a little context.</h2><p>Choose a conversation to read its history, control Auto, or write directly.</p>{error && <p className="notice error">{error}</p>}</section>}
    {chat && <aside className="scope-inspector"><div className="section-heading"><h3>This conversation</h3><button className="icon-button" aria-label="Close conversation context" onClick={() => setInspector(false)}><Icon name="close" size={17}/></button></div><span className="eyebrow">EXACT SCOPE</span><p><strong>{chat.title}</strong><br/>{chat.account_label}</p><code>{chat.provider_chat_id}</code><div className="inspector-block"><h4>Voice & history</h4><p>{textValue(state.data.styles.find(profile => profile.conversation_id === chat.id)?.sufficiency, 'No history profile yet')}</p><span className="fine-print">Available owner examples only. Imported history never triggers a send.</span><button className="text-button" onClick={() => actions.navigate('/memory')}>People & voice <Icon name="arrow" size={14}/></button></div><div className="inspector-block"><h4>Memories</h4>{state.data.memories.filter(memory => memory.conversation_id === chat.id).map(memory => <p className="memory-preview" key={memory.id}>{textValue(memory.text)}</p>)}<button className="text-button" onClick={() => actions.navigate('/memory')}>Review evidence <Icon name="arrow" size={14}/></button></div><div className="inspector-block"><h4>Effective mode</h4><p>{stateLabel(chat.control_state)}</p><span className="fine-print">Phone takeover stays until explicitly resumed. Account linking grants no send authority.</span></div></aside>}
  </div>;
}
