'use client';

import { useEffect, useRef, useState } from 'react';
import { ownerAnswerIsCurrent, ownerAnswerExpiresAt, type OwnerAnswer } from '@milo/contracts';
import { shortTime, textValue, useMilo } from '@/lib/state';
import type { DataRecord, Message } from '@/lib/types';
import { Icon, Milo } from './icons';

export function Assistant({ contextId, intent = 'catch_me_up', onClose }: { contextId?: string; intent?: string; onClose(): void }) {
  const { state, actions, authorizedSnapshot } = useMilo();
  const [kind, setKind] = useState(intent);
  const [text, setText] = useState('');
  const [chosenId, setChosenId] = useState(contextId ?? '');
  const effectiveContextId = ['write_with_me', 'teach_me', 'ask_me'].includes(kind) ? chosenId : contextId;
  const chat = state.data.conversations.find(item => item.id === effectiveContextId);
  const [processing, setProcessing] = useState(false);
  const [result, setResult] = useState('');
  const [error, setError] = useState('');
  const [digest, setDigest] = useState<Record<string, unknown> | null>(null);
  const [sources, setSources] = useState<Message[]>([]);
  const [selectedSources, setSelectedSources] = useState<string[]>([]);
  const [proposal, setProposal] = useState<DataRecord | null>(null);
  const [answer, setAnswer] = useState<OwnerAnswer | null>(null);
  const currentAnswer = answer && ownerAnswerIsCurrent(answer, authorizedSnapshot) ? answer : null;
  const scopedChats = effectiveContextId ? state.data.conversations.filter(item => item.id === effectiveContextId) : state.data.conversations;
  const scopeKey = JSON.stringify([state.mode, state.user.id, state.workspaceId, kind, effectiveContextId ?? null,
    scopedChats.map(item => [item.id, item.revision, item.permissions ?? null])]);
  const choiceKey = JSON.stringify([state.mode,state.user.id,state.workspaceId,kind,effectiveContextId ?? null]);
  const choiceFence = useRef({key:choiceKey,generation:0});
  if (choiceFence.current.key !== choiceKey) choiceFence.current = {key:choiceKey,generation:choiceFence.current.generation + 1};
  const scopeFence = useRef({ key: scopeKey, generation: 0 });
  if (scopeFence.current.key !== scopeKey) scopeFence.current = { key: scopeKey, generation: scopeFence.current.generation + 1 };
  const initialScope = useRef(scopeKey);
  const hasServerContent = useRef(false);
  const textarea = useRef<HTMLTextAreaElement>(null);
  const closeButton = useRef<HTMLButtonElement>(null);
  useEffect(() => { actions.clearOwnerAnswerScope(); return () => actions.clearOwnerAnswerScope(); }, [actions,choiceKey]);
  useEffect(() => {
    if (!answer) return;
    if (!ownerAnswerIsCurrent(answer, authorizedSnapshot)) { setAnswer(null); setResult(''); return; }
    const timer = window.setTimeout(() => setAnswer(previous => previous === answer ? null : previous), Math.max(0, (ownerAnswerExpiresAt(answer) ?? 0) - Date.now()));
    return () => window.clearTimeout(timer);
  }, [answer, authorizedSnapshot]);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    textarea.current?.focus();
    const onEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
      if (event.key === 'Tab') {
        const items = Array.from(document.querySelectorAll<HTMLElement>('.assistant-sheet button:not(:disabled), .assistant-sheet input, .assistant-sheet textarea, .assistant-sheet select'));
        if (event.shiftKey && document.activeElement === items[0]) { event.preventDefault(); items.at(-1)?.focus(); }
        else if (!event.shiftKey && document.activeElement === items.at(-1)) { event.preventDefault(); items[0]?.focus(); }
      }
    };
    document.addEventListener('keydown', onEscape);
    return () => { document.removeEventListener('keydown', onEscape); previous?.focus(); };
  }, [onClose]);
  useEffect(() => {
    if (initialScope.current === scopeKey) return;
    initialScope.current = scopeKey;
    const validOwnerAnswer = kind === 'ask_me' && currentAnswer?.conversation_id === effectiveContextId;
    setDigest(null); setProposal(null); setSources([]); setSelectedSources([]); setProcessing(false);
    if (!validOwnerAnswer) {
      setAnswer(null); setResult('');
      if (hasServerContent.current) setError('Your selected scope or source context changed. Previous results and evidence were cleared; your typed instruction is still here.');
      hasServerContent.current = false;
    }
  }, [scopeKey]);
  useEffect(() => {
    setSelectedSources([]); setSources([]);
    if (kind !== 'teach_me' || !chosenId) return;
    if (!chat) return;
    let active = true;
    const capturedGeneration = scopeFence.current.generation;
    void actions.request<Message[]>('GET', `/conversations/${chosenId}/messages?derived_evidence=true`).then(items => {
      if (active && capturedGeneration === scopeFence.current.generation) { setSources(items); hasServerContent.current = items.length > 0; }
    }).catch(failure => { if (active && capturedGeneration === scopeFence.current.generation) setError(failure instanceof Error ? failure.message : 'Source evidence is unavailable.'); });
    return () => { active = false; };
  }, [chosenId, kind, actions, scopeKey]);
  async function submit(event: React.FormEvent) {
    event.preventDefault(); setError(''); setProcessing(true); setResult(''); setDigest(null); setProposal(null); setAnswer(null);
    const capturedGeneration = scopeFence.current.generation;
    const capturedChoice = choiceFence.current.generation;
    try {
      if (['write_with_me', 'teach_me', 'ask_me'].includes(kind) && !chosenId) throw new Error('Choose the exact conversation first. Home has no implicit recipient.');
      const body: Record<string, unknown> = { command: kind, workspace_id: state.workspaceId };
      if (kind === 'catch_me_up' && contextId) body.conversation_id = contextId;
      if (kind === 'write_with_me') { body.conversation_id = chosenId; body.instruction = text; }
      if (kind === 'ask_me') {
        if (!text.trim()) throw new Error('Enter a question about this conversation.');
        if (state.mode === 'demo') throw new Error('Conversation intelligence needs a signed-in workspace and a configured AI provider. The synthetic demo does not call a model.');
        body.conversation_id = chosenId; body.question = text;
      }
      if (kind === 'teach_me') {
        if (!selectedSources.length) throw new Error('Select the source messages that support this preference or fact.');
        body.conversation_id = chosenId; body.text = text; body.source_message_ids = selectedSources; body.status = 'candidate';
      }
      if ((kind === 'pause' || kind === 'resume') && contextId) body.conversation_id = contextId;
      const response = kind === 'pause' || kind === 'resume'
        ? { result: await actions.request('POST', contextId
          ? `/conversations/${encodeURIComponent(contextId)}/${kind === 'pause' ? 'takeover' : 'resume'}`
          : `/${kind}-all?workspace_id=${encodeURIComponent(state.workspaceId)}`) }
        : await actions.request<{ result: unknown }>('POST', '/assistant/commands', body);
      if (kind === 'ask_me') {
        const stillSelected = () => capturedChoice === choiceFence.current.generation;
        if (!stillSelected()) return;
        const fresh = await actions.confirmOwnerAnswer(response.result, stillSelected);
        if (!stillSelected() || !ownerAnswerIsCurrent(response.result,fresh)) return;
        hasServerContent.current = true;
        setAnswer(response.result);
        setResult('An answer for you, based on this conversation’s available evidence. No message or action was sent.');
        return;
      }
      if (capturedGeneration !== scopeFence.current.generation) return;
      hasServerContent.current = true;
      if (kind === 'catch_me_up') { setDigest(response.result as Record<string, unknown>); setResult('Here are the actual source updates returned in your selected scope.'); }
      else if (kind === 'write_with_me') { setProposal(response.result as DataRecord); setResult('A scoped draft is ready for your review. It has not been sent. Open the conversation to edit, approve exact text, or discard.'); }
      else if (kind === 'teach_me') setResult('Saved as a candidate memory in this conversation. Review its evidence in Memory before treating it as a confirmed fact.');
      else setResult(`${kind === 'pause' ? 'Pause' : 'Resume'} acknowledged${state.mode === 'demo' ? ' in the synthetic demo' : ' by the server'}. ${contextId ? 'Only this conversation was changed.' : 'External schedules remain durable and are rechecked before execution.'}`);
      await actions.refresh();
    } catch (failure) { if ((kind === 'ask_me' ? capturedChoice === choiceFence.current.generation : capturedGeneration === scopeFence.current.generation)) setError(failure instanceof Error ? failure.message : 'This request was not confirmed.'); }
    finally { if ((kind === 'ask_me' ? capturedChoice === choiceFence.current.generation : capturedGeneration === scopeFence.current.generation)) setProcessing(false); }
  }
  return <div className="sheet-backdrop" onMouseDown={event => { if (event.target === event.currentTarget) onClose(); }}><aside className="assistant-sheet" role="dialog" aria-modal="true" aria-labelledby="assistant-heading">
    <header className="assistant-header"><Milo size={48}/><div><h2 id="assistant-heading">A moment with Milo</h2><p>{chat ? `${chat.title} · this conversation only` : 'Home context · no recipient selected'}</p></div><button ref={closeButton} className="icon-button" onClick={onClose} aria-label="Close Milo"><Icon name="close"/></button></header>
    <div className="assistant-body"><div className="assistant-greeting"><Milo size={100}/><h3>{chat ? `Let’s think about ${chat.title}.` : 'What would make today lighter?'}</h3><p>I can catch you up, help with a reply, or learn a preference in the scope you choose.</p></div>
      <div className="assistant-presets">{[['catch_me_up','Catch me up'],['ask_me','Ask about this chat'],['write_with_me','Write with me'],['teach_me','Teach me'],['pause','Pause'],['resume','Resume']].map(([value,label]) => <button key={value} className={`chip ${kind === value ? 'selected' : ''}`} onClick={() => setKind(value)}>{label}</button>)}</div>
      <form onSubmit={submit} className="assistant-form">
        {['write_with_me', 'teach_me', 'ask_me'].includes(kind) && <label className="field">Exact conversation<select value={chosenId} onChange={event => setChosenId(event.target.value)} required><option value="">Choose a person or group</option>{state.data.conversations.map(item => <option value={item.id} key={item.id}>{item.title} · {item.account_label}</option>)}</select></label>}
        <label className="field">{kind === 'teach_me' ? 'Preference or fact to review' : kind === 'write_with_me' ? 'What would you like to say?' : kind === 'ask_me' ? 'Your question about this chat' : 'Ask Milo'}<textarea ref={textarea} value={text} onChange={event => setText(event.target.value)} required={kind === 'teach_me' || kind === 'ask_me'} rows={4} maxLength={2000} placeholder={kind === 'teach_me' ? 'For Maya, keep replies short and warm…' : kind === 'ask_me' ? 'What did we decide about the meeting?' : 'Catch me up, help me write, or teach a preference…'}/></label>
        {kind === 'teach_me' && chosenId && <fieldset className="evidence-picker"><legend>Choose supporting source messages</legend>{sources.slice(-12).map(source => <label className="source-choice" key={source.id}><input type="checkbox" checked={selectedSources.includes(source.id)} onChange={event => setSelectedSources(previous => event.target.checked ? [...previous,source.id] : previous.filter(id => id !== source.id))}/><span>{source.text.slice(0,180)}<small>{shortTime(source.provider_timestamp, state.timezone)} · {source.author_kind}</small></span></label>)}{!sources.length && <p className="fine-print">No available source messages in this scope.</p>}</fieldset>}
        <div className="assistant-controls"><button type="button" className="button secondary" onClick={() => setError('Voice transcription is not configured. Type your request here; no microphone has been activated.')}><Icon name="mic" size={17}/> Talk</button><button className="button" disabled={processing}>{processing ? 'Processing…' : 'Ask Milo'}<Icon name="arrow" size={17}/></button></div>
      </form>
      <div aria-live="polite">{error && <p className="notice error">{error}</p>}{result && (kind !== 'ask_me' || currentAnswer) && <div className="assistant-result"><span className="pill">{state.mode === 'demo' ? 'Synthetic outcome' : 'Server response'}</span><p>{result}</p>{proposal && <blockquote>{textValue(proposal.text)}</blockquote>}{currentAnswer && <><blockquote>{currentAnswer.text}</blockquote>{currentAnswer.missing_facts.length > 0 && <p className="fine-print">Still unknown: {currentAnswer.missing_facts.join('; ')}</p>}{currentAnswer.evidence_message_ids.map(id => <button key={id} className="text-button" onClick={() => { actions.navigate(`/inbox/${encodeURIComponent(chosenId)}?message=${encodeURIComponent(id)}`); onClose(); }}>Open supporting message <Icon name="arrow" size={14}/></button>)}</>}{digest && Array.isArray(digest.conversations) && (digest.conversations as Record<string, unknown>[]).map(item => {
        const latest = item.latest && typeof item.latest === 'object' ? item.latest as Record<string,unknown> : null;
        const id = textValue(item.conversation_id,textValue(item.id));
        return <div className="digest-source" key={id}><strong>{textValue(item.title,'Selected conversation')}</strong><p>{latest ? textValue(latest.preview,'No recent available update') : textValue(item.preview,'No recent available update')}</p><span className="fine-print">{latest ? shortTime(latest.timestamp, state.timezone) : shortTime(item.timestamp, state.timezone)} · {textValue(item.control_state).replaceAll('_',' ').toLowerCase()}</span><button className="text-button" onClick={() => { actions.navigate(`/inbox/${encodeURIComponent(id)}${latest?.source_ref ? `?message=${encodeURIComponent(String(latest.source_ref))}` : ''}`); onClose(); }}>Open source <Icon name="arrow" size={14}/></button></div>;
      })}{!digest && <button className="text-button" onClick={() => { actions.navigate(kind === 'teach_me' ? '/memory' : chosenId ? `/inbox/${chosenId}` : '/actions'); onClose(); }}>Open the detail <Icon name="arrow" size={15}/></button>}</div>}</div>
      <p className="fine-print">Typed commands keep the same account and permissions. Voice is optional and never grants send authority.</p>
    </div>
  </aside></div>;
}
