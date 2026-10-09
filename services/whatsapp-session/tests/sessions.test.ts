import test from "node:test";
import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { createHash } from "node:crypto";
import { setTimeout as wait } from "node:timers/promises";
import { initAuthCreds } from "@whiskeysockets/baileys";
import type { WASocket, UserFacingSocketConfig } from "@whiskeysockets/baileys";
import { Sessions } from "../src/sessions.ts";
import { Blocked } from "../src/protocol.ts";
import type { Authority, Identity, Grant, SendEnvelope, SessionEvent, Operation } from "../src/protocol.ts";
const initial: Identity = { schema_version: 1, workspace_id: "synthetic_owner", connector_id: "synthetic_connector", connector_fence: 1, account_id: null };
const account = "15550000001@s.whatsapp.net", peer = "15550000002@s.whatsapp.net";
const connected: Identity = { ...initial, account_id: account };
function fixture(options: { enabled?: boolean; clock?: () => number; max?: number } = {}) {
  const events: SessionEvent[] = [], configs: UserFacingSocketConfig[] = [], emitter = new EventEmitter();
  const calls: string[] = []; let grants: Grant[] = [], denied: Operation | null = null;
  let sendMode: "accept" | "throw" | "mismatch" = "accept", logoutMode: "ok" | "throw" | "hang" = "ok";
  let ended = 0, clears = 0, lost = () => {};
  let rejectEvent: ((event: SessionEvent) => boolean) | null = null;
  const socket = { ev: emitter, user: { id: "15550000001:4@s.whatsapp.net" },
    async sendMessage(_jid: string, _body: unknown, args: { messageId: string }) {
      calls.push("socket"); if (sendMode === "throw") throw new Error("uncertain network");
      return { key: { id: sendMode === "mismatch" ? "different" : args.messageId } };
    }, async requestPairingCode(phone: string) { calls.push(`pair:${phone}`); return "ABCD2345"; },
    async logout() { calls.push("logout"); if (logoutMode === "throw") throw new Error("remote unlink unavailable");
      if (logoutMode === "hang") await new Promise<void>(() => {}); }, async end() { calls.push("end"); ended += 1; },
  } as unknown as WASocket;
  const sessions = new Sessions({ enabled: options.enabled ?? true,
    ...(options.clock ? { clock: options.clock } : {}), ...(options.max ? { maxSessions: options.max } : {}),
    authority: { async authorize(operation, identity) {
      calls.push(`authority:${operation}`); if (denied === operation) throw new Blocked("AUTHORITY_DENIED");
      return { ...identity, allowed: true, reason_code: "ALLOWED", grants,
        authority_expires_at: new Date(Date.now() + 4000).toISOString() } as Authority;
    }, async event(event) { calls.push(`event:${String(event.data.status ?? event.data.state ?? event.event_type)}`);
      if (rejectEvent?.(event)) throw new Blocked("EVENT_DENIED"); events.push(event); } },
    stores: async (_identity, onLost) => { lost = onLost; return { state: { creds: initAuthCreds(), keys: { async get() { return {}; }, async set() {} } },
      async saveCreds() {}, async assertLive() {}, async clear() { clears += 1; }, async release() {} }; },
    socketFactory: config => { configs.push(config); return socket; },
  });
  return { sessions, events, configs, emitter, calls, lost: () => lost(),
    grant: (value: Grant[]) => { grants = value; }, deny: (value: Operation | null) => { denied = value; },
    mode: (value: typeof sendMode) => { sendMode = value; }, reject: (value: typeof rejectEvent) => { rejectEvent = value; },
    logoutMode: (value: typeof logoutMode) => { logoutMode = value; },
    ended: () => ended, clears: () => clears };
}
async function paired(f: ReturnType<typeof fixture>) {
  await f.sessions.start(initial); f.emitter.emit("connection.update", { connection: "open" }); await wait(20);
}
function message(fromMe = false) { return { key: { id: "synthetic_message", remoteJid: peer, fromMe },
  messageTimestamp: Math.floor(Date.now() / 1000), message: { conversation: "Synthetic test text" } }; }
function send(): SendEnvelope { const text = "Synthetic approved reply"; return { ...connected, account_id: account,
  conversation_id: "conversation", recipient_id: peer, draft_id: "draft", attempt_id: "attempt",
  payload_hash: createHash("sha256").update(text).digest("hex"), text }; }
