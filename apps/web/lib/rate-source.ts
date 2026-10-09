import { createHmac } from 'node:crypto';
import { isIP } from 'node:net';

const TRUSTED_HEADERS = new Set(['', 'x-real-ip']);

/**
 * Signs the trusted client address used for private API rate limits.
 * Returns false when the ingress trust configuration is invalid (fail closed).
 * TRUST_PROXY_HEADER=x-real-ip uses an edge-set header such as Railway's;
 * otherwise TRUST_PROXY_HOPS selects the Nth X-Forwarded-For entry from the right.
 * Earlier caller-supplied entries never define the rate-limit source.
 */
export function signRateSource(request: Request, headers: Headers): boolean {
  const trustedHeader = (process.env.TRUST_PROXY_HEADER ?? '').trim().toLowerCase();
  const rawHops = process.env.TRUST_PROXY_HOPS ?? '0';
  if (!TRUSTED_HEADERS.has(trustedHeader) || !/^\d+$/.test(rawHops) || Number(rawHops) > 10) return false;
  const hops = Number(rawHops);
  if (!trustedHeader && hops === 0) return true;
  const key = process.env.BACKEND_PROXY_KEY;
  if (!key || key.length < 32) return false;
  let source: string | undefined;
  if (trustedHeader) {
    const value = request.headers.get(trustedHeader);
    source = value && value.length <= 64 ? value.trim() : undefined;
  } else {
    const forwarded = request.headers.get('x-forwarded-for');
    const chain = forwarded && forwarded.length <= 2048 ? forwarded.split(',').map(value => value.trim()) : [];
    source = chain[chain.length - hops];
  }
  if (source && /^[a-fA-F0-9:.]+$/.test(source) && isIP(source)) {
    const timestamp = String(Math.floor(Date.now() / 1000));
    headers.set('x-milo-rate-source', source);
    headers.set('x-milo-rate-timestamp', timestamp);
    headers.set('x-milo-rate-signature', createHmac('sha256', key).update(`${timestamp}.${source}`).digest('hex'));
  }
  return true;
}
