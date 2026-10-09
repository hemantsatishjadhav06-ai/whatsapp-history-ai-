import type { Metadata } from 'next';
import { CtaBand, PageHero, SectionHead, Status, styles, type Availability } from '@/components/site/chrome';

export const metadata: Metadata = { title: 'Roadmap', description: 'What Milo does today, what is next, and where the replica goes after WhatsApp.' };

const rows: [string, string, Availability][] = [
  ['Milo web app (desktop & mobile web)', 'Home, Inbox, Actions, Memory, Rules, Settings — hosted on Railway', 'now'],
  ['Google sign-in & private workspaces', 'Verified owner identity, HttpOnly sessions, CSRF, per-owner isolation', 'now'],
  ['WhatsApp Business connection', 'Signed webhooks, per-contact consent, up to 180 days of eligible 1:1 history', 'now'],
  ['Personal WhatsApp via QR', 'Private linked-device service with encrypted keys; 1:1 text', 'now'],
  ['Chat export import', 'Personal and group text history with explicit owner/date mapping', 'now'],
  ['Replies in your style', 'Per-chat style statistics, owner rules, draft + exact approval', 'now'],
  ['Background drafts & bounded Auto', 'Opt-in per chat, expiring grants, quiet hours, hourly budgets', 'now'],
  ['Ask Milo (owner-only answers)', 'Evidence-linked answers from one chat; cannot send', 'now'],
  ['OpenRouter model routing', 'Use any OpenRouter model; the key and spend budgets stay on the server', 'now'],
  ['Booking agent', 'Turn “book a table / a slot” into a researched option list and a calendar hold for approval', 'next'],
  ['Research agent', 'Quick web lookups (“what time does the store close?”) with sources, prepared as a draft', 'next'],
  ['Calendar & Gmail', 'Independent grants and adapters for scheduling and email follow-ups', 'next'],
  ['Push reminders & voice notes', 'Private notifications for follow-ups; speech input with explicit permission', 'next'],
  ['iOS & Android apps', 'Shared contracts already built; store builds and device testing pending', 'next'],
  ['SMS, Instagram, Telegram, email', 'Same replica, consent model and approvals across every channel', 'later'],
  ['Scale to 50,000 connected accounts', 'Horizontal workers and session sharding with measured capacity', 'later'],
];

export default function Roadmap() {
  return <>
    <PageHero kicker="Roadmap" title="WhatsApp today. Everywhere you talk, next.">The goal is a replica of you that handles the busywork across every channel. We are building it in the order that keeps you safest: consent and control first, more autonomy later.</PageHero>
    <section className={`${styles.wrap} ${styles.section}`} style={{ paddingTop: 16 }}>
      <SectionHead kicker="Status" title="What is built, and what is coming."/>
      <div className={styles.tableWrap}>
        <table className={styles.table}>
          <thead><tr><th>Capability</th><th>What it means</th><th>Status</th></tr></thead>
          <tbody>{rows.map(([name, detail, status]) => <tr key={name}><td><strong>{name}</strong></td><td>{detail}</td><td><Status value={status}/></td></tr>)}</tbody>
        </table>
      </div>
      <p className={styles.fine} style={{ marginTop: 16 }}>“Built &amp; tested” means implemented and covered by automated tests. Real WhatsApp numbers and model keys are connected per deployment by the operator; the hosted demo uses synthetic data.</p>
    </section>
    <section className={`${styles.wrap} ${styles.section}`}>
      <div className={styles.grid3}>
        <article className={styles.card}><h3>Principle 1 · Consent first</h3><p>No new capability ships without its own switch, off by default, per conversation.</p></article>
        <article className={styles.card}><h3>Principle 2 · Approval before autonomy</h3><p>Agents propose; you approve. Autonomy is earned per chat with time-limited grants.</p></article>
        <article className={styles.card}><h3>Principle 3 · Honest status</h3><p>We label what is tested, what is a pilot and what is planned — no fake “AI magic”.</p></article>
      </div>
    </section>
    <CtaBand title="Want early access to booking and research agents?" body="Open the demo, then sign in to set up your workspace. New agents arrive as opt-in switches."/>
  </>;
}