test("feature-off and denied starts never instantiate a WhatsApp socket", async () => {
  const off = fixture({ enabled: false }); await assert.rejects(off.sessions.start(initial)); assert.equal(off.configs.length, 0);
  const denied = fixture(); denied.deny("start"); await assert.rejects(denied.sessions.start(initial)); assert.equal(denied.configs.length, 0);
});
test("owner QR expires, is never a callback payload, and clears after connection", async () => {
  let now = Date.now(); const f = fixture({ clock: () => now }); await f.sessions.start(initial);
  f.emitter.emit("connection.update", { qr: "synthetic-qr-no-account" }); await wait(10);
  const view = await f.sessions.status(initial); assert.equal(view.qr?.value, "synthetic-qr-no-account");
  assert.equal(f.events.some(row => JSON.stringify(row).includes("synthetic-qr")), false);
  f.deny("status"); await assert.rejects(f.sessions.status(initial)); f.deny(null);
  now += 45_001; assert.equal((await f.sessions.status(initial)).qr, undefined);
  f.emitter.emit("connection.update", { connection: "open" }); await wait(10);
  assert.equal((await f.sessions.status(connected)).qr, undefined); assert.equal((await f.sessions.status(connected)).state, "connected");
  await f.sessions.close();
});
test("one connector starts once, pilot admission is bounded, wrong owners cannot read it", async () => {
  const f = fixture({ max: 1 }); await f.sessions.start(initial); await f.sessions.start(initial); assert.equal(f.configs.length, 1);
  await assert.rejects(f.sessions.start({ ...initial, connector_id: "second" }));
  await assert.rejects(f.sessions.status({ ...initial, workspace_id: "other_owner" })); await f.sessions.close();
});
test("startup restoration is bounded, rechecks authority, and never replays a prior send", async () => {
  const old = fixture(); await paired(old); await old.sessions.send(send()); await old.sessions.close();
  const restored = fixture({ max: 1 });
  assert.deepEqual(await restored.sessions.restore([connected, { ...connected, connector_id: "outside_bound" }]),
    { restored: 1, rejected: 0 });
  assert.equal(restored.configs.length, 1); assert.equal(restored.calls.includes("authority:start"), true);
  assert.equal(restored.calls.includes("socket"), false); await restored.sessions.close();
  const denied = fixture(); denied.deny("start");
  assert.deepEqual(await denied.sessions.restore([connected]), { restored: 0, rejected: 1 });
  assert.equal(denied.configs.length, 0); await denied.sessions.close();
  const disabled = fixture({ enabled: false });
  assert.deepEqual(await disabled.sessions.restore([connected]), { restored: 0, rejected: 0 });
  assert.equal(disabled.configs.length, 0); await disabled.sessions.close();
});
test("SDK blind retries, full history and online phone notification suppression are disabled", async () => {
  const f = fixture(); await f.sessions.start(initial); const config = f.configs[0]!;
  assert.equal(config.maxMsgRetryCount, 0); assert.equal(config.enableRecentMessageCache, false);
  assert.equal(config.enableAutoSessionRecreation, false); assert.equal(await config.getMessage?.({ id: "any" }), undefined);
  assert.equal(config.syncFullHistory, false); assert.equal(config.markOnlineOnConnect, false);
  assert.equal(config.shouldIgnoreJid?.("15550000000@g.us"), true);
  await f.sessions.close();
});
test("an account is bound by Python before any text is ingested", async () => {
  const f = fixture(); f.reject(event => event.event_type === "connection"); await paired(f);
  f.emitter.emit("messages.upsert", { type: "notify", messages: [message()] }); await wait(10);
  assert.equal(f.events.filter(row => row.event_type === "message").length, 0);
  assert.equal((await f.sessions.status(initial)).state, "failed"); assert.equal(f.ended(), 1); await f.sessions.close();
});
test("first account binding uses the authorized null envelope and the observed account only in connection data", async () => {
  const f = fixture(); await paired(f);
  const binding = f.events.find(row => row.event_type === "connection" && row.data.state === "connected");
  assert.equal(binding?.account_id, null); assert.equal(binding?.data.account_id, account);
  assert.equal((await f.sessions.status(connected)).account_id, account); await f.sessions.close();
});
test("ungranted text and read-without-retain text never reaches persistence", async () => {
  const f = fixture(); await paired(f); f.emitter.emit("messages.upsert", { type: "notify", messages: [message()] }); await wait(10);
  f.grant([{ conversation_id: "conversation", provider_chat_id: peer, read: true, retain: false, send: false }]);
  f.emitter.emit("messages.upsert", { type: "notify", messages: [message()] }); await wait(10);
  assert.equal(f.events.filter(row => row.event_type === "message").length, 0); await f.sessions.close();
});
test("granted live, replay and history events retain their actual origin; fromMe is unreviewed", async () => {
  const f = fixture(); await paired(f); f.grant([{ conversation_id: "conversation", provider_chat_id: peer, read: true, retain: true, send: false }]);
  f.emitter.emit("messages.upsert", { type: "notify", messages: [message()] });
  f.emitter.emit("messages.upsert", { type: "append", messages: [message(true)] });
  f.emitter.emit("messaging-history.set", { chats: [], messages: [message()] }); await wait(20);
  const rows = f.events.filter(row => row.event_type === "message"); assert.equal(rows.length, 3);
  assert.deepEqual(rows.map(row => row.data.origin), ["live", "replay", "history"]);
  assert.equal(rows[1]?.data.author_kind, "unknown_owner_outgoing"); await f.sessions.close();
});
test("group, media, wrapped ephemeral and future-dated payloads are not ingested", async () => {
  const f = fixture(); await paired(f); f.grant([{ conversation_id: "conversation", provider_chat_id: peer, read: true, retain: true, send: false }]);
  f.emitter.emit("messages.upsert", { type: "notify", messages: [{ ...message(), key: { id: "group", remoteJid: "123456@g.us" } },
    { ...message(), message: { imageMessage: { caption: "ignore" } } }, { ...message(), message: { ephemeralMessage: { message: { conversation: "ignore" } } } },
    { ...message(), messageTimestamp: Math.floor(Date.now() / 1000) + 1000 }] }); await wait(10);
  assert.equal(f.events.filter(row => row.event_type === "message").length, 0); await f.sessions.close();
});
test("discovery stores at most 200 minimal contacts and verifies selected IDs", async () => {
  const f = fixture(); await paired(f); f.emitter.emit("contacts.upsert", Array.from({ length: 300 }, (_, i) => ({ id: `${15550000000 + i}@s.whatsapp.net`, name: "x".repeat(200) })));
  const rows = (await f.sessions.chats(connected)).chats; assert.equal(rows.length, 200);
  assert.equal(rows[0]?.title.length, 160); assert.deepEqual(Object.keys(rows[0]!).sort(), ["kind", "provider_chat_id", "title"]);
  assert.equal((await f.sessions.chats(connected, rows[0]!.provider_chat_id)).chats.length, 1);
  assert.equal((await f.sessions.chats(connected, "19999999999@s.whatsapp.net")).chats.length, 0); await f.sessions.close();
});
test("reviewed sends durably commit the provider ID then recheck authority before the socket", async () => {
  const f = fixture(); await paired(f); f.calls.length = 0; const result = await f.sessions.send(send());
  assert.equal(result.status, "accepted");
  const submit = f.calls.indexOf("event:submitting"), socket = f.calls.indexOf("socket");
  assert.ok(submit >= 0 && socket > submit); assert.ok(f.calls.slice(submit + 1, socket).includes("authority:send"));
  assert.equal(f.events.find(row => row.data.status === "submitting")?.data.provider_message_id, result.provider_message_id); await f.sessions.close();
});
test("duplicate concurrent send attempts invoke the socket once and reject a changed attempt", async () => {
  const f = fixture(); await paired(f); const envelope = send();
  const results = await Promise.all([f.sessions.send(envelope), f.sessions.send(envelope)]);
  assert.equal(f.calls.filter(row => row === "socket").length, 1); assert.equal(results[0]?.provider_message_id, results[1]?.provider_message_id);
  await assert.rejects(f.sessions.send({ ...envelope, recipient_id: "15550000003@s.whatsapp.net" })); await f.sessions.close();
});
test("a revoked final authority or failed durable claim prevents all socket submission", async () => {
  const revoked = fixture(); await paired(revoked); revoked.reject(event => { if (event.data.status === "submitting") revoked.deny("send"); return false; });
  await assert.rejects(revoked.sessions.send(send())); assert.equal(revoked.calls.includes("socket"), false); await revoked.sessions.close();
  const failed = fixture(); await paired(failed); failed.reject(event => event.data.status === "submitting");
  await assert.rejects(failed.sessions.send(send())); assert.equal(failed.calls.includes("socket"), false); await failed.sessions.close();
});
test("socket timeout or mismatched IDs remain uncertain with no automatic resend", async () => {
  for (const mode of ["throw", "mismatch"] as const) {
    const f = fixture(); await paired(f); f.mode(mode); const one = await f.sessions.send(send()); const two = await f.sessions.send(send());
    assert.equal(one.status, "uncertain"); assert.equal(two.status, "uncertain"); assert.equal(f.calls.filter(row => row === "socket").length, 1); await f.sessions.close();
  }
});
test("ownership loss closes the socket and blocks send; disconnect purges credentials and QR", async () => {
  const f = fixture(); await paired(f); f.lost(); await wait(10); assert.equal(f.ended(), 1); await assert.rejects(f.sessions.send(send())); await f.sessions.close();
  const other = fixture(); await paired(other); await other.sessions.disconnect(connected);
  assert.equal(other.clears(), 1); assert.equal((await other.sessions.status(connected)).state, "disconnected");
  assert.ok(other.calls.indexOf("logout") < other.calls.indexOf("end"));
  assert.equal((await other.sessions.chats(connected)).chats.length, 0); await other.sessions.close();
});
test("current revoked fence closes an older local session without stale deletion or callbacks", async () => {
  const f = fixture(); await paired(f); const before = f.events.length;
  const result = await f.sessions.disconnect({ ...connected, connector_fence: 2 });
  assert.equal(result.state, "disconnected"); assert.equal(result.provider_unlink_verified, false);
  assert.equal(f.clears(), 0); assert.equal(f.events.length, before); assert.equal(f.ended(), 1);
  assert.ok(f.calls.indexOf("logout") < f.calls.indexOf("end")); await f.sessions.close();
  const empty = fixture(); assert.equal((await empty.sessions.disconnect(connected)).state, "disconnected");
  assert.equal(empty.ended(), 0); await empty.sessions.close();
});
test("disconnect cannot close a newer fence or another workspace", async () => {
  const f = fixture(); await f.sessions.start({ ...initial, connector_fence: 2 });
  await assert.rejects(f.sessions.disconnect(connected));
  await assert.rejects(f.sessions.disconnect({ ...connected, workspace_id: "other_owner", connector_fence: 3 }));
  assert.equal(f.ended(), 0); assert.equal(f.calls.includes("logout"), false); await f.sessions.close();
});
test("failed and timed-out remote logout remain unverified and still end the local socket", async () => {
  for (const mode of ["throw", "hang"] as const) {
    const f = fixture(); await paired(f); f.logoutMode(mode);
    // Keep the test process alive while the production unlink deadline is unref'd.
    const keepAlive = setTimeout(() => {}, 3000);
    try {
      const result = await f.sessions.disconnect(connected);
      assert.equal(result.provider_unlink_verified, false); assert.equal(f.ended(), 1); assert.equal(f.clears(), 1);
      assert.ok(f.calls.indexOf("logout") < f.calls.indexOf("end"));
    } finally { clearTimeout(keepAlive); await f.sessions.close(); }
  }
});
test("idle SQL authority revocation stops the socket without inbound messages", async () => {
  const f = fixture(); await paired(f); f.deny("status");
  await wait(5100); await f.sessions.settlePendingEvents();
  assert.equal(f.ended(), 1); await assert.rejects(f.sessions.send(send())); await f.sessions.close();
});
test("a pairing phone requests one link code on the first QR, exposes it only to the owner view, and expires", async () => {
  let now = Date.now(); const f = fixture({ clock: () => now }); await f.sessions.start(initial, "15550000001");
  f.emitter.emit("connection.update", { qr: "synthetic-qr-one" }); f.emitter.emit("connection.update", { qr: "synthetic-qr-two" }); await wait(20);
  assert.deepEqual(f.calls.filter(call => call.startsWith("pair:")), ["pair:15550000001"]);
  assert.equal((await f.sessions.status(initial)).pairing?.code, "ABCD2345");
  assert.equal(f.events.some(row => JSON.stringify(row).includes("ABCD2345")), false);
  now += 160_001; assert.equal((await f.sessions.status(initial)).pairing, undefined);
  f.emitter.emit("connection.update", { connection: "open" }); await wait(20);
  assert.equal((await f.sessions.status(connected)).state, "connected"); await f.sessions.close();
});
test("an account other than the entered pairing number is unlinked and never bound", async () => {
  const f = fixture(); await f.sessions.start(initial, "919999999999");
  f.emitter.emit("connection.update", { qr: "synthetic-qr" }); await wait(10);
  f.emitter.emit("connection.update", { connection: "open" }); await wait(20);
  assert.equal(f.calls.includes("logout"), true); assert.equal(f.clears(), 1);
  assert.equal(f.events.some(row => row.data.state === "connected"), false);
  assert.equal(f.events.at(-1)?.data.state, "failed");
  await assert.rejects(f.sessions.status(connected)); await f.sessions.close();
});
test("an account binding refused by Python is unlinked from the phone", async () => {
  const f = fixture(); f.reject(event => event.data.state === "connected"); await f.sessions.start(initial);
  f.emitter.emit("connection.update", { connection: "open" }); await wait(20);
  assert.equal(f.calls.includes("logout"), true); assert.equal((await f.sessions.status(initial)).state, "failed");
  await f.sessions.close();
});
