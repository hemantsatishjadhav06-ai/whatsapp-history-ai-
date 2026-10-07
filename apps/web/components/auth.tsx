'use client';

import { useEffect, useRef, useState } from 'react';
import { ApiError, createClient } from '@milo/contracts';
import { useMilo } from '@/lib/state';
import { Icon, Milo } from './icons';
import { ExportChatSetup } from './history-setup';
import { loadGoogleIdentity } from '@/lib/google-signin';
import { WhatsAppConnection } from './whatsapp-connection';
import { PersonalConnection } from './personal-connection';

export function Login() {
  const { state, actions, authenticate, browserSessionPresent, browserSessionChecking, resumeBrowserSession } = useMilo();
  const holder = useRef<HTMLDivElement>(null);
  const mounted = useRef(false);
  const [available, setAvailable] = useState(false);
  const [loading, setLoading] = useState(true);
  const [configured, setConfigured] = useState(false);
  const [error, setError] = useState('');
  const [attempt, setAttempt] = useState(0);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => {
    let alive = true;
    let exchanging = false;
    let refreshTimer: number | undefined;
    const client = createClient({ baseUrl: '/api' });
    if (browserSessionChecking) { setAvailable(false); setLoading(true); holder.current?.replaceChildren(); return; }
    if (browserSessionPresent) { setAvailable(false); setLoading(false); holder.current?.replaceChildren(); return; }
    async function prepare() {
      setLoading(true); setAvailable(false); setError(''); holder.current?.replaceChildren();
      try {
        const config = await client.request<{ google_configured: boolean; google_client_id?: string; client_id?: string }>('/auth/config');
        if (!alive) return;
        const clientId = config.google_client_id ?? config.client_id;
        setConfigured(Boolean(config.google_configured && clientId));
        if (!config.google_configured || !clientId) { if (alive) setLoading(false); return; }
        const identity = await loadGoogleIdentity();
        if (!alive) return;
        // Obtain the five-minute server nonce after SDK loading, then refresh
        // the visible button early. Old popup callbacks cannot exchange it.
        const { nonce } = await client.request<{ nonce: string }>('/auth/nonce');
        if (!alive || !holder.current) return;
        identity.initialize({ client_id: clientId, nonce, callback(response) {
          if (!alive || exchanging) return;
          exchanging = true; window.clearTimeout(refreshTimer);
          setLoading(true); setAvailable(false); holder.current?.replaceChildren();
          void authenticate(response.credential, nonce).catch(failure => {
            if (!mounted.current) return;
            setError(failure instanceof Error ? failure.message : 'Sign-in was not confirmed.'); setLoading(false);
          });
        } });
        identity.renderButton(holder.current, { theme: 'outline', size: 'large', text: 'continue_with', shape: 'pill', width: 280 });
        setAvailable(true); setLoading(false);
        refreshTimer = window.setTimeout(() => {
          if (alive && !exchanging) setAttempt(previous => previous + 1);
        }, 240_000);
      } catch (failure) { if (alive) { setError(failure instanceof Error ? failure.message : 'Sign-in is unavailable.'); setLoading(false); } }
    }
    void prepare(); return () => { alive = false; window.clearTimeout(refreshTimer); holder.current?.replaceChildren(); };
  }, [authenticate, attempt, browserSessionPresent, browserSessionChecking]);
  async function resume() {
    setLoading(true); setError('');
    try { await resumeBrowserSession(); }
    catch (failure) { if (mounted.current) { setError(failure instanceof Error ? failure.message : 'Your workspace is temporarily unavailable.'); setLoading(false); } }
  }
  async function signOut() {
    setLoading(true); setError('');
    try { await actions.logout(); }
    catch (failure) {
      if (mounted.current) {
        setError(`Sign-out was not confirmed. ${failure instanceof Error ? failure.message : 'Retry signing out.'}`); setLoading(false);
      }
    }
  }
  const shownError = error || (browserSessionPresent ? state.error : '');
  return <div className="auth-page"><div className="auth-brand"><Milo size={52}/><span>milo</span></div><section className="auth-card"><Milo size={126}/><span className="eyebrow">A LITTLE LESS NOISE</span><h1>Your people.<br/>A little more presence.</h1><p>A companion for the conversations that matter, with you in control of every scope.</p><div ref={holder} className="google-holder"/>{browserSessionPresent ? <><p role="status">You are signed in. Your private workspace is loaded separately.</p><button className="button google-button" disabled={loading} onClick={resume}>{loading ? 'Loading your workspace…' : 'Continue to your workspace'}</button><button className="button secondary" disabled={loading} onClick={signOut}>Sign out</button></> : !available && <button className="button google-button" disabled>{loading ? 'Checking sign-in…' : configured ? 'Google sign-in needs another attempt' : 'Google sign-in not configured'}</button>}{shownError && <p className="notice error" role="alert">{shownError}</p>}{!browserSessionPresent && error && <button className="button secondary" disabled={loading} onClick={() => setAttempt(previous => previous + 1)}>Retry Google sign-in</button>}<button className="button secondary" onClick={() => actions.navigate('/')}>{browserSessionPresent ? 'Return home' : 'Explore the synthetic demo'} <Icon name="arrow" size={17}/></button><p className="fine-print">Google verifies your identity. It does not grant Gmail, Calendar, Contacts or WhatsApp access.</p></section><p className="auth-footnote"><Icon name="shield" size={16}/> Your scope. Your voice. Your call.</p></div>;
}

