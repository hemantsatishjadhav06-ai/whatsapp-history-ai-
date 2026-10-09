import type { Metadata } from 'next';
import { CtaBand, Feature, PageHero, SectionHead, styles } from '@/components/site/chrome';

export const metadata: Metadata = { title: 'Trust & safety', description: 'How Milo protects your chats: per-chat consent, exact approvals, encryption, and the results of our latest security review.' };

type Severity = 'High' | 'Medium' | 'Low';
const review: [Severity, string, string][] = [
  ['High', 'Anonymous requests could exhaust the shared limit for Pause and Takeover', 'Fixed: control actions without a session are refused before they count, and the web proxy can sign each visitor’s real address so limits are per person.'],
  ['High', 'Any new account could spend the operator’s model credits without a ceiling', 'Fixed: every owner gets a default daily model allowance across all their workspaces until an explicit budget is set.'],
  ['Medium', 'One account could hold every API worker with slow model calls', 'Fixed: model calls have their own concurrency limit — one at a time per owner — and fail fast when busy.'],
  ['Medium', 'A history-collection name used by one customer blocked every other customer', 'Fixed: names are now unique per workspace, so they can neither collide nor reveal another customer’s label.'],
  ['Low', 'Database error text could include internal identifiers', 'Fixed: SQL parameters are hidden from errors and logs.'],
  ['Low', 'Background workers stopped permanently after three crashes', 'Fixed: Jobs and Retention now always restart.'],
  ['Medium', 'Slow uploads could tie up every web proxy slot', 'Fixed: requests without a session never take a slot, sign-in traffic has its own small pool, uploads must finish in seconds, and each visitor is capped.'],
  ['Medium', 'The WhatsApp session service shared an all-purpose internal key', 'Fixed: it now has its own key that only opens its two routes.'],
  ['Low', 'A pairing QR code could be relayed to someone else’s phone', 'Fixed: pairing is bound to the number you enter; any other account is unlinked and refused.'],
  ['Low', 'The browser did not restrict which scripts could run', 'Fixed: a per-request Content Security Policy blocks injected scripts.'],
  ['Low', 'A stolen mobile refresh token could keep a session alive', 'Fixed: reusing an old token signs out every holder.'],
  ['Low', 'AI drafts were not screened before approval', 'Fixed: drafts with links, payment details, phone numbers or promises show a warning next to Approve.'],
];
const sevClass: Record<Severity, string> = { High: styles.sevHigh, Medium: styles.sevMed, Low: styles.sevLow };

export default function Trust() {
  return <>
    <PageHero kicker="Trust & safety" title="A replica you can switch off in one tap.">Milo reads private conversations, so we treat every capability as something you grant, can see, and can take back. Here is how it works — and what our latest security review found.</PageHero>

    <section className={`${styles.wrap} ${styles.section}`} style={{ paddingTop: 16 }}>
      <div className={styles.grid3}>
        <Feature icon="shield" title="Consent per chat"><p>Read, retain, learn, draft, send and share are separate switches for every conversation. All start off; group sends need their own approval.</p></Feature>
        <Feature icon="check" tone="green" title="Exact approval"><p>Your approval is bound to the exact text. Any edit after approval cancels it. Sends re-check permissions at the moment they leave.</p></Feature>
        <Feature icon="pause" tone="peach" title="Pause & take over"><p>“Pause all” stops every job and draft at once. Take over any chat and Milo steps back until you hand it back.</p></Feature>
        <Feature icon="memory" title="Encrypted & forgettable"><p>Message, draft and memory text is encrypted at rest. Export your data, forget a memory, or delete a chat — dependent work is cancelled too.</p></Feature>
        <Feature icon="sparkles" tone="green" title="No hidden training"><p>Your style is a set of readable statistics and rules you can inspect. Milo does not fine-tune a model on your messages.</p></Feature>
        <Feature icon="connections" tone="peach" title="Keys stay on the server"><p>WhatsApp tokens and model keys (for example OpenRouter) live only on the private API. The browser and phone apps never hold them.</p></Feature>
      </div>
    </section>

    <section className={`${styles.wrap} ${styles.section}`}>
      <SectionHead kicker="Security review · October 2026" title="What we checked, and what we fixed.">Three independent reviews covered the API and sign-in, the AI agent pipeline, and the WhatsApp session service and web proxy. Cross-customer data access, webhook signatures, session handling, encryption and approval races were checked and found sound.</SectionHead>
      <div className={styles.tableWrap}>
        <table className={styles.table}>
          <thead><tr><th>Severity</th><th>Finding</th><th>Resolution</th></tr></thead>
          <tbody>{review.map(([severity, finding, fix]) => <tr key={finding}><td><span className={`${styles.sev} ${sevClass[severity]}`}>{severity}</span></td><td><strong>{finding}</strong></td><td>{fix}</td></tr>)}</tbody>
        </table>
      </div>
      <p className={styles.fine} style={{ marginTop: 16 }}>One item remains before the mobile apps ship: verified app links for mobile sign-in. Mobile sign-in is not enabled on the hosted service.</p>
    </section>

    <section className={`${styles.wrap} ${styles.section}`}>
      <SectionHead kicker="Questions" title="Straight answers."/>
      <div className={styles.faq}>
        <details><summary>Can Milo send a message without me?</summary><p>Only inside limits you set: a business-hours reply from a template you confirmed, or a small acknowledgement in a chat where you granted time-limited Auto. Drafts written by the AI always wait for your approval.</p></details>
        <details><summary>What if someone sends a message trying to trick the AI?</summary><p>Incoming text is treated as untrusted. The model has no tools, cannot send, and can only cite messages from that chat. The worst case is a bad draft — which you review before it goes anywhere.</p></details>
        <details><summary>Who can see my chats?</summary><p>Only your signed-in account, and only the chats you allowed Milo to read. Every request is checked against the owner of the workspace.</p></details>
        <details><summary>Which AI model does Milo use?</summary><p>Whichever the operator configures — any OpenAI-compatible provider, including OpenRouter. Text is sent only for the chat you are working on, with spend limits per owner.</p></details>
        <details><summary>How do I delete everything?</summary><p>Settings → Privacy lets you export or erase your workspace. Erasure removes message text, drafts and memories and cancels pending work.</p></details>
      </div>
    </section>

    <CtaBand/>
  </>;
}
