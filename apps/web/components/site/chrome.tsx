import Link from 'next/link';
import { Icon, Milo } from '@/components/icons';
import { SiteNav } from './nav';
import { sitePages } from './pages';
import styles from './site.module.css';

export { styles };

export function SiteShell({ children }: { children: React.ReactNode }) {
  return <div className={styles.page}>
    <a className="skip-link" href="#site-main">Skip to content</a>
    <header className={styles.header}>
      <div className={`${styles.wrap} ${styles.headerInner}`}>
        <Link href="/product" className={styles.brand} aria-label="Milo product overview"><Milo size={34}/><span>milo</span></Link>
        <SiteNav/>
        <Link href="/" className={`${styles.cta} ${styles.headerCta}`}>Open Milo</Link>
      </div>
    </header>
    <main id="site-main" className={styles.main}>{children}</main>
    <footer className={styles.footer}>
      <div className={`${styles.wrap} ${styles.footerInner}`}>
        <div><strong style={{ color: 'var(--ink)' }}>Milo</strong> — your replica on WhatsApp. Nothing is sent without your permission.</div>
        <nav aria-label="Footer">{sitePages.map(([href, label]) => <Link key={href} href={href}>{label}</Link>)}<Link href="/">Live demo</Link></nav>
      </div>
    </footer>
  </div>;
}

export function PageHero({ kicker, title, children }: { kicker: string; title: React.ReactNode; children: React.ReactNode }) {
  return <section className={styles.wrap}><div className={styles.pageHero}>
    <div className={styles.kicker}>{kicker}</div>
    <h1>{title}</h1>
    <p className={styles.lead}>{children}</p>
  </div></section>;
}

export function SectionHead({ kicker, title, children }: { kicker: string; title: React.ReactNode; children?: React.ReactNode }) {
  return <div className={styles.sectionHead}>
    <div className={styles.kicker}>{kicker}</div>
    <h2 className={styles.h2}>{title}</h2>
    {children && <p className={styles.sub}>{children}</p>}
  </div>;
}

export function Feature({ icon, tone, title, children }: { icon: string; tone?: 'peach' | 'green'; title: string; children: React.ReactNode }) {
  const toneClass = tone === 'peach' ? styles.iconPeach : tone === 'green' ? styles.iconGreen : '';
  return <article className={styles.card}>
    <div className={`${styles.icon} ${toneClass}`}><Icon name={icon} size={22}/></div>
    <h3>{title}</h3>
    {children}
  </article>;
}

export type Availability = 'now' | 'next' | 'later';
const availabilityLabel: Record<Availability, string> = { now: 'Built & tested', next: 'Next up', later: 'Later' };
export function Status({ value }: { value: Availability }) {
  return <span className={`${styles.badge} ${styles[value]}`}>{availabilityLabel[value]}</span>;
}

export function CtaBand({ title = 'See Milo with safe sample data.', body = 'The live demo runs on synthetic conversations — explore drafts, memory and controls without connecting a phone.' }: { title?: string; body?: string }) {
  return <section className={`${styles.wrap} ${styles.section}`}>
    <div className={styles.ctaBand}>
      <div><h2>{title}</h2><p>{body}</p></div>
      <div className={styles.actions}><Link href="/" className={styles.cta}>Open the live demo</Link><Link href="/login" className={styles.ghost}>Sign in</Link></div>
    </div>
  </section>;
}
