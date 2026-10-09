import { MiloProvider } from '@/lib/state';

export default function AppLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <MiloProvider>{children}</MiloProvider>;
}
