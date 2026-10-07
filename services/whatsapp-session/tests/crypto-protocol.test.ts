import test from "node:test";
import assert from "node:assert/strict";
import { randomBytes } from "node:crypto";
import { BufferJSON, initAuthCreds, generateWAMessageContent } from "@whiskeysockets/baileys";
import { encryptAuth, decryptAuth, encryptionKey } from "../src/crypto.ts";
import { checkAuthority, parseIdentity } from "../src/protocol.ts";
import { authorityClient } from "../src/authority.ts";
import type { Identity, SendEnvelope } from "../src/protocol.ts";
const identity: Identity = { schema_version: 1, workspace_id: "owner_workspace", connector_id: "connector", connector_fence: 1, account_id: null };
const binding = { workspace_id: identity.workspace_id, connector_id: identity.connector_id, key_type: "session", key_id: "signal_key" };
test("actual SDK text encoding never resolves server URL previews when explicitly disabled", async () => {
  let fetched = 0;
  const value = await generateWAMessageContent({ text: "http://127.0.0.1/internal/private", linkPreview: null }, {
    getUrlInfo: async () => { fetched += 1; throw new Error("must not fetch"); },
    upload: async () => { throw new Error("must not upload"); },
  });
  assert.equal(fetched, 0); assert.equal(value.extendedTextMessage?.text, "http://127.0.0.1/internal/private");
});
test("actual pinned SDK initializes Signal keys offline with no socket construction", () => {
  const creds = initAuthCreds(); assert.equal(creds.noiseKey.private.length, 32); assert.equal(creds.registered, false);
  const json = JSON.stringify(creds, BufferJSON.replacer);
  const key = randomBytes(32); const cipher = encryptAuth(key, binding, json);
  const restored = JSON.parse(decryptAuth(key, binding, cipher), BufferJSON.reviver) as typeof creds;
  assert.deepEqual(restored.signedIdentityKey.private, creds.signedIdentityKey.private);
  assert.equal(cipher.includes(creds.advSecretKey), false);
});
test("AES-GCM uses unique IVs and authenticates the owner and key row", () => {
  const key = randomBytes(32); const one = encryptAuth(key, binding, "synthetic auth value");
  assert.notEqual(one, encryptAuth(key, binding, "synthetic auth value"));
  for (const changed of [{ ...binding, workspace_id: "other_owner" }, { ...binding, connector_id: "other" },
    { ...binding, key_type: "creds" }, { ...binding, key_id: "another" }]) assert.throws(() => decryptAuth(key, changed, one));
  const value = JSON.parse(one) as { body: string }; value.body = Buffer.from("tampered").toString("base64");
  assert.throws(() => decryptAuth(key, binding, JSON.stringify(value)));
  assert.throws(() => decryptAuth(randomBytes(32), binding, one));
});
test("encryption keys and envelopes reject malformed or unbounded inputs", () => {
  const key = randomBytes(32); assert.deepEqual(encryptionKey(key.toString("base64")), key);
  for (const value of ["", "not-secret", randomBytes(31).toString("base64"), `${key.toString("base64")}\n`]) assert.throws(() => encryptionKey(value));
  assert.throws(() => encryptAuth(key, binding, "a".repeat(1_048_577)));
  assert.throws(() => decryptAuth(key, binding, '{"v":2}'));
});
test("private identity parsing rejects groups, extra fields, and unsafe fences", () => {
  assert.deepEqual(parseIdentity(identity), identity);
  for (const extra of [{ ...identity, recipient_id: "123456789@g.us" }, { ...identity, account_id: "123456@g.us" },
    { ...identity, connector_fence: 0 }, { ...identity, connector_fence: 1.1 }, { ...identity, workspace_id: "../owner" }]) {
    assert.throws(() => parseIdentity(extra));
  }
});
test("SQL authority must match every identity and a bounded live deadline", () => {
  const now = Date.now(); const row = { ...identity, allowed: true, reason_code: "ALLOWED", grants: [], authority_expires_at: new Date(now + 4000).toISOString() };
  assert.equal(checkAuthority(row, identity, now).allowed, true);
  for (const extra of [{ ...row, account_id: "123456@s.whatsapp.net" }, { ...row, connector_fence: 2 },
    { ...row, workspace_id: "other" }, { ...row, allowed: false }, { ...row, authority_expires_at: new Date(now - 1).toISOString() },
    { ...row, authority_expires_at: new Date(now + 5001).toISOString() }, { ...row, grants: Array(251).fill({}) }]) {
    assert.throws(() => checkAuthority(extra, identity, now));
  }
});
test("authority transport fixes destinations, rejects redirects and bounds streamed replies", async () => {
  for (const origin of ["http://example.com", "https://user:pass@example.com", "https://example.com/evil", "https://example.com?token=secret"]) {
    assert.throws(() => authorityClient({ origin, token: "internal" }));
  }
  let target = "", redirect = "";
  const authority = authorityClient({ origin: "https://api.example.test", token: "internal", fetcher: async (url, init) => {
    target = String(url); redirect = init?.redirect ?? "";
    return Response.json({ ...identity, allowed: true, reason_code: "ALLOWED", grants: [], authority_expires_at: new Date(Date.now() + 4000).toISOString() });
  } });
  await authority.authorize("start", identity);
  assert.equal(target, "https://api.example.test/internal/whatsapp-session-authority"); assert.equal(redirect, "error");
  const oversized = authorityClient({ origin: "https://api.example.test", token: "internal", fetcher: async () => new Response("x".repeat(131_073)) });
  await assert.rejects(oversized.authorize("start", identity));
});
test("send authority projects only identity fields at the top level of its exact Python DTO", async () => {
  const envelope: SendEnvelope = { ...identity, account_id: "15550000001@s.whatsapp.net", conversation_id: "conversation",
    recipient_id: "15550000002@s.whatsapp.net", draft_id: "draft", attempt_id: "attempt", payload_hash: "a".repeat(64), text: "Synthetic reply" };
  const authority = authorityClient({ origin: "http://127.0.0.1:8000", token: "internal", fetcher: async (_url, init) => {
    const request = JSON.parse(String(init?.body)) as Record<string, unknown>;
    assert.deepEqual(Object.keys(request).sort(), ["schema_version", "workspace_id", "connector_id", "connector_fence", "account_id", "operation", "send"].sort());
    assert.deepEqual(request.send, envelope);
    return Response.json({ ...identity, account_id: envelope.account_id, allowed: true, reason_code: "ALLOWED", grants: [],
      authority_expires_at: new Date(Date.now() + 4000).toISOString() });
  } });
  assert.equal((await authority.authorize("send", envelope, envelope)).allowed, true);
});
