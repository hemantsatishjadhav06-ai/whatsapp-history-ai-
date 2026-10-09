import { createHash } from "node:crypto";
import makeWASocket, { Browsers, DisconnectReason, generateMessageIDV2 } from "@whiskeysockets/baileys";
import type { WASocket, UserFacingSocketConfig, WAMessage, AuthenticationCreds, Chat, Contact } from "@whiskeysockets/baileys";
import type { AuthStore, AuthStoreFactory } from "./storage.ts";
import type { AuthorityClient } from "./authority.ts";
import { Blocked, canonicalJid, individualJid } from "./protocol.ts";
import type { Identity, SendEnvelope } from "./protocol.ts";

/** No SDK log argument is serialized, including QR, credentials, JIDs, text, or errors. */
export const silentLogger = Object.freeze({ level: "silent", child() { return silentLogger; },
  trace() {}, debug() {}, info() {}, warn() {}, error() {} });
export type SocketFactory = (options: UserFacingSocketConfig) => WASocket;
type State = "starting" | "qr" | "connected" | "reconnecting" | "disconnected" | "logged_out" | "failed";
type ChatMetadata = Readonly<{ provider_chat_id: string; title: string; kind: "contact" }>;
type RecordState = { identity: Identity; state: State; store: AuthStore | null; socket: WASocket | null;
  generation: number; qr: { value: string; expires_at: string } | null; chats: Map<string, ChatMetadata>;
  pairingPhone: string | null; pairingRequested: boolean; pairing: { code: string; expires_at: string } | null;
  tail: Promise<void>; pending: number; reconnects: number; timer: ReturnType<typeof setTimeout> | null;
  authorityTimer: ReturnType<typeof setInterval> | null };
