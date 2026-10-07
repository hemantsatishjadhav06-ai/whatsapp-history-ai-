'use client';

import { useEffect, useRef, useState } from 'react';
import { personalWritingExamples } from '@milo/contracts';
import type { MiloActions, MiloState } from '../lib/types';
import { currentToolResult, toolAuthorizationVersion, type PrivateToolResult } from '../lib/tool-privacy';
import styles from './tools.module.css';

export function PersonalAuthorship({ state, actions, conversationId, title }: { state: MiloState; actions: MiloActions; conversationId: string; title: string }) {
  const scope = `${toolAuthorizationVersion(state)}:${conversationId}`;
  const latest = useRef(scope); latest.current = scope; const mounted = useRef(false); const flight = useRef(false);
  const [entry, setEntry] = useState<PrivateToolResult<ReturnType<typeof personalWritingExamples>> | null>(null);
  const [selected, setSelected] = useState<{ scope: string; ids: string[] }>({ scope, ids: [] });
  const [error, setError] = useState<PrivateToolResult<string> | null>(null); const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState('');
  const examples = currentToolResult(entry, scope); const shownError = currentToolResult(error, scope);
  const ids = selected.scope === scope ? selected.ids.filter(id => examples?.some(row => row.id === id)) : [];
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => { setSelected({ scope, ids: [] }); setNotice(''); }, [scope]);
  async function review() {
    if (flight.current || !state.online || state.mode !== 'live') return;
    const started = scope; flight.current = true; setBusy(true); setEntry(null); setError(null); setNotice(''); setSelected({ scope, ids: [] });
    try {
      const value = await actions.request<unknown>('GET', `/conversations/${encodeURIComponent(conversationId)}/messages?limit=30`);
      if (mounted.current && latest.current === started) setEntry({ value: personalWritingExamples(value, conversationId), authorizationVersion: started });
    } catch { if (mounted.current && latest.current === started) setError({ value: 'Writing examples could not be confirmed in this conversation’s current scope. Refresh the examples before trying again.', authorizationVersion: started }); }
    finally { flight.current = false; if (mounted.current) setBusy(false); }
  }
  async function confirm() {
    if (flight.current || !state.online || !ids.length || state.mode !== 'live') return;
    const started = scope; flight.current = true; setBusy(true); setError(null); setNotice(''); let acknowledged = false;
    try {
      const result = await actions.request<{ status: string; conversation_id: string; confirmed_count: number }>('POST', '/integrations/whatsapp/personal/authorship/confirm', { conversation_id: conversationId, message_ids: ids, confirm_authored_by_owner: true });
      if (!mounted.current || latest.current !== started) return;
      if (result.status !== 'confirmed' || result.conversation_id !== conversationId || !Number.isInteger(result.confirmed_count) || result.confirmed_count < 0 || result.confirmed_count > ids.length) throw new Error('Confirmation acknowledgement was unavailable');
      acknowledged = true;
      setEntry(null); setSelected({ scope: started, ids: [] });
      setNotice(`${result.confirmed_count} writing example(s) were acknowledged as yours. Learning still requires separate permission.`);
      await actions.refresh();
    } catch { if (mounted.current && latest.current === started) { setEntry(null); setSelected({ scope: started, ids: [] }); setError({ value: acknowledged ? 'Your writing confirmation was acknowledged, but current examples could not reload. Refresh the examples to review current authority.' : 'Authorship confirmation was not confirmed. Reload current examples before an explicit retry; no confirmation was automatically repeated.', authorizationVersion: started }); } }
    finally { flight.current = false; if (mounted.current) setBusy(false); }
  }
  return <section className={styles.coverage} aria-label={`Your writing examples for ${title}`}><div><h3>Review your own phone writing</h3><p className={styles.meta}>Outgoing phone messages can be written by someone else. Select only messages you personally wrote. Confirmation grants no learning or send permission.</p>
    <button type="button" className="button secondary" disabled={busy || !state.online} onClick={() => void review()}>Read latest writing examples</button>
    {shownError && <p role="alert" className={styles.warning}>{shownError}</p>}{notice && <p role="status">{notice}</p>}
    {examples && <fieldset className={styles.fieldset} disabled={busy}><legend>Personally authored examples · latest 30 messages only</legend>{examples.length ? examples.map(row => <label className={styles.checkbox} key={row.id}><input type="checkbox" checked={ids.includes(row.id)} onChange={event => setSelected({ scope, ids: event.target.checked ? [...ids, row.id] : ids.filter(id => id !== row.id) })}/><span>I personally wrote this message:<blockquote className={styles.evidenceQuote}>{row.text}</blockquote></span></label>) : <p className={styles.meta}>No unreviewed outgoing examples are available in this selected batch. This does not prove all history has been reviewed.</p>}</fieldset>}
    {examples && <button type="button" className="button" disabled={busy || !state.online || !ids.length} onClick={() => void confirm()}>Confirm selected messages are my writing</button>}
  </div></section>;
}
