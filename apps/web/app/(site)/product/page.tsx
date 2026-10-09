import Link from 'next/link';
import { Icon } from '@/components/icons';
import { CtaBand, Feature, SectionHead, styles } from '@/components/site/chrome';

function PhoneDemo() {
  return <div>
    <div className={styles.phone} role="img" aria-label="Example: Milo drafts a reply in your style and adds a follow-up task">
      <div className={styles.screen}>
        <div className={styles.chatHead}><span className={styles.avatar}>A</span><div><strong>Aisha</strong><small>WhatsApp · online</small></div></div>
        <div className={styles.chatBody}>
          <div className={`${styles.bubble} ${styles.in}`}>Hey! Are we still on for Saturday? 🎉<span className={styles.time}>18:02</span></div>
          <div className={`${styles.bubble} ${styles.in}`}>Can you book a table for 6 somewhere near Bandra?<span className={styles.time}>18:02</span></div>
          <div className={styles.draft}>
            <div className={styles.draftLabel}><Icon name="sparkles" size={14}/>Drafted in your style</div>
            <p>Yesss still on!! 🙌 Leave the table to me — 6 people, Bandra, around 8? Will confirm tonight</p>
            <div className={styles.chips}><span className={`${styles.chip} ${styles.chipPrimary}`}>Approve &amp; send</span><span className={styles.chip}>Edit</span><span className={styles.chip}>Not now</span></div>
          </div>
          <div className={styles.task}><Icon name="clock" size={18}/><div><strong>Follow-up saved</strong><span>Book table for 6 · Bandra · Sat 8pm</span></div></div>
        </div>
      </div>
    </div>
    <div className={styles.floating}><Icon name="shield" size={18}/>Sent only after you approve</div>
  </div>;
}

export default function ProductOverview() {
  return <>
    <section className={styles.wrap}>
      <div className={styles.hero}>
        <div>
          <div className={styles.eyebrow}><span className={styles.dot}/>WhatsApp first · more channels later</div>
          <h1 className={styles.heroTitle}>Your replica on WhatsApp — <span className={styles.accent}>working in the background.</span></h1>
          <p className={styles.lead}>Milo learns how you write, keeps up with every chat, drafts replies in your voice and picks up the things you would otherwise forget — bookings to make, facts to look up, people to get back to. You stay in charge: nothing leaves your phone number without your permission.</p>
          <div className={styles.actions}><Link href="/" className={styles.cta}>Try the live demo <Icon name="arrow" size={18}/></Link><Link href="/product/how-it-works" className={styles.ghost}>How it works</Link></div>
          <p className={styles.fine}>The demo uses synthetic chats. Connecting a real account is opt-in, per chat.</p>
        </div>
        <PhoneDemo/>
      </div>
    </section>

    <section className={`${styles.wrap} ${styles.section}`}>
      <SectionHead kicker="What Milo does" title="Three jobs, one assistant that sounds like you.">Milo is not a chatbot that answers for you blindly. It is a set of small agents, each with a narrow job and explicit limits.</SectionHead>
      <div className={styles.grid3}>
        <Feature icon="sparkles" title="Replies in your voice">
          <p>Milo builds a style profile per conversation from messages you wrote and approved — your length, emoji, greetings, language mix — and drafts replies that sound like you, not like a bot.</p>
        </Feature>
        <Feature icon="clock" tone="peach" title="Background follow-through">
          <p>When a chat contains something to do — a table to book, a date to remember, a question to answer later — save it as a follow-up linked to the message, and let Milo keep it in front of you until it is done.</p>
        </Feature>
        <Feature icon="shield" tone="green" title="You stay in control">
          <p>Six independent switches per chat: read, retain, learn, draft, send, share. Pause everything in one tap, take over any conversation, and approve the exact text before it goes out.</p>
        </Feature>
      </div>
    </section>

    <section className={`${styles.wrap} ${styles.section}`}>
      <div className={styles.grid2} style={{ alignItems: 'center', gap: 40 }}>
        <div>
          <SectionHead kicker="A day with Milo" title="Less inbox. More life.">Milo works while you are in meetings, travelling or simply offline, and hands you a short list instead of a hundred unread chats.</SectionHead>
          <Link href="/product/use-cases" className={styles.ghost}>More use cases <Icon name="arrow" size={16}/></Link>
        </div>
        <div className={styles.timeline}>
          <div className={styles.tItem}><time>08:00</time><h3>Morning digest</h3><p>Who needs you today, what you promised, and which replies are ready for review.</p></div>
          <div className={styles.tItem}><time>11:30</time><h3>Drafts prepared while you were busy</h3><p>Three chats got fresh drafts in your tone. Approve, edit or skip each one.</p></div>
          <div className={styles.tItem}><time>14:15</time><h3>Ask Milo</h3><p>“What did Rahul say about the price last week?” — answered from that chat only, with the messages it used.</p></div>
          <div className={styles.tItem}><time>21:00</time><h3>Open follow-ups</h3><p>“Confirm the table for 6 on Saturday” is still waiting. Quiet hours start at 22:00 — Milo stays silent.</p></div>
        </div>
      </div>
    </section>

    <section className={`${styles.wrap} ${styles.section}`}>
      <div className={styles.band}>
        <div className={styles.kicker}>Built to be trusted</div>
        <h2 className={styles.h2}>Private by design, not as an afterthought.</h2>
        <p className={styles.sub}>Message, draft and memory text is encrypted at rest. Every send passes a fresh permission check and a durable ledger, so a retry can never double-send.</p>
        <div className={styles.stats}>
          <div className={styles.stat}><strong>6</strong><span>independent permissions per chat</span></div>
          <div className={styles.stat}><strong>800+</strong><span>automated backend tests per release</span></div>
          <div className={styles.stat}><strong>120+</strong><span>browser journeys on desktop &amp; mobile</span></div>
          <div className={styles.stat}><strong>0</strong><span>messages sent without your permission</span></div>
        </div>
      </div>
    </section>

    <CtaBand/>
  </>;
}