export function Onboarding() {
  const { state, actions } = useMilo();
  const [step, setStep] = useState(state.workspaceId ? 1 : 0);
  const [timezone, setTimezone] = useState(state.timezone);
  const [language, setLanguage] = useState('English');
  const [name, setName] = useState('My Milo');
  const [selected, setSelected] = useState<string[]>([]);
  const [learn, setLearn] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const flight = useRef(false);
  const createdWorkspace = useRef<string | null>(null);
  const workspaceAttempt = useRef<{ idempotencyKey: string; name: string; timezone: string } | null>(null);
  const active = useRef(true);
  useEffect(() => { active.current = true; return () => { active.current = false; }; }, []);
  const steps = ['Make it yours', 'Connect carefully', 'Choose the scope', 'Choose your mode'];
  const [historySetup, setHistorySetup] = useState(false);
  const [historyChat, setHistoryChat] = useState<{ id: string; title: string } | null>(null);
  async function next() {
    if (flight.current) return;
    flight.current = true;
    setError(''); setBusy(true);
    try {
      if (step === 0 && !state.workspaceId) {
        // Keep setup visible when the new workspace replaces the empty owner snapshot.
        actions.navigate('/onboarding');
        if (!createdWorkspace.current) {
          if (!state.online) throw new Error('Your connection is unavailable. Restore it before creating your workspace.');
          workspaceAttempt.current ??= { idempotencyKey: crypto.randomUUID(), name: name.trim(), timezone };
          const workspace = await actions.request<{ id: string }>('POST', '/workspaces', { name: workspaceAttempt.current.name, timezone: workspaceAttempt.current.timezone }, { idempotencyKey: workspaceAttempt.current.idempotencyKey });
          createdWorkspace.current = workspace.id;
        }
        // The server acknowledged creation. A failed refresh must never create
        // another workspace when the owner retries Continue.
        await actions.refresh();
      }
      if (!active.current) return;
      if (step === 0) {
        try { sessionStorage.setItem('milo.device-preferences', JSON.stringify({ language, timezone })); }
        catch { /* Device preference storage is optional; account setup succeeded. */ }
      }
      if (step === 2) {
        if (selected.length && !state.online) throw new Error('Refresh your connection before changing chat permissions.');
        const currentChats = new Set(state.data.conversations.map(chat => chat.id));
        for (const id of selected.filter(id => currentChats.has(id))) await actions.request('PUT', `/conversations/${encodeURIComponent(id)}/permissions`, { read: true, retain: true, learn, draft: true, send: false, share: false });
        await actions.refresh();
      }
      if (!active.current) return;
      if (step === 3) { actions.navigate('/rules'); return; }
      setStep(previous => previous + 1);
    } catch (failure) {
      if (failure instanceof ApiError && failure.status === 422 && !createdWorkspace.current) workspaceAttempt.current = null;
      if (active.current) setError(failure instanceof Error ? failure.message : 'Setup was not confirmed.');
    }
    finally { flight.current = false; if (active.current) setBusy(false); }
  }
  return <div className="onboarding-page"><header><button className="brand-button" disabled={busy} onClick={() => actions.navigate('/')}><Milo size={42}/><span>milo</span></button><button className="text-button" disabled={busy} onClick={() => actions.navigate('/')}>Finish setup later</button></header><div className="onboarding-layout"><aside><span className="eyebrow">A SMALL START, YOUR WAY</span><h1>Let’s make<br/>some room.</h1><p>Linking an account is just the beginning. You choose what Milo can read, learn and do.</p><ol className="setup-steps">{steps.map((label,i) => <li className={step === i ? 'active' : ''} key={label}><span>{i < step ? <Icon name="check" size={15}/> : i+1}</span>{label}</li>)}</ol><Milo size={132}/></aside><section className="setup-card"><span className="eyebrow">STEP {step+1} OF 4</span><h2>{steps[step]}</h2>
    {step === 0 && <><p>Your timezone helps make reminders and quiet hours precise.</p><label className="field">Workspace name<input value={name} disabled={busy || !!workspaceAttempt.current} onChange={event => setName(event.target.value)} maxLength={120}/></label><label className="field">Timezone<select value={timezone} disabled={busy || !!workspaceAttempt.current} onChange={event => setTimezone(event.target.value)}>{['Asia/Kolkata','UTC','Europe/London','America/New_York','America/Los_Angeles','Asia/Dubai','Asia/Singapore','Australia/Sydney'].map(zone => <option key={zone}>{zone}</option>)}</select></label><label className="field">Preferred language on this device<select value={language} onChange={event => setLanguage(event.target.value)}>{['English','Hindi','Marathi','Tamil','Telugu'].map(item => <option key={item}>{item}</option>)}</select></label><p className="fine-print">Interface language is English. This device preference does not translate your conversations or change their permissions.</p></>}
    {step === 1 && <><p>Choose how to begin: link your WhatsApp phone when the pilot is available, connect an eligible Business number for new messages, or import one chat to explore its history.</p><div className="connection-choice"><Icon name="whatsapp" size={32}/><div><h3>WhatsApp</h3><p>{state.mode === 'demo' ? 'Synthetic connection · no personal account linked' : state.data.connections.length ? `${state.data.connections.length} configured collection(s) or account(s)` : 'No history collection or account yet'}</p></div><span className="pill">{state.mode === 'demo' ? 'Demo' : 'Choose a route'}</span></div>{state.mode === 'live' && <><PersonalConnection key={`phone:${state.mode}:${state.user.id}:${state.workspaceId}`} state={state} actions={actions}/><WhatsAppConnection key={`${state.mode}:${state.user.id}:${state.workspaceId}`} state={state} actions={actions}/></>}{state.mode === 'live' && !historyChat && <button className="button secondary" onClick={() => setHistorySetup(previous => !previous)}>{historySetup ? 'Hide chat setup' : 'Set up an export-only chat'}<Icon name="arrow" size={16}/></button>}{historySetup && state.mode === 'live' && !historyChat && <ExportChatSetup key={`${state.user.id}:${state.workspaceId}`} state={state} actions={actions} onCreated={chat => { setHistoryChat(chat); setHistorySetup(false); }}/>}{historyChat && <div className="notice" role="status"><p>{historyChat.title} is ready for its selected history. Sending and sharing are disabled.</p><button className="button" onClick={() => actions.navigate(`/connections/import/${encodeURIComponent(historyChat.id)}`)}>Import this chat’s history <Icon name="arrow" size={16}/></button></div>}<div className="notice">Phone linking is available only when the linked-device pilot is enabled. Live group access is unavailable. A selected text export lets you explore history without linking your phone.</div><button className="text-button" onClick={() => actions.navigate('/connections')}>View connection capabilities <Icon name="arrow" size={16}/></button><div className="planned-services"><span>Gmail · Planned</span><span>Calendar · Planned</span><span>Social · Planned</span></div></>}
    {step === 2 && <><p>Select chats deliberately. These choices permit reading and drafting; they do not permit automatic sending.</p><div className="scope-checklist">{state.data.conversations.map(chat => <label key={chat.id}><input type="checkbox" disabled={busy} checked={selected.includes(chat.id)} onChange={event => setSelected(previous => event.target.checked ? [...previous,chat.id] : previous.filter(id => id !== chat.id))}/><span>{chat.title}<small>{chat.account_label} · {chat.kind}</small></span></label>)}</div>{!state.data.conversations.length && <div className="notice">No chats have been selected yet. Return to Connect carefully, or finish setup later and connect when you are ready.</div>}<label className="check-label"><input type="checkbox" disabled={busy} checked={learn} onChange={event => setLearn(event.target.checked)}/> Learn from verified owner examples in these selected chats</label><p className="fine-print">Imports are historical. They never trigger an automatic reply, reaction or forward.</p></>}
    {step === 3 && <><p>Draft, read-only and Auto are different choices. A selected-chat Auto grant is explicit and bounded.</p><div className="mode-choice"><Icon name="shield" size={28}/><h3>One clear grant. No repeated approvals.</h3><p>Choose exact chats, allowed actions, quiet hours, expiry and hourly limits in Rules. Native forwarding also needs a precise source → destination route.</p></div><p className="notice">No Auto grant is created just by finishing onboarding. Review the final scope in Rules.</p><button className="text-button" onClick={() => actions.navigate('/inbox')}>Keep it in Draft for now <Icon name="arrow" size={16}/></button></>}
    {error && <p className="notice error" role="alert">{error}</p>}{step === 0 && createdWorkspace.current && error && <p className="notice" role="status">Your workspace was created. Continue retries loading it; it does not create another workspace.</p>}<div className="setup-controls">{step > 0 && <button className="text-button" disabled={busy} onClick={() => setStep(previous => previous-1)}>Back</button>}<button className="button" onClick={next} disabled={busy || (step === 0 && !name.trim())}>{busy ? 'Saving…' : step === 3 ? 'Review Auto rules' : 'Continue'}<Icon name="arrow" size={16}/></button></div>
    </section></div></div>;
}
