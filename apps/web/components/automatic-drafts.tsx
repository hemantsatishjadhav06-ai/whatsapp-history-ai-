'use client';

import { useEffect, useRef, useState } from 'react';
import { automaticDraftReason, automaticDraftState, type AutomaticDraftChoice } from '@milo/contracts';
import type { MiloActions, MiloState } from '../lib/types';
import { currentToolResult, toolAuthorizationVersion, type PrivateToolResult } from '../lib/tool-privacy';
import styles from './tools.module.css';

export function AutomaticDrafts({ state, actions, conversationId, title }: { state: MiloState; actions: MiloActions; conversationId: string; title: string }) {
  const scope = `${toolAuthorizationVersion(state)}:${conversationId}`;
  const latest = useRef(scope); latest.current = scope;
  const actionRef = useRef(actions); actionRef.current = actions;
  const mounted = useRef(false); const flight = useRef(false); const sequence = useRef(0);
  const [entry, setEntry] = useState<PrivateToolResult<AutomaticDraftChoice> | null>(null);
  const [error, setError] = useState<PrivateToolResult<string> | null>(null);
  const [loading, setLoading] = useState(true); const [busy, setBusy] = useState(false); const [attempt, setAttempt] = useState(0);
  const [now, setNow] = useState(Date.now());
  const [days, setDays] = useState('7'); const [limit, setLimit] = useState('3');
  const current = currentToolResult(entry, scope); const shownError = currentToolResult(error, scope);
  const path = `/conversations/${encodeURIComponent(conversationId)}/automatic-drafts`;
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => {
    let active = true; const id = ++sequence.current;
    setEntry(null); setError(null); setLoading(state.mode === 'live');
    if (state.mode !== 'live') return;
    void actionRef.current.request<AutomaticDraftChoice>('GET', path).then(value => {
      if (!active || latest.current !== scope || sequence.current !== id || value.conversation_id !== conversationId) return;
      setEntry({ value, authorizationVersion: scope }); setLimit(String(value.max_drafts_per_hour));
    }).catch(() => { if (active && latest.current === scope && sequence.current === id) setError({ value: 'Automatic draft status could not be confirmed. Refresh it before changing this choice.', authorizationVersion: scope }); })
      .finally(() => { if (active && latest.current === scope && sequence.current === id) setLoading(false); });
    return () => { active = false; };
  }, [scope, path, conversationId, state.mode, attempt]);

  useEffect(() => {
    setNow(Date.now());
    if (!current?.enabled || !current.expires_at) return;
    const expiresAt = Date.parse(current.expires_at);
    if (!Number.isFinite(expiresAt) || expiresAt <= Date.now()) return;
    let timer: ReturnType<typeof setTimeout>;
    const tick = () => { setNow(Date.now()); const remaining = expiresAt - Date.now(); if (remaining > 0) timer = setTimeout(tick, Math.min(remaining, 2_147_483_647)); };
    timer = setTimeout(tick, Math.min(expiresAt - Date.now(), 2_147_483_647));
    return () => clearTimeout(timer);
  }, [current]);

  async function save(enabled: boolean) {
    if (!current || flight.current || !state.online) return;
    const started = scope; flight.current = true; sequence.current++; setBusy(true); setError(null);
    try {
      const value = await actions.request<AutomaticDraftChoice>('PUT', path, {
        enabled, expected_version: current.version,
        ...(enabled ? { expires_at: new Date(Date.now() + Number(days) * 86_400_000).toISOString(), max_drafts_per_hour: Number(limit) } : {}),
      });
      if (mounted.current && latest.current === started && value.conversation_id === conversationId) setEntry({ value, authorizationVersion: started });
    } catch {
      if (mounted.current && latest.current === started) { setEntry(null); setError({ value: 'This choice was not confirmed. Refresh its current status before explicitly trying again.', authorizationVersion: started }); }
    } finally { flight.current = false; if (mounted.current) setBusy(false); }
  }

  if (state.mode !== 'live') return <p className={styles.meta}>Automatic draft preparation requires your signed-in conversation. The demo creates no automatic draft choice.</p>;
  const status = current ? automaticDraftState(current, now) : null;
  const valid = Number.isInteger(Number(days)) && Number(days) >= 1 && Number(days) <= 30 && Number.isInteger(Number(limit)) && Number(limit) >= 1 && Number(limit) <= 10;
  return <section aria-label={`Automatic drafts for ${title}`} className={styles.coverage}>
    <div><h3>Prepare drafts automatically</h3><p>Only for {title}. Fresh eligible messages can prepare unsent replies for your review. Every automatically prepared draft waits for your approval before sending.</p>
      <p className={styles.meta}>Reading, retention and draft permission are checked separately. An imported history file never triggers this choice.</p>
      {loading && <p role="status">Checking automatic draft status…</p>}{shownError && <p role="alert" className={styles.warning}>{shownError}</p>}
      {current && <><p role="status">{status?.status === 'ready' ? 'Enabled · eligible messages can prepare drafts.' : status?.status === 'disabled' ? 'Off · no automatic draft preparation is authorized.' : `Enabled · preparation is blocked. ${automaticDraftReason(status?.reason ?? null)}`}</p>
        {current.enabled && <p className={styles.meta}>Expires {current.expires_at ? new Date(current.expires_at).toLocaleString() : 'at an unconfirmed time'} · maximum {current.max_drafts_per_hour} drafts per hour.</p>}
        {current.latest_job && <p className={styles.meta}>Latest preparation: {current.latest_job.status.replaceAll('_', ' ')}{current.latest_job.reason_code && ` · ${automaticDraftReason(current.latest_job.reason_code)}`}{current.latest_job.expires_at && ` · expires ${new Date(current.latest_job.expires_at).toLocaleString()}`}.</p>}
        <div className={styles.formGrid}><label className={styles.field}><span>Authorize for days</span><input type="number" min={1} max={30} value={days} disabled={busy} onChange={event => setDays(event.target.value)}/></label><label className={styles.field}><span>Maximum drafts per hour</span><input type="number" min={1} max={10} value={limit} disabled={busy} onChange={event => setLimit(event.target.value)}/></label></div>
        <button type="button" className="button" disabled={busy || !state.online || !valid} onClick={() => void save(true)}>{busy ? 'Saving choice…' : current.enabled ? 'Renew automatic draft choice' : 'Enable automatic draft preparation'}</button>
        {current.enabled && <button type="button" className="button secondary" disabled={busy || !state.online} onClick={() => void save(false)}>Revoke automatic draft preparation</button>}
        {current.latest_job?.draft_id && <button type="button" className="button secondary" onClick={() => actions.selectConversation(conversationId)}>Review draft and its sources</button>}
      </>}
      <button type="button" className="button secondary" disabled={busy || loading} onClick={() => setAttempt(value => value + 1)}>Refresh automatic draft status</button>
    </div>
  </section>;
}

export function AutomaticDraftChoices({ state, actions }: { state: MiloState; actions: MiloActions }) {
  const [selectedId, setSelectedId] = useState('');
  const selected = state.data.conversations.find(row => row.id === selectedId && row.kind === 'contact');
  return <section className={styles.panel} aria-label="Automatic draft choices"><h2>Unsent replies, prepared for you</h2><p className={styles.bodyMuted}>Choose a person and explicitly enable preparation. Ordinary draft permission enables no automatic preparation.</p>
    <label className={styles.field}><span>Conversation for automatic drafts</span><select value={selected?.id ?? ''} onChange={event => setSelectedId(event.target.value)}><option value="">Choose a person</option>{state.data.conversations.filter(row => row.kind === 'contact').map(row => <option value={row.id} key={row.id}>{row.title}</option>)}</select></label>
    {selected && <AutomaticDrafts key={`${state.user.id}:${state.workspaceId}:${selected.id}`} state={state} actions={actions} conversationId={selected.id} title={selected.title}/>}
  </section>;
}
