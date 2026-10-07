import assert from "node:assert/strict";
import test from "node:test";
import { parseCanonicalEvent } from "../src/validation.ts";

const event = {
  schema_version: 1, event_id: "evt-synthetic", workspace_id: "ws-synthetic", connector_id: "conn-synthetic",
  conversation_id: "chat-synthetic", provider: "mock", account_id: "account-synthetic",
  provider_message_id: "message-synthetic", sender_id: "sender-synthetic", direction: "inbound", origin: "history",
  provider_timestamp: "2026-10-06T10:00:00+05:30", content: { type: "text", text: "Synthetic example" },
};

test("canonical event keeps Python directions, origins, defaults and nullable fields", () => {
  const parsed = parseCanonicalEvent({ ...event, received_at: null, reply_to: null, author_kind: null });
  assert.equal(parsed.schema_version, 1);
  assert.equal(parsed.direction, "inbound");
  assert.equal(parsed.origin, "history");
  assert.equal(parsed.event_type, "message.created");
  assert.equal(parsed.source_revision, 1);
  assert.equal(parsed.reply_to, null);
  for (const origin of ["live", "history", "replay", "unknown"]) {
    assert.equal(parseCanonicalEvent({ ...event, origin }).origin, origin);
  }
  assert.equal(parseCanonicalEvent({ ...event, direction: "outbound", author_kind: "human_owner" }).direction, "outbound");
});

test("schema validation does not silently accept unsupported enums or unknown fields", () => {
  for (const invalid of [
    { ...event, schema_version: 2 }, { ...event, direction: "incoming" }, { ...event, origin: "backlog" },
    { ...event, event_type: "call" }, { ...event, source_revision: 0 }, { ...event, source_revision: 1.5 },
    { ...event, owner_authorized: true }, { ...event, content: { type: "image", text: "" } },
    { ...event, content: { type: "text", text: "x".repeat(20_001) } },
    { ...event, content: { type: "text", text: "hello", instruction: "send" } },
    { ...event, sender_id: "" }, { ...event, author_kind: "model_asserted_owner" },
    { ...event, schema_version: null }, { ...event, source_revision: null }, { ...event, event_type: null },
    { ...event, content: null }, { ...event, content: { type: "text", text: null } },
  ]) assert.throws(() => parseCanonicalEvent(invalid));
});

test("timestamps require a valid date and explicit zone", () => {
  for (const provider_timestamp of ["2026-10-06T10:00:00", "2026-02-30T10:00:00Z", "2026-10-06T25:00:00Z",
    "2026-10-06T10:00:60Z", "not a date", "2026-10-06T10:00:00+25:30"]) {
    assert.throws(() => parseCanonicalEvent({ ...event, provider_timestamp }));
  }
  assert.throws(() => parseCanonicalEvent({ ...event, received_at: "2026-10-06T10:00:00" }));
});

test("handoff aliases normalize trusted origins and identities without name merging", () => {
  const { connector_id: _connector, sender_id: _sender, ...base } = event;
  const parsed = parseCanonicalEvent({ ...base, connection_id: "conn-synthetic", origin: "BACKFILL",
    authorship: "HUMAN_OWNER", sender_identity: "owner-stable-provider-id", reply_to_source_id: "source-id" });
  assert.equal(parsed.connector_id, "conn-synthetic");
  assert.equal(parsed.origin, "history");
  assert.equal(parsed.author_kind, "human_owner");
  assert.equal(parsed.sender_id, "owner-stable-provider-id");
  assert.equal(parsed.reply_to, "source-id");
  assert.throws(() => parseCanonicalEvent({ ...event, connection_id: "another-connector" }), /Conflicting/);
  assert.throws(() => parseCanonicalEvent({ ...event, sender_identity: { id: "another-sender" } }), /does not match/);
  assert.equal(parseCanonicalEvent({ ...event, origin: "LIVE", authorship: "OTHER" }).author_kind, "contact_human");
});

test("canonical native observations preserve exact original key and expiry as syntax, never permission", () => {
  const native = { provider_record_ref: "provider-record-synthetic", account_id: "account-synthetic",
    key: { id: event.provider_message_id, remoteJid: "15550001234@s.whatsapp.net", fromMe: false },
    payload: { key: { id: event.provider_message_id, remoteJid: "15550001234@s.whatsapp.net", fromMe: false },
      message: { conversation: "Synthetic original" } } };
  const parsed = parseCanonicalEvent({ ...event, native_record: native, provider_record_ref: native.provider_record_ref,
    owner_addressed: true, expires_at: "2026-10-07T10:00:00Z", participant_identity: { id: "participant-jid" } });
  assert.equal(parsed.native_record?.key.id, event.provider_message_id);
  assert.equal(parsed.native_record?.view_once, false);
  assert.equal(parsed.owner_addressed, true);
  assert.equal(parsed.expires_at, "2026-10-07T10:00:00Z");
  for (const bad of [ { ...event, native_record: { ...native, key: { ...native.key, fromMe: "false" } } },
    { ...event, native_record: native, provider_record_ref: "wrong-original" },
    { ...event, owner_addressed: "model-assertion" }, { ...event, deleted_at: "unqualified-date" },
    { ...event, native_record: { ...native, key: { ...native.key, made_up_participant: "owner" } } } ]) {
    assert.throws(() => parseCanonicalEvent(bad));
  }
});

test("reaction events require exact target, paired type, valid emoji and bounded operation", () => {
  const reaction = { target_provider_message_id: event.provider_message_id, emoji: "🙏", action: "add" };
  const parsed = parseCanonicalEvent({ ...event, event_type: "reaction.added", reaction, authorship: "ASSISTANT" });
  assert.equal(parsed.reaction?.target_provider_message_id, event.provider_message_id);
  assert.equal(parsed.author_kind, "assistant");
  const removed = parseCanonicalEvent({ ...event, event_type: "reaction.removed", reaction: { ...reaction, emoji: "", action: "remove" } });
  assert.equal(removed.reaction?.action, "remove");
  for (const bad of [ { ...event, reaction }, { ...event, event_type: "reaction.added" },
    { ...event, event_type: "reaction.added", reaction: { ...reaction, emoji: "approve payment" } },
    { ...event, event_type: "reaction.added", reaction: { ...reaction, target_provider_message_id: "" } },
    { ...event, event_type: "reaction.added", reaction: { ...reaction, action: "send" } } ]) {
    assert.throws(() => parseCanonicalEvent(bad));
  }
});
