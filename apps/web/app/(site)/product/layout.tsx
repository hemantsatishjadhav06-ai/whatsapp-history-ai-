import type { Metadata } from 'next';
import { SiteShell } from '@/components/site/chrome';

export const metadata: Metadata = {
  title: { default: 'Milo — your replica on WhatsApp', template: '%s · Milo' },
  description: 'Milo learns how you write, keeps up with every WhatsApp chat, drafts replies in your voice and handles follow-ups in the background — never sending anything you have not allowed.',
};

export default function ProductLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <SiteShell>{children}</SiteShell>;
}
