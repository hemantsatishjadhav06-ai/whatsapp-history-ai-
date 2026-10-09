import type { Metadata } from 'next';
import { CtaBand, PageHero, Status, styles, type Availability } from '@/components/site/chrome';

export const metadata: Metadata = { title: 'Use cases', description: 'How founders, small businesses and busy families use Milo to keep up with WhatsApp in their own voice.' };

const cases: { who: string; title: string; status: Availability; body: string; example: [string, string] }[] = [
  { who: 'Founders & consultants', title: 'Clients answered in your tone, between meetings', status: 'now',
    body: 'A client pings while you are presenting. Milo prepares a reply that matches how you usually write to that client and keeps it ready. One tap to send, or edit first.',
    example: ['Client', 'Can we move tomorrow’s call to 4?'] },
  { who: 'Small businesses', title: 'Business hours and FAQs on autopilot', status: 'now',
    body: 'Confirm your opening hours once and Milo answers “are you open?” with your own template outside office hours — with quiet hours, hourly limits and an expiry you set.',
    example: ['Customer', 'Are you open on Sunday?'] },
  { who: 'Everyone', title: 'Never drop a promise', status: 'now',
    body: 'Book a table, send the document, call back on Friday — save any promise in a chat as a follow-up with a date and the message it came from, and see it in your inbox until it is done.',
    example: ['You', 'I’ll send the deck by Thursday'] },
  { who: 'Families & friends', title: 'Ask anything about one chat', status: 'now',
    body: 'Ask “which flight is Mom on?” and Milo answers from that chat only, showing the messages it used. It cannot message anyone while answering.',
    example: ['Ask Milo', 'What time does Mom land on Friday?'] },
  { who: 'Teams on one number', title: 'Shared Business number, owner’s voice', status: 'now',
    body: 'On a shared Business number, Milo learns only from examples the owner reviews, so employees’ messages never become the owner’s style by accident.',
    example: ['Owner review', 'Use this message as my style? Yes / No'] },
  { who: 'Planners', title: 'Scheduled messages that respect your day', status: 'now',
    body: 'Write a birthday wish or weekly check-in once, approve it, and Milo sends it at the right time — paused automatically if you pause everything.',
    example: ['Scheduled', 'Every Monday 9:00 — “Weekly update?”'] },
  { who: 'Personal assistants', title: 'Booking and research in the background', status: 'next',
    body: 'When a chat asks for a reservation or a quick lookup, Milo will research options and prepare the booking for your approval. Calendar and web research agents are on the roadmap.',
    example: ['Friend', 'Can you find a place for 6 near Bandra?'] },
  { who: 'Everywhere you talk', title: 'Beyond WhatsApp', status: 'later',
    body: 'The same replica, consent model and approval flow extended to SMS, Instagram, Telegram and email — one place to see who needs you.',
    example: ['Inbox', 'WhatsApp · SMS · Email · Instagram'] },
];

export default function UseCases() {
  return <>
    <PageHero kicker="Use cases" title="Made for people who live on WhatsApp.">From client chats to family plans, Milo takes the busywork and leaves the decisions to you. Every card shows what you can use today and what is still coming.</PageHero>
    <section className={`${styles.wrap} ${styles.section}`} style={{ paddingTop: 16 }}>
      <div className={styles.grid2}>
        {cases.map(item => <article key={item.title} className={styles.card}>
          <div className={styles.kicker} style={{ marginBottom: 6 }}>{item.who}</div>
          <div className={styles.cardTop}><h3>{item.title}</h3><Status value={item.status}/></div>
          <p>{item.body}</p>
          <div className={styles.quote}><b>{item.example[0]}:</b> {item.example[1]}</div>
        </article>)}
      </div>
      <p className={styles.fine} style={{ marginTop: 20 }}><strong>Built &amp; tested</strong> features run in the live demo today. On real chats they switch on once the operator connects a WhatsApp number and a model key.</p>
    </section>
    <CtaBand/>
  </>;
}
