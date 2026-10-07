import type { AuthorKind, CanonicalEvent, MessageDirection, MessageEventType, MessageOrigin } from "./contract.ts";

export function record(value: unknown, name: string): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    throw new Error(`${name} must be an object`);
  }
  const prototype: unknown = Object.getPrototypeOf(value);
  if (prototype !== Object.prototype && prototype !== null) {
    throw new Error(`${name} must be a plain object`);
  }
  return value as Record<string, unknown>;
}
export function exactKeys(value: Record<string, unknown>, keys: readonly string[]): void {
  if (Object.keys(value).some((key) => !keys.includes(key))) throw new Error("Unknown field in contract");
}
export function stringValue(value: unknown, name: string, max = 200): string {
  if (typeof value !== "string" || value.length < 1 || value.length > max) {
    throw new Error(`${name} must be a nonempty string of at most ${max} characters`);
  }
  return value;
}
export function integerValue(value: unknown, name: string, min = 0): number {
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < min) {
    throw new Error(`${name} must be a safe integer >= ${min}`);
  }
  return value;
}
export function enumValue<T extends string>(value: unknown, name: string, allowed: readonly T[]): T {
  if (typeof value !== "string" || !allowed.includes(value as T)) throw new Error(`Invalid ${name}`);
  return value as T;
}
export function instantMilliseconds(value: unknown, name: string): number {
  const text = stringValue(value, name, 40);
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})$/.exec(text);
  if (!match) throw new Error(`${name} requires an ISO timestamp with an explicit offset`);
  const [year, month, day, hour, minute, second] = match.slice(1, 7).map(Number);
  if (year === undefined || month === undefined || day === undefined || hour === undefined ||
      minute === undefined || second === undefined || year < 1 || hour > 23 || minute > 59 || second > 59) {
    throw new Error(`Invalid ${name}`);
  }
  const calendar = new Date(0);
  calendar.setUTCFullYear(year, month - 1, day);
  if (calendar.getUTCFullYear() !== year || calendar.getUTCMonth() !== month - 1 || calendar.getUTCDate() !== day) {
    throw new Error(`Invalid calendar date in ${name}`);
  }
  const milliseconds = Date.parse(text);
  if (!Number.isFinite(milliseconds)) throw new Error(`Invalid ${name}`);
  return milliseconds;
}

