import assert from "node:assert/strict";
import test from "node:test";
import type { AuthoritativeAuthorization, CurrentAuthorizer, SendPermitClaims, ValidatedAction } from "../src/contract.ts";
import { MockConnectorAdapter } from "../src/mock-adapter.ts";
import { contentHash, issueSendPermit } from "../src/permits.ts";

const TEST_KEY = Buffer.alloc(32, 7); // Synthetic test fixture, never a deployment credential.
const NOW = Date.parse("2026-10-06T10:00:00Z");
const identity = { workspace_id: "workspace-test", connector_id: "connector-test", account_id: "account-test" };
function fixture(options: { group?: boolean; groupAllowed?: boolean; uncertain?: boolean; authorizer?: CurrentAuthorizer } = {}) {
  let now = NOW;
  let calls = 0;
  const claims: SendPermitClaims = { ...identity, schema_version: 1, intent_id: "intent-test",
    conversation_id: "conversation-test", recipient_id: "recipient-test", conversation_kind: options.group ? "group" : "contact",
    content_hash: contentHash("Approved exact text"), conversation_revision: 2, control_epoch: 3,
    permission_version: 4, pause_generation: 5, connector_fence: 1,
    issued_at: new Date(NOW).toISOString(), expires_at: new Date(NOW + 30_000).toISOString() };
  let current: AuthoritativeAuthorization = { ...identity, conversation_id: claims.conversation_id,
    recipient_id: claims.recipient_id, conversation_kind: claims.conversation_kind, content_hash: claims.content_hash,
    conversation_revision: 2, control_epoch: 3, permission_version: 4, pause_generation: 5, connector_fence: 1,
    connected: true, paused: false, send_allowed: true, group_send_allowed: true, recipient_opted_out: false,
    control_state: "DRAFT_MODE", lease_expires_at: new Date(NOW + 60_000).toISOString(),
    approval_expires_at: new Date(NOW + 60_000).toISOString() };
  const read: CurrentAuthorizer = async (incoming) => { calls++; return options.authorizer ? options.authorizer(incoming) : current; };
  const adapter = new MockConnectorAdapter({ identity, permitKey: TEST_KEY, readCurrentAuthorization: read,
    now: () => now, allowGroupSend: options.groupAllowed ?? false, submissionOutcome: options.uncertain ? "uncertain" : "accepted" });
  const action = (overrides: Partial<SendPermitClaims> = {}): ValidatedAction => {
    const permitClaims = { ...claims, ...overrides };
    return { account_id: permitClaims.account_id, recipient_id: permitClaims.recipient_id,
      text: "Approved exact text", permit: issueSendPermit(permitClaims, TEST_KEY) };
  };
  return { adapter, action, claims, current: () => current, calls: () => calls,
    updateCurrent: (patch: Partial<AuthoritativeAuthorization>) => { current = { ...current, ...patch }; },
    setTime: (time: number) => { now = time; } };
}

test("mock connect/status/disconnect explicitly report simulation and cannot pair a device", async () => {
  const f = fixture();
  assert.equal(f.adapter.status().state, "disconnected");
  const connected = await f.adapter.connect(1);
  assert.equal(connected.provider, "mock");
  assert.equal(connected.simulation, true);
  assert.equal(connected.capabilities.pairing_code.status, "unsupported");
  assert.match(connected.capabilities.send_text.reason, /simulation/i);
  assert.equal(Object.hasOwn(connected, "qr"), false);
  assert.equal((await f.adapter.disconnect()).state, "disconnected");
  await assert.rejects(() => f.adapter.sendValidatedAction(f.action()), /disconnected/);
});

test("signed exact-content submission calls trusted authorizer and acceptance is distinct from delivery", async () => {
  const f = fixture();
  await f.adapter.connect(1);
  const accepted = await f.adapter.sendValidatedAction(f.action());
  assert.equal(accepted.status, "accepted");
  assert.equal(accepted.simulation, true);
  assert.match(accepted.provider_message_id!, /^mock-/);
  assert.equal(f.calls(), 1);
  assert.equal((await f.adapter.reconcile("intent-test"))?.status, "accepted");
  f.adapter.simulateDeliveryReceipt(accepted.provider_message_id!);
  assert.equal((await f.adapter.reconcile("intent-test"))?.status, "delivered");
  assert.throws(() => f.adapter.simulateDeliveryReceipt("unrelated-provider-id"), /correlated/);
});

