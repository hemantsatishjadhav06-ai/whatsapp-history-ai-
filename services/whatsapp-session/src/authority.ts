import { isIP } from "node:net";
import { Blocked, checkAuthority } from "./protocol.ts";
import type { Authority, Identity, Operation, SendEnvelope, SessionEvent } from "./protocol.ts";
export interface AuthorityClient {
  authorize(operation: Operation, identity: Identity, send?: SendEnvelope): Promise<Authority>;
  event(event: SessionEvent): Promise<void>;
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
  async function post(path: string, body: unknown): Promise<unknown> {
    const response = await fetcher(new URL(path, url), { method: "POST", redirect: "error",
      headers: { Authorization: `Bearer ${options.token}`, "Content-Type": "application/json" },
      body: JSON.stringify(body), signal: AbortSignal.timeout(4000) });
    if (!response.ok) throw new Blocked("AUTHORITY_UNAVAILABLE");
    const declared = Number(response.headers.get("content-length") ?? 0);
    if (declared > 131_072) throw new Blocked("AUTHORITY_UNAVAILABLE");
    const reader = response.body?.getReader();
    if (!reader) throw new Blocked("AUTHORITY_UNAVAILABLE");
    const chunks: Uint8Array[] = []; let length = 0;
    try {
      while (true) {
        const value = await reader.read(); if (value.done) break;
        length += value.value.length;
        if (length > 131_072) { await reader.cancel(); throw new Blocked("AUTHORITY_UNAVAILABLE"); }
        chunks.push(value.value);
      }
    } finally { reader.releaseLock(); }
    return JSON.parse(Buffer.concat(chunks).toString("utf8")) as unknown;
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
  };
}
