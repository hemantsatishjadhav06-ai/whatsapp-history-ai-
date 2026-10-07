import React, { useCallback, useEffect, useRef, useState } from 'react';
import { AppState, Switch, View } from 'react-native';
import { useFocusEffect } from 'expo-router';
import { personalWritingExamples } from '@milo/contracts';
import { useMilo } from '../lib/state';
import { snapshotVersion, type PrivateResult } from '../lib/private-results';
import { Body, Button, Card, Heading, styles } from './ui';

export function PersonalAuthorship({ conversationId, title }: { conversationId: string; title: string }) {
  const app = useMilo(); const scope = `${app.mode}:${snapshotVersion(app.snapshot)}:${conversationId}`;
  const latest = useRef(scope); latest.current = scope; const mounted = useRef(false); const flight = useRef(false);
  const [focused, setFocused] = useState(false); const [foreground, setForeground] = useState(AppState.currentState === 'active');
  const [entry, setEntry] = useState<PrivateResult<ReturnType<typeof personalWritingExamples>> | null>(null);
  const [selected, setSelected] = useState<{ scope: string; ids: string[] }>({ scope, ids: [] });
  const [error, setError] = useState<PrivateResult<string> | null>(null); const [busy, setBusy] = useState(false); const [notice, setNotice] = useState('');
  const visible = focused && foreground; const visibility = useRef(visible); visibility.current = visible;
  const sequence = useRef(0);
  const examples = visible && entry?.version === scope ? entry.value : null; const shownError = visible && error?.version === scope ? error.value : null;
  const ids = selected.scope === scope ? selected.ids.filter(id => examples?.some(row => row.id === id)) : [];
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => { setSelected({ scope, ids: [] }); setNotice(''); }, [scope]);
  useFocusEffect(useCallback(() => { setFocused(true); return () => { setFocused(false); setEntry(null); sequence.current++; }; }, []));
  useEffect(() => { const listener = AppState.addEventListener('change', state => { setForeground(state === 'active'); setEntry(null); sequence.current++; }); return () => listener.remove(); }, []);
  async function review() {
    if (flight.current || !app.online || app.mode !== 'live' || !visible) return;
    const started = scope; const id = ++sequence.current; flight.current = true; setBusy(true); setEntry(null); setError(null); setNotice(''); setSelected({ scope, ids: [] });
    try {
      const value = await app.request<unknown>(`/v1/conversations/${encodeURIComponent(conversationId)}/messages?limit=30`);
      if (mounted.current && latest.current === started && sequence.current === id && visibility.current) setEntry({ value: personalWritingExamples(value, conversationId), version: started });
    } catch { if (mounted.current && latest.current === started && sequence.current === id) setError({ value: 'Writing examples could not be confirmed in this conversation’s current scope. Refresh the examples before trying again.', version: started }); }
    finally { flight.current = false; if (mounted.current) setBusy(false); }
  }
  async function confirm() {
    if (flight.current || !app.online || !ids.length || app.mode !== 'live' || !visible) return;
    const started = scope; const id = ++sequence.current; flight.current = true; setBusy(true); setError(null); setNotice(''); let acknowledged = false;
    try {
      const result = await app.request<{ status: string; conversation_id: string; confirmed_count: number }>('/v1/integrations/whatsapp/personal/authorship/confirm', { method: 'POST', body: { conversation_id: conversationId, message_ids: ids, confirm_authored_by_owner: true } });
      if (!mounted.current || latest.current !== started || sequence.current !== id || !visibility.current) return;
      if (result.status !== 'confirmed' || result.conversation_id !== conversationId || !Number.isInteger(result.confirmed_count) || result.confirmed_count < 0 || result.confirmed_count > ids.length) throw new Error('Confirmation acknowledgement was unavailable');
      acknowledged = true;
      setEntry(null); setSelected({ scope: started, ids: [] });
      setNotice(`${result.confirmed_count} writing example(s) were acknowledged as yours. Learning still requires separate permission.`);
      if (!await app.refresh()) throw new Error('Current writing authority was not confirmed');
    } catch { if (mounted.current && latest.current === started && sequence.current === id) { setEntry(null); setSelected({ scope: started, ids: [] }); setError({ value: acknowledged ? 'Your writing confirmation was acknowledged, but current examples could not reload. Refresh the examples to review current authority.' : 'Authorship confirmation was not confirmed. Reload current examples before an explicit retry; no confirmation was automatically repeated.', version: started }); } }
    finally { flight.current = false; if (mounted.current) setBusy(false); }
  }
  return <Card><Heading>Review your own phone writing</Heading><Body small>Only for {title}. Outgoing phone messages can be written by someone else. Select only messages you personally wrote. Confirmation grants no learning or send permission.</Body>
    <Button secondary label="Read latest writing examples" disabled={busy || !app.online || !visible} onPress={() => void review()}/>
    {shownError && <View accessibilityLiveRegion="assertive"><Body>{shownError}</Body></View>}{notice && <View accessibilityLiveRegion="polite"><Body>{notice}</Body></View>}
    {examples && <><Body small>Personally authored examples · latest 30 messages only</Body>{examples.length ? examples.map(row => <View key={row.id} style={[styles.row, { minHeight: 48, flexWrap: 'nowrap' }]}><Switch accessibilityLabel={`I personally wrote this message: ${row.text}`} value={ids.includes(row.id)} disabled={busy} onValueChange={checked => setSelected({ scope, ids: checked ? [...ids, row.id] : ids.filter(id => id !== row.id) })}/><View style={{ flex: 1 }}><Body small>I personally wrote this message:</Body><Body>{row.text}</Body></View></View>) : <Body small>No unreviewed outgoing examples are available in this selected batch. This does not prove all history has been reviewed.</Body>}
      <Button label="Confirm selected messages are my writing" disabled={busy || !app.online || !ids.length} onPress={() => void confirm()}/></>}
  </Card>;
}
