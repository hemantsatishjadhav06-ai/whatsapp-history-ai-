'use client';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { sitePages } from './pages';
import styles from './site.module.css';

export function SiteNav() {
  const pathname = usePathname();
  return <nav className={styles.nav} aria-label="Product pages">
    {sitePages.map(([href, label]) => {
      const active = pathname === href;
      return <Link key={href} href={href} className={`${styles.navLink} ${active ? styles.navActive : ''}`} aria-current={active ? 'page' : undefined}>{label}</Link>;
    })}
  </nav>;
}
