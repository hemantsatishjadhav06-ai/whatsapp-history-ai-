/** Test-only simulation: no sockets, QR pairing, provider credentials or outward messages.
 * Ledgers are in memory and do not provide durability across process restarts. A production
 * implementation must use the SQL send ledger and account lease/fencing service.
 */
import { randomUUID } from "node:crypto";
import { CAPABILITY_NAMES } from "./contract.ts";
import type { AuthoritativeAuthorization, Capabilities, ConnectionStatus, ConnectorAdapter, ConnectorIdentity,
  CurrentAuthorizer, SendPermitClaims, SendResult, ValidatedAction } from "./contract.ts";
import { contentHash, signingKey, verifySendPermit } from "./permits.ts";
import { instantMilliseconds, integerValue, stringValue } from "./validation.ts";

export interface MockAdapterOptions {
  readonly identity: ConnectorIdentity;
  readonly permitKey: Uint8Array;
  readonly readCurrentAuthorization: CurrentAuthorizer;
  readonly allowGroupSend?: boolean;
  readonly submissionOutcome?: "accepted" | "uncertain";
  readonly now?: () => number;
}
interface RecordedAttempt {
  readonly binding: string;
  readonly providerMessageId: string;
  result: SendResult;
}
const SNAPSHOT_FIELDS = ["workspace_id", "connector_id", "conversation_id", "account_id", "recipient_id",
  "conversation_kind", "content_hash", "conversation_revision", "control_epoch", "permission_version",
  "pause_generation", "connector_fence"] as const;

export class MockConnectorAdapter implements ConnectorAdapter {
  readonly #identity: Readonly<ConnectorIdentity>;
  readonly #permitKey: Buffer;
  readonly #authorizer: CurrentAuthorizer;
  readonly #capabilities: Capabilities;
  readonly #clock: () => number;
  readonly #outcome: "accepted" | "uncertain";
  readonly #attempts = new Map<string, RecordedAttempt>();
  readonly #inflight = new Map<string, { binding: string; promise: Promise<SendResult> }>();
  #state: "connected" | "disconnected" = "disconnected";
  #fence = 0;

