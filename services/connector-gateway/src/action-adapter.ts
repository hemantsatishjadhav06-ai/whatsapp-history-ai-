/** Pinned operation mapping with a mandatory current-authority dependency; mock only. */
import { randomUUID } from "node:crypto";
import { ActionBlocked, canonicalJson, parseActionEnvelope } from "./action-contract.ts";
import type { ActionAuthorizer, ActionEnvelope, ActionResult, Json, NativeSource } from "./action-contract.ts";
import { exactKeys, instantMilliseconds, integerValue, record, stringValue } from "./validation.ts";

export const MOCK_ADAPTER_VERSION = "mock-actions-v1";
export const MOCK_CAPABILITIES = Object.freeze(Object.fromEntries([
  "send_text", "personalized_reply", "quoted_reply", "emoji_reaction", "native_forward", "save_contact",
  "contact_save_local", "contact_write_whatsapp", "contact_write_google", "contact_write_os", "pairing_code",
].map((name) => [name, Object.freeze({ status: ["send_text", "personalized_reply", "quoted_reply", "emoji_reaction",
  "native_forward"].includes(name) ? "supported" : "unsupported", adapter_version: MOCK_ADAPTER_VERSION,
  evidence_date: "2026-10-06", test_reference: "tests/action-bridge.test.ts",
  reason: name === "contact_save_local" || name === "save_contact" ?
    "Assistant-local contacts belong to Python SQL; this gateway performs no contact write." :
    "Synthetic adapter contract only; no real WhatsApp account or device capability is demonstrated." })])));

export type MockSocketOperation =
  { readonly kind: "send_text"; readonly recipient_id: string; readonly text: string } |
  { readonly kind: "quoted_reply"; readonly recipient_id: string; readonly text: string;
    readonly quoted: Readonly<Record<string, Json>> } |
  { readonly kind: "reaction"; readonly recipient_id: string; readonly react: { readonly text: string;
    readonly key: Readonly<Record<string, Json>> } } |
  { readonly kind: "native_forward"; readonly recipient_id: string;
    readonly forward: Readonly<Record<string, Json>> };

const BINDINGS = ["action_id", "workspace_id", "connector_id", "account_id", "conversation_id", "recipient_id",
  "kind", "payload_hash", "connector_fence"] as const;
function nativeSource(value: unknown, envelope: ActionEnvelope, now: number): NativeSource {
  const source = record(value, "source_record");
  exactKeys(source, ["ref", "key", "revision", "deleted", "expires_at", "record"]);
  const key = record(source.key, "source_key");
  exactKeys(key, ["remote_jid", "message_id", "from_me", "participant"]);
  const original = record(source.record, "authentic_record");
  const originalKey = record(original.key, "authentic_record.key");
  const ref = stringValue(source.ref, "source_ref", 255);
  stringValue(key.remote_jid, "remote_jid", 255);
  stringValue(key.message_id, "message_id", 255);
  integerValue(source.revision, "source_revision", 1);
  if (typeof key.from_me !== "boolean" || source.deleted !== false ||
      (source.expires_at != null && instantMilliseconds(source.expires_at, "source_expires_at") <= now)) {
    throw new ActionBlocked("SOURCE_MISSING");
  }
  if (key.participant != null) stringValue(key.participant, "participant", 255);
  if (originalKey.remoteJid !== key.remote_jid || originalKey.id !== key.message_id ||
      originalKey.fromMe !== key.from_me || (originalKey.participant ?? null) !== (key.participant ?? null)) {
    throw new ActionBlocked("SOURCE_MISSING");
  }
  record(original.message, "authentic_record.message");
  if (envelope.kind !== "FORWARD" && key.remote_jid !== envelope.recipient_id) {
    throw new ActionBlocked("SCOPE_DENIED");
  }
  if (envelope.payload.native_record_ref !== ref || envelope.payload.target_source_revision !== source.revision) {
    throw new ActionBlocked("SOURCE_MISSING");
  }
  return source as unknown as NativeSource;
}
/** Native operations use authoritative provider originals, never reconstructed text. */
export function mapMockOperation(envelope: ActionEnvelope, source: unknown, now: number): MockSocketOperation {
  if (envelope.kind === "SEND_TEXT") {
    const text = stringValue(envelope.payload.text, "text", 4096);
    if (!text.trim()) throw new ActionBlocked("INVALID_ACTION");
    return Object.freeze({ kind: "send_text", recipient_id: envelope.recipient_id, text });
  }
  const original = nativeSource(source, envelope, now);
  if (envelope.kind === "QUOTE") {
    const text = stringValue(envelope.payload.text, "text", 4096);
    if (!text.trim()) throw new ActionBlocked("INVALID_ACTION");
    return Object.freeze({ kind: "quoted_reply", recipient_id: envelope.recipient_id, text, quoted: original.record });
  }
  if (envelope.kind === "REACTION") {
    const emoji = stringValue(envelope.payload.emoji, "emoji", 32);
    return Object.freeze({ kind: "reaction", recipient_id: envelope.recipient_id,
      react: Object.freeze({ text: emoji, key: original.record.key as Readonly<Record<string, Json>> }) });
  }
  return Object.freeze({ kind: "native_forward", recipient_id: envelope.recipient_id, forward: original.record });
}