test("clients cannot supply assertions in place of a mandatory authorizer callback", () => {
  assert.throws(() => new MockConnectorAdapter({ identity, permitKey: TEST_KEY,
    readCurrentAuthorization: undefined } as unknown as ConstructorParameters<typeof MockConnectorAdapter>[0]), /mandatory/);
  assert.throws(() => new MockConnectorAdapter({ identity, permitKey: Buffer.alloc(2),
    readCurrentAuthorization: async () => null }), /32 bytes/);
});

test("wrong account, recipient, text, connector or workspace cannot use a permit", async () => {
  const f = fixture();
  await f.adapter.connect(1);
  const action = f.action();
  for (const changed of [
    { ...action, account_id: "wrong-account" }, { ...action, recipient_id: "wrong-recipient" },
    { ...action, text: "Edited after approval" }, f.action({ account_id: "wrong-account" }),
    f.action({ connector_id: "wrong-connector" }), f.action({ workspace_id: "wrong-workspace" }),
  ]) await assert.rejects(() => f.adapter.sendValidatedAction(changed), /match/);
  assert.equal(f.adapter.acceptedSubmissionCount, 0);
});

test("an altered signature or unsigned permit fields are rejected", async () => {
  const f = fixture();
  await f.adapter.connect(1);
  const action = f.action();
  await assert.rejects(() => f.adapter.sendValidatedAction({ ...action, permit: {
    ...action.permit, claims: { ...action.permit.claims, recipient_id: "attacker" },
  } }), /signature/);
  await assert.rejects(() => f.adapter.sendValidatedAction({ ...action, permit: {
    ...action.permit, signature: "0".repeat(64),
  } }), /signature/);
  await assert.rejects(() => f.adapter.sendValidatedAction({ ...action,
    permit: issueSendPermit(f.claims, Buffer.alloc(32, 1)) }), /signature/);
});

test("expired, future or overlong permits cannot send", async () => {
  const f = fixture();
  await f.adapter.connect(1);
  f.setTime(NOW + 30_000);
  await assert.rejects(() => f.adapter.sendValidatedAction(f.action()), /expired/);
  f.setTime(NOW - 1);
  await assert.rejects(() => f.adapter.sendValidatedAction(f.action()), /not yet valid/);
  assert.throws(() => f.action({ expires_at: new Date(NOW + 60_001).toISOString() }), /60 seconds/);
});

test("all current identity and version changes invalidate a previously signed permit", async () => {
  const changes: Partial<AuthoritativeAuthorization>[] = [
    { workspace_id: "another-workspace" }, { connector_id: "another-connector" }, { account_id: "another-account" },
    { conversation_id: "another-conversation" }, { recipient_id: "another-recipient" }, { conversation_kind: "group" },
    { content_hash: contentHash("different approved content") }, { conversation_revision: 99 }, { control_epoch: 99 },
    { permission_version: 99 }, { pause_generation: 99 }, { connector_fence: 99 },
  ];
  for (const change of changes) {
    const f = fixture();
    await f.adapter.connect(1);
    f.updateCurrent(change);
    await assert.rejects(() => f.adapter.sendValidatedAction(f.action()), /authoritative .* changed/);
    assert.equal(f.adapter.acceptedSubmissionCount, 0);
  }
});

test("current pause, takeover, revoked permission, opt-out, disconnected state and expiry block sends", async () => {
  const changes: Partial<AuthoritativeAuthorization>[] = [
    { paused: true }, { send_allowed: false }, { recipient_opted_out: true }, { connected: false },
    { control_state: "HUMAN_TAKEOVER" }, { control_state: "AI_OFF" }, { control_state: "READ_ONLY" },
    { control_state: "RECONNECT_REVIEW" }, { lease_expires_at: new Date(NOW).toISOString() },
    { approval_expires_at: new Date(NOW).toISOString() },
  ];
  for (const change of changes) {
    const f = fixture();
    await f.adapter.connect(1);
    f.updateCurrent(change);
    await assert.rejects(() => f.adapter.sendValidatedAction(f.action()), /blocks|expired/);
    assert.equal(f.adapter.acceptedSubmissionCount, 0);
  }
});