  constructor(options: MockAdapterOptions) {
    if (typeof options.readCurrentAuthorization !== "function") {
      throw new Error("A trusted authoritative authorization callback is mandatory");
    }
    this.#identity = Object.freeze({
      workspace_id: stringValue(options.identity.workspace_id, "workspace_id"),
      connector_id: stringValue(options.identity.connector_id, "connector_id"),
      account_id: stringValue(options.identity.account_id, "account_id"),
    });
    this.#permitKey = signingKey(options.permitKey);
    this.#authorizer = options.readCurrentAuthorization;
    this.#clock = options.now ?? Date.now;
    this.#outcome = options.submissionOutcome ?? "accepted";
    this.#capabilities = Object.freeze(Object.fromEntries(CAPABILITY_NAMES.map((name) => [name, Object.freeze({
      status: ["pairing_code", "qr_pairing", "phone_continuity", "owner_self_chat", "contact_save_local",
        "quoted_reply", "emoji_reaction", "native_forward",
        "contact_write_whatsapp", "contact_write_google", "contact_write_os"].includes(name) ||
        (name === "group_send" && !options.allowGroupSend) ? "unsupported" : "supported",
      adapter_version: "mock-text-v1", evidence_date: "2026-10-06", test_reference: "tests/mock-adapter.test.ts",
      reason: name === "pairing_code" || name === "qr_pairing" ? "Mock adapter cannot pair devices or authenticate WhatsApp." :
        name === "group_send" && !options.allowGroupSend ? "Group submission is disabled in this simulation." :
          "Synthetic simulation only; no actual provider capability has been demonstrated.",
    })])) as Capabilities);
  }
  status(): ConnectionStatus {
    return Object.freeze({ ...this.#identity, provider: "mock", simulation: true, state: this.#state,
      fencing_token: this.#fence, capabilities: this.#capabilities });
  }
  async connect(fencingToken: number): Promise<ConnectionStatus> {
    integerValue(fencingToken, "fencingToken", 1);
    if (this.#state === "connected" && fencingToken === this.#fence) return this.status();
    if (fencingToken <= this.#fence) throw new Error("A new connection requires a strictly increasing fencing token");
    this.#fence = fencingToken;
    this.#state = "connected";
    return this.status();
  }
  async disconnect(): Promise<ConnectionStatus> {
    this.#state = "disconnected";
    return this.status();
  }
  #now(): number {
    const now = this.#clock();
    if (!Number.isFinite(now)) throw new Error("Authoritative clock is unavailable");
    return now;
  }
  #validateLiveBinding(claims: SendPermitClaims): void {
    if (this.#state !== "connected") throw new Error("Connector is disconnected");
    if (claims.connector_fence !== this.#fence) throw new Error("Connector fence is stale");
    const now = this.#now();
    if (instantMilliseconds(claims.issued_at, "issued_at") > now ||
        instantMilliseconds(claims.expires_at, "expires_at") <= now) throw new Error("Permit is expired or not yet valid");
    if (claims.conversation_kind === "group" && this.#capabilities.group_send.status !== "supported") {
      throw new Error("group_send capability is unavailable");
    }
  }
  #validateAuthoritative(claims: SendPermitClaims, current: Readonly<AuthoritativeAuthorization> | null): void {
    if (current === null || current === undefined) throw new Error("Current authoritative state is unavailable");
    for (const field of SNAPSHOT_FIELDS) {
      if (current[field] !== claims[field]) throw new Error(`Current authoritative ${field} changed`);
    }
    if (current.connected !== true || current.paused !== false || current.send_allowed !== true ||
        current.recipient_opted_out !== false ||
        !["DRAFT_MODE", "AI_ACTIVE"].includes(current.control_state)) throw new Error("Current authorization blocks submission");
    if (claims.conversation_kind === "group" && current.group_send_allowed !== true) {
      throw new Error("Current group sending permission is unavailable");
    }
    const now = this.#now();
    if (instantMilliseconds(current.lease_expires_at, "lease_expires_at") <= now ||
        instantMilliseconds(current.approval_expires_at, "approval_expires_at") <= now) {
      throw new Error("Current lease or approval has expired");
    }
  }
  async #submit(claims: SendPermitClaims, binding: string): Promise<SendResult> {
    this.#validateLiveBinding(claims);
    // The callback is an injected trusted service dependency; action JSON cannot replace it.
    const current = await this.#authorizer(claims);
    // Recheck connection/fence/expiry after the await; no await separates final checks
    // from recording this simulation's acceptance. Real networks retain residual races.
    this.#validateLiveBinding(claims);
    this.#validateAuthoritative(claims, current);
    const providerMessageId = `mock-${randomUUID()}`;
    const result: SendResult = Object.freeze({ intent_id: claims.intent_id,
      provider_message_id: this.#outcome === "uncertain" ? null : providerMessageId,
      status: this.#outcome, simulation: true, submitted_at: new Date(this.#now()).toISOString() });
    this.#attempts.set(claims.intent_id, { binding, providerMessageId, result });
    return result;
  }
  async sendValidatedAction(action: ValidatedAction): Promise<SendResult> {
    const claims = verifySendPermit(action.permit, this.#permitKey);
    for (const field of ["workspace_id", "connector_id", "account_id"] as const) {
      if (claims[field] !== this.#identity[field]) throw new Error(`Permit ${field} does not match this connector`);
    }
    const text = stringValue(action.text, "text", 4096);
    if (!text.trim() || action.account_id !== claims.account_id || action.recipient_id !== claims.recipient_id ||
        contentHash(text) !== claims.content_hash) throw new Error("Exact account, recipient or content does not match permit");
    const binding = contentHash(JSON.stringify(claims));
    const existing = this.#attempts.get(claims.intent_id);
    const pending = this.#inflight.get(claims.intent_id);
    if (existing || pending) {
      if ((existing?.binding ?? pending?.binding) !== binding) throw new Error("Intent ID was reused with a different action");
      return existing?.result ?? pending!.promise;
    }
    // Reserve the intent before invoking even a synchronously reentrant reader.
    const promise = Promise.resolve().then(() => this.#submit(claims, binding));
    this.#inflight.set(claims.intent_id, { binding, promise });
    try {
      return await promise;
    } finally {
      this.#inflight.delete(claims.intent_id);
    }
  }
  async reconcile(intentId: string): Promise<SendResult | null> {
    const attempt = this.#attempts.get(stringValue(intentId, "intent_id"));
    if (!attempt) return null; // Unknown outcome must never trigger a blind resend.
    if (attempt.result.status === "uncertain") {
      attempt.result = Object.freeze({ ...attempt.result, status: "accepted", provider_message_id: attempt.providerMessageId });
    }
    return attempt.result;
  }
  /** Test helper representing a correlated receipt; acceptance alone never means delivery. */
  simulateDeliveryReceipt(providerMessageId: string): SendResult {
    for (const attempt of this.#attempts.values()) {
      if (attempt.providerMessageId === providerMessageId) {
        attempt.result = Object.freeze({ ...attempt.result, status: "delivered", provider_message_id: providerMessageId });
        return attempt.result;
      }
    }
    throw new Error("No correlated simulated send attempt");
  }
  get acceptedSubmissionCount(): number {
    return this.#attempts.size;
  }
}
