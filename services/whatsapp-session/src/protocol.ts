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