export class MockActionAdapter {
  readonly #authorizer: ActionAuthorizer;
  readonly #now: () => number;
  readonly #outcome: "accepted" | "uncertain";
  readonly #attempts = new Map<string, { binding: string; result: ActionResult }>();
  readonly #inflight = new Map<string, { binding: string; promise: Promise<ActionResult> }>();
  readonly #fences = new Map<string, number>();
  readonly #operations: MockSocketOperation[] = [];
  constructor(options: { readCurrentAuthority: ActionAuthorizer; now?: () => number;
    submissionOutcome?: "accepted" | "uncertain" }) {
    if (typeof options.readCurrentAuthority !== "function") throw new Error("Current authority service is mandatory");
    this.#authorizer = options.readCurrentAuthority;
    this.#now = options.now ?? Date.now;
    this.#outcome = options.submissionOutcome ?? "accepted";
  }
  #scope(envelope: ActionEnvelope): string { return canonicalJson([envelope.workspace_id, envelope.connector_id]); }
  /** Trusted local control event; higher fences permanently reject older account leases. */
  fence(connector: { workspace_id: string; connector_id: string }, value: number): void {
    integerValue(value, "connector_fence", 1);
    const scope = canonicalJson([connector.workspace_id, connector.connector_id]);
    this.#fences.set(scope, Math.max(this.#fences.get(scope) ?? 0, value));
  }
  async #submit(envelope: ActionEnvelope, binding: string): Promise<ActionResult> {
    const authority = record(await this.#authorizer(envelope), "authority");
    const now = this.#now();
    if (!Number.isFinite(now)) throw new ActionBlocked("AUTHORITY_UNAVAILABLE");
    exactKeys(authority, ["schema_version", "allowed", "reason_code", ...BINDINGS, "authority_expires_at", "source_record"]);
    if (authority.schema_version !== 1 || authority.allowed !== true) {
      throw new ActionBlocked(typeof authority.reason_code === "string" ? authority.reason_code : "SCOPE_DENIED");
    }
    for (const field of BINDINGS) {
      if (authority[field] !== envelope[field]) throw new ActionBlocked("CONTEXT_STALE");
    }
    const expires = instantMilliseconds(authority.authority_expires_at, "authority_expires_at");
    if (expires <= now || expires - now > 5000) throw new ActionBlocked("EXPIRED");
    const scope = this.#scope(envelope);
    if (envelope.connector_fence < (this.#fences.get(scope) ?? 0)) throw new ActionBlocked("CONTEXT_STALE");
    const operation = mapMockOperation(envelope, authority.source_record, now);
    // No await separates the current-authority check from the simulated socket submission.
    // Real providers still have residual network races; SQL handles uncertain outcomes.
    this.#fences.set(scope, envelope.connector_fence);
    this.#operations.push(operation);
    const result: ActionResult = Object.freeze({ schema_version: 1, simulation: true, action_id: envelope.action_id,
      payload_hash: envelope.payload_hash, connector_fence: envelope.connector_fence, status: this.#outcome,
      provider_message_id: this.#outcome === "accepted" ? `mock-${randomUUID()}` : null,
      submitted_at: new Date(now).toISOString() });
    this.#attempts.set(envelope.action_id, { binding, result });
    return result;
  }
  async submit(value: unknown): Promise<ActionResult> {
    const envelope = parseActionEnvelope(value);
    const binding = canonicalJson(envelope);
    const prior = this.#attempts.get(envelope.action_id);
    const pending = this.#inflight.get(envelope.action_id);
    if (prior || pending) {
      if ((prior?.binding ?? pending?.binding) !== binding) throw new ActionBlocked("ACTION_REUSED");
      return prior?.result ?? pending!.promise;
    }
    if (this.#attempts.size + this.#inflight.size >= 10_000) throw new ActionBlocked("QUOTA_HELD");
    const promise = Promise.resolve().then(() => this.#submit(envelope, binding));
    this.#inflight.set(envelope.action_id, { binding, promise });
    try { return await promise; } finally { this.#inflight.delete(envelope.action_id); }
  }
  reconcile(actionId: string): ActionResult | null {
    // A missing entry after restart means unknown, never permission to submit again.
    return this.#attempts.get(stringValue(actionId, "action_id", 255))?.result ?? null;
  }
  get submittedOperations(): readonly MockSocketOperation[] { return Object.freeze([...this.#operations]); }
}
