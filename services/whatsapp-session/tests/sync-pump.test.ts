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