test("unavailable or failed authoritative state is fail-closed", async () => {
  for (const authorizer of [async () => null, async () => { throw new Error("SQL unavailable"); }]) {
    const f = fixture({ authorizer });
    await f.adapter.connect(1);
    await assert.rejects(() => f.adapter.sendValidatedAction(f.action()), /unavailable/);
    assert.equal(f.adapter.acceptedSubmissionCount, 0);
  }
});

test("fence is checked again after waiting for authoritative state", async () => {
  let release!: (current: AuthoritativeAuthorization) => void;
  const f = fixture({ authorizer: () => new Promise((resolve) => { release = resolve; }) });
  await f.adapter.connect(1);
  const pending = f.adapter.sendValidatedAction(f.action());
  await Promise.resolve();
  await f.adapter.disconnect();
  await f.adapter.connect(2);
  release(f.current());
  await assert.rejects(() => pending, /fence/);
  assert.equal(f.adapter.acceptedSubmissionCount, 0);
});

test("permit expiry is checked again after waiting for authoritative state", async () => {
  let release!: (current: AuthoritativeAuthorization) => void;
  const f = fixture({ authorizer: () => new Promise((resolve) => { release = resolve; }) });
  await f.adapter.connect(1);
  const pending = f.adapter.sendValidatedAction(f.action());
  await Promise.resolve();
  f.setTime(NOW + 30_000);
  release(f.current());
  await assert.rejects(() => pending, /expired/);
  assert.equal(f.adapter.acceptedSubmissionCount, 0);
});

test("group sends require both supported capability and current group permission", async () => {
  const blocked = fixture({ group: true });
  await blocked.adapter.connect(1);
  await assert.rejects(() => blocked.adapter.sendValidatedAction(blocked.action()), /group_send/);
  const allowed = fixture({ group: true, groupAllowed: true });
  await allowed.adapter.connect(1);
  allowed.updateCurrent({ group_send_allowed: false });
  await assert.rejects(() => allowed.adapter.sendValidatedAction(allowed.action()), /group sending/);
  allowed.updateCurrent({ group_send_allowed: true });
  assert.equal((await allowed.adapter.sendValidatedAction(allowed.action())).status, "accepted");
});

test("concurrent and later duplicate intent submissions record one acceptance", async () => {
  const f = fixture();
  await f.adapter.connect(1);
  const action = f.action();
  const results = await Promise.all(Array.from({ length: 20 }, () => f.adapter.sendValidatedAction(action)));
  assert.equal(new Set(results.map((result) => result.provider_message_id)).size, 1);
  assert.equal(f.adapter.acceptedSubmissionCount, 1);
  assert.equal(f.calls(), 1);
  assert.deepEqual(await f.adapter.sendValidatedAction(action), results[0]);
  await assert.rejects(() => f.adapter.sendValidatedAction(f.action({ recipient_id: "new-recipient" })), /reused/);
  assert.equal(f.adapter.acceptedSubmissionCount, 1);
});

test("uncertain acceptance is reconciled without a second submission", async () => {
  const f = fixture({ uncertain: true });
  await f.adapter.connect(1);
  const uncertain = await f.adapter.sendValidatedAction(f.action());
  assert.equal(uncertain.status, "uncertain");
  assert.equal(uncertain.provider_message_id, null);
  const accepted = await f.adapter.reconcile("intent-test");
  assert.equal(accepted?.status, "accepted");
  assert.ok(accepted?.provider_message_id);
  assert.equal((await f.adapter.sendValidatedAction(f.action())).status, "accepted");
  assert.equal(f.adapter.acceptedSubmissionCount, 1);
  assert.equal(await f.adapter.reconcile("unknown-intent"), null);
});

test("reconnect requires a larger fence and invalidates old permits", async () => {
  const f = fixture();
  await f.adapter.connect(1);
  await f.adapter.disconnect();
  await assert.rejects(() => f.adapter.connect(1), /increasing/);
  await f.adapter.connect(2);
  await assert.rejects(() => f.adapter.sendValidatedAction(f.action()), /fence/);
  f.updateCurrent({ connector_fence: 2 });
  assert.equal((await f.adapter.sendValidatedAction(f.action({ connector_fence: 2 }))).status, "accepted");
  assert.equal(f.adapter.acceptedSubmissionCount, 1);
});
