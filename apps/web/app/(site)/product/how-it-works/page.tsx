import type { Metadata } from 'next';
import { CtaBand, Feature, PageHero, SectionHead, styles } from '@/components/site/chrome';

export const metadata: Metadata = { title: 'How it works', description: 'How Milo connects to WhatsApp, learns your style, runs background agents and keeps every send under your control.' };

export default function HowItWorks() {
  return <>
    <PageHero kicker="How it works" title="From your chats to a replica you can trust.">Milo connects to WhatsApp, learns only from the chats you allow, and runs small background agents that prepare work for you. A send always passes a fresh permission check.</PageHero>

    <section className={`${styles.wrap} ${styles.section}`}>
      <SectionHead kicker="Six steps" title="What happens to a message."/>
      <div className={styles.steps}>
        <div className={styles.step}><h3>Connect</h3><p>Link a WhatsApp Business number through Meta’s signed webhooks, link your personal number with a one-time code typed into WhatsApp, or import an exported chat. Nothing is read until you pick chats.</p></div>
        <div className={styles.step}><h3>Choose per chat</h3><p>Each conversation has its own switches — read, retain, learn, draft, send and share. All start off. Turning one on never turns on another.</p></div>
        <div className={styles.step}><h3>Learn your style</h3><p>From messages you wrote (and reviewed, for shared business numbers) Milo measures your length, tone, emoji and phrasing per person. No model is fine-tuned on your data.</p></div>
        <div className={styles.step}><h3>Agents prepare</h3><p>When a new message arrives in an opted-in chat, a background worker prepares a draft, a follow-up or an answer — inside time, cost and quiet-hour limits you set.</p></div>
        <div className={styles.step}><h3>You approve</h3><p>You see the exact text. Approval is bound to that exact content: if anything changes after you approve, the approval no longer matches and nothing is sent.</p></div>
        <div className={styles.step}><h3>Send safely</h3><p>Every send re-checks permissions at that moment and is written to a durable ledger, so retries never double-send and “pause all” takes effect immediately.</p></div>
      </div>
    </section>

    <section className={`${styles.wrap} ${styles.section}`}>
      <SectionHead kicker="The agents" title="Small agents with narrow jobs.">Instead of one all-powerful bot, Milo splits the work. Each agent can only do what its job and your grants allow.</SectionHead>
      <div className={styles.grid3}>
        <Feature icon="sparkles" title="Draft agent"><p>Prepares replies in your voice for chats where you switched on background drafting. Every draft waits for your approval.</p></Feature>
        <Feature icon="search" title="Ask Milo"><p>Answers your private questions about one chat — “when is her flight?” — from that chat’s messages and confirmed memories, and shows the evidence. It cannot send or change permissions.</p></Feature>
        <Feature icon="actions" tone="peach" title="Auto agent"><p>Optional, per chat, time-limited. Handles small things you explicitly allow — a quick acknowledgement, a clarifying question, a reaction — and abstains when unsure.</p></Feature>
        <Feature icon="clock" tone="peach" title="Scheduler"><p>Sends messages you wrote and approved at a later time, with daily or weekly repeats, quiet hours and a pause that holds every job.</p></Feature>
        <Feature icon="memory" tone="green" title="Memory keeper"><p>Proposes facts worth remembering (“Mom prefers calls after 7”) with the message they came from. You confirm, correct or forget them.</p></Feature>
        <Feature icon="shield" tone="green" title="Retention worker"><p>Runs on its own schedule to expire old content, honour deletions and keep only what your retention settings allow.</p></Feature>
      </div>
    </section>

    <section className={`${styles.wrap} ${styles.section}`}>
      <SectionHead kicker="Under the hood" title="The server, in one picture.">A public web front door, a private API that holds all the rules, background workers, and encrypted storage. Model keys and WhatsApp tokens live only on the server.</SectionHead>
      <div className={styles.arch}>
        <div className={styles.archCol}>
          <p className={styles.archTitle}>Where messages come from</p>
          <div className={styles.node}><strong>WhatsApp Business</strong><span>Signed Meta webhooks (HMAC verified)</span></div>
          <div className={styles.node}><strong>Personal WhatsApp</strong><span>Private linked-device service bound to your number, encrypted keys</span></div>
          <div className={styles.node}><strong>Chat exports</strong><span>Imported text, deduplicated on re-import</span></div>
          <div className={styles.node}><strong>You</strong><span>Web app today; native apps in testing</span></div>
        </div>
        <div className={styles.archCol}>
          <p className={styles.archTitle}>Milo platform</p>
          <div className={styles.node}><strong>Web (Next.js)</strong><span>Public HTTPS. Same-origin proxy with request limits; never sees model or WhatsApp keys</span></div>
          <div className={styles.flow}>↓ private network ↓</div>
          <div className={`${styles.node} ${styles.nodeCore}`}><strong>API (FastAPI)</strong><span>Google sign-in sessions, CSRF, per-chat permissions, approvals, send ledger</span></div>
          <div className={styles.grid2} style={{ gap: 12 }}>
            <div className={styles.node}><strong>Jobs worker</strong><span>Drafts, Auto, schedules</span></div>
            <div className={styles.node}><strong>Retention</strong><span>Expiry &amp; deletion</span></div>
          </div>
        </div>
        <div className={styles.archCol}>
          <p className={styles.archTitle}>Storage &amp; models</p>
          <div className={`${styles.node} ${styles.nodeDark}`}><strong>PostgreSQL</strong><span>Message, draft and memory text encrypted at rest</span></div>
          <div className={styles.node}><strong>Redis</strong><span>Shared rate limits; never decides permissions</span></div>
          <div className={styles.node}><strong>Model provider</strong><span>Any OpenAI-compatible endpoint, including OpenRouter — keys stay server-side, with budgets per owner</span></div>
        </div>
      </div>
    </section>

    <section className={`${styles.wrap} ${styles.section}`}>
      <div className={styles.callout}>
        <span className={styles.icon}>🔑</span>
        <p><strong>Bring your own model.</strong> Point Milo at OpenRouter to choose between hundreds of models (for example a fast model for drafts and a stronger one for questions). Generation stays disabled until a server operator adds a key — the web app never holds one.</p>
      </div>
    </section>

    <CtaBand/>
  </>;
}
