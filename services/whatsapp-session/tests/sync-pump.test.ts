import test from "node:test";
import assert from "node:assert/strict";
import { SyncPump } from "../src/sync.ts";
import { convertMessage, describe } from "../src/convert.ts";
import type { Identity, SyncBatch, SyncMessage } from "../src/protocol.ts";

const identity: Identity = { schema_version: 1, workspace_id: "w", connector_id: "c", connector_fence: 1, account_id: "15550000001@s.whatsapp.net" };
const peer = "15550000002@s.whatsapp.net";
function row(id: string, text = "Synthetic text"): SyncMessage {
  return { id, chat_jid: peer, from_me: false, timestamp: new Date().toISOString(), event: "created", kind: "text", text };
}
function pump(status: (batch: SyncBatch) => number, refused: number[] = []) {
  const posted: SyncBatch[] = [];
  const instance = new SyncPump({ identity: () => identity, sleep: async () => {}, maxBatchBytes: 20_000,
    onRefused: code => refused.push(code),
    post: async batch => { const code = status(batch); if (code === 200) posted.push(batch); return { status: code, body: null }; } });
  return { instance, posted };
}

test("live messages jump ahead of history that is still waiting", async () => {
  let release!: () => void; const gate = new Promise<void>(resolve => { release = resolve; });
  const posted: string[] = [];
  const instance = new SyncPump({ identity: () => identity, sleep: async () => {}, maxBatchMessages: 1,
    post: async batch => { if (!posted.length) await gate; posted.push(batch.messages[0]!.id); return { status: 200, body: null }; } });
  instance.push("history", [row("h1"), row("h2")]);
  instance.push("live", [row("l1")]);
  release(); await instance.drain();
  assert.deepEqual(posted, ["h1", "l1", "h2"]);
});

test("batches respect the UTF-8 byte budget for Indic text and emoji", async () => {
  const { instance, posted } = pump(() => 200);
  instance.push("history", Array.from({ length: 12 }, (_, index) => row(`hi-${index}`, "नमस्ते 🙏 ".repeat(150))));
  await instance.drain();
  assert.ok(posted.length > 1);
  for (const batch of posted) assert.ok(Buffer.byteLength(JSON.stringify(batch.messages)) <= 20_000 + 220 * batch.messages.length);
  assert.equal(posted.flatMap(batch => batch.messages).length, 12);
});

test("a paused workspace or refused lease keeps the batch, in order, for a later retry", async () => {
  const refused: number[] = []; let answers = [423, 409, 200];
  const { instance, posted } = pump(() => answers.shift() ?? 200, refused);
  instance.push("history", [row("a"), row("b")]);
  await instance.drain();
  assert.deepEqual(refused, [409]);
  assert.deepEqual(posted.map(batch => batch.messages.map(item => item.id)), [["a", "b"]]);
  answers = [];
});

test("chat metadata arriving while a batch is in flight merges per contact", async () => {
  let release!: () => void; const gate = new Promise<void>(resolve => { release = resolve; });
  const posted: SyncBatch[] = [];
  const instance = new SyncPump({ identity: () => identity, sleep: async () => {},
    post: async batch => { if (!posted.length) { posted.push(batch); await gate; } else posted.push(batch); return { status: 200, body: null }; } });
  instance.push("live", [row("first")]);
  instance.upsertChats([{ jid: peer, title: "Push", title_source: "push" }]);
  instance.upsertChats([{ jid: peer, unread_count: 4 }]);
  release(); await instance.drain();
  assert.deepEqual(posted[1]?.chats, [{ jid: peer, title: "Push", title_source: "push", unread_count: 4 }]);
});

