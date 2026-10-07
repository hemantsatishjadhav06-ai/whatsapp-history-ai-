'use client';

import { useCallback, useEffect, useState } from 'react';
import { usePathname } from 'next/navigation';
import { useMilo } from '@/lib/state';
import type { Section } from '@/lib/types';
import { Assistant } from './assistant';
import { Home } from './home';
import { Inbox } from './inbox';
import { Login, Onboarding } from './auth';
import { ToolsView } from './tools';
import { Icon, Milo } from './icons';

const mainNav: [Section,string][] = [['home','Home'],['inbox','Inbox'],['actions','Actions'],['memory','Memory'],['rules','Rules']];
const utilityNav: [Section,string][] = [['connections','Connections'],['activity','Activity'],['settings','Settings']];
const pathFor = (section: Section) => section === 'home' ? '/' : `/${section}`;

export function MiloApp() {
  const pathname = usePathname();
  const { state, actions } = useMilo();
  const parts = pathname.split('/').filter(Boolean);
  const section = (parts[0] ?? 'home') as Section;
  const [assistant, setAssistant] = useState<{ contextId?: string; intent?: string } | null>(null);
  const [search, setSearch] = useState('');
  const [pauseError, setPauseError] = useState('');
  const closeAssistant = useCallback(() => setAssistant(null), []);
  useEffect(() => { setSearch(''); setAssistant(null); }, [pathname]);
  const matched = search.trim() ? state.data.conversations.filter(chat => chat.title.toLowerCase().includes(search.toLowerCase())).slice(0,6) : [];
  if (parts[0] === 'login') return <Login/>;
  if (parts[0] === 'onboarding' || (state.mode === 'live' && !state.workspaceId)) return <Onboarding/>;
  const valid = ['home','inbox','actions','memory','rules','connections','activity','settings','more','assistant'].includes(section);
  const active = (id: Section) => section === id || (id === 'home' && !parts[0]);
  async function togglePause() { setPauseError(''); try { await actions.setPaused(!state.paused); } catch (failure) { setPauseError(failure instanceof Error ? failure.message : 'The server did not acknowledge the control request.'); } }
  const selected = section === 'inbox' ? state.data.conversations.find(chat => chat.id === parts[1]) : undefined;
  return <div className="application"><a className="skip-link" href="#main-content">Skip to content</a><aside className="sidebar"><button className="brand-button" onClick={() => actions.navigate('/')} aria-label="Milo home"><Milo size={54}/><span>milo</span></button><div className="rail-label">YOUR DAILY WORKSPACE</div><nav aria-label="Primary navigation">{mainNav.map(([id,label]) => <button className={`nav-link ${active(id) ? 'active' : ''}`} aria-current={active(id) ? 'page' : undefined} key={id} onClick={() => actions.navigate(pathFor(id))}><Icon name={id} size={19}/>{label}{id === 'inbox' && state.data.conversations.some(chat => chat.unread) && <span className="nav-dot"/>}</button>)}</nav><div className="sidebar-bottom"><nav aria-label="Workspace utilities">{utilityNav.map(([id,label]) => <button className={`nav-link ${active(id) ? 'active' : ''}`} key={id} aria-current={active(id) ? 'page' : undefined} onClick={() => actions.navigate(pathFor(id))}><Icon name={id} size={18}/>{label}</button>)}</nav><button className="owner-button" onClick={() => actions.navigate(state.mode === 'demo' ? '/login' : '/settings')}><span className="owner-avatar">{state.user.display_name.slice(0,1)}</span><span>{state.user.display_name.split(' ')[0]}<small>{state.mode === 'demo' ? 'Explore & sign in' : 'Personal space'}</small></span><Icon name="more" size={17}/></button></div></aside>
    <div className="workspace"><header className="topbar"><button className="mobile-brand" onClick={() => actions.navigate('/')} aria-label="Milo home"><Milo size={36}/><span>milo</span></button><div className="global-search"><Icon name="search" size={19}/><input aria-label="Search people, conversations and actions" placeholder="Search people, conversations and actions" value={search} onChange={event => setSearch(event.target.value)}/><span className="search-key">⌘ K</span>{search && <div className="search-results">{matched.length ? matched.map(chat => <button key={chat.id} onClick={() => actions.selectConversation(chat.id)}><span className="person-avatar small" style={{ background:chat.avatar_color }}>{chat.title.slice(0,1)}</span><span>{chat.title}<small>{chat.account_label}</small></span><Icon name="arrow" size={16}/></button>) : <p>No readable conversation matches.</p>}</div>}</div><button className={`pause-control ${state.paused ? 'paused' : ''}`} onClick={togglePause} disabled={state.pausePending} aria-label={state.pausePending ? 'Pause request pending' : state.paused ? 'Resume replies & actions' : 'Pause replies & actions'}><Icon name={state.paused ? 'play' : 'pause'} size={15}/><span>{state.pausePending ? 'Awaiting confirmation…' : state.paused ? 'Replies & actions paused' : 'Pause replies & actions'}</span></button><button className="demo-badge" onClick={() => actions.navigate(state.mode === 'demo' ? '/login' : '/settings')}>{state.mode === 'demo' ? 'Synthetic demo' : 'Your workspace'}</button></header>
      <div className="scope-bar"><Icon name="whatsapp" size={13}/><span>{state.mode === 'demo' ? 'WhatsApp · Synthetic' : state.data.connections.length ? 'WhatsApp · Selected accounts' : 'No account connected'}</span><span className="scope-separator">/</span><span>Auto configured in {state.data.grants.filter(grant => grant.enabled).length} selected chats</span><span className="scope-separator">/</span><span>{state.mode === 'demo' ? 'Phone continuity unverified' : 'Available history only'}</span><span className="scope-separator">/</span><span>{state.data.conversations.length} observed chats · {state.mode === 'demo' ? 'Partial demo history' : 'Coverage varies by account'}</span></div>
      {(!state.online || state.error || pauseError || state.paused) && <div className="workspace-notices" aria-live="polite">{!state.online && <p className="notice warning">App offline. Last known state is shown; server Auto may still be active.</p>}{(state.error || pauseError) && <p className="notice error">{state.error || pauseError}</p>}{state.paused && <p className="notice paused-notice"><Icon name="pause" size={16}/> {state.mode === 'demo' ? 'Demo pause acknowledged. No real account is connected.' : 'Global pause acknowledged. No new external actions start; submitted work remains in Activity.'}</p>}</div>}
      <main id="main-content" className={`content-wrap ${section === 'inbox' ? 'inbox-content' : ''}`}>
        {!valid ? <div className="not-found"><Milo size={90}/><h1>This page isn’t in your workspace.</h1><button className="button" onClick={() => actions.navigate('/')}>Go home</button></div> : section === 'home' ? <Home openAssistant={intent => setAssistant({ intent })}/> : section === 'inbox' ? <Inbox selectedId={parts[1]} openAssistant={contextId => setAssistant({ contextId })}/> : section === 'more' ? <div className="more-page"><h1>A little more control.</h1><p>Your scope, connections and privacy.</p>{[...mainNav.filter(([id]) => id === 'rules'),...utilityNav].map(([id,label]) => <button className="more-link" key={id} onClick={() => actions.navigate(pathFor(id))}><Icon name={id} size={23}/><span>{label}</span><Icon name="chevron"/></button>)}<button className="more-link" onClick={() => actions.navigate('/onboarding')}><Icon name="shield" size={23}/><span>Review setup</span><Icon name="chevron"/></button></div> : section === 'assistant' ? <div className="assistant-page"><Milo size={150}/><h1>A moment with Milo.</h1><p>Home context. You choose the scope for every request.</p><button className="button" onClick={() => setAssistant({})}>Ask Milo</button></div> : <ToolsView section={section as 'actions'|'memory'|'rules'|'connections'|'activity'|'settings'} state={state} actions={actions}/>}
        {section !== 'inbox' && <div className="assistant-dock"><Milo size={32}/><button onClick={() => setAssistant({})} className="dock-prompt">Ask Milo · Catch me up, explain a reply or teach a preference</button><button className="button" onClick={() => setAssistant({})} aria-label="Talk to Milo"><Icon name="mic" size={17}/><span>Talk to Milo</span><Icon name="arrow" size={16}/></button></div>}
      </main>
    </div><nav className="mobile-tabs" aria-label="Mobile navigation">{([['home','Home'],['inbox','Inbox'],['actions','Actions'],['memory','Memory'],['more','More']] as [Section,string][]).map(([id,label]) => <button key={id} className={active(id) ? 'active' : ''} aria-current={active(id) ? 'page' : undefined} onClick={() => actions.navigate(pathFor(id))}><Icon name={id} size={21}/><span>{label}</span></button>)}</nav>
    {section === 'inbox' && <button className="mobile-milo" onClick={() => setAssistant(selected ? { contextId:selected.id } : {})}><Milo size={27}/> Ask Milo</button>}
    {assistant && <Assistant contextId={assistant.contextId} intent={assistant.intent} onClose={closeAssistant}/>}
  </div>;
}
