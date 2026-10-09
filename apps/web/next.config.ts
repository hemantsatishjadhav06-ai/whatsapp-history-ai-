import type { NextConfig } from 'next';
import path from 'node:path';

const config: NextConfig = {
  output: 'standalone',
  outputFileTracingRoot: path.resolve(process.cwd(), '../..'),
  transpilePackages: ['@milo/contracts'],
  poweredByHeader: false,
  async headers() {
    // Pages receive a per-request nonce policy from proxy.ts. Route handlers and
    // build assets are never rendered as documents, so they allow nothing.
    const locked = [{ key: 'Content-Security-Policy', value: "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'" }];
    return [{ source: '/:path*', headers: [
      { key: 'X-Content-Type-Options', value: 'nosniff' },
      { key: 'Referrer-Policy', value: 'strict-origin-when-cross-origin' },
      { key: 'Permissions-Policy', value: 'microphone=(self), camera=()' },
      { key: 'X-Frame-Options', value: 'DENY' },
      ...(process.env.NODE_ENV === 'production' ? [{ key: 'Strict-Transport-Security', value: 'max-age=31536000; includeSubDomains' }] : []),
    ] }, ...['/api/:path+', '/native-api/:path+', '/healthz', '/readyz', '/_next/static/:path+'].map(source => ({ source, headers: locked }))];
  },
};
export default config;
