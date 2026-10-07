import type { Metadata, Viewport } from 'next';
import '@fontsource/space-grotesk/700.css';
import '@fontsource/space-grotesk/500.css';
import '@fontsource/dm-sans/400.css';
import '@fontsource/dm-sans/500.css';
import '@fontsource/dm-sans/600.css';
import './globals.css';
import { MiloProvider } from '@/lib/state';

export const metadata: Metadata = { title: 'Milo — a little less noise', description: 'Your conversations, with a little more room to breathe. A private, scoped AI companion.' };
export const viewport: Viewport = { width: 'device-width', initialScale: 1, themeColor: '#FAF7F2' };
export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body><MiloProvider>{children}</MiloProvider></body></html>;
}
