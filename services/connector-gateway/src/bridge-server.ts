/** Authenticated HTTP bridge to Python's current SQL authority. Mock transport only. */
import { timingSafeEqual } from "node:crypto";
import { createServer } from "node:http";
import type { IncomingMessage, ServerResponse } from "node:http";
import { isIP } from "node:net";
import { MockActionAdapter, MOCK_CAPABILITIES, MOCK_ADAPTER_VERSION } from "./action-adapter.ts";
import { ActionBlocked } from "./action-contract.ts";
import type { ActionAuthorizer } from "./action-contract.ts";

function tokenBytes(token: string): Buffer {
  if (typeof token !== "string" || Buffer.byteLength(token) < 32 || /\s/.test(token)) {
    throw new Error("A separate configured gateway token of at least 32 bytes is required");
  }
  return Buffer.from(token);
}
function authenticated(request: IncomingMessage, token: Buffer): boolean {
  const header = request.headers.authorization;
  if (typeof header !== "string" || !header.startsWith("Bearer ")) return false;
  const supplied = Buffer.from(header.slice(7));
  return supplied.length === token.length && timingSafeEqual(supplied, token);
}
function configuredAuthorityUrl(value: string): string {
  const url = new URL(value);
  if (!["http:", "https:"].includes(url.protocol) || url.username || url.password || url.search || url.hash ||
      !["", "/", "/internal/dispatch-authority"].includes(url.pathname)) {
    throw new Error("Authority URL must be a configured HTTP(S) origin or exact authority endpoint");
  }
  const host = url.hostname.replace(/^\[|\]$/g, "");
  const loopback = host === "localhost" || host === "::1" || (isIP(host) === 4 && host.startsWith("127."));
  if (url.protocol === "http:" && !loopback) throw new Error("Plain HTTP authority must use loopback");
  url.pathname = "/internal/dispatch-authority";
  return url.toString();
}
export function pythonAuthorityClient(options: { authorityUrl: string; internalToken: string;
  timeoutMs?: number; fetcher?: typeof fetch }): ActionAuthorizer {
  const target = configuredAuthorityUrl(options.authorityUrl);
  // The Python internal token may predate the new separate gateway token. Require
  // a configured nonempty value, without imposing a new length on existing installs.
  if (!options.internalToken || /\s/.test(options.internalToken)) throw new Error("Python internal token is required");
  const timeoutMs = options.timeoutMs ?? 2000;
  if (!Number.isSafeInteger(timeoutMs) || timeoutMs < 1 || timeoutMs > 5000) throw new Error("Invalid authority timeout");
  const fetcher = options.fetcher ?? fetch;
  return async (envelope) => {
    const response = await fetcher(target, { method: "POST", redirect: "error",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${options.internalToken}` },
      body: JSON.stringify(envelope), signal: AbortSignal.timeout(timeoutMs) });
    if (!response.ok) throw new ActionBlocked("AUTHORITY_UNAVAILABLE");
    const body = await response.text();
    if (Buffer.byteLength(body) > 262_144) throw new ActionBlocked("AUTHORITY_UNAVAILABLE");
    return JSON.parse(body) as unknown;
  };
}
function respond(response: ServerResponse, code: number, body: unknown): void {
  response.writeHead(code, { "Content-Type": "application/json", "Cache-Control": "no-store" });
  response.end(JSON.stringify(body));
}
async function body(request: IncomingMessage): Promise<unknown> {
  if (!/^application\/json(?:\s*;\s*charset=utf-8)?$/i.test(request.headers["content-type"] ?? "")) {
    throw new Error("JSON content type required");
  }
  const declared = request.headers["content-length"];
  if (declared && (!/^\d+$/.test(declared) || Number(declared) > 65_536)) throw new Error("Body too large");
  const chunks: Buffer[] = [];
  let length = 0;
  for await (const chunk of request) {
    const data = Buffer.from(chunk as Uint8Array);
    length += data.length;
    if (length > 65_536) throw new Error("Body too large");
    chunks.push(data);
  }
  return JSON.parse(Buffer.concat(chunks).toString("utf8")) as unknown;
}
export function createActionGateway(options: { token: string; readCurrentAuthority: ActionAuthorizer;
  adapter?: MockActionAdapter }) {
  const token = tokenBytes(options.token);
  const adapter = options.adapter ?? new MockActionAdapter({ readCurrentAuthority: options.readCurrentAuthority });
  const server = createServer(async (request, response) => {
    if (request.method === "GET" && request.url === "/healthz") {
      respond(response, 200, { status: "ok", provider: "mock", simulation: true,
        adapter_version: MOCK_ADAPTER_VERSION, capabilities: MOCK_CAPABILITIES });
      return;
    }
    if (!authenticated(request, token)) { respond(response, 401, { reason_code: "UNAUTHENTICATED" }); return; }
    if (request.method === "POST" && request.url === "/v1/actions") {
      let value: unknown;
      try { value = await body(request); }
      catch { respond(response, 400, { reason_code: "INVALID_ACTION" }); return; }
      try { respond(response, 200, await adapter.submit(value)); }
      catch (error) {
        // Do not echo malformed requests, private original records, URLs or credentials.
        respond(response, error instanceof ActionBlocked ? 409 : 400,
          { reason_code: error instanceof ActionBlocked ? error.reason_code : "INVALID_ACTION" });
      }
      return;
    }
    const reconcile = request.url?.match(/^\/v1\/actions\/([A-Za-z0-9_-]{1,255})$/);
    if (request.method === "GET" && reconcile?.[1]) {
      const result = adapter.reconcile(reconcile[1]);
      respond(response, result ? 200 : 404, result ?? { reason_code: "DELIVERY_UNCERTAIN" });
      return;
    }
    respond(response, 404, { reason_code: "NOT_FOUND" });
  });
  server.requestTimeout = 10_000;
  server.headersTimeout = 5000;
  server.keepAliveTimeout = 5000;
  return { server, adapter };
}
