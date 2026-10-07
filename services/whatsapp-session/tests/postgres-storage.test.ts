import test from "node:test";
import assert from "node:assert/strict";
import { randomBytes, randomUUID } from "node:crypto";
import { Pool } from "pg";
import { setTimeout as wait } from "node:timers/promises";
import { postgresAuthStores } from "../src/storage.ts";
import type { AuthStore } from "../src/storage.ts";
import type { Identity } from "../src/protocol.ts";
const database = process.env.NODE_TEST_DATABASE_URL;
test("real PostgreSQL Signal transactions, locks, restart and stale-cell fencing", { skip: !database }, async t => {
  const admin = new Pool({ connectionString: database!, max: 2, connectionTimeoutMillis: 5000 });
  const schema = `milo_qr_test_${randomUUID().replaceAll("-", "")}`;
  const url = new URL(database!); url.searchParams.set("options", `-c search_path=${schema}`);
  url.searchParams.set("application_name", schema);
  const sql = new Pool({ connectionString: url.toString(), max: 2 });
  const key = randomBytes(32); const storage = postgresAuthStores({ connectionString: url.toString(), key });
  const stores: AuthStore[] = []; const initial: Identity = { schema_version: 1, workspace_id: "synthetic_owner",
    connector_id: randomUUID(), connector_fence: 1, account_id: null };
  let lost = 0;
  try {
    await admin.query(`CREATE SCHEMA ${schema}`);
    await sql.query(`CREATE TABLE connectors (id text PRIMARY KEY,workspace_id text NOT NULL,fence integer NOT NULL,
      provider text NOT NULL DEFAULT 'whatsapp_personal',status text NOT NULL DEFAULT 'connected',
      account_id text NOT NULL DEFAULT '15550000001@s.whatsapp.net')`);
    await sql.query(`CREATE TABLE whatsapp_personal_sessions (connector_id text PRIMARY KEY REFERENCES connectors(id),
      workspace_id text NOT NULL,connector_fence integer NOT NULL,status text NOT NULL)`);
    await sql.query(`CREATE TABLE wa_personal_auth_keys (workspace_id text NOT NULL,connector_id text NOT NULL REFERENCES connectors(id),
      key_type varchar(80) NOT NULL,key_id varchar(256) NOT NULL,ciphertext text NOT NULL,updated_at timestamptz NOT NULL DEFAULT now(),
      PRIMARY KEY(connector_id,key_type,key_id))`);
    await sql.query("INSERT INTO connectors(id,workspace_id,fence) VALUES($1,$2,$3)", [initial.connector_id, initial.workspace_id, initial.connector_fence]);
    await storage.ready();
    const first = await storage.factory(initial, () => { lost += 1; }); stores.push(first);
    await t.test("startup placement selects only current active personal owner/fence rows within its bound", async () => {
      const cases = [
        ["a_current", "synthetic_owner", 1, "whatsapp_personal", "connected", "synthetic_owner", 1, "connected"],
        ["b_current", "synthetic_owner", 1, "whatsapp_personal", "reconnecting", "synthetic_owner", 1, "reconnecting"],
        ["c_revoked", "synthetic_owner", 2, "whatsapp_personal", "disconnected", "synthetic_owner", 1, "connected"],
        ["d_session_revoked", "synthetic_owner", 1, "whatsapp_personal", "connected", "synthetic_owner", 1, "logged_out"],
        ["e_moved", "new_owner", 1, "whatsapp_personal", "connected", "old_owner", 1, "connected"],
        ["f_fence", "synthetic_owner", 2, "whatsapp_personal", "connected", "synthetic_owner", 1, "connected"],
        ["g_provider", "synthetic_owner", 1, "whatsapp_business", "connected", "synthetic_owner", 1, "connected"],
        ["h_failed", "synthetic_owner", 1, "whatsapp_personal", "failed", "synthetic_owner", 1, "connected"],
      ];
      try {
        for (const [id, owner, fence, provider, status, sessionOwner, sessionFence, sessionStatus] of cases) {
          await sql.query("INSERT INTO connectors(id,workspace_id,fence,provider,status) VALUES($1,$2,$3,$4,$5)", [id, owner, fence, provider, status]);
          await sql.query("INSERT INTO whatsapp_personal_sessions VALUES($1,$2,$3,$4)", [id, sessionOwner, sessionFence, sessionStatus]);
        }
        assert.deepEqual((await storage.restorableIdentities(20)).map(row => row.connector_id), ["a_current", "b_current"]);
        assert.deepEqual((await storage.restorableIdentities(1)).map(row => row.connector_id), ["a_current"]);
        await assert.rejects(storage.restorableIdentities(251));
      } finally {
        await sql.query("DELETE FROM whatsapp_personal_sessions");
        await sql.query("DELETE FROM connectors WHERE id=ANY($1::text[])", [cases.map(row => row[0])]);
      }
    });
    await t.test("auth keys roundtrip encrypted and update/delete atomically", async () => {
      const signal = Buffer.from("synthetic-signal-session-private-key");
      await first.state.keys.set({ session: { one: signal, two: Buffer.from("second") } });
      assert.deepEqual((await first.state.keys.get("session", ["one"])).one, signal);
      const rows = await sql.query<{ ciphertext: string }>("SELECT ciphertext FROM wa_personal_auth_keys");
      assert.equal(rows.rows.length, 2); assert.ok(rows.rows.every(row => !row.ciphertext.includes(signal.toString())));
      await first.state.keys.set({ session: { two: null } }); assert.equal((await first.state.keys.get("session", ["two"])).two, undefined);
    });
    await t.test("another cell cannot acquire the same connector lock", async () => { await assert.rejects(storage.factory(initial, () => {})); });
    await t.test("credentials survive a normal restart without plaintext export", async () => {
      first.state.creds.accountSyncCounter = 42; await first.saveCreds(); await first.release();
      const replacement = await storage.factory(initial, () => { lost += 1; }); stores.push(replacement);
      assert.equal(replacement.state.creds.accountSyncCounter, 42);
    });
    let current = stores.at(-1)!;
    await t.test("AAD rejects ciphertext copied into another Signal key row", async () => {
      await sql.query(`INSERT INTO wa_personal_auth_keys(workspace_id,connector_id,key_type,key_id,ciphertext)
        SELECT workspace_id,connector_id,key_type,'copied',ciphertext FROM wa_personal_auth_keys WHERE key_id='one'`);
      await assert.rejects(async () => current.state.keys.get("session", ["copied"]));
      await sql.query("DELETE FROM wa_personal_auth_keys WHERE key_id='copied'");
    });
    await t.test("a changed fence stops the old cell and cannot erase replacement credentials", async () => {
      await sql.query("UPDATE connectors SET fence=2 WHERE id=$1", [initial.connector_id]);
      await assert.rejects(current.clear()); await assert.rejects(async () => current.state.keys.set({ session: { one: Buffer.from("old-write") } }));
      await current.release();
      current = await storage.factory({ ...initial, connector_fence: 2 }, () => { lost += 1; }); stores.push(current);
      await current.state.keys.set({ session: { replacement: Buffer.from("new-fence-private-value") } });
      assert.equal((await current.state.keys.get("session", ["replacement"])).replacement?.toString(), "new-fence-private-value");
    });
    await t.test("abrupt lock connection loss invalidates old clear even for a same-fence takeover", async () => {
      // This is a dedicated synthetic test schema; identify the lock holder by its unique connector query parameter.
      const candidates = await admin.query<{ pid: number; query: string; }>(`SELECT a.pid,a.query FROM pg_stat_activity a JOIN pg_locks l ON l.pid=a.pid
        WHERE a.datname=current_database() AND a.application_name=$1 AND l.locktype='advisory' AND a.backend_type='client backend'`, [schema]);
      const owned = candidates.rows.filter(row => row.query.includes("SELECT fence FROM connectors"));
      assert.equal(owned.length, 1);
      await admin.query("SELECT pg_terminate_backend($1)", [owned[0]!.pid]); await wait(30);
      await assert.rejects(current.clear()); assert.ok(lost >= 1);
      const next = await storage.factory({ ...initial, connector_fence: 2 }, () => {}); stores.push(next);
      await next.state.keys.set({ session: { replacement: Buffer.from("same-fence-new-cell") } });
      await assert.rejects(current.clear()); assert.equal((await next.state.keys.get("session", ["replacement"])).replacement?.toString(), "same-fence-new-cell");
      current = next;
    });
    await t.test("current owner disconnect removes every stored auth key", async () => {
      await current.clear(); assert.equal((await sql.query<{ count: string }>("SELECT count(*) FROM wa_personal_auth_keys")).rows[0]?.count, "0");
    });
  } finally {
    for (const store of stores) await store.release(); await storage.close(); await sql.end();
    await admin.query(`DROP SCHEMA IF EXISTS ${schema} CASCADE`); await admin.end();
  }
});
