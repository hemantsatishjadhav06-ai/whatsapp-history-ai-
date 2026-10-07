export const dynamic = 'force-dynamic';
export function GET() { return Response.json({ status: 'ok', service: 'milo-web', external_integrations: 'not_validated' }, { headers: { 'Cache-Control': 'no-store' } }); }
