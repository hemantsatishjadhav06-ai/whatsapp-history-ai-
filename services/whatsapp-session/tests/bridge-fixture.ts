/** Synthetic, loopback-only bridge fixture; the production entrypoint cannot load it. */
import { EventEmitter } from "node:events";
import { createServer } from "node:http";
import type { IncomingMessage, ServerResponse } from "node:http";
import { timingSafeEqual } from "node:crypto";
import type { WASocket, AuthenticationState, Contact } from "@whiskeysockets/baileys";
import type { Identity } from "../src/protocol.ts";
import { silenceDependencyConsole } from "../src/isolated-console.ts";

if (process.env.NODE_ENV !== "test") throw new Error("Synthetic fixture requires NODE_ENV=test");
silenceDependencyConsole();
const { Sessions } = await import("../src/sessions.ts");
const { postgresAuthStores } = await import("../src/storage.ts");
const { authorityClient } = await import("../src/authority.ts");
const { encryptionKey } = await import("../src/crypto.ts");
const { privateServer } = await import("../src/server.ts");
const { individualJid, canonicalJid } = await import("../src/protocol.ts");

function required(name: string): string {
  const value = process.env[name]; if (!value) throw new Error(`Missing fixture variable ${name}`); return value;
}
function port(name: string): number {
  const value = Number(required(name));
  if (!Number.isSafeInteger(value) || value < 1 || value > 65535) throw new Error("Invalid fixture port");
  return value;
}
function loopback(url: URL): boolean {
  const host = url.hostname.replace(/^\[|\]$/g, "");
  return host === "localhost" || host === "::1" || /^127\.(?:\d{1,3}\.){2}\d{1,3}$/.test(host);
}
const sessionPort = port("SESSION_TEST_PORT"), controlPort = port("SESSION_TEST_CONTROL_PORT");
if (sessionPort === controlPort) throw new Error("Fixture ports must be separate");
const databaseUrl = new URL(required("NODE_TEST_DATABASE_URL"));
if (!["postgres:", "postgresql:"].includes(databaseUrl.protocol) || !loopback(databaseUrl) ||
    ["no-verify", "require"].includes(databaseUrl.searchParams.get("sslmode") ?? "")) {
  throw new Error("Synthetic fixture requires loopback PostgreSQL with normal verification");
}
const authorityUrl = new URL(required("PYTHON_AUTHORITY_URL"));
if (!loopback(authorityUrl)) throw new Error("Synthetic fixture requires a loopback Python authority");
const controlSecret = required("SESSION_TEST_CONTROL_TOKEN"), gatewaySecret = required("SESSION_GATEWAY_TOKEN");
if (Buffer.byteLength(controlSecret) < 32 || /\s/.test(controlSecret) || controlSecret === gatewaySecret) {
  throw new Error("Fixture control bearer must be a separate token of at least 32 bytes");
}
const controlToken = Buffer.from(controlSecret);
const storage = postgresAuthStores({ connectionString: databaseUrl.toString(),
  key: encryptionKey(required("SESSION_ENCRYPTION_KEY")) });
const identities = new WeakMap<AuthenticationState, Identity>();
type SyntheticSocket = { emitter: EventEmitter; socket: WASocket; ended: boolean };
const sockets = new Map<string, SyntheticSocket>();
const stats = { sends: 0, sockets_started: 0, sockets_ended: 0, logouts: 0 };
const sessions = new Sessions({ enabled: true,
  authority: authorityClient({ origin: authorityUrl.toString(), token: required("PYTHON_INTERNAL_TOKEN") }),
  stores: async (identity, onLost) => {
    const store = await storage.factory(identity, onLost); identities.set(store.state, identity); return store;
  },
  socketFactory(config) {
    const identity = identities.get(config.auth);
    if (!identity) throw new Error("Synthetic socket is missing its store identity");
    const emitter = new EventEmitter();
    const row: SyntheticSocket = { emitter, socket: null as unknown as WASocket, ended: false };
    const socket = { ev: emitter, user: config.auth.creds.me ?? undefined,
      async sendMessage(_jid: string, _body: unknown, options: { messageId: string }) {
        if (row.ended) throw new Error("Synthetic socket ended");
        stats.sends += 1; return { key: { id: options.messageId } };
      },
      async logout() { stats.logouts += 1; },
      async end() { if (!row.ended) { row.ended = true; stats.sockets_ended += 1; } },
    } as unknown as WASocket;
    row.socket = socket; sockets.set(identity.connector_id, row); stats.sockets_started += 1;
    // Restored synthetic registered credentials exercise real encrypted SQL restart.
    if (config.auth.creds.registered && config.auth.creds.me) {
      setImmediate(() => { if (!row.ended) emitter.emit("connection.update", { connection: "open" }); });
    }
    return socket;
  },
});
const server = privateServer({ token: gatewaySecret, sessions, ready: () => storage.ready() });

