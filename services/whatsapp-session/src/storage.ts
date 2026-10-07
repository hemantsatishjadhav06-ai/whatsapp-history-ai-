import { createHash } from "node:crypto";
import { Client, Pool } from "pg";
import { BufferJSON, initAuthCreds, proto } from "@whiskeysockets/baileys";
import type { AuthenticationCreds, AuthenticationState, SignalDataSet, SignalDataTypeMap } from "@whiskeysockets/baileys";
import { decryptAuth, encryptAuth } from "./crypto.ts";
import { Blocked, individualJid } from "./protocol.ts";
import type { Identity } from "./protocol.ts";

export interface AuthStore {
  readonly state: AuthenticationState;
  saveCreds(): Promise<void>;
  clear(): Promise<void>;
  assertLive(): Promise<void>;
  release(): Promise<void>;
}
export type AuthStoreFactory = (identity: Identity, onLost: () => void) => Promise<AuthStore>;
/** One PostgreSQL session lock per linked account. Bounded pilot; not 50k DB connections. */
export function postgresAuthStores(options: { connectionString: string; key: Buffer; poolSize?: number }) {
  const pool = new Pool({ connectionString: options.connectionString, max: options.poolSize ?? 8,
    connectionTimeoutMillis: 5000, idleTimeoutMillis: 30_000, query_timeout: 5000, statement_timeout: 5000, lock_timeout: 3000 });
  pool.on("error", () => { /* Never print database URLs or auth material. Individual calls fail closed. */ });
  const factory: AuthStoreFactory = async (identity, onLost) => {
    const lock = new Client({ connectionString: options.connectionString, connectionTimeoutMillis: 5000,
      query_timeout: 5000, statement_timeout: 5000, lock_timeout: 3000 });
    let live = true, released = false;
    const lost = () => { if (!released && live) { live = false; onLost(); } };
    lock.on("error", lost); lock.on("end", lost);
    try {
      await lock.connect();
      const key = createHash("sha256").update(`milo-personal-session:${identity.connector_id}`).digest().readBigInt64BE().toString();
      const result = await lock.query<{ acquired: boolean }>("SELECT pg_try_advisory_lock($1::bigint) AS acquired", [key]);
      if (!result.rows[0]?.acquired) throw new Blocked("SESSION_OWNED_BY_ANOTHER_CELL");
    } catch (error) { released = true; await lock.end().catch(() => {}); throw error; }
    async function assertLive() {
      if (!live || released) throw new Blocked("SESSION_OWNERSHIP_LOST");
      try {
        const current = await lock.query<{ fence: number }>("SELECT fence FROM connectors WHERE id=$1 AND workspace_id=$2",
          [identity.connector_id, identity.workspace_id]);
        if (current.rows[0]?.fence !== identity.connector_fence) { lost(); throw new Blocked("STALE_CONNECTOR_FENCE"); }
      } catch { lost(); throw new Blocked("SESSION_OWNERSHIP_LOST"); }
    }
    const heartbeat = setInterval(() => { void assertLive().catch(lost); }, 1000);
    heartbeat.unref();
    const binding = (type: string, id: string) => ({ workspace_id: identity.workspace_id,
      connector_id: identity.connector_id, key_type: type, key_id: id });
    async function read(type: string, ids: readonly string[]): Promise<Record<string, unknown>> {
      if (ids.length > 500 || ids.some(id => !id || id.length > 256)) throw new Blocked("AUTH_STORAGE_LIMIT");
      await assertLive();
      const rows = await pool.query<{ key_id: string; ciphertext: string }>(
        `SELECT a.key_id,a.ciphertext FROM wa_personal_auth_keys a JOIN connectors c ON c.id=a.connector_id
         WHERE a.workspace_id=$1 AND a.connector_id=$2 AND a.key_type=$3 AND a.key_id=ANY($4::text[])
         AND c.workspace_id=$1 AND c.fence=$5`, [identity.workspace_id, identity.connector_id, type, ids, identity.connector_fence]);
      const result: Record<string, unknown> = Object.create(null) as Record<string, unknown>;
      for (const row of rows.rows) result[row.key_id] = JSON.parse(decryptAuth(options.key, binding(type, row.key_id), row.ciphertext), BufferJSON.reviver) as unknown;
      await assertLive();
      return result;
    }
    let mutationTail: Promise<void> = Promise.resolve();
    function mutate(work: () => Promise<void>): Promise<void> {
      const task = mutationTail.then(work, work); mutationTail = task.catch(() => {}); return task;
    }
    async function write(values: readonly { type: string; id: string; value: unknown }[]) {
      if (values.length > 500) throw new Blocked("AUTH_STORAGE_LIMIT");
      return mutate(async () => {
      await assertLive();
      // All mutations use the advisory-lock-owning connection. Lost ownership cannot
      // leave a pooled statement alive to overwrite a replacement cell's Signal keys.
      const client = lock;
      try {
        await client.query("BEGIN");
        const current = await client.query<{ fence: number }>(
          "SELECT fence FROM connectors WHERE id=$1 AND workspace_id=$2 FOR SHARE", [identity.connector_id, identity.workspace_id]);
        if (current.rows[0]?.fence !== identity.connector_fence) throw new Blocked("STALE_CONNECTOR_FENCE");
        for (const item of values) {
          if (!item.type || item.type.length > 80 || !item.id || item.id.length > 256) throw new Blocked("AUTH_STORAGE_LIMIT");
          if (item.value === null || item.value === undefined) {
            await client.query("DELETE FROM wa_personal_auth_keys WHERE workspace_id=$1 AND connector_id=$2 AND key_type=$3 AND key_id=$4",
              [identity.workspace_id, identity.connector_id, item.type, item.id]);
          } else {
            const ciphertext = encryptAuth(options.key, binding(item.type, item.id), JSON.stringify(item.value, BufferJSON.replacer));
            await client.query(`INSERT INTO wa_personal_auth_keys(workspace_id,connector_id,key_type,key_id,ciphertext,updated_at)
              VALUES($1,$2,$3,$4,$5,now()) ON CONFLICT(connector_id,key_type,key_id)
              DO UPDATE SET ciphertext=EXCLUDED.ciphertext,updated_at=now() WHERE wa_personal_auth_keys.workspace_id=EXCLUDED.workspace_id`,
            [identity.workspace_id, identity.connector_id, item.type, item.id, ciphertext]);
          }
        }
        await assertLive();
        await client.query("COMMIT");
      } catch (error) { await client.query("ROLLBACK").catch(() => {}); throw error; }
      });
    }
    try {
      const stored = await read("creds", ["current"]);
      const creds = (stored.current ?? initAuthCreds()) as AuthenticationCreds;
      const state: AuthenticationState = { creds, keys: {
        async get<T extends keyof SignalDataTypeMap>(type: T, ids: string[]) {
          const values = await read(type, ids);
          if (type === "app-state-sync-key") {
            for (const id of Object.keys(values)) values[id] = proto.Message.AppStateSyncKeyData.fromObject(values[id] as Record<string, unknown>);
          }
          return values as { [id: string]: SignalDataTypeMap[T] };
        },
        async set(data: SignalDataSet) {
          const values: { type: string; id: string; value: unknown }[] = [];
          for (const [type, items] of Object.entries(data)) for (const [id, value] of Object.entries(items ?? {})) values.push({ type, id, value });
          await write(values);
        },
      } };
      return { state, saveCreds: () => write([{ type: "creds", id: "current", value: state.creds }]), assertLive,
        async clear() { return mutate(async () => {
          await assertLive(); const client = lock;
          try {
            await client.query("BEGIN");
            const current = await client.query<{ fence: number }>("SELECT fence FROM connectors WHERE id=$1 AND workspace_id=$2 FOR SHARE",
              [identity.connector_id, identity.workspace_id]);
            if (current.rows[0]?.fence !== identity.connector_fence) throw new Blocked("STALE_CONNECTOR_FENCE");
            await assertLive();
            await client.query("DELETE FROM wa_personal_auth_keys WHERE workspace_id=$1 AND connector_id=$2", [identity.workspace_id, identity.connector_id]);
            await assertLive(); await client.query("COMMIT");
          } catch (error) { await client.query("ROLLBACK").catch(() => {}); throw error; }
        }); },
        async release() { if (released) return; released = true; live = false; clearInterval(heartbeat); await lock.end().catch(() => {}); },
      };
    } catch (error) { released = true; live = false; clearInterval(heartbeat); await lock.end().catch(() => {}); throw error; }
  };
  return { factory,
    async restorableIdentities(limit: number): Promise<Identity[]> {
      if (!Number.isSafeInteger(limit) || limit < 1 || limit > 250) throw new Blocked("AUTH_STORAGE_LIMIT");
      const rows = await pool.query<{ workspace_id: string; connector_id: string; connector_fence: number; account_id: string }>(
        `SELECT c.workspace_id,c.id AS connector_id,c.fence AS connector_fence,c.account_id
         FROM whatsapp_personal_sessions s JOIN connectors c ON c.id=s.connector_id
         AND c.workspace_id=s.workspace_id AND c.fence=s.connector_fence
         WHERE c.provider='whatsapp_personal'
         AND c.status IN ('starting','pairing','connected','reconnecting')
         AND s.status IN ('starting','pairing','connected','reconnecting')
         ORDER BY c.id LIMIT $1`, [limit]);
      // This query reads content-free placement only. The factory decrypts auth
      // after fresh Python authority and exclusive lock acquisition in start().
      return rows.rows.map(row => ({ schema_version: 1, workspace_id: row.workspace_id,
        connector_id: row.connector_id, connector_fence: row.connector_fence,
        account_id: individualJid(row.account_id) ? row.account_id : null }));
    },
    async ready() { await pool.query("SELECT workspace_id,connector_id,key_type,key_id,ciphertext,updated_at FROM wa_personal_auth_keys LIMIT 0"); },
    async close() { await pool.end(); },
  };
}
