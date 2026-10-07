import assert from "node:assert/strict";
import { createServer } from "node:http";
import type { Server } from "node:http";
import test from "node:test";
import type { TestContext } from "node:test";
import { MockActionAdapter, MOCK_CAPABILITIES, mapMockOperation } from "../src/action-adapter.ts";
import { actionPayloadHash, canonicalJson, parseActionEnvelope } from "../src/action-contract.ts";
import type { ActionAuthority, ActionEnvelope, Json, NativeSource } from "../src/action-contract.ts";
import { createActionGateway, pythonAuthorityClient } from "../src/bridge-server.ts";

const NOW = Date.parse("2026-10-06T12:00:00Z");
const TOKEN = "gateway-synthetic-token-" + "a".repeat(40);
function envelope(kind: ActionEnvelope["kind"] = "SEND_TEXT", overrides: Partial<ActionEnvelope> = {}): ActionEnvelope {
  const native = { target_message_id: "message-synthetic", target_source_revision: 1, native_record_ref: "native-synthetic" };
  const payload: Record<string, Json> = kind === "SEND_TEXT" ? { text: "Thanks!" } : kind === "QUOTE" ?
    { text: "Thanks!", ...native } : kind === "REACTION" ? { emoji: "🙏", ...native } :
      { ...native, route_id: "route-synthetic", route_version: 1, category: "owner_selected_text" };
  return { schema_version: 1, action_id: "action-synthetic", workspace_id: "workspace-synthetic",
    connector_id: "connector-synthetic", account_id: "account-synthetic", conversation_id: "chat-synthetic",
    recipient_id: "15550001234@s.whatsapp.net", kind, payload, payload_hash: actionPayloadHash(payload),
    connector_fence: 1, ...overrides };
}
function source(): NativeSource {
  return { ref: "native-synthetic", key: { remote_jid: "15550001234@s.whatsapp.net", message_id: "provider-synthetic",
    participant: null, from_me: false }, revision: 1, deleted: false, expires_at: null,
  record: { key: { remoteJid: "15550001234@s.whatsapp.net", id: "provider-synthetic", fromMe: false },
    message: { conversation: "Authentic synthetic provider original" } } };
}
function authority(action: ActionEnvelope, patch: Partial<ActionAuthority> = {}): ActionAuthority {
  const { payload: _payload, ...bindings } = action;
  return { ...bindings, allowed: true, reason_code: "OK", authority_expires_at: new Date(NOW + 1000).toISOString(),
    ...(action.kind === "SEND_TEXT" ? {} : { source_record: source() }), ...patch };
}
async function listen(t: TestContext, server: Server): Promise<string> {
  await new Promise<void>((resolve) => { server.listen(0, "127.0.0.1", resolve); });
  t.after(async () => { server.closeAllConnections(); await new Promise<void>((resolve) => server.close(() => resolve())); });
  const address = server.address();
  if (!address || typeof address === "string") throw new Error("Test server unavailable");
  return `http://127.0.0.1:${address.port}`;
}

test("action schema strictly binds each typed operation and canonical UTF-8 hash", () => {
  const input = envelope();
  assert.equal(parseActionEnvelope(input).kind, "SEND_TEXT");
  assert.equal(canonicalJson({ b: { z: 1, a: "ठीक आहे 🙏" }, a: false }),
    '{"a":false,"b":{"a":"ठीक आहे 🙏","z":1}}');
  for (const bad of [ { ...input, owner_authorized: true }, { ...input, payload_hash: "0".repeat(64) },
    { ...input, connector_fence: true }, { ...input, schema_version: 2 }, { ...input, kind: "send_anything" },
    { ...input, payload: { text: "Thanks!", recipient: "attacker" } }, { ...input, action_id: "" } ]) {
    assert.throws(() => parseActionEnvelope(bad));
  }
  assert.throws(() => actionPayloadHash({ nested: NaN }));
  assert.throws(() => actionPayloadHash({ "नाम": "hello" }));
});

test("capabilities pin exact mock adapter evidence and distinguish contact destinations", () => {
  assert.equal(MOCK_CAPABILITIES.native_forward?.status, "supported");
  for (const destination of ["contact_save_local", "contact_write_whatsapp", "contact_write_google", "contact_write_os"]) {
    assert.equal(MOCK_CAPABILITIES[destination]?.status, "unsupported");
  }
  assert.equal(MOCK_CAPABILITIES.pairing_code?.status, "unsupported");
  assert.equal(MOCK_CAPABILITIES.emoji_reaction?.adapter_version, "mock-actions-v1");
  assert.equal(MOCK_CAPABILITIES.quoted_reply?.test_reference, "tests/action-bridge.test.ts");
});

