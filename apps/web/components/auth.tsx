'use client';

import { useEffect, useRef, useState } from 'react';
import { createClient } from '@milo/contracts';
import { useMilo } from '@/lib/state';
import { Icon, Milo } from './icons';
import { ExportChatSetup } from './history-setup';

declare global { interface Window { google?: { accounts: { id: {
  initialize(options: { client_id: string; nonce: string; callback(response: { credential: string }): void }): void;
  renderButton(element: HTMLElement, options: Record<string, string | number>): void;
} } } } }

export function Login() {
  const { actions, authenticate } = useMilo();
  const holder = useRef<HTMLDivElement>(null);
  const [available, setAvailable] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  useEffect(() => {
    let alive = true;
    const client = createClient({ baseUrl: '/api' });
    async function prepare() {
      try {
        const config = await client.request<{ google_configured: boolean; google_client_id?: string; client_id?: string }>('/auth/config');
        const clientId = config.google_client_id ?? config.client_id;
        if (!config.google_configured || !clientId) { if (alive) setLoading(false); return; }
        const { nonce } = await client.request<{ nonce: string }>('/auth/nonce');
        await new Promise<void>((resolve, reject) => {
          if (window.google) { resolve(); return; }
          const script = document.createElement('script'); script.src = 'https://accounts.google.com/gsi/client'; script.async = true;
          script.onload = () => resolve(); script.onerror = () => reject(new Error('Google sign-in could not load.')); document.head.appendChild(script);
        });
        if (!alive || !holder.current || !window.google) return;
        window.google.accounts.id.initialize({ client_id: clientId, nonce, callback(response) {
          setLoading(true); void authenticate(response.credential, nonce).catch(failure => { setError(failure instanceof Error ? failure.message : 'Sign-in was not confirmed.'); setLoading(false); });
        } });
        window.google.accounts.id.renderButton(holder.current, { theme: 'outline', size: 'large', text: 'continue_with', shape: 'pill', width: 280 });
        setAvailable(true); setLoading(false);
      } catch (failure) { if (alive) { setError(failure instanceof Error ? failure.message : 'Sign-in is unavailable.'); setLoading(false); } }
    }
    void prepare(); return () => { alive = false; };
  }, [authenticate]);
  return <div className="auth-page"><div className="auth-brand"><Milo size={52}/><span>milo</span></div><section className="auth-card"><Milo size={126}/><span className="eyebrow">A LITTLE LESS NOISE</span><h1>Your people.<br/>A little more presence.</h1><p>A companion for the conversations that matter, with you in control of every scope.</p><div ref={holder} className="google-holder"/>{!available && <button className="button google-button" disabled>{loading ? 'Checking sign-in…' : 'Google sign-in not configured'}</button>}{error && <p className="notice error" role="alert">{error}</p>}<button className="button secondary" onClick={() => actions.navigate('/')}>Explore the synthetic demo <Icon name="arrow" size={17}/></button><p className="fine-print">Google verifies your identity. It does not grant Gmail, Calendar, Contacts or WhatsApp access.</p></section><p className="auth-footnote"><Icon name="shield" size={16}/> Your scope. Your voice. Your call.</p></div>;
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
  const steps = ['Make it yours', 'Connect carefully', 'Choose the scope', 'Choose your mode'];
  const [historySetup, setHistorySetup] = useState(false);
  const [historyChat, setHistoryChat] = useState<{ id: string; title: string } | null>(null);
  async function next() {
    setError(''); setBusy(true);
    try {
      if (step === 0 && !state.workspaceId) {
        // Keep setup visible when the new workspace replaces the empty owner snapshot.
        actions.navigate('/onboarding');
        await actions.request('POST', '/workspaces', { name, timezone }); await actions.refresh();
      }
      if (step === 0) sessionStorage.setItem('milo.device-preferences', JSON.stringify({ language, timezone }));
      if (step === 2) {
        for (const id of selected) await actions.request('PUT', `/conversations/${id}/permissions`, { read: true, retain: true, learn, draft: true, send: false, share: false });
        await actions.refresh();
      }
      if (step === 3) { actions.navigate('/rules'); return; }
      setStep(previous => previous + 1);
    } catch (failure) { setError(failure instanceof Error ? failure.message : 'Setup was not confirmed.'); }
    finally { setBusy(false); }
  }
  return <div className="onboarding-page"><header><button className="brand-button" onClick={() => actions.navigate('/')}><Milo size={42}/><span>milo</span></button><button className="text-button" onClick={() => actions.navigate('/')}>Return home</button></header><div className="onboarding-layout"><aside><span className="eyebrow">A SMALL START, YOUR WAY</span><h1>Let’s make<br/>some room.</h1><p>Linking an account is just the beginning. You choose what Milo can read, learn and do.</p><ol className="setup-steps">{steps.map((label,i) => <li className={step === i ? 'active' : ''} key={label}><span>{i < step ? <Icon name="check" size={15}/> : i+1}</span>{label}</li>)}</ol><Milo size={132}/></aside><section className="setup-card"><span className="eyebrow">STEP {step+1} OF 4</span><h2>{steps[step]}</h2>
    {step === 0 && <><p>Your timezone helps make reminders and quiet hours precise.</p><label className="field">Workspace name<input value={name} onChange={event => setName(event.target.value)} maxLength={120}/></label><label className="field">Timezone<select value={timezone} onChange={event => setTimezone(event.target.value)}>{['Asia/Kolkata','UTC','Europe/London','America/New_York','America/Los_Angeles','Asia/Dubai','Asia/Singapore','Australia/Sydney'].map(zone => <option key={zone}>{zone}</option>)}</select></label><label className="field">Preferred language on this device<select value={language} onChange={event => setLanguage(event.target.value)}>{['English','Hindi','Marathi','Tamil','Telugu'].map(item => <option key={item}>{item}</option>)}</select></label><p className="fine-print">Language preference is local to this setup. Provider history and outgoing permissions remain separate.</p></>}
    {step === 1 && <><p>WhatsApp first. Begin with an exported chat you choose, then review its separate permissions.</p><div className="connection-choice"><Icon name="whatsapp" size={32}/><div><h3>WhatsApp</h3><p>{state.mode === 'demo' ? 'Synthetic connection · no personal account linked' : state.data.connections.length ? `${state.data.connections.length} configured collection(s) or account(s)` : 'No history collection or account yet'}</p></div><span className="pill">{state.mode === 'demo' ? 'Demo' : 'Scope required'}</span></div>{state.mode === 'live' && !historyChat && <button className="button secondary" onClick={() => setHistorySetup(previous => !previous)}>{historySetup ? 'Hide chat setup' : 'Set up an export-only chat'}<Icon name="arrow" size={16}/></button>}{historySetup && state.mode === 'live' && !historyChat && <ExportChatSetup key={`${state.user.id}:${state.workspaceId}`} state={state} actions={actions} onCreated={chat => { setHistoryChat(chat); setHistorySetup(false); }}/>}{historyChat && <div className="notice" role="status"><p>{historyChat.title} is ready for its selected history. Sending and sharing are disabled.</p><button className="button" onClick={() => actions.navigate(`/connections/import/${encodeURIComponent(historyChat.id)}`)}>Import this chat’s history <Icon name="arrow" size={16}/></button></div>}<div className="notice">Personal QR / same-phone linking requires an eligible live connector. No pairing secret or fake QR is shown here.</div><button className="text-button" onClick={() => actions.navigate('/connections')}>View connection capabilities <Icon name="arrow" size={16}/></button><div className="planned-services"><span>Gmail · Planned</span><span>Calendar · Planned</span><span>Social · Planned</span></div></>}
    {step === 2 && <><p>Select chats deliberately. These choices permit reading and drafting; they do not permit automatic sending.</p><div className="scope-checklist">{state.data.conversations.map(chat => <label key={chat.id}><input type="checkbox" checked={selected.includes(chat.id)} onChange={event => setSelected(previous => event.target.checked ? [...previous,chat.id] : previous.filter(id => id !== chat.id))}/><span>{chat.title}<small>{chat.account_label} · {chat.kind}</small></span></label>)}</div>{!state.data.conversations.length && <div className="notice">No selected conversations are available. Connect an account or import permitted history first.</div>}<label className="check-label"><input type="checkbox" checked={learn} onChange={event => setLearn(event.target.checked)}/> Learn from verified owner examples in these selected chats</label><p className="fine-print">Imports are historical. They never trigger an automatic reply, reaction or forward.</p></>}
    {step === 3 && <><p>Draft, read-only and Auto are different choices. A selected-chat Auto grant is explicit and bounded.</p><div className="mode-choice"><Icon name="shield" size={28}/><h3>One clear grant. No repeated approvals.</h3><p>Choose exact chats, allowed actions, quiet hours, expiry and hourly limits in Rules. Native forwarding also needs a precise source → destination route.</p></div><p className="notice">No Auto grant is created just by finishing onboarding. Review the final scope in Rules.</p><button className="text-button" onClick={() => actions.navigate('/inbox')}>Keep it in Draft for now <Icon name="arrow" size={16}/></button></>}
    {error && <p className="notice error" role="alert">{error}</p>}<div className="setup-controls">{step > 0 && <button className="text-button" onClick={() => setStep(previous => previous-1)}>Back</button>}<button className="button" onClick={next} disabled={busy || (step === 0 && !name.trim())}>{busy ? 'Saving…' : step === 3 ? 'Review Auto rules' : 'Continue'}<Icon name="arrow" size={16}/></button></div>
    </section></div></div>;
}
