import { isIP } from "node:net";
import { Blocked, checkAuthority } from "./protocol.ts";
import type { Answer, Authority, BackfillReport, Identity, Operation, SendEnvelope, SessionEvent, SyncBatch } from "./protocol.ts";
export interface AuthorityClient {
  authorize(operation: Operation, identity: Identity, send?: SendEnvelope): Promise<Authority>;
  event(event: SessionEvent): Promise<void>;
  /** Bulk chat and message sync. Never throws: the caller decides retry, split or stop from the status. */
  sync?(batch: SyncBatch): Promise<Answer>;
  /** Report backfill outcomes and receive the next anchors (newest chats first). */
  backfill?(input: Identity & { reports: BackfillReport[]; limit: number }): Promise<Answer>;
}
export function authorityClient(options: { origin: string; token: string; fetcher?: typeof fetch }): AuthorityClient {
  let url: URL;
  try { url = new URL(options.origin); } catch { throw new Error("Invalid private authority configuration"); }
  const host = url.hostname.replace(/^\[|\]$/g, "");
  const loopback = host === "localhost" || host === "::1" || (isIP(host) === 4 && host.startsWith("127."));
  const railwayPrivate = /^[a-z0-9-]+\.railway\.internal$/.test(host);
  if (url.username || url.password || url.search || url.hash || !["", "/"].includes(url.pathname) ||
      !["https:", "http:"].includes(url.protocol) || (url.protocol === "http:" && !loopback && !railwayPrivate) ||
      !options.token || /\s/.test(options.token)) throw new Error("Invalid private authority configuration");
  const fetcher = options.fetcher ?? fetch;
  async function request(path: string, body: unknown, timeout: number): Promise<Answer> {
    let response: Response;
    try {
      response = await fetcher(new URL(path, url), { method: "POST", redirect: "error",
        headers: { Authorization: `Bearer ${options.token}`, "Content-Type": "application/json" },
        body: JSON.stringify(body), signal: AbortSignal.timeout(timeout) });
    } catch { return { status: 0, body: null }; }
    const declared = Number(response.headers.get("content-length") ?? 0);
    const reader = response.body?.getReader();
    if (declared > 131_072 || !reader) { await reader?.cancel().catch(() => {}); return { status: response.status, body: null }; }
    const chunks: Uint8Array[] = []; let length = 0;
    try {
      while (true) {
        const value = await reader.read(); if (value.done) break;
        length += value.value.length;
        if (length > 131_072) { await reader.cancel(); return { status: response.status, body: null }; }
        chunks.push(value.value);
      }
    } catch { return { status: 0, body: null }; } finally { reader.releaseLock(); }
    try { return { status: response.status, body: JSON.parse(Buffer.concat(chunks).toString("utf8")) as unknown }; }
    catch { return { status: response.status, body: null }; }
  }
  async function post(path: string, body: unknown): Promise<unknown> {
    const answer = await request(path, body, 4000);
    if (answer.status < 200 || answer.status > 299 || answer.body === null) throw new Blocked("AUTHORITY_UNAVAILABLE");
    return answer.body;
  }
  return {
    async authorize(operation, identity, send) {
      const result = await post("/internal/whatsapp-session-authority", {
        schema_version: identity.schema_version, workspace_id: identity.workspace_id,
        connector_id: identity.connector_id, connector_fence: identity.connector_fence,
        account_id: identity.account_id, operation, send: send ?? null });
      return checkAuthority(result, identity);
    },
    async event(event) { await post("/internal/whatsapp-session-events", event); },
    sync: batch => request("/internal/whatsapp-session-sync", batch, 30_000),
    backfill: input => request("/internal/whatsapp-session-backfill", input, 10_000),
  };
}
