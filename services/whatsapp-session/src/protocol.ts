/** Private server protocol. Public callers never choose account IDs or send envelopes. */
export type Identity = Readonly<{ schema_version: 1; workspace_id: string; connector_id: string;
  connector_fence: number; account_id: string | null }>;
export type Operation = "start" | "status" | "chats" | "disconnect" | "ingest" | "send";
export type SendEnvelope = Identity & Readonly<{ account_id: string; conversation_id: string; recipient_id: string;
  draft_id: string; attempt_id: string; payload_hash: string; text: string }>;
export type Grant = Readonly<{ conversation_id: string; provider_chat_id: string; read: boolean; retain: boolean; send: boolean }>;
export type Authority = Identity & Readonly<{ allowed: boolean; reason_code: string;
  authority_expires_at: string; grants: readonly Grant[] }>;
export type SessionEvent = Identity & Readonly<{ event_type: "connection" | "message" | "receipt";
  data: Readonly<Record<string, unknown>> }>;
export class Blocked extends Error {
  readonly reason_code: string;
  constructor(reason: string) { super(reason); this.reason_code = reason; }
}
/** Python answered and refused; `denial` is its content-free reason (for example lease_expired or revoked). */
export class Denied extends Blocked {
  readonly denial: string;
  constructor(denial: string) { super("AUTHORITY_DENIED"); this.denial = denial; }
}
export type SyncOrigin = "live" | "history" | "replay" | "backfill";
/** `contact_only` rows (address book, live chat events) describe existing chats but never create one. */
export type SyncChat = { jid: string; alt_jid?: string; title?: string;
  title_source?: "contact" | "verified" | "chat" | "push"; unread_count?: number; archived?: boolean; contact_only?: boolean;
  last_activity_at?: string };
export type SyncMessage = { id: string; chat_jid: string; chat_alt_jid?: string; from_me: boolean; timestamp: string;
  event: "created" | "edited" | "deleted";
  kind: "text" | "media" | "location" | "contact" | "poll" | "event" | "call" | "other";
  text: string; push_name?: string; reply_to?: string; revision?: number };
export type SyncProgress = { phase: "initial" | "recent" | "full" | "on_demand" | "push_name" | "complete" | "live";
  percent?: number };
export type SyncBatch = Identity & Readonly<{ origin: SyncOrigin; chats: SyncChat[]; messages: SyncMessage[];
  progress?: SyncProgress }>;
export type BackfillTarget = Readonly<{ jid: string; oldest_id: string; oldest_from_me: boolean; oldest_at: string }>;
export type BackfillReport = Readonly<{ jid: string; outcome: "requested" | "exhausted" | "failed" }>;
/** HTTP status from the private authority; 0 means the request never got an answer. */
export type Answer = Readonly<{ status: number; body: unknown }>;
export function parseIdentity(value: unknown, send = false): Identity | SendEnvelope {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Blocked("INVALID_REQUEST");
  const row = value as Record<string, unknown>;
  const expected = ["schema_version", "workspace_id", "connector_id", "connector_fence", "account_id",
    ...(send ? ["conversation_id", "recipient_id", "draft_id", "attempt_id", "payload_hash", "text"] : [])];
  if (Object.keys(row).length !== expected.length || Object.keys(row).some(key => !expected.includes(key)) ||
      row.schema_version !== 1 || typeof row.connector_fence !== "number" ||
      !Number.isSafeInteger(row.connector_fence) || row.connector_fence < 1) throw new Blocked("INVALID_REQUEST");
  for (const key of ["workspace_id", "connector_id", ...(send ? ["conversation_id", "draft_id", "attempt_id"] : [])]) {
    if (typeof row[key] !== "string" || !/^[A-Za-z0-9_-]{1,120}$/.test(row[key])) throw new Blocked("INVALID_REQUEST");
  }
  if (row.account_id !== null && !individualJid(row.account_id)) throw new Blocked("INVALID_REQUEST");
  if (send && (!individualJid(row.recipient_id) || !individualJid(row.account_id) ||
      typeof row.text !== "string" || !row.text.trim() || row.text.length > 4096 ||
      typeof row.payload_hash !== "string" || !/^[a-f0-9]{64}$/.test(row.payload_hash))) throw new Blocked("INVALID_REQUEST");
  return Object.freeze({ ...row }) as Identity | SendEnvelope;
}
export function individualJid(value: unknown): value is string {
  return typeof value === "string" && /^[0-9]{5,24}@(s\.whatsapp\.net|lid)$/.test(value);
}
export function canonicalJid(value: string): string {
  return value.replace(/:\d+@/, "@");
}
export function checkAuthority(value: unknown, identity: Identity, now = Date.now()): Authority {
  if (!value || typeof value !== "object") throw new Blocked("AUTHORITY_UNAVAILABLE");
  const row = value as Record<string, unknown>;
  if (row.schema_version === 1 && row.allowed === false) {
    throw new Denied(typeof row.reason_code === "string" && /^[a-z_]{1,40}$/.test(row.reason_code) ? row.reason_code : "denied");
  }
  if (row.schema_version !== 1 || row.allowed !== true ||
      ["workspace_id", "connector_id", "connector_fence", "account_id"].some(key =>
        row[key] !== identity[key as keyof Identity]) || typeof row.authority_expires_at !== "string") {
    throw new Blocked("AUTHORITY_DENIED");
  }
  const until = Date.parse(row.authority_expires_at);
  if (!Number.isFinite(until) || until <= now || until > now + 5000 || !Array.isArray(row.grants) || row.grants.length > 250) {
    throw new Blocked("AUTHORITY_EXPIRED");
  }
  const grants: Grant[] = [];
  for (const item of row.grants) {
    if (!item || typeof item !== "object") throw new Blocked("INVALID_AUTHORITY");
    const grant = item as Record<string, unknown>;
    if (typeof grant.conversation_id !== "string" || !individualJid(grant.provider_chat_id) ||
        ["read", "retain", "send"].some(key => typeof grant[key] !== "boolean")) throw new Blocked("INVALID_AUTHORITY");
    grants.push(Object.freeze({ conversation_id: grant.conversation_id, provider_chat_id: grant.provider_chat_id,
      read: grant.read as boolean, retain: grant.retain as boolean, send: grant.send as boolean }));
  }
  return Object.freeze({ ...identity, allowed: true, reason_code: String(row.reason_code ?? "ALLOWED"),
    authority_expires_at: row.authority_expires_at, grants: Object.freeze(grants) });
}
