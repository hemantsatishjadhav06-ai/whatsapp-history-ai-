'use client';

import { stateLabel } from '@milo/contracts';
import { useMilo, shortTime, textValue } from '@/lib/state';
import { Icon, Milo } from './icons';

export function Home({ openAssistant }: { openAssistant(intent?: string): void }) {
  const { state, actions } = useMilo();
  const takeover = state.data.conversations.filter(chat => chat.control_state === 'HUMAN_TAKEOVER');
  const missing = state.data.actions.filter(action => action.reason_code === 'MISSING_FACTS' || action.status === 'blocked' || action.status === 'uncertain');
  const receipts = state.data.actions.filter(action => ['accepted', 'delivered', 'read'].includes(String(action.status))).slice(0, 3);
  const upcoming = [...state.data.tasks, ...state.data.jobs].filter(job => ['pending', 'active', 'scheduled', 'held'].includes(String(job.status))).sort((a,b) => String(a.due_at ?? '').localeCompare(String(b.due_at ?? ''))).slice(0, 2);
  const needs = takeover.length + missing.length;
  return <div className="home-content">
    <section className="briefing" aria-labelledby="home-heading">
      <Milo size={92}/><div className="briefing-copy"><div className="eyebrow">A LITTLE MORE ROOM TO BREATHE</div>
        <h1 id="home-heading">Hi, {state.user.display_name.split(' ')[0]}. A little less noise.</h1>
        <p>{state.paused ? 'Replies and external actions are paused. Your reminders are still here.' : state.mode === 'demo' ? 'Dinner is settled. The group is up to date. Neha is in your hands.' : `${state.data.conversations.length} selected conversations, together in one place.`}</p>
        <div className="briefing-actions"><button className="button" onClick={() => openAssistant('catch_me_up')}>Catch me up <Icon name="arrow" size={16}/></button>
          <button className="text-button" onClick={() => openAssistant('write_with_me')}>Write with me</button><button className="text-button" onClick={() => openAssistant('teach_me')}>Teach me</button></div>
      </div>
    </section>
    <div className="home-columns"><section aria-labelledby="needs-heading" className="needs-column"><div className="section-heading"><h2 id="needs-heading">What needs you</h2><span>{needs ? `${Math.min(needs, 5)} things` : 'You’re caught up'}</span></div>
      {takeover.slice(0, 3).map(chat => <article className="need-card caution" key={chat.id}>
        <span className="card-marker"><Icon name="whatsapp" size={18}/> PHONE TAKEOVER</span><h3>Your phone has the floor.</h3><p>You replied to {chat.title} on WhatsApp. Auto stays paused in this chat.</p>
        <div className="fine-print">Other selected chats remain active · {state.mode === 'demo' ? 'Synthetic phone observation' : 'Observed by your connector'}</div>
        <button className="button secondary" onClick={() => actions.selectConversation(chat.id)}>Open {chat.title}’s control <Icon name="arrow" size={15}/></button>
      </article>)}
      {missing.slice(0, 5 - Math.min(takeover.length,3)).map(action => {
        const chat = state.data.conversations.find(item => item.id === action.conversation_id);
        const uncertain = action.status === 'uncertain';
        return <article className="need-card" key={action.id}><span className="card-marker"><Icon name="info" size={18}/> A LITTLE CLARITY</span>
          <h3>{uncertain ? 'Submission needs reconciliation.' : `${chat?.title ?? 'This conversation'} needs a detail I don’t have.`}</h3><p>{uncertain ? 'The provider outcome is unknown. Review the exact attempt; no blind retry is available.' : String(action.reason_code) === 'MISSING_FACTS' ? 'A required detail is missing. Review the source before preparing a reply.' : 'This action is held until its scope and context are clear.'}</p>
          <div className="fine-print">WhatsApp · {chat?.title ?? 'Selected chat'} · {stateLabel(String(action.status))}</div>
          <button className="button secondary" onClick={() => uncertain ? actions.navigate(`/activity/${encodeURIComponent(action.id)}`) : chat ? actions.selectConversation(chat.id) : actions.navigate('/actions')}>{uncertain ? 'Review the attempt' : 'See the conversation'} <Icon name="arrow" size={15}/></button>
        </article>;
      })}
      {!needs && <article className="need-card empty-card"><Icon name="check" size={30}/><h3>Nothing urgent needs your attention.</h3><p>New decisions will appear here with their conversation and scope.</p><button className="button secondary" onClick={() => actions.navigate('/inbox')}>Open your inbox</button></article>}
    </section><aside className="home-right"><section><div className="section-heading"><h2>Upcoming</h2><button className="text-button small" onClick={() => actions.navigate('/actions')}>View all <Icon name="chevron" size={13}/></button></div>
      {upcoming.length ? upcoming.map(job => {
        const kind = textValue(job.action_kind,textValue(job.kind)).toUpperCase();
        const external = job.external_action === true || ['SEND_TEXT','QUOTE','REACTION','FORWARD'].includes(kind);
        const route = state.data.routes.find(item => item.id === job.route_id);
        const destinationId = kind === 'FORWARD' ? route?.destination_conversation_id : job.conversation_id;
        const destination = state.data.conversations.find(chat => chat.id === destinationId);
        const zone = textValue(job.timezone,state.timezone);
        return <button className="summary-card" key={job.id} onClick={() => actions.navigate(`/actions/${encodeURIComponent(job.id)}`)}><span className="summary-icon"><Icon name="clock" size={18}/></span><h3>{job.due_at ? new Intl.DateTimeFormat('en', { weekday:'short', hour:'numeric', minute:'2-digit', timeZone: zone }).format(new Date(String(job.due_at))) : 'When you’re ready'}</h3><p>{textValue(job.title, textValue(job.content,textValue(job.text, kind === 'FORWARD' ? 'Forward the selected native source' : kind === 'REACTION' ? `React with ${textValue(job.emoji)}` : 'Scheduled action')))}</p><div className="fine-print">{external ? `Scheduled external action · ${destination?.title ?? 'Exact saved destination'} · ${destination?.account_label ?? 'Selected account'}` : 'Owner reminder'} · {zone}</div></button>;
      }) : <div className="summary-card"><h3>A little breathing room.</h3><p>No upcoming reminders.</p><button className="text-button" onClick={() => actions.navigate('/actions')}>Add a reminder <Icon name="arrow" size={14}/></button></div>}
    </section><section><div className="section-heading"><h2>Handled quietly</h2><span className="tiny-dot"/></div>
      {receipts.map(receipt => {
        const source = state.data.conversations.find(chat => chat.id === receipt.conversation_id);
        const destination = state.data.conversations.find(chat => chat.id === receipt.destination_conversation_id);
        const forward = ['forward','FORWARD'].includes(String(receipt.kind));
        const payload = receipt.payload && typeof receipt.payload === 'object' ? receipt.payload as Record<string,unknown> : {};
        return <button className="summary-card" key={receipt.id} onClick={() => actions.navigate(`/activity/${encodeURIComponent(receipt.id)}`)}>
          <span className="summary-icon"><Icon name="check" size={18}/></span><h3>{source?.title ?? 'Selected chat'}{forward ? ` → ${destination?.title ?? 'Saved route'}` : source?.title === 'Maya' ? ' · Dinner' : ''}</h3>
          <p>{forward ? receipt.status === 'accepted' ? 'Forward accepted.' : 'Forward delivery confirmed.' : textValue(receipt.text,textValue(payload.text,'An authorized action was accepted.'))}</p>
          <div className="fine-print">{state.mode === 'demo' ? `Simulated ${stateLabel(String(receipt.status)).toLowerCase()}` : stateLabel(String(receipt.status))} · {forward ? 'Saved route' : 'This conversation only'}</div>
        </button>;
      })}
      {!receipts.length && <div className="summary-card"><p>Actual action receipts will appear here.</p><div className="fine-print">Acceptance and delivery are separate states.</div></div>}
    </section></aside></div>
    <div className="source-note"><Icon name="shield" size={15}/> {state.mode === 'demo' ? 'Synthetic examples, dated for this demo. Nothing is sent to a real account.' : `Current selected scope · Updated ${shortTime(state.lastUpdated, state.timezone) || 'recently'}`}</div>
  </div>;
}
