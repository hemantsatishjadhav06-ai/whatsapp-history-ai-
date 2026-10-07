import React, { useCallback, useEffect, useRef, useState } from 'react';
import { AppState, View } from 'react-native';
import { useFocusEffect, useRouter, type Href } from 'expo-router';
import { automaticDraftReason, automaticDraftState, type AutomaticDraftChoice } from '@milo/contracts';
import { useMilo } from '../lib/state';
import { snapshotVersion, type PrivateResult } from '../lib/private-results';
import { Body, Button, Card, Field, Heading } from './ui';

export function AutomaticDrafts({ conversationId, title }: { conversationId: string; title: string }) {
  const app = useMilo(); const router = useRouter();
  const scope = `${app.mode}:${snapshotVersion(app.snapshot)}:${conversationId}`;
  const latest = useRef(scope); latest.current = scope; const appRef = useRef(app); appRef.current = app;
  const mounted = useRef(false); const flight = useRef(false); const sequence = useRef(0);
  const [focused, setFocused] = useState(false); const [foreground, setForeground] = useState(AppState.currentState === 'active');
  const [entry, setEntry] = useState<PrivateResult<AutomaticDraftChoice> | null>(null);
  const [error, setError] = useState<PrivateResult<string> | null>(null);
  const [loading, setLoading] = useState(true); const [busy, setBusy] = useState(false); const [attempt, setAttempt] = useState(0);
  const [now, setNow] = useState(Date.now());
  const [days, setDays] = useState('7'); const [limit, setLimit] = useState('3');
  const current = focused && foreground && entry?.version === scope ? entry.value : null; const shownError = focused && foreground && error?.version === scope ? error.value : null;
  const path = `/v1/conversations/${encodeURIComponent(conversationId)}/automatic-drafts`;
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useFocusEffect(useCallback(() => { setFocused(true); return () => { setFocused(false); }; }, []));
  useEffect(() => { const listener = AppState.addEventListener('change', state => setForeground(state === 'active')); return () => listener.remove(); }, []);
  useEffect(() => {
    let active = true; const id = ++sequence.current;
    setEntry(null); setError(null); setLoading(app.mode === 'live' && focused && foreground);
    if (app.mode !== 'live' || !focused || !foreground) return;
    void appRef.current.request<AutomaticDraftChoice>(path).then(value => {
      if (!active || latest.current !== scope || sequence.current !== id || value.conversation_id !== conversationId) return;
      setEntry({ value, version: scope }); setLimit(String(value.max_drafts_per_hour));
    }).catch(() => { if (active && latest.current === scope && sequence.current === id) setError({ value: 'Automatic draft status could not be confirmed. Refresh it before changing this choice.', version: scope }); })
      .finally(() => { if (active && latest.current === scope && sequence.current === id) setLoading(false); });
    return () => { active = false; };
  }, [scope, path, conversationId, app.mode, focused, foreground, attempt]);

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
    if (!current || flight.current || !app.online || !focused || !foreground) return;
    const started = scope; const id = ++sequence.current; flight.current = true; setBusy(true); setError(null);
    try {
      const value = await app.request<AutomaticDraftChoice>(path, { method: 'PUT', body: {
        enabled, expected_version: current.version,
        ...(enabled ? { expires_at: new Date(Date.now() + Number(days) * 86_400_000).toISOString(), max_drafts_per_hour: Number(limit) } : {}),
      } });
      if (mounted.current && latest.current === started && sequence.current === id && value.conversation_id === conversationId) setEntry({ value, version: started });
    } catch {
      if (mounted.current && latest.current === started && sequence.current === id) { setEntry(null); setError({ value: 'This choice was not confirmed. Refresh its current status before explicitly trying again.', version: started }); }
    } finally { flight.current = false; if (mounted.current) setBusy(false); }
  }

  if (app.mode !== 'live') return <Body small>Automatic draft preparation requires your signed-in conversation. The demo creates no automatic draft choice.</Body>;
  const status = current ? automaticDraftState(current, now) : null;
  const valid = Number.isInteger(Number(days)) && Number(days) >= 1 && Number(days) <= 30 && Number.isInteger(Number(limit)) && Number(limit) >= 1 && Number(limit) <= 10;
  return <Card><Heading>Prepare drafts automatically</Heading><Body>Only for {title}. Fresh eligible messages can prepare unsent replies for your review. Every automatically prepared draft waits for your approval before sending.</Body>
    <Body small>Reading, retention and draft permission are checked separately. An imported history file never triggers this choice.</Body>
    {loading && <Body small>Checking automatic draft status…</Body>}{shownError && <View accessibilityLiveRegion="assertive"><Body>{shownError}</Body></View>}
    {current && <><View accessibilityLiveRegion="polite"><Body>{status?.status === 'ready' ? 'Enabled · eligible messages can prepare drafts.' : status?.status === 'disabled' ? 'Off · no automatic draft preparation is authorized.' : `Enabled · preparation is blocked. ${automaticDraftReason(status?.reason ?? null)}`}</Body></View>
      {current.enabled && <Body small>Expires {current.expires_at ? new Date(current.expires_at).toLocaleString() : 'at an unconfirmed time'} · maximum {current.max_drafts_per_hour} drafts per hour.</Body>}
      {current.latest_job && <Body small>Latest preparation: {current.latest_job.status.replaceAll('_', ' ')}{current.latest_job.reason_code && ` · ${automaticDraftReason(current.latest_job.reason_code)}`}{current.latest_job.expires_at && ` · expires ${new Date(current.latest_job.expires_at).toLocaleString()}`}.</Body>}
      <Field label="Authorize for days · 1–30" value={days} onChangeText={setDays} keyboardType="number-pad" editable={!busy}/><Field label="Maximum drafts per hour · 1–10" value={limit} onChangeText={setLimit} keyboardType="number-pad" editable={!busy}/>
      <Button label={busy ? 'Saving choice…' : current.enabled ? 'Renew automatic draft choice' : 'Enable automatic draft preparation'} disabled={busy || !app.online || !valid} onPress={() => void save(true)}/>
      {current.enabled && <Button secondary label="Revoke automatic draft preparation" disabled={busy || !app.online} onPress={() => void save(false)}/>}
      {current.latest_job?.draft_id && <Button secondary label="Review draft and its sources" onPress={() => router.push(`/detail/conversation/${conversationId}` as Href)}/>}
    </>}
    <Button secondary label="Refresh automatic draft status" disabled={busy || loading} onPress={() => setAttempt(value => value + 1)}/>
  </Card>;
}
