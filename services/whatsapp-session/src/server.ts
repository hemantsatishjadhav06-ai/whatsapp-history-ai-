import { timingSafeEqual } from "node:crypto";
import { createServer } from "node:http";
import type { IncomingMessage, ServerResponse } from "node:http";
import { Blocked, parseIdentity } from "./protocol.ts";
import type { Identity, SendEnvelope } from "./protocol.ts";
import type { Sessions } from "./sessions.ts";
function respond(response: ServerResponse, status: number, body: unknown) {
  response.writeHead(status, { "Content-Type": "application/json", "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff" }); response.end(JSON.stringify(body));
}
async function readBody(request: IncomingMessage): Promise<unknown> {
  if (!/^application\/json(?:\s*;\s*charset=utf-8)?$/i.test(request.headers["content-type"] ?? "")) throw new Blocked("INVALID_REQUEST");
  const declared = request.headers["content-length"];
  if (declared && (!/^\d+$/.test(declared) || Number(declared) > 32_768)) throw new Blocked("BODY_TOO_LARGE");
  const chunks: Buffer[] = []; let length = 0;
  for await (const chunk of request) {
    const bytes = Buffer.from(chunk as Uint8Array); length += bytes.length;
    if (length > 32_768) throw new Blocked("BODY_TOO_LARGE"); chunks.push(bytes);
  }
  try { return JSON.parse(Buffer.concat(chunks).toString("utf8")) as unknown; } catch { throw new Blocked("INVALID_REQUEST"); }
}
export function privateServer(options: { token: string; sessions: Sessions; ready: () => Promise<void>; maxConcurrent?: number }) {
  if (Buffer.byteLength(options.token) < 32 || /\s/.test(options.token)) throw new Error("SESSION_GATEWAY_TOKEN must contain at least 32 bytes");
  const token = Buffer.from(options.token); let inFlight = 0;
  const limit = options.maxConcurrent ?? 32;
  if (!Number.isSafeInteger(limit) || limit < 1 || limit > 256) throw new Error("Invalid gateway admission limit");
  const server = createServer(async (request, response) => {
    if (request.method === "GET" && request.url === "/healthz") {
      respond(response, 200, { status: "ok", provider: "whatsapp_personal", simulation: false, capacity_verified: false }); return;
    }
    if (request.method === "GET" && request.url === "/readyz") {
      try { await options.ready(); respond(response, 200, { status: "ready", capacity_verified: false }); }
      catch { respond(response, 503, { reason_code: "STORAGE_NOT_READY" }); } return;
    }
    const auth = request.headers.authorization;
    const supplied = typeof auth === "string" && auth.startsWith("Bearer ") ? Buffer.from(auth.slice(7)) : Buffer.alloc(0);
    if (supplied.length !== token.length || !timingSafeEqual(supplied, token)) { respond(response, 401, { reason_code: "UNAUTHENTICATED" }); return; }
    // This is private service ingress, not a browser or cookie-authenticated endpoint.
    if (request.headers.origin || request.headers.cookie || request.headers["sec-fetch-site"]) { respond(response, 403, { reason_code: "BROWSER_INGRESS_FORBIDDEN" }); return; }
    if (request.method !== "POST" || !["/v1/sessions/start", "/v1/sessions/status", "/v1/sessions/chats",
      "/v1/sessions/disconnect", "/v1/messages/send"].includes(request.url ?? "")) { respond(response, 404, { reason_code: "NOT_FOUND" }); return; }
    if (inFlight >= limit) { respond(response, 503, { reason_code: "GATEWAY_BUSY" }); return; }
    inFlight += 1;
    try {
      let raw = await readBody(request); let filter: string | undefined;
      if (request.url === "/v1/sessions/chats" && raw && typeof raw === "object" && !Array.isArray(raw)) {
        const row = raw as Record<string, unknown>;
        if (Object.hasOwn(row, "provider_chat_id")) {
          if (typeof row.provider_chat_id !== "string") throw new Blocked("INVALID_REQUEST");
          filter = row.provider_chat_id; const { provider_chat_id: _filter, ...rest } = row; raw = rest;
        }
      }
      const identity = parseIdentity(raw, request.url === "/v1/messages/send");
      const result = request.url === "/v1/sessions/start" ? await options.sessions.start(identity) :
        request.url === "/v1/sessions/status" ? await options.sessions.status(identity) :
        request.url === "/v1/sessions/chats" ? await options.sessions.chats(identity, filter) :
        request.url === "/v1/sessions/disconnect" ? await options.sessions.disconnect(identity) :
        await options.sessions.send(identity as SendEnvelope);
      respond(response, 200, result);
    } catch (error) {
      const reason = error instanceof Blocked ? error.reason_code : "SESSION_UNAVAILABLE";
      const status = reason === "INVALID_REQUEST" || reason === "BODY_TOO_LARGE" ? 400 :
        ["GATEWAY_BUSY", "SESSION_CELL_FULL", "PERSONAL_QR_DISABLED", "SESSION_UNAVAILABLE"].includes(reason) ? 503 : 409;
      respond(response, status, { reason_code: reason });
    } finally { inFlight -= 1; }
  });
  server.headersTimeout = 5000; server.requestTimeout = 15_000; server.keepAliveTimeout = 5000;
  return server;
}
