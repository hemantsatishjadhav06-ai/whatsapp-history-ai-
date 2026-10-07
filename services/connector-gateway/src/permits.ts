import { createHash, createHmac, timingSafeEqual } from "node:crypto";
import type { SendPermitClaims, SignedSendPermit } from "./contract.ts";
import { enumValue, exactKeys, instantMilliseconds, integerValue, record, stringValue } from "./validation.ts";

export const MAX_PERMIT_LIFETIME_MS = 60_000;
const CLAIM_KEYS = ["schema_version", "intent_id", "workspace_id", "connector_id", "conversation_id", "account_id",
  "recipient_id", "conversation_kind", "content_hash", "conversation_revision", "control_epoch", "permission_version",
  "pause_generation", "connector_fence", "issued_at", "expires_at"] as const;

export function contentHash(text: string): string {
  return createHash("sha256").update(text, "utf8").digest("hex");
}
export function signingKey(key: Uint8Array): Buffer {
  if (!(key instanceof Uint8Array) || key.byteLength < 32) throw new Error("Signing key must contain at least 32 bytes");
  return Buffer.from(key);
}
export function parsePermitClaims(value: unknown): Readonly<SendPermitClaims> {
  const claims = record(value, "claims");
  exactKeys(claims, CLAIM_KEYS);
  if (claims.schema_version !== 1) throw new Error("Unsupported permit schema_version");
  const hash = stringValue(claims.content_hash, "content_hash", 64);
  if (!/^[0-9a-f]{64}$/.test(hash)) throw new Error("Invalid SHA-256 content_hash");
  const issued = instantMilliseconds(claims.issued_at, "issued_at");
  const expires = instantMilliseconds(claims.expires_at, "expires_at");
  if (expires <= issued || expires - issued > MAX_PERMIT_LIFETIME_MS) throw new Error("Permit lifetime must be within 60 seconds");
  return Object.freeze({
    schema_version: 1,
    intent_id: stringValue(claims.intent_id, "intent_id"),
    workspace_id: stringValue(claims.workspace_id, "workspace_id"),
    connector_id: stringValue(claims.connector_id, "connector_id"),
    conversation_id: stringValue(claims.conversation_id, "conversation_id"),
    account_id: stringValue(claims.account_id, "account_id"),
    recipient_id: stringValue(claims.recipient_id, "recipient_id"),
    conversation_kind: enumValue(claims.conversation_kind, "conversation_kind", ["contact", "group"]),
    content_hash: hash,
    conversation_revision: integerValue(claims.conversation_revision, "conversation_revision"),
    control_epoch: integerValue(claims.control_epoch, "control_epoch"),
    permission_version: integerValue(claims.permission_version, "permission_version", 1),
    pause_generation: integerValue(claims.pause_generation, "pause_generation"),
    connector_fence: integerValue(claims.connector_fence, "connector_fence", 1),
    issued_at: String(claims.issued_at),
    expires_at: String(claims.expires_at),
  });
}
function signature(claims: SendPermitClaims, key: Uint8Array): string {
  // Fixed field order makes the signature independent of incoming JSON key order.
  return createHmac("sha256", signingKey(key)).update(JSON.stringify(parsePermitClaims(claims)), "utf8").digest("hex");
}
/** Trusted dispatcher helper. No public endpoint should expose this operation or its key. */
export function issueSendPermit(claims: SendPermitClaims, key: Uint8Array): SignedSendPermit {
  const parsed = parsePermitClaims(claims);
  return Object.freeze({ claims: parsed, signature: signature(parsed, key) });
}
/** A correct signature is insufficient without a separate current-state authorization read. */
export function verifySendPermit(value: unknown, key: Uint8Array): Readonly<SendPermitClaims> {
  const permit = record(value, "permit");
  exactKeys(permit, ["claims", "signature"]);
  const claims = parsePermitClaims(permit.claims);
  const supplied = stringValue(permit.signature, "signature", 64);
  if (!/^[0-9a-f]{64}$/.test(supplied) ||
      !timingSafeEqual(Buffer.from(supplied, "hex"), Buffer.from(signature(claims, key), "hex"))) {
    throw new Error("Invalid permit signature");
  }
  return claims;
}
