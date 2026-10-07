import test from "node:test";
import assert from "node:assert/strict";
import type { AddressInfo } from "node:net";
import { privateServer } from "../src/server.ts";
import { Sessions } from "../src/sessions.ts";
import type { Authority, Identity } from "../src/protocol.ts";
const token = "synthetic_gateway_token_with_32_bytes_minimum";
const identity: Identity = { schema_version: 1, workspace_id: "synthetic_workspace", connector_id: "synthetic_connector", connector_fence: 1, account_id: null };
test("private ingress authenticates before body parsing and refuses browser-cookie requests", async () => {
  let storeOpened = 0;
  const sessions = new Sessions({ enabled: false, stores: async () => { storeOpened += 1; throw new Error("must not open"); },
    authority: { async authorize(_operation, row) { return { ...row, allowed: true, reason_code: "ALLOWED", grants: [],
      authority_expires_at: new Date(Date.now() + 4000).toISOString() } as Authority; }, async event() {} } });
  let ready = true;
  const server = privateServer({ token, sessions, ready: async () => { if (!ready) throw new Error("storage"); } });
  await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve));
  const origin = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
  const post = (headers: Record<string, string> = {}, body = JSON.stringify(identity)) => fetch(`${origin}/v1/sessions/start`, {
    method: "POST", headers: { "Content-Type": "application/json", ...headers }, body });
  try {
    assert.equal((await post({}, "not-json")).status, 401);
    assert.equal((await post({ Authorization: "Bearer incorrect" })).status, 401);
    for (const headers of [{ Origin: "https://evil.example" }, { Cookie: "session_token=synthetic" }, { "Sec-Fetch-Site": "same-origin" }]) {
      assert.equal((await post({ Authorization: `Bearer ${token}`, ...headers })).status, 403);
    }
    assert.equal((await post({ Authorization: `Bearer ${token}` }, "not-json")).status, 400);
    assert.equal((await post({ Authorization: `Bearer ${token}` }, "x".repeat(32_769))).status, 400);
    const off = await post({ Authorization: `Bearer ${token}` }); assert.equal(off.status, 503);
    assert.equal((await off.json() as { reason_code: string }).reason_code, "PERSONAL_QR_DISABLED"); assert.equal(storeOpened, 0);
    const health = await fetch(`${origin}/healthz`); assert.equal(health.status, 200); assert.equal(health.headers.get("cache-control"), "no-store");
    assert.equal((await fetch(`${origin}/readyz`)).status, 200); ready = false; assert.equal((await fetch(`${origin}/readyz`)).status, 503);
  } finally {
    server.closeAllConnections(); await new Promise<void>(resolve => server.close(() => resolve())); await sessions.close();
  }
});
