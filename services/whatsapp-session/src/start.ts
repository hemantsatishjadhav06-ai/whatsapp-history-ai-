import { encryptionKey } from "./crypto.ts";
import { authorityClient } from "./authority.ts";
import { postgresAuthStores } from "./storage.ts";
import { Sessions } from "./sessions.ts";
import { privateServer } from "./server.ts";
import { setTimeout as wait } from "node:timers/promises";

const enabled = process.env.ENABLE_PERSONAL_WHATSAPP === "true";
const port = Number(process.env.PORT ?? process.env.SESSION_PORT ?? "8091");
if (!Number.isSafeInteger(port) || port < 1 || port > 65535) throw new Error("Invalid session service port");
const databaseUrl = process.env.DATABASE_URL ?? "";
let parsed: URL;
try { parsed = new URL(databaseUrl); } catch { throw new Error("Invalid session database configuration"); }
if (!["postgres:", "postgresql:"].includes(parsed.protocol)) throw new Error("PostgreSQL is required for session ownership");
if (parsed.searchParams.get("sslmode") === "no-verify" || parsed.searchParams.get("sslmode") === "require") {
  throw new Error("Use PostgreSQL verified TLS or the provider's private network; do not disable certificate verification");
}
const storage = postgresAuthStores({ connectionString: databaseUrl, key: encryptionKey(process.env.SESSION_ENCRYPTION_KEY ?? "") });
const maxSessions = Number(process.env.MAX_PERSONAL_SESSIONS ?? "20");
// Content-free operational events only: counts, status codes and a hashed session tag. Never JIDs or text.
const log = (event: string, fields: Record<string, string | number | boolean>) => {
  process.stdout.write(`${JSON.stringify({ at: new Date().toISOString(), event, ...fields })}\n`);
};
const sessions = new Sessions({ enabled, maxSessions, log,
  stores: storage.factory, authority: authorityClient({ origin: process.env.PYTHON_AUTHORITY_URL ?? "http://127.0.0.1:8000",
    token: process.env.PYTHON_INTERNAL_TOKEN ?? "" }) });
const server = privateServer({ token: process.env.SESSION_GATEWAY_TOKEN ?? "", sessions,
  ready: async () => { await storage.ready(); } });
server.listen(port, "::", () => { process.stdout.write(`Private WhatsApp linked-device pilot listening on port ${port}; account capacity unverified.\n`); });
let stopping = false;
// API migrations and authority may start after this private process. Restore only
// the bounded current SQL placement set, and never log identities or credentials.
void (async () => {
  if (!enabled) return;
  const deadline = Date.now() + 120_000;
  while (!stopping && Date.now() < deadline) {
    try {
      const result = await sessions.restore(await storage.restorableIdentities(maxSessions));
      if (result.rejected === 0) return;
    } catch { /* Wait for migrated private storage; no boot DDL or raw database error output. */ }
    await wait(2000);
  }
  if (!stopping) process.stdout.write("Private session startup restoration unavailable; current SQL authority remains required.\n");
})();
for (const signal of ["SIGINT", "SIGTERM"] as const) process.on(signal, () => {
  if (stopping) return; stopping = true;
  server.close(() => { void sessions.close().then(() => storage.close()).then(() => process.exit(0)); });
  setTimeout(() => { server.closeAllConnections(); void sessions.close().then(() => storage.close()).finally(() => process.exit(1)); }, 30_000).unref();
});