test("pinned quote, reaction and native forward maps preserve authenticated original records", () => {
  for (const kind of ["SEND_TEXT", "QUOTE", "REACTION", "FORWARD"] as const) {
    const input = parseActionEnvelope(envelope(kind));
    const operation = mapMockOperation(input, source(), NOW);
    if (operation.kind === "quoted_reply") assert.deepEqual(operation.quoted, source().record);
    if (operation.kind === "reaction") {
      assert.deepEqual(operation.react.key, source().record.key);
      assert.equal(operation.react.text, "🙏");
    }
    if (operation.kind === "native_forward") assert.deepEqual(operation.forward, source().record);
  }
});

test("text-only exports, forged native keys, missing, stale, deleted or expired sources cannot map", () => {
  const action = parseActionEnvelope(envelope("QUOTE"));
  for (const bad of [ null, { text: "Reconstructed export" }, { ...source(), deleted: true },
    { ...source(), revision: 2 }, { ...source(), ref: "another-source" },
    { ...source(), expires_at: new Date(NOW).toISOString() },
    { ...source(), key: { ...source().key, remote_jid: "other-chat" } },
    { ...source(), record: { key: { remoteJid: "other-chat", id: "provider-synthetic", fromMe: false }, message: {} } } ]) {
    assert.throws(() => mapMockOperation(action, bad, NOW));
  }
});

test("current server authority is mandatory and rechecked after queued admission", async () => {
  assert.throws(() => new MockActionAdapter({ readCurrentAuthority: undefined } as never), /mandatory/);
  let release!: (value: unknown) => void;
  const adapter = new MockActionAdapter({ now: () => NOW, readCurrentAuthority: async () =>
    new Promise((resolve) => { release = resolve; }) });
  const action = envelope();
  const pending = adapter.submit(action);
  await Promise.resolve();
  release(authority(action, { allowed: false, reason_code: "HUMAN_TAKEOVER" }));
  await assert.rejects(() => pending, /HUMAN_TAKEOVER/);
  assert.equal(adapter.submittedOperations.length, 0);
});

test("all authoritative account, recipient and immutable reference changes block the socket", async () => {
  for (const patch of [{ action_id: "forged" }, { workspace_id: "other" }, { connector_id: "other" },
    { account_id: "other" }, { conversation_id: "other" }, { recipient_id: "other" }, { kind: "QUOTE" as const },
    { payload_hash: "0".repeat(64) }, { connector_fence: 2 }, { authority_expires_at: new Date(NOW).toISOString() }]) {
    const adapter = new MockActionAdapter({ now: () => NOW, readCurrentAuthority: async (action) => authority(action, patch) });
    await assert.rejects(() => adapter.submit(envelope()), /CONTEXT_STALE|EXPIRED/);
    assert.equal(adapter.submittedOperations.length, 0);
  }
});

test("lease handoff during authority wait rejects the old fence at the socket boundary", async () => {
  let release!: (value: unknown) => void;
  const adapter = new MockActionAdapter({ now: () => NOW, readCurrentAuthority: async () =>
    new Promise((resolve) => { release = resolve; }) });
  const action = envelope();
  const pending = adapter.submit(action);
  await Promise.resolve();
  adapter.fence(action, 2);
  release(authority(action));
  await assert.rejects(() => pending, /CONTEXT_STALE/);
  assert.equal(adapter.submittedOperations.length, 0);
});

test("pause, revoke, source deletion and route rejection return reasons without external operation", async () => {
  for (const reason of ["GLOBAL_PAUSE", "SCOPE_DENIED", "SOURCE_MISSING", "ROUTE_DENIED", "CAPABILITY_UNAVAILABLE"]) {
    const adapter = new MockActionAdapter({ now: () => NOW,
      readCurrentAuthority: async (action) => authority(action, { allowed: false, reason_code: reason }) });
    await assert.rejects(() => adapter.submit(envelope()), new RegExp(reason));
    assert.equal(adapter.submittedOperations.length, 0);
  }
});