/** Parsing establishes syntax only; a supplied workspace ID is never an authorization grant. */
export function parseCanonicalEvent(value: unknown): CanonicalEvent {
  const event = { ...record(value, "event") };
  for (const [alternate, name] of [["connection_id", "connector_id"], ["authorship", "author_kind"],
    ["reply_to_source_id", "reply_to"]] as const) {
    if (alternate in event) {
      if (name in event && event[name] !== event[alternate]) throw new Error(`Conflicting ${name} aliases`);
      event[name] = event[alternate];
      delete event[alternate];
    }
  }
  for (const field of ["sender_identity", "participant_identity"] as const) {
    if (typeof event[field] === "string") event[field] = { id: event[field] };
  }
  const sender = event.sender_identity == null ? {} : record(event.sender_identity, "sender_identity");
  if (event.sender_id === undefined && sender.id) event.sender_id = sender.id;
  if (sender.id && sender.id !== event.sender_id) throw new Error("Sender identity does not match sender_id");
  if (typeof event.origin === "string") {
    event.origin = event.origin.toLowerCase() === "backfill" ? "history" : event.origin.toLowerCase();
  }
  if (typeof event.author_kind === "string") {
    const author = event.author_kind.toLowerCase();
    event.author_kind = author === "other" ? "contact_human" : author === "unknown_owner" ? "unknown_owner_outgoing" : author;
  }
  exactKeys(event, ["schema_version", "event_id", "workspace_id", "connector_id", "conversation_id",
    "provider", "account_id", "provider_message_id", "sender_id", "direction", "origin", "event_type",
    "author_kind", "provider_timestamp", "received_at", "content", "reply_to", "source_revision",
    "sender_identity", "participant_identity", "provider_record_ref", "native_record", "reaction", "expires_at",
    "deleted_at", "owner_addressed"]);
  if ((event.schema_version === undefined ? 1 : event.schema_version) !== 1) throw new Error("Unsupported schema_version");
  const content = record(event.content === undefined ? { type: "text", text: "" } : event.content, "content");
  exactKeys(content, ["type", "text"]);
  const text = content.text === undefined ? "" : content.text;
  if ((content.type === undefined ? "text" : content.type) !== "text" || typeof text !== "string" ||
      text.length > 20_000) throw new Error("Invalid text content");
  instantMilliseconds(event.provider_timestamp, "provider_timestamp");
  const optional: Record<string, string | null> = {};
  for (const key of ["workspace_id", "provider", "account_id", "received_at", "reply_to", "provider_record_ref",
    "expires_at", "deleted_at"] as const) {
    if (event[key] !== undefined) {
      optional[key] = event[key] === null ? null : stringValue(event[key], key, key === "reply_to" ? 180 : 200);
      if (["received_at", "expires_at", "deleted_at"].includes(key) && event[key] !== null) instantMilliseconds(event[key], key);
    }
  }
  const author = event.author_kind == null ? event.author_kind : enumValue<AuthorKind>(event.author_kind,
    "author_kind", ["contact_human", "human_owner", "assistant", "other_authorized_operator", "unknown_owner_outgoing"]);
  const eventType = enumValue<MessageEventType>(event.event_type === undefined ? "message.created" : event.event_type,
    "event_type", ["message.created", "message.edited", "message.deleted", "reaction.added", "reaction.removed"]);
  const metadata: Record<string, unknown> = {};
  for (const field of ["sender_identity", "participant_identity"] as const) {
    if (event[field] !== undefined) metadata[field] = Object.freeze({ ...record(event[field], field) });
  }
  if (event.owner_addressed !== undefined) {
    if (typeof event.owner_addressed !== "boolean") throw new Error("owner_addressed must be boolean");
    metadata.owner_addressed = event.owner_addressed;
  }
  if (event.native_record !== undefined) {
    if (event.native_record === null) metadata.native_record = null;
    else {
      const original = record(event.native_record, "native_record");
      exactKeys(original, ["provider_record_ref", "account_id", "key", "payload", "view_once"]);
      const key = record(original.key, "native_key");
      exactKeys(key, ["id", "remoteJid", "fromMe", "participant"]);
      if (typeof key.fromMe !== "boolean" || (original.view_once !== undefined && typeof original.view_once !== "boolean")) {
        throw new Error("Native flags must be boolean");
      }
      const ref = stringValue(original.provider_record_ref, "provider_record_ref", 180);
      if (event.provider_record_ref != null && event.provider_record_ref !== ref) throw new Error("Native provider reference mismatch");
      const payload = record(original.payload, "native_payload");
      if (Buffer.byteLength(JSON.stringify(payload), "utf8") > 131_072) throw new Error("Native record exceeds 128 KiB");
      metadata.native_record = Object.freeze({ provider_record_ref: ref,
        account_id: stringValue(original.account_id, "account_id", 120),
        key: Object.freeze({ id: stringValue(key.id, "native_key.id", 180),
          remoteJid: stringValue(key.remoteJid, "native_key.remoteJid", 160), fromMe: key.fromMe,
          ...(key.participant === undefined ? {} : { participant: key.participant === null ? null :
            stringValue(key.participant, "participant", 160) }) }),
        payload: Object.freeze({ ...payload }), view_once: original.view_once ?? false });
    }
  }
  if (event.reaction != null) {
    if (!eventType.startsWith("reaction.")) throw new Error("Reaction payload requires a reaction event");
    const reaction = record(event.reaction, "reaction");
    exactKeys(reaction, ["target_provider_message_id", "emoji", "action"]);
    const emoji = reaction.emoji === undefined ? "" : reaction.emoji;
    if (typeof emoji !== "string" || emoji.length > 32 || (emoji && (/[\p{L}\p{N}\s]/u.test(emoji) ||
      !Array.from(emoji).some((character) => character.codePointAt(0)! >= 0x2300)))) throw new Error("Invalid reaction emoji");
    metadata.reaction = Object.freeze({ target_provider_message_id:
      stringValue(reaction.target_provider_message_id, "target_provider_message_id", 180), emoji,
    action: enumValue(reaction.action === undefined ? "add" : reaction.action, "reaction.action", ["add", "remove"]) });
  } else {
    if (eventType.startsWith("reaction.")) throw new Error("Reaction event requires exact target and emoji operation");
    if (event.reaction === null) metadata.reaction = null;
  }
  return Object.freeze({
    schema_version: 1,
    event_id: stringValue(event.event_id, "event_id"),
    connector_id: stringValue(event.connector_id, "connector_id"),
    conversation_id: stringValue(event.conversation_id, "conversation_id"),
    provider_message_id: stringValue(event.provider_message_id, "provider_message_id", 180),
    sender_id: stringValue(event.sender_id, "sender_id", 160),
    direction: enumValue<MessageDirection>(event.direction, "direction", ["inbound", "outbound"]),
    origin: enumValue<MessageOrigin>(event.origin === undefined ? "unknown" : event.origin, "origin", ["live", "history", "replay", "unknown"]),
    event_type: eventType,
    provider_timestamp: String(event.provider_timestamp),
    content: Object.freeze({ type: "text", text }),
    source_revision: integerValue(event.source_revision === undefined ? 1 : event.source_revision, "source_revision", 1),
    ...optional,
    ...metadata,
    ...(author === undefined ? {} : { author_kind: author }),
  });
}
