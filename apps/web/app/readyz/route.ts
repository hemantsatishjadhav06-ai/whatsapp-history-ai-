export const dynamic = 'force-dynamic';
const MAX_AGE_MS = 5_000;
let probe: { key: string; expires: number; result: Promise<boolean> } | undefined;

/** Coarse dependency status only; no owner content, credentials or private URL. */
export async function GET() {
  let origin: string;
  try {
    const configured = new URL(process.env.BACKEND_URL || '');
    if (!['http:', 'https:'].includes(configured.protocol) || configured.username || configured.password
        || configured.search || configured.hash || configured.pathname !== '/') throw new Error('Invalid origin');
    origin = configured.origin;
  } catch { return output(false); }
  if (!probe || probe.key !== origin || probe.expires <= Date.now()) {
    const result = (async () => {
      try {
        const response = await fetch(new URL('/health/ready', origin), {
          cache: 'no-store', redirect: 'manual', signal: AbortSignal.timeout(3_000),
        });
        const ready = response.status === 200;
        // The health response body is unnecessary; avoid buffering it.
        void response.body?.cancel().catch(() => {});
        return ready;
      } catch { return false; }
    })();
    probe = { key: origin, expires: Date.now() + MAX_AGE_MS, result };
  }
  return output(await probe.result);
}

function output(ready: boolean) {
  const sha = process.env.RENDER_GIT_COMMIT;
  return Response.json({ status: ready ? 'ready' : 'unavailable', service: 'milo-web',
    release_commit: sha && /^[a-f0-9]{40}$/.test(sha) ? sha : null,
    dependency_status_max_age_seconds: 5,
    external_integrations: 'not_validated' }, { status: ready ? 200 : 503,
    headers: { 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff' } });
}