test("concurrent duplicates and uncertain outcomes never submit a second mock operation", async () => {
  let reads = 0;
  const adapter = new MockActionAdapter({ now: () => NOW, submissionOutcome: "uncertain",
    readCurrentAuthority: async (action) => { reads++; return authority(action); } });
  const results = await Promise.all(Array.from({ length: 20 }, () => adapter.submit(envelope())));
  assert.equal(adapter.submittedOperations.length, 1);
  assert.equal(reads, 1);
  assert.equal(results[0]?.status, "uncertain");
  assert.equal(results[0]?.provider_message_id, null);
  assert.deepEqual(await adapter.submit(envelope()), results[0]);
  assert.equal(adapter.reconcile("action-synthetic")?.status, "uncertain");
  const different = envelope("SEND_TEXT", { recipient_id: "attacker" });
  await assert.rejects(() => adapter.submit(different), /ACTION_REUSED/);
  assert.equal(adapter.reconcile("unknown-action"), null);
});

test("HTTP gateway authenticates before parsing and cannot trust supplied authorization assertions", async (t) => {
  let reads = 0;
  const gateway = createActionGateway({ token: TOKEN, readCurrentAuthority: async (action) => {
    reads++; return authority(action); }, adapter: new MockActionAdapter({ now: () => NOW,
    readCurrentAuthority: async (action) => { reads++; return authority(action); } }) });
  const origin = await listen(t, gateway.server);
  const post = (value: unknown, token = TOKEN) => fetch(origin + "/v1/actions", { method: "POST",
    headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" }, body: JSON.stringify(value) });
  assert.equal((await post(envelope(), "wrong")).status, 401);
  assert.equal((await post({ ...envelope(), allowed: true })).status, 400);
  assert.equal(reads, 0);
  const response = await post(envelope());
  assert.equal(response.status, 200);
  assert.equal((await response.json() as { status: string }).status, "accepted");
  assert.equal(reads, 1);
  assert.equal(gateway.adapter.submittedOperations.length, 1);
  assert.equal((await fetch(origin + "/v1/actions/action-synthetic", {
    headers: { Authorization: `Bearer ${TOKEN}` } })).status, 200);
  assert.equal((await fetch(origin + "/healthz")).status, 200);
});

test("gateway performs a real authenticated HTTP current-authority call before mock submission", async (t) => {
  let reads = 0;
  let permitted = true;
  const sqlAuthority = createServer(async (request, response) => {
    assert.equal(request.url, "/internal/dispatch-authority");
    assert.equal(request.headers.authorization, "Bearer synthetic-python-token");
    const chunks: Buffer[] = [];
    for await (const chunk of request) chunks.push(Buffer.from(chunk as Uint8Array));
    const input = parseActionEnvelope(JSON.parse(Buffer.concat(chunks).toString()));
    reads++;
    response.writeHead(200, { "Content-Type": "application/json" });
    response.end(JSON.stringify(authority(input, permitted ? {} : { allowed: false, reason_code: "GLOBAL_PAUSE" })));
  });
  const origin = await listen(t, sqlAuthority);
  const adapter = new MockActionAdapter({ now: () => NOW, readCurrentAuthority: pythonAuthorityClient({
    authorityUrl: origin, internalToken: "synthetic-python-token" }) });
  assert.equal((await adapter.submit(envelope())).status, "accepted");
  permitted = false;
  await assert.rejects(() => adapter.submit(envelope("SEND_TEXT", { action_id: "second-action" })), /GLOBAL_PAUSE/);
  assert.equal(reads, 2);
  assert.equal(adapter.submittedOperations.length, 1);
});

test("authority client refuses userinfo, nonloopback plaintext and redirects", async () => {
  for (const authorityUrl of ["http://public.example.test", "http://token@localhost", "file:///tmp/authority",
    "http://localhost?recipient=evil"]) {
    assert.throws(() => pythonAuthorityClient({ authorityUrl, internalToken: "synthetic" }));
  }
  const reader = pythonAuthorityClient({ authorityUrl: "http://localhost", internalToken: "synthetic",
    fetcher: async (_url, init) => {
      assert.equal(init?.redirect, "error");
      return new Response("", { status: 302, headers: { Location: "https://attacker.example.test" } });
    } });
  await assert.rejects(() => reader(envelope()), /AUTHORITY_UNAVAILABLE/);
});