test("a live chat update never stops a pending history chat from being created", async () => {
  let release!: () => void; const gate = new Promise<void>(resolve => { release = resolve; });
  const posted: SyncBatch[] = []; const other = "15550000003@s.whatsapp.net";
  const instance = new SyncPump({ identity: () => identity, sleep: async () => {},
    post: async batch => { if (!posted.length) { posted.push(batch); await gate; } else posted.push(batch); return { status: 200, body: null }; } });
  instance.push("live", [row("first")]);
  instance.upsertChats([{ jid: peer, title: "Asha", title_source: "chat", last_activity_at: "2026-10-01T10:00:00.000Z" }]);
  instance.upsertChats([{ jid: peer, unread_count: 0, contact_only: true }, { jid: other, contact_only: true }]);
  instance.upsertChats([{ jid: other, title: "Ravi", title_source: "contact", contact_only: true }]);
  release(); await instance.drain();
  assert.deepEqual(posted[1]?.chats, [
    { jid: peer, title: "Asha", title_source: "chat", last_activity_at: "2026-10-01T10:00:00.000Z", unread_count: 0 },
    { jid: other, title: "Ravi", title_source: "contact", contact_only: true }]);
});

test("calls and newer message types stay visible; actions on other messages never become messages", () => {
  const at = Math.floor(Date.now() / 1000);
  const convert = (content: object) => convertMessage({ key: { id: "m", remoteJid: peer, fromMe: false }, messageTimestamp: at,
    message: content } as never, jid => ({ jid }));
  assert.deepEqual(convert({ callLogMesssage: { isVideo: true, callOutcome: 1 } })?.text, "📹 Missed video call");
  assert.deepEqual(convert({ callLogMesssage: { callOutcome: 0, durationSecs: 151 } })?.text, "📞 Voice call (2:31)");
  assert.deepEqual(convert({ callLogMesssage: { callOutcome: 0, durationSecs: { toNumber: () => 62 } } })?.text, "📞 Voice call (1:02)");
  assert.deepEqual(convert({ stickerPackMessage: { name: "Diwali" } })?.text, "Sticker pack Diwali");
  assert.deepEqual(convert({ placeholderMessage: { type: 0 } })?.text, "🔒 This message is only on your phone");
  assert.deepEqual(convert({ futureShinyMessage: { body: "x" } }), { id: "m", chat_jid: peer, from_me: false,
    timestamp: new Date(at * 1000).toISOString(), event: "created", kind: "other", text: "💬 Message — open WhatsApp on your phone to see it" });
  for (const content of [{ albumMessage: { expectedImageCount: 3 } }, { pinInChatMessage: {} }, { keepInChatMessage: {} },
    { reactionMessage: { text: "👍" } }, { messageContextInfo: {} }]) assert.equal(convert(content), null);
});

test("message types become readable text without downloading media", () => {
  assert.deepEqual(describe({ documentMessage: { fileName: "Invoice.pdf", caption: "for March" } }), { kind: "media", text: "📄 Invoice.pdf for March" });
  assert.deepEqual(describe({ locationMessage: { name: "Cafe", address: "Bandra" } }), { kind: "location", text: "📍 Cafe, Bandra" });
  assert.deepEqual(describe({ pollCreationMessageV3: { name: "Dinner?", options: [{ optionName: "Yes" }, { optionName: "No" }] } }),
    { kind: "poll", text: "📊 Dinner? — Yes / No" });
  assert.deepEqual(describe({ imageMessage: { caption: "secret" } }, true), { kind: "media", text: "📷 View once Photo" });
  assert.equal(describe({ reactionMessage: { text: "👍" } }), null);
  const call = convertMessage({ key: { id: "call", remoteJid: peer, fromMe: false }, messageTimestamp: Math.floor(Date.now() / 1000),
    messageStubType: 40 } as never, jid => ({ jid }));
  assert.equal(call?.text, "📞 Missed voice call");
  const reply = convertMessage({ key: { id: "reply", remoteJid: peer, fromMe: true }, messageTimestamp: Math.floor(Date.now() / 1000),
    message: { extendedTextMessage: { text: "yes", contextInfo: { stanzaId: "original" } } } } as never, jid => ({ jid }));
  assert.deepEqual([reply?.text, reply?.reply_to, reply?.from_me], ["yes", "original", true]);
});