export class Sessions {
  #records = new Map<string, RecordState>();
  #sends = new Map<string, { fingerprint: string; promise: Promise<Record<string, unknown>>; expires: number; done: boolean }>();
  #authority: AuthorityClient; #stores: AuthStoreFactory; #factory: SocketFactory;
  #limit: number; #enabled: boolean; #clock: () => number;
  #closed = false;
  constructor(options: { authority: AuthorityClient; stores: AuthStoreFactory; socketFactory?: SocketFactory;
    enabled?: boolean; maxSessions?: number; clock?: () => number }) {
    this.#authority = options.authority; this.#stores = options.stores;
    this.#factory = options.socketFactory ?? (config => makeWASocket(config));
    this.#limit = options.maxSessions ?? 20; this.#enabled = options.enabled ?? false;
    this.#clock = options.clock ?? Date.now;
    if (!Number.isSafeInteger(this.#limit) || this.#limit < 1 || this.#limit > 250) throw new Error("Pilot capacity must be 1..250");
  }
  #record(identity: Identity): RecordState {
    const record = this.#records.get(identity.connector_id);
    if (!record || record.identity.workspace_id !== identity.workspace_id || record.identity.connector_fence !== identity.connector_fence ||
        (identity.account_id !== null && identity.account_id !== record.identity.account_id)) throw new Blocked("SESSION_NOT_FOUND");
    return record;
  }
  #queue(record: RecordState, work: () => Promise<void>): void {
    if (record.pending >= 100) { void this.#stop(record, "failed"); return; }
    record.pending += 1;
    record.tail = record.tail.then(work).catch(() => this.#stop(record, "failed")).finally(() => { record.pending -= 1; });
  }
  #view(record: RecordState) {
    return { schema_version: 1 as const, state: record.state, account_id: record.identity.account_id,
      ...(record.qr && Date.parse(record.qr.expires_at) > this.#clock() ? { qr: { ...record.qr } } : {}),
      ...(record.pairing && Date.parse(record.pairing.expires_at) > this.#clock() ? { pairing: { ...record.pairing } } : {}) };
  }
  async restore(identities: readonly Identity[]) {
    let restored = 0, rejected = 0;
    if (!this.#enabled) return { restored, rejected };
    for (const identity of identities.slice(0, this.#limit)) {
      try { await this.start(identity); restored += 1; }
      catch { rejected += 1; }
    }
    return { restored, rejected };
  }
  /** A pairing phone requests a WhatsApp link code for exactly that number; only that account may connect. */
  async start(identity: Identity, pairingPhone?: string) {
    if (!this.#enabled) throw new Blocked("PERSONAL_QR_DISABLED");
    if (this.#closed) throw new Blocked("SESSION_DISCONNECTED");
    await this.#authority.authorize("start", identity);
    if (this.#closed) throw new Blocked("SESSION_DISCONNECTED");
    const previous = this.#records.get(identity.connector_id);
    if (previous) {
      if (previous.identity.workspace_id !== identity.workspace_id || previous.identity.connector_fence !== identity.connector_fence) {
        await this.#stop(previous, "disconnected"); this.#records.delete(identity.connector_id);
      } else if (!["failed", "disconnected", "logged_out"].includes(previous.state)) return this.#view(this.#record(identity));
      else { await this.#stop(previous, previous.state); this.#records.delete(identity.connector_id); }
    }
    const active = [...this.#records.values()].filter(row => !["failed", "disconnected", "logged_out"].includes(row.state)).length;
    if (active >= this.#limit) throw new Blocked("SESSION_CELL_FULL");
    const record: RecordState = { identity, state: "starting", store: null, socket: null, generation: 0,
      qr: null, chats: new Map(), pairingPhone: pairingPhone ?? null, pairingRequested: false, pairing: null,
      tail: Promise.resolve(), pending: 0, reconnects: 0, timer: null, authorityTimer: null };
    this.#records.set(identity.connector_id, record);
    try {
      record.store = await this.#stores(identity, () => { void this.#stop(record, "failed"); });
      if (this.#closed) throw new Blocked("SESSION_DISCONNECTED");
      record.authorityTimer = setInterval(() => this.#queue(record, async () => {
        if (!record.store) return;
        await record.store.assertLive();
        // A restored SQL lease may have expired while this process was down.
        // Its accepted connection callback applies the lease-gap review policy.
        await this.#authority.authorize(record.state === "connected" ? "status" : "start", record.identity);
      }), 5000);
      record.authorityTimer.unref();
      await this.#connect(record);
      return this.#view(record);
    } catch (error) { await this.#stop(record, "failed"); throw error; }
  }
  async status(identity: Identity) {
    await this.#authority.authorize("status", identity);
    return this.#view(this.#record(identity));
  }
  async chats(identity: Identity, filter?: string) {
    if (filter !== undefined && !individualJid(filter)) throw new Blocked("INVALID_REQUEST");
    await this.#authority.authorize("chats", identity);
    const record = this.#record(identity);
    const chats = filter ? [record.chats.get(filter)].filter((row): row is ChatMetadata => Boolean(row)) : [...record.chats.values()].slice(0, 200);
    return { ...this.#view(record), chats };
  }
  async disconnect(identity: Identity) {
    await this.#authority.authorize("disconnect", identity);
    // Python commits revocation, advances the fence and erases credentials first.
    // A current authorized owner may close its older local socket, but may never
    // use the older store to mutate the replacement fence's credentials.
    const record = this.#records.get(identity.connector_id);
    const response = { schema_version: 1 as const, state: "disconnected" as const,
      account_id: identity.account_id, provider_unlink_verified: false };
    if (!record) return response;
    if (record.identity.workspace_id !== identity.workspace_id ||
        record.identity.connector_fence > identity.connector_fence) throw new Blocked("SESSION_NOT_FOUND");
    record.generation += 1; record.qr = null; record.chats.clear(); record.state = "disconnected";
    // Revocation is locally final even if provider logout is unreachable.
    const socket = record.socket;
    try {
      if (socket) {
        let timer: ReturnType<typeof setTimeout> | undefined;
        try { await Promise.race([socket.logout("owner_disconnect"), new Promise<void>(resolve => {
          timer = setTimeout(resolve, 1500); timer.unref();
        })]); } catch { /* Local revocation remains final; provider unlink is unverified. */ }
        finally { if (timer) clearTimeout(timer); }
      }
      if (record.store && record.identity.connector_fence === identity.connector_fence) await record.store.clear();
    } finally { await this.#stop(record, "disconnected"); }
    // No stale callback: Python has already committed the revoked session state.
    return response;
  }
  async send(envelope: SendEnvelope) {
    await this.#authority.authorize("send", envelope, envelope);
    const key = `${envelope.workspace_id}:${envelope.connector_id}:${envelope.attempt_id}`;
    const fingerprint = createHash("sha256").update(JSON.stringify([envelope.workspace_id, envelope.connector_id,
      envelope.connector_fence, envelope.account_id, envelope.conversation_id, envelope.recipient_id,
      envelope.draft_id, envelope.attempt_id, envelope.payload_hash])).digest("hex");
    for (const [id, row] of this.#sends) if (row.done && row.expires <= this.#clock()) this.#sends.delete(id);
    const old = this.#sends.get(key);
    if (old) {
      if (old.fingerprint !== fingerprint) throw new Blocked("ATTEMPT_CHANGED");
      return old.promise;
    }
    if (this.#sends.size >= 512) throw new Blocked("GATEWAY_BUSY");
    const row = { fingerprint, promise: Promise.resolve({}) as Promise<Record<string, unknown>>, expires: this.#clock() + 300_000, done: false };
    row.promise = this.#sendOnce(envelope).finally(() => { row.done = true; });
    this.#sends.set(key, row);
    return row.promise;
  }
  async #sendOnce(envelope: SendEnvelope) {
    const record = this.#record(envelope);
    if (createHash("sha256").update(envelope.text, "utf8").digest("hex") !== envelope.payload_hash) throw new Blocked("PAYLOAD_CHANGED");
    if (record.state !== "connected" || !record.socket || !record.store) throw new Blocked("SESSION_DISCONNECTED");
    await record.store.assertLive();
    await this.#authority.authorize("send", envelope, envelope);
    const providerId = generateMessageIDV2(record.socket.user?.id);
    const receipt = { attempt_id: envelope.attempt_id, draft_id: envelope.draft_id, payload_hash: envelope.payload_hash,
      provider_message_id: providerId };
    // Durable preallocated ID must be committed by Python before the socket call.
    await this.#authority.event({ ...record.identity, event_type: "receipt", data: { ...receipt, status: "submitting" } });
    await record.store.assertLive();
    await this.#authority.authorize("send", envelope, envelope);
    let status: "accepted" | "uncertain" = "uncertain";
    try {
      const sendOptions = { messageId: providerId, getUrlInfo: async () => undefined };
      const result = await record.socket.sendMessage(envelope.recipient_id, { text: envelope.text, linkPreview: null }, sendOptions);
      if (result?.key.id === providerId) status = "accepted";
    } catch { /* Socket exceptions never prove a provider did not accept the message. */ }
    try { await this.#authority.event({ ...record.identity, event_type: "receipt", data: { ...receipt, status } }); }
    catch { status = "uncertain"; }
    return { schema_version: 1, simulation: false, status, provider_message_id: providerId,
      attempt_id: envelope.attempt_id, draft_id: envelope.draft_id, payload_hash: envelope.payload_hash,
      connector_fence: envelope.connector_fence };
  }
  async #connect(record: RecordState) {
    if (!record.store) throw new Blocked("SESSION_DISCONNECTED");
    await record.store.assertLive();
    await this.#authority.authorize("start", record.identity);
    if (this.#closed || !record.store) throw new Blocked("SESSION_DISCONNECTED");
    const generation = ++record.generation;
    const socket = this.#factory({
      auth: record.store.state, browser: Browsers.ubuntu("Milo"), logger: silentLogger,
      markOnlineOnConnect: false, syncFullHistory: false, shouldSyncHistoryMessage: () => false,
      maxMsgRetryCount: 0, enableRecentMessageCache: false, enableAutoSessionRecreation: false,
      getMessage: async () => undefined, shouldIgnoreJid: jid => !individualJid(canonicalJid(jid)),
      cachedGroupMetadata: async () => undefined, connectTimeoutMs: 20_000, defaultQueryTimeoutMs: 15_000,
      qrTimeout: 45_000,
    });
    record.socket = socket;
    const current = () => record.generation === generation && record.socket === socket;
    socket.ev.on("creds.update", (update: Partial<AuthenticationCreds>) => {
      if (!current()) return;
      this.#queue(record, async () => { if (!current() || !record.store) return;
        Object.assign(record.store.state.creds, update); await record.store.saveCreds(); });
    });
    socket.ev.on("connection.update", update => {
      if (!current()) return;
      this.#queue(record, async () => {
        if (!current()) return;
        if (update.qr) {
          if (update.qr.length > 4096) throw new Blocked("INVALID_QR");
          record.state = "qr"; record.qr = { value: update.qr, expires_at: new Date(this.#clock() + 45_000).toISOString() };
          if (record.pairingPhone && !record.pairingRequested && record.store && !record.store.state.creds.registered) {
            record.pairingRequested = true;
            const code = await socket.requestPairingCode(record.pairingPhone);
            if (!current()) return;
            if (!/^[A-Z0-9]{8}$/.test(code)) throw new Blocked("INVALID_PAIRING_CODE");
            record.pairing = { code, expires_at: new Date(this.#clock() + 160_000).toISOString() };
          }
        }
        if (update.connection === "open") {
          const account = canonicalJid(socket.user?.id ?? "");
          if (!individualJid(account)) throw new Blocked("INVALID_ACCOUNT_IDENTITY");
          const aliases = [...new Set([account, canonicalJid(socket.user?.lid ?? "")].filter(individualJid))];
          const connected = { ...record.identity, account_id: account };
          record.qr = null; record.pairing = null;
          if (record.pairingPhone && !aliases.includes(`${record.pairingPhone}@s.whatsapp.net`)) {
            // A relayed code or QR completed by a different account is unlinked, never bound.
            await this.#logout(socket); if (record.store) await record.store.clear();
            await this.#authority.event({ ...record.identity, event_type: "connection", data: { state: "failed", account_id: record.identity.account_id } });
            throw new Blocked("ACCOUNT_MISMATCH");
          }
          // Python verifies global account ownership before message processing becomes eligible.
          try { await this.#authority.event({ ...record.identity, event_type: "connection", data: { state: "connected", account_id: account, account_aliases: aliases } }); }
          catch (error) { await this.#logout(socket); throw error; }
          record.identity = connected; record.state = "connected"; record.reconnects = 0;
          if (record.store) await record.store.saveCreds();
        }
        if (update.connection === "close") {
          record.qr = null; record.pairing = null;
          const error = update.lastDisconnect?.error as { output?: { statusCode?: number } } | undefined;
          const code = error?.output?.statusCode;
          if (code === DisconnectReason.loggedOut || code === DisconnectReason.connectionReplaced || code === DisconnectReason.badSession) {
            if (record.store) await record.store.clear(); await this.#stop(record, "logged_out");
            await this.#authority.event({ ...record.identity, event_type: "connection", data: { state: "logged_out", account_id: record.identity.account_id } });
            return;
          }
          if (++record.reconnects > 6) { await this.#stop(record, "failed"); return; }
          record.state = "reconnecting";
          await this.#authority.event({ ...record.identity, event_type: "connection", data: { state: "reconnecting", account_id: record.identity.account_id } });
          const delay = code === DisconnectReason.restartRequired ? 10 : Math.min(30_000, 1000 * 2 ** (record.reconnects - 1)) + Math.floor(Math.random() * 250);
          record.timer = setTimeout(() => { record.timer = null; this.#queue(record, () => this.#connect(record)); }, delay);
          record.timer.unref();
        }
      });
    });
    socket.ev.on("chats.upsert", (chats: Chat[]) => { if (current()) this.#metadata(record, chats.map(row => ({ id: row.id ?? "", name: row.name ?? undefined }))); });
    socket.ev.on("contacts.upsert", (contacts: Contact[]) => { if (current()) this.#metadata(record, contacts.map(row => ({ id: row.id, name: row.name ?? row.notify }))); });
    socket.ev.on("messages.upsert", event => {
      if (!current()) return;
      for (const message of event.messages.slice(0, 100)) {
        this.#metadata(record, [{ id: message.key.remoteJid ?? "", name: undefined }]);
        this.#queue(record, async () => { if (current()) await this.#message(record, message, event.type === "notify" ? "live" : "replay"); });
      }
    });
    socket.ev.on("messaging-history.set", event => {
      if (!current()) return;
      this.#metadata(record, event.chats.map(row => ({ id: row.id ?? "", name: row.name ?? undefined })));
      // Defensive handling for SDK history events. History never creates a live automation trigger.
      for (const message of event.messages.slice(0, 100)) this.#queue(record, async () => { if (current()) await this.#message(record, message, "history"); });
    });
    socket.ev.on("message-receipt.update", receipts => {
      if (!current()) return;
      for (const receipt of receipts.slice(0, 100)) if (receipt.key.fromMe && receipt.key.id && receipt.receipt.receiptTimestamp) {
        this.#queue(record, async () => { if (!current()) return; await this.#authority.authorize("ingest", record.identity);
          await this.#authority.event({ ...record.identity, event_type: "receipt", data: { provider_message_id: receipt.key.id, status: "delivered" } }); });
      }
    });
  }
  #metadata(record: RecordState, rows: readonly { id: string; name: string | undefined }[]) {
    for (const row of rows.slice(0, 200)) {
      const id = canonicalJid(row.id);
      if (!individualJid(id) || (!record.chats.has(id) && record.chats.size >= 200)) continue;
      record.chats.set(id, Object.freeze({ provider_chat_id: id, title: (row.name ?? record.chats.get(id)?.title ?? id).slice(0, 160), kind: "contact" }));
    }
  }
  async #message(record: RecordState, message: WAMessage, origin: "live" | "history" | "replay") {
    if (record.state !== "connected" || !record.store || !message.key.id) return;
    const jid = canonicalJid(message.key.remoteJid ?? "");
    if (!individualJid(jid)) return;
    await record.store.assertLive();
    const authority = await this.#authority.authorize("ingest", record.identity);
    const grant = authority.grants.find(row => row.provider_chat_id === jid && row.read && row.retain);
    if (!grant) return;
    // Keep the pilot text-only. No download, link fetch, view-once or ephemeral wrapper extraction.
    const text = message.message?.conversation ?? message.message?.extendedTextMessage?.text;
    if (!text || text.length > 4096) return;
    const timestamp = Number(message.messageTimestamp);
    if (!Number.isFinite(timestamp) || timestamp <= 0 || timestamp > this.#clock() / 1000 + 60) return;
    await this.#authority.event({ ...record.identity, event_type: "message", data: {
      conversation_id: grant.conversation_id, provider_message_id: message.key.id, provider_chat_id: jid,
      sender_id: message.key.fromMe ? record.identity.account_id : jid,
      direction: message.key.fromMe ? "outbound" : "inbound", origin, event_type: "message.created",
      author_kind: message.key.fromMe ? "unknown_owner_outgoing" : "contact_human",
      provider_timestamp: new Date(timestamp * 1000).toISOString(), content: { type: "text", text }, source_revision: 1,
    } });
  }
  async #logout(socket: WASocket) {
    let timer: ReturnType<typeof setTimeout> | undefined;
    try { await Promise.race([socket.logout("account_not_authorized"), new Promise<void>(resolve => {
      timer = setTimeout(resolve, 1500); timer.unref();
    })]); } catch { /* The local socket still ends; provider unlink stays unverified. */ }
    finally { if (timer) clearTimeout(timer); }
  }
  async #stop(record: RecordState, state: State) {
    record.generation += 1; record.qr = null; record.pairing = null; record.state = state;
    if (record.timer) { clearTimeout(record.timer); record.timer = null; }
    if (record.authorityTimer) { clearInterval(record.authorityTimer); record.authorityTimer = null; }
    const socket = record.socket; record.socket = null;
    if (socket) { void socket.end(new Error("session_stopped")).catch(() => {}); }
    const store = record.store; record.store = null;
    if (store) await store.release();
  }
  /** Internal queue barrier for deterministic shutdown and synthetic HTTP checks. */
  async settlePendingEvents() {
    while ([...this.#records.values()].some(row => row.pending > 0)) {
      await Promise.all([...this.#records.values()].map(row => row.tail));
    }
  }
  async close() { this.#closed = true; await Promise.all([...this.#records.values()].map(row => this.#stop(row, "disconnected"))); this.#records.clear(); this.#sends.clear(); }
}
