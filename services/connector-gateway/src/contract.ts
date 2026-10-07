/** Future connector boundary. This package provides a simulation, not a live transport. */
export const CAPABILITY_NAMES = [
  "history_sync", "live_receive", "send_text", "group_read", "group_send",
  "owner_self_chat", "pairing_code", "message_edits", "deletions",
  "qr_pairing", "quoted_reply", "emoji_reaction", "native_forward", "delivery_receipts", "read_receipts",
  "phone_continuity", "human_outgoing", "human_reaction", "assistant_echo", "participant_identity",
  "message_expiry", "native_records", "contact_save_local", "contact_write_whatsapp", "contact_write_google",
  "contact_write_os",
] as const;
export type CapabilityName = (typeof CAPABILITY_NAMES)[number];
export type CapabilityStatus = "supported" | "unsupported" | "unknown" | "unavailable";
export interface Capability {
  readonly status: CapabilityStatus;
  readonly reason: string;
  readonly adapter_version: string;
  readonly evidence_date: string;
  readonly test_reference: string;
}
export type Capabilities = Readonly<Record<CapabilityName, Capability>>;
export type MessageDirection = "inbound" | "outbound";
export type MessageOrigin = "live" | "history" | "replay" | "unknown";
export type MessageEventType = "message.created" | "message.edited" | "message.deleted" | "reaction.added" | "reaction.removed";
export type AuthorKind = "contact_human" | "human_owner" | "assistant" |
  "other_authorized_operator" | "unknown_owner_outgoing";

/** Compatible with the Python canonical-event API; identities still require trusted lookup. */
export interface CanonicalEvent {
  readonly schema_version: 1;
  readonly event_id: string;
  readonly workspace_id?: string | null;
  readonly connector_id: string;
  readonly conversation_id: string;
  readonly provider?: string | null;
  readonly account_id?: string | null;
  readonly provider_message_id: string;
  readonly sender_id: string;
  readonly direction: MessageDirection;
  readonly origin: MessageOrigin;
  readonly event_type: MessageEventType;
  readonly author_kind?: AuthorKind | null;
  readonly provider_timestamp: string;
  readonly received_at?: string | null;
  readonly content: Readonly<{ type: "text"; text: string }>;
  readonly reply_to?: string | null;
  readonly source_revision: number;
  readonly sender_identity?: Readonly<Record<string, unknown>>;
  readonly participant_identity?: Readonly<Record<string, unknown>>;
  readonly provider_record_ref?: string | null;
  readonly native_record?: Readonly<{ provider_record_ref: string; account_id: string;
    key: Readonly<{ id: string; remoteJid: string; fromMe: boolean; participant?: string | null }>;
    payload: Readonly<Record<string, unknown>>; view_once: boolean }> | null;
  readonly reaction?: Readonly<{ target_provider_message_id: string; emoji: string; action: "add" | "remove" }> | null;
  readonly expires_at?: string | null;
  readonly deleted_at?: string | null;
  readonly owner_addressed?: boolean;
}

export interface ConnectorIdentity {
  readonly workspace_id: string;
  readonly connector_id: string;
  readonly account_id: string;
}
export interface ConnectionStatus extends ConnectorIdentity {
  readonly provider: "mock";
  readonly simulation: true;
  readonly state: "connected" | "disconnected";
  readonly fencing_token: number;
  readonly capabilities: Capabilities;
}

/** Claims issued by an authoritative dispatcher, never by an LLM or public client. */
export interface SendPermitClaims extends ConnectorIdentity {
  readonly schema_version: 1;
  readonly intent_id: string;
  readonly conversation_id: string;
  readonly recipient_id: string;
  readonly conversation_kind: "contact" | "group";
  readonly content_hash: string;
  readonly conversation_revision: number;
  readonly control_epoch: number;
  readonly permission_version: number;
  readonly pause_generation: number;
  readonly connector_fence: number;
  readonly issued_at: string;
  readonly expires_at: string;
}
export interface SignedSendPermit {
  readonly claims: SendPermitClaims;
  readonly signature: string;
}
export interface ValidatedAction {
  readonly account_id: string;
  readonly recipient_id: string;
  readonly text: string;
  readonly permit: SignedSendPermit;
}

/** Must come from trusted current SQL/control state through an injected server dependency. */
export interface AuthoritativeAuthorization extends ConnectorIdentity {
  readonly conversation_id: string;
  readonly recipient_id: string;
  readonly conversation_kind: "contact" | "group";
  readonly conversation_revision: number;
  readonly control_epoch: number;
  readonly permission_version: number;
  readonly pause_generation: number;
  readonly connector_fence: number;
  readonly content_hash: string;
  readonly connected: boolean;
  readonly paused: boolean;
  readonly send_allowed: boolean;
  readonly group_send_allowed: boolean;
  readonly recipient_opted_out: boolean;
  readonly control_state: "DRAFT_MODE" | "AI_ACTIVE" | "AI_OFF" | "READ_ONLY" |
    "HUMAN_TAKEOVER" | "RECONNECT_REVIEW";
  readonly lease_expires_at: string;
  readonly approval_expires_at: string;
}
export type CurrentAuthorizer = (
  claims: Readonly<SendPermitClaims>,
) => Promise<Readonly<AuthoritativeAuthorization> | null>;

export type SendStatus = "accepted" | "delivered" | "failed" | "uncertain";
export interface SendResult {
  readonly intent_id: string;
  readonly provider_message_id: string | null;
  readonly status: SendStatus;
  readonly simulation: true;
  readonly submitted_at: string;
}
export interface ConnectorAdapter {
  connect(fencingToken: number): Promise<ConnectionStatus>;
  status(): ConnectionStatus;
  disconnect(): Promise<ConnectionStatus>;
  sendValidatedAction(action: ValidatedAction): Promise<SendResult>;
  reconcile(intentId: string): Promise<SendResult | null>;
}