function respond(response: ServerResponse, status: number, body: unknown): void {
  response.writeHead(status, { "Content-Type": "application/json", "Cache-Control": "no-store" });
  response.end(JSON.stringify(body));
}
async function body(request: IncomingMessage): Promise<Record<string, unknown>> {
  if (request.headers["content-type"] !== "application/json") throw new Error("Invalid control request");
  const chunks: Buffer[] = []; let size = 0;
  for await (const chunk of request) {
    const bytes = Buffer.from(chunk as Uint8Array); size += bytes.length;
    if (size > 32_768) throw new Error("Control request too large"); chunks.push(bytes);
  }
  const value: unknown = JSON.parse(Buffer.concat(chunks).toString("utf8"));
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("Invalid control object");
  return value as Record<string, unknown>;
}
function string(value: unknown, max = 4096): string {
  if (typeof value !== "string" || !value || value.length > max) throw new Error("Invalid control string");
  return value;
}
function jid(value: unknown): string {
  if (!individualJid(value)) throw new Error("Invalid synthetic individual JID"); return value;
}
let inFlight = 0;
const control = createServer(async (request, response) => {
  const supplied = Buffer.from(request.headers.authorization?.replace(/^Bearer /, "") ?? "");
  if (!request.headers.authorization?.startsWith("Bearer ") || supplied.length !== controlToken.length ||
      !timingSafeEqual(supplied, controlToken)) { respond(response, 401, { reason_code: "UNAUTHENTICATED" }); return; }
  if (request.headers.origin || request.headers.cookie || request.headers["sec-fetch-site"]) {
    respond(response, 403, { reason_code: "BROWSER_INGRESS_FORBIDDEN" }); return;
  }
  if (request.method === "GET" && request.url === "/stats") { respond(response, 200, { ...stats }); return; }
  if (request.method !== "POST" || !["/qr", "/connect", "/contacts", "/message", "/receipt", "/wait"].includes(request.url ?? "")) {
    respond(response, 404, { reason_code: "NOT_FOUND" }); return;
  }
  if (inFlight >= 8) { respond(response, 503, { reason_code: "FIXTURE_BUSY" }); return; }
  inFlight += 1;
  try {
    const input = await body(request);
    if (request.url !== "/wait") {
      const row = sockets.get(string(input.connector_id, 120));
      if (!row || row.ended) throw new Error("No active synthetic socket");
      if (request.url === "/qr") {
        const value = string(input.value); if (!value.startsWith("synthetic-")) throw new Error("Synthetic QR required");
        row.emitter.emit("connection.update", { qr: value });
      } else if (request.url === "/connect") {
        const account = jid(input.account_id), aliases = input.account_aliases ?? [];
        if (!Array.isArray(aliases) || aliases.length > 2) throw new Error("Invalid synthetic aliases");
        const lid = aliases.map(jid).find(alias => alias !== account && alias.endsWith("@lid"));
        const me: Contact = { id: account, name: "Synthetic linked account", ...(lid ? { lid } : {}) };
        row.socket.user = me;
        row.emitter.emit("creds.update", { me, registered: true });
        row.emitter.emit("connection.update", { connection: "open" });
      } else if (request.url === "/contacts") {
        if (!Array.isArray(input.chats) || input.chats.length > 300) throw new Error("Invalid synthetic contacts");
        row.emitter.emit("contacts.upsert", input.chats.map(value => {
          if (!value || typeof value !== "object") throw new Error("Invalid synthetic contact");
          const chat = value as Record<string, unknown>;
          return { id: jid(chat.provider_chat_id), name: string(chat.title, 160) };
        }));
      } else if (request.url === "/message") {
        const remoteJid = jid(input.provider_chat_id), outgoing = input.direction === "outbound";
        if (!["inbound", "outbound"].includes(String(input.direction)) ||
            !["live", "replay", "history"].includes(String(input.origin))) throw new Error("Invalid synthetic message");
        if (input.sender_id !== undefined && input.sender_id !== (outgoing ? canonicalJid(row.socket.user?.id ?? "") : remoteJid)) {
          throw new Error("Synthetic sender must match one-to-one SDK semantics");
        }
        const message = { key: { id: string(input.provider_message_id, 180), remoteJid, fromMe: outgoing },
          messageTimestamp: Math.floor(Date.now() / 1000), message: { conversation: string(input.text) } };
        if (input.origin === "history") row.emitter.emit("messaging-history.set", { chats: [], messages: [message] });
        else row.emitter.emit("messages.upsert", { type: input.origin === "live" ? "notify" : "append", messages: [message] });
      } else {
        if (input.status !== "delivered") throw new Error("Only real SDK delivery receipt shape is supported");
        row.emitter.emit("message-receipt.update", [{ key: { id: string(input.provider_message_id, 180), fromMe: true },
          receipt: { receiptTimestamp: Math.floor(Date.now() / 1000) } }]);
      }
    }
    await sessions.settlePendingEvents(); respond(response, 200, { status: "settled" });
  } catch { respond(response, 400, { reason_code: "INVALID_SYNTHETIC_CONTROL" }); }
  finally { inFlight -= 1; }
});
control.headersTimeout = 5000; control.requestTimeout = 15_000; control.keepAliveTimeout = 5000;
await storage.ready();
await sessions.restore(await storage.restorableIdentities(20));
await new Promise<void>(resolve => server.listen(sessionPort, "127.0.0.1", resolve));
await new Promise<void>(resolve => control.listen(controlPort, "127.0.0.1", resolve));
process.stdout.write("Synthetic private session fixture ready; no WhatsApp socket constructed.\n");
let stopping = false;
for (const signal of ["SIGINT", "SIGTERM"] as const) process.on(signal, () => {
  if (stopping) return; stopping = true;
  server.closeAllConnections(); control.closeAllConnections();
  void Promise.all([new Promise<void>(resolve => server.close(() => resolve())),
    new Promise<void>(resolve => control.close(() => resolve()))]).then(() => sessions.close())
    .then(() => storage.close()).then(() => process.exit(0));
});
