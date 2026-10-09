import { NextResponse, type NextRequest } from 'next/server';

// Google Identity Services: https://developers.google.com/identity/gsi/web/guides/get-google-api-clientid#content_security_policy
const GIS = 'https://accounts.google.com/gsi/';

/**
 * Per-request script nonce for rendered pages. Next.js reads it from the request's
 * CSP header and applies it to its own scripts; GIS is loaded by those trusted
 * scripts ('strict-dynamic'). JSON and probe routes keep the static policy in next.config.ts.
 */
export function proxy(request: NextRequest) {
  const nonce = Buffer.from(crypto.getRandomValues(new Uint8Array(16))).toString('base64');
  const policy = [`default-src 'self'`,
    `script-src 'self' 'nonce-${nonce}' 'strict-dynamic' ${GIS}client${process.env.NODE_ENV === 'development' ? ` 'unsafe-eval'` : ''}`,
    // React style attributes and the GIS button need inline styles; styles cannot run script.
    `style-src 'self' 'unsafe-inline' ${GIS}style`, `img-src 'self' data: blob:`, `font-src 'self' data:`,
    `connect-src 'self' ${GIS}`, `frame-src ${GIS}`, `object-src 'none'`, `base-uri 'self'`, `form-action 'self'`, `frame-ancestors 'none'`].join('; ');
  const headers = new Headers(request.headers);
  headers.set('x-nonce', nonce);
  headers.set('content-security-policy', policy);
  const response = NextResponse.next({ request: { headers } });
  response.headers.set('Content-Security-Policy', policy);
  return response;
}

// Route handlers stream their own bodies (a proxy match would buffer them) and render no scripts.
// Every other document, including unknown files that fall through to the app page, gets the
// policy. Client-router flight requests (RSC header) are never rendered as documents.
export const config = { matcher: [{ source: '/((?!api/|native-api/|healthz$|readyz$|_next/static/|_next/image).*)', missing: [{ type: 'header', key: 'rsc' }] }] };
