/** Versioned internal action protocol. Never expose this API to a model or browser. */
import { createHash } from "node:crypto";
import { enumValue, exactKeys, integerValue, record, stringValue } from "./validation.ts";

export const ACTION_KINDS = ["SEND_TEXT", "QUOTE", "REACTION", "FORWARD"] as const;
export type ActionKind = (typeof ACTION_KINDS)[number];
export type Json = null | boolean | number | string | readonly Json[] | { readonly [key: string]: Json };
export interface ActionEnvelope {
  readonly schema_version: 1;
  readonly action_id: string;
  readonly workspace_id: string;
  readonly connector_id: string;
  readonly account_id: string;
  readonly conversation_id: string;
  readonly recipient_id: string;
  readonly kind: ActionKind;
  readonly payload: Readonly<Record<string, Json>>;
  readonly payload_hash: string;
  readonly connector_fence: number;
}
export interface NativeSource {
  readonly ref: string;
  readonly key: { readonly remote_jid: string; readonly message_id: string; readonly from_me: boolean;
    readonly participant?: string | null };
  readonly revision: number;
  readonly deleted: boolean;
  readonly expires_at?: string | null;
  readonly record: Readonly<Record<string, Json>>;
}
export interface ActionAuthority {
  readonly schema_version: 1;
  readonly allowed: boolean;
  readonly reason_code: string;
  readonly action_id: string;
  readonly workspace_id: string;
  readonly connector_id: string;
  readonly account_id: string;
  readonly conversation_id: string;
  readonly recipient_id: string;
  readonly kind: ActionKind;
  readonly payload_hash: string;
  readonly connector_fence: number;
  readonly authority_expires_at: string;
  readonly source_record?: NativeSource | null;
}
export type ActionAuthorizer = (envelope: ActionEnvelope) => Promise<unknown>;
export interface ActionResult {
  readonly schema_version: 1;
  readonly simulation: true;
  readonly action_id: string;
  readonly payload_hash: string;
  readonly connector_fence: number;
  readonly status: "accepted" | "uncertain" | "failed";
  readonly provider_message_id: string | null;
  readonly submitted_at: string;
}
export class ActionBlocked extends Error {
  readonly reason_code: string;
  constructor(reason: string) { super(reason); this.reason_code = reason; }
}
const ENVELOPE_KEYS = ["schema_version", "action_id", "workspace_id", "connector_id", "account_id",
  "conversation_id", "recipient_id", "kind", "payload", "payload_hash", "connector_fence"] as const;

function jsonValue(value: unknown, depth = 0): Json {
  if (depth > 8) throw new Error("Payload is too deeply nested");
  if (value === null || typeof value === "string" || typeof value === "boolean") return value;
  if (typeof value === "number" && Number.isSafeInteger(value)) return value;
  if (Array.isArray(value)) return Object.freeze(value.map((item) => jsonValue(item, depth + 1)));
  const object = record(value, "payload");
  const result: Record<string, Json> = Object.create(null) as Record<string, Json>;
  for (const key of Object.keys(object).sort()) {
    if (!/^[\x00-\x7f]*$/.test(key) || ["__proto__", "constructor", "prototype"].includes(key)) {
      throw new Error("Invalid payload key");
    }
    result[key] = jsonValue(object[key], depth + 1);
  }
  return Object.freeze(result);
}
export function canonicalJson(value: unknown): string { return JSON.stringify(jsonValue(value)); }
export function actionPayloadHash(payload: unknown): string {
  return createHash("sha256").update(canonicalJson(payload), "utf8").digest("hex");
}
export function parseActionEnvelope(value: unknown): ActionEnvelope {
  const envelope = record(value, "action");
  exactKeys(envelope, ENVELOPE_KEYS);
  if (Object.keys(envelope).length !== ENVELOPE_KEYS.length || envelope.schema_version !== 1) {
    throw new Error("Missing fields or unsupported action schema");
  }
  const payload = jsonValue(record(envelope.payload, "payload")) as Readonly<Record<string, Json>>;
  const kind = enumValue(envelope.kind, "kind", ACTION_KINDS);
  const payloadFields = kind === "SEND_TEXT" ? ["text"] : kind === "QUOTE" ?
    ["text", "target_message_id", "target_source_revision", "native_record_ref"] : kind === "REACTION" ?
      ["emoji", "target_message_id", "target_source_revision", "native_record_ref"] :
      ["target_message_id", "target_source_revision", "native_record_ref", "route_id", "route_version", "category"];
  exactKeys(payload as Record<string, unknown>, payloadFields);
  if (Object.keys(payload).length !== payloadFields.length) throw new Error("Missing action payload fields");
  if (kind !== "SEND_TEXT") {
    stringValue(payload.target_message_id, "target_message_id", 255);
    stringValue(payload.native_record_ref, "native_record_ref", 255);
    integerValue(payload.target_source_revision, "target_source_revision", 1);
  }
  if (kind === "FORWARD") {
    stringValue(payload.route_id, "route_id", 255);
    integerValue(payload.route_version, "route_version", 1);
    if (payload.category !== "owner_selected_text") throw new Error("Invalid forwarding category");
  }
  const hash = stringValue(envelope.payload_hash, "payload_hash", 64);
  if (!/^[0-9a-f]{64}$/.test(hash) || hash !== actionPayloadHash(payload)) throw new Error("Payload hash mismatch");
  const parsed: ActionEnvelope = Object.freeze({ schema_version: 1,
    action_id: stringValue(envelope.action_id, "action_id", 255),
    workspace_id: stringValue(envelope.workspace_id, "workspace_id", 255),
    connector_id: stringValue(envelope.connector_id, "connector_id", 255),
    account_id: stringValue(envelope.account_id, "account_id", 255),
    conversation_id: stringValue(envelope.conversation_id, "conversation_id", 255),
    recipient_id: stringValue(envelope.recipient_id, "recipient_id", 255),
    kind, payload, payload_hash: hash,
    connector_fence: integerValue(envelope.connector_fence, "connector_fence", 1) });
  if (Buffer.byteLength(canonicalJson(parsed), "utf8") > 65_536) throw new Error("Action is too large");
  return parsed;
}
