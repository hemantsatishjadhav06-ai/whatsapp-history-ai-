import { createHash, randomUUID } from "node:crypto";
import makeWASocket, { DisconnectReason, generateMessageIDV2, proto } from "@whiskeysockets/baileys";
import type { WASocket, UserFacingSocketConfig, WAMessage, AuthenticationCreds, Chat, Contact } from "@whiskeysockets/baileys";
import type { AuthStore, AuthStoreFactory } from "./storage.ts";
import type { AuthorityClient } from "./authority.ts";
import { Blocked, Denied, canonicalJid, individualJid } from "./protocol.ts";
import type { BackfillReport, BackfillTarget, Identity, SendEnvelope, SyncChat, SyncMessage, SyncOrigin, SyncProgress } from "./protocol.ts";
import { convertMessage, isoSeconds } from "./convert.ts";
import type { ChatAddress } from "./convert.ts";
import { SyncPump } from "./sync.ts";
import type { PumpLog } from "./sync.ts";

/** No SDK log argument is serialized, including QR, credentials, JIDs, text, or errors. */
export const silentLogger = Object.freeze({ level: "silent", child() { return silentLogger; },
  trace() {}, debug() {}, info() {}, warn() {}, error() {} });
export type SocketFactory = (options: UserFacingSocketConfig) => WASocket;
type State = "starting" | "qr" | "connected" | "reconnecting" | "disconnected" | "logged_out" | "failed";
type ChatMetadata = Readonly<{ provider_chat_id: string; title: string; kind: "contact" }>;
type OnDemand = { jid: string; count: number; resolve: (count: number | null) => void };
type RecordState = { identity: Identity; state: State; store: AuthStore | null; socket: WASocket | null;
  generation: number; qr: { value: string; expires_at: string } | null; chats: Map<string, ChatMetadata>;
  pairingPhone: string | null; pairingRequested: boolean; pairing: { code: string; expires_at: string } | null;
  tail: Promise<void>; pending: number; reconnects: number; timer: ReturnType<typeof setTimeout> | null;
  authorityTimer: ReturnType<typeof setInterval> | null; beating: boolean;
  pump: SyncPump | null; ingest: Promise<void>; owner: Set<string>;
  lidToPn: Map<string, string>; addressing: Map<string, string>; sent: Set<string>;
  historyAt: number; onDemand: OnDemand | null; misses: Map<string, number> };
const HISTORY_PHASE: Record<number, SyncProgress["phase"]> = {
  [proto.HistorySync.HistorySyncType.INITIAL_BOOTSTRAP]: "initial", [proto.HistorySync.HistorySyncType.RECENT]: "recent",
  [proto.HistorySync.HistorySyncType.FULL]: "full", [proto.HistorySync.HistorySyncType.ON_DEMAND]: "on_demand",
  [proto.HistorySync.HistorySyncType.PUSH_NAME]: "push_name" };
/** Desktop companions receive the phone's full history; Baileys reads the platform from the middle element. */
export const LINKED_DEVICE_BROWSER: [string, string, string] = ["Milo", "Desktop", "1.0.0"];
const FULL_HISTORY_CONFIG = { storageQuotaMb: 10240, inlineInitialPayloadInE2EeMsg: true, supportCallLogHistory: false,
  supportBotUserAgentChatHistory: true, supportCagReactionsAndPolls: true, supportBizHostedMsg: true,
  supportRecentSyncChunkMessageCountTuning: true, supportHostedGroupMsg: true, supportFbidBotChatHistory: true,
  supportMessageAssociation: true, supportGroupHistory: false };
const TITLE_SOURCES = ["contact", "verified", "chat", "push"] as const;

export type SessionTiming = { heartbeatMs: number; backfillStartMs: number; backfillIdleMs: number;
  backfillGapMs: number; backfillWaitMs: number; backfillRetryMs: number; fullHistoryAfterMs: number };
const DEFAULT_TIMING: SessionTiming = { heartbeatMs: 5000, backfillStartMs: 120_000, backfillIdleMs: 90_000,
  backfillGapMs: 3000, backfillWaitMs: 45_000, backfillRetryMs: 300_000, fullHistoryAfterMs: 120_000 };

export class Sessions {
  #records = new Map<string, RecordState>();
  #sends = new Map<string, { fingerprint: string; promise: Promise<Record<string, unknown>>; expires: number; done: boolean }>();
  #authority: AuthorityClient; #stores: AuthStoreFactory; #factory: SocketFactory;
  #limit: number; #enabled: boolean; #clock: () => number; #log: PumpLog; #timing: SessionTiming;
  #closed = false;
  constructor(options: { authority: AuthorityClient; stores: AuthStoreFactory; socketFactory?: SocketFactory;
    enabled?: boolean; maxSessions?: number; clock?: () => number; log?: PumpLog; timing?: Partial<SessionTiming> }) {
    this.#authority = options.authority; this.#stores = options.stores;
    this.#factory = options.socketFactory ?? (config => makeWASocket(config));
    this.#limit = options.maxSessions ?? 20; this.#enabled = options.enabled ?? false;
    this.#clock = options.clock ?? Date.now;
    this.#log = options.log ?? (() => {});
    this.#timing = { ...DEFAULT_TIMING, ...options.timing };
    if (!Number.isSafeInteger(this.#limit) || this.#limit < 1 || this.#limit > 250) throw new Error("Pilot capacity must be 1..250");
  }
  #record(identity: Identity): RecordState {
    const record = this.#records.get(identity.connector_id);
    if (!record || record.identity.workspace_id !== identity.workspace_id || record.identity.connector_fence !== identity.connector_fence ||
        (identity.account_id !== null && identity.account_id !== record.identity.account_id)) throw new Blocked("SESSION_NOT_FOUND");
    return record;
  }
  #tag(record: RecordState): string {
    return createHash("sha256").update(record.identity.connector_id).digest("hex").slice(0, 8);
  }
  /** Control work (connection, credentials) stays strictly ordered and fails closed. Content never runs here. */
  #queue(record: RecordState, work: () => Promise<void>): void {
    if (record.pending >= 1000) { void this.#stop(record, "failed"); return; }
    record.pending += 1;
    record.tail = record.tail.then(work).catch(error => {
      this.#log("session_control_failed", { session: this.#tag(record),
        reason: error instanceof Blocked ? error.reason_code : "unexpected" });
      return this.#stop(record, "failed");
    }).finally(() => { record.pending -= 1; });
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
      tail: Promise.resolve(), pending: 0, reconnects: 0, timer: null, authorityTimer: null, beating: false,
      pump: null, ingest: Promise.resolve(), owner: new Set(), lidToPn: new Map(), addressing: new Map(),
      sent: new Set(), historyAt: 0, onDemand: null, misses: new Map() };
    this.#records.set(identity.connector_id, record);
    try {
      record.store = await this.#stores(identity, () => { void this.#stop(record, "failed"); });
      if (this.#closed) throw new Blocked("SESSION_DISCONNECTED");
      record.pump = this.#authority.sync ? new SyncPump({ identity: () => record.identity,
        post: batch => this.#authority.sync!(batch), log: this.#log,
        onRefused: () => { void this.#heartbeat(record); } }) : null;
      // Independent of message volume: a busy history import can never starve the lease.
      record.authorityTimer = setInterval(() => { void this.#heartbeat(record); }, this.#timing.heartbeatMs);
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
    record.sent.add(providerId);
    if (record.sent.size > 2000) record.sent.delete(record.sent.values().next().value as string);
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
  /** Lease renewal and revocation checks, outside the content path. Transient failures never end the socket. */
  async #heartbeat(record: RecordState): Promise<void> {
    if (record.beating || !record.store || ["failed", "disconnected", "logged_out"].includes(record.state)) return;
    record.beating = true;
    try {
      try { await record.store.assertLive(); }
      catch { await this.#stop(record, "failed"); return; }
      try { await this.#authority.authorize(record.state === "connected" ? "status" : "start", record.identity); }
      catch (error) {
        if (error instanceof Denied && error.denial === "lease_expired" && record.state === "connected") {
          // A gap (deploy, network) expired the SQL lease: re-announce; Python applies its gap review.
          await this.#announce(record).catch(() => {});
        } else if (error instanceof Blocked && error.reason_code === "AUTHORITY_UNAVAILABLE") {
          this.#log("authority_unavailable", { session: this.#tag(record) });
        } else {
          this.#log("authority_revoked", { session: this.#tag(record),
            reason: error instanceof Denied ? error.denial : error instanceof Blocked ? error.reason_code : "unexpected" });
          await this.#stop(record, "failed");
        }
      }
    } finally { record.beating = false; }
  }
  #aliases(socket: WASocket): { account: string; aliases: string[] } {
    const account = canonicalJid(socket.user?.id ?? "");
    const aliases = [...new Set([account, canonicalJid(socket.user?.lid ?? "")].filter(individualJid))];
    return { account, aliases };
  }
  async #announce(record: RecordState) {
    const socket = record.socket; if (!socket || record.state !== "connected") return;
    const { account, aliases } = this.#aliases(socket);
    await this.#authority.event({ ...record.identity, event_type: "connection",
      data: { state: "connected", account_id: account, account_aliases: aliases } });
  }
  async #connect(record: RecordState) {
    if (!record.store) throw new Blocked("SESSION_DISCONNECTED");
    await record.store.assertLive();
    await this.#authority.authorize("start", record.identity);
    if (this.#closed || !record.store) throw new Blocked("SESSION_DISCONNECTED");
    const generation = ++record.generation;
    const socket = this.#factory({
      auth: record.store.state, browser: LINKED_DEVICE_BROWSER, logger: silentLogger,
      // Full history from the phone, all one-to-one chats. Decryption retries stay at SDK defaults so
      // "waiting for this message" recovers; getMessage returns nothing, so no blind resend is possible.
      markOnlineOnConnect: false, syncFullHistory: true, shouldSyncHistoryMessage: () => true,
      enableRecentMessageCache: false, getMessage: async () => undefined,
      shouldIgnoreJid: jid => !individualJid(canonicalJid(jid)),
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
          const { account, aliases } = this.#aliases(socket);
          if (!individualJid(account)) throw new Blocked("INVALID_ACCOUNT_IDENTITY");
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
          record.owner = new Set(aliases);
          if (record.store) await record.store.saveCreds();
          this.#log("session_open", { session: this.#tag(record) });
          void this.#backfill(record, generation);
          void this.#requestFullHistory(record, generation);
        }
        if (update.connection === "close") {
          record.qr = null; record.pairing = null; record.onDemand?.resolve(null);
          const error = update.lastDisconnect?.error as { output?: { statusCode?: number } } | undefined;
          const code = error?.output?.statusCode;
          this.#log("session_close", { session: this.#tag(record), code: code ?? 0 });
          if (code === DisconnectReason.loggedOut || code === DisconnectReason.connectionReplaced || code === DisconnectReason.badSession) {
            if (record.store) await record.store.clear(); await this.#stop(record, "logged_out");
            await this.#authority.event({ ...record.identity, event_type: "connection", data: { state: "logged_out", account_id: record.identity.account_id } });
            return;
          }
          if (code === DisconnectReason.forbidden) { await this.#stop(record, "failed"); return; }
          record.reconnects += 1;
          // An unpaired socket that keeps failing is abandoned; a linked account keeps retrying with a capped backoff.
          if (!record.store?.state.creds.registered && record.reconnects > 6) { await this.#stop(record, "failed"); return; }
          record.state = "reconnecting";
          try {
            await this.#authority.event({ ...record.identity, event_type: "connection", data: { state: "reconnecting", account_id: record.identity.account_id } });
          } catch { /* Python unreachable: keep reconnecting; the heartbeat rechecks revocation. */ }
          const delay = code === DisconnectReason.restartRequired ? 10 :
            Math.min(300_000, 1000 * 2 ** Math.min(record.reconnects - 1, 9)) + Math.floor(Math.random() * 250);
          record.timer = setTimeout(() => { record.timer = null; this.#queue(record, () => this.#connect(record)); }, delay);
          record.timer.unref();
        }
      });
    });
    socket.ev.on("lid-mapping.update", mapping => {
      if (!current() || typeof mapping?.lid !== "string" || typeof mapping.pn !== "string") return;
      const lid = canonicalJid(mapping.lid), pn = canonicalJid(mapping.pn);
      if (lid.endsWith("@lid") && pn.endsWith("@s.whatsapp.net")) record.lidToPn.set(lid, pn);
    });
    socket.ev.on("chats.upsert", (chats: Chat[]) => { if (current()) this.#after(record, async () => this.#chatRows(record, chats, true)); });
    socket.ev.on("chats.update", (chats: Partial<Chat>[]) => { if (current()) this.#after(record, async () => this.#chatRows(record, chats, true)); });
    socket.ev.on("contacts.upsert", (contacts: Contact[]) => { if (current()) this.#after(record, async () => this.#contactRows(record, contacts)); });
    socket.ev.on("contacts.update", (contacts: Partial<Contact>[]) => { if (current()) this.#after(record, async () => this.#contactRows(record, contacts)); });
    socket.ev.on("messages.upsert", event => {
      if (!current()) return;
      this.#ingest(record, event.type === "notify" ? "live" : "replay", event.messages);
    });
    socket.ev.on("messaging-history.set", event => {
      if (!current()) return;
      record.historyAt = this.#clock();
      for (const mapping of event.lidPnMappings ?? []) {
        const lid = canonicalJid(mapping.lid), pn = canonicalJid(mapping.pn);
        if (lid.endsWith("@lid") && pn.endsWith("@s.whatsapp.net")) record.lidToPn.set(lid, pn);
      }
      this.#after(record, async () => { this.#chatRows(record, event.chats ?? [], false); this.#contactRows(record, event.contacts ?? []); });
      const onDemand = event.syncType === proto.HistorySync.HistorySyncType.ON_DEMAND;
      this.#ingest(record, onDemand ? "backfill" : "history", event.messages ?? [], rows => {
        if (onDemand && record.onDemand) {
          const pending = record.onDemand; record.onDemand = null;
          pending.resolve(rows.filter(row => row.chat_jid === pending.jid || row.chat_alt_jid === pending.jid).length);
        }
      });
      const phase = HISTORY_PHASE[event.syncType ?? -1];
      const percent = typeof event.progress === "number" ? { percent: Math.max(0, Math.min(100, event.progress)) } : {};
      if (phase) this.#after(record, async () => { record.pump?.progress({ phase, ...percent }); });
    });
    socket.ev.on("messaging-history.status", event => {
      if (!current() || event.status !== "complete") return;
      this.#after(record, async () => { record.pump?.progress({ phase: "complete", percent: 100 }); });
    });
    // Delivery receipts only for messages this service sent; the send ledger stays authoritative.
    socket.ev.on("messages.update", updates => {
      if (!current()) return;
      for (const row of updates.slice(0, 500)) {
        if (row.key.fromMe && typeof row.update.status === "number" && row.update.status >= 3) this.#delivered(record, row.key.id);
      }
    });
    socket.ev.on("message-receipt.update", receipts => {
      if (!current()) return;
      for (const row of receipts.slice(0, 500)) if (row.key.fromMe && row.receipt.receiptTimestamp) this.#delivered(record, row.key.id);
    });
  }
  #delivered(record: RecordState, id: string | null | undefined) {
    if (!id || !record.sent.has(id)) return;
    record.sent.delete(id);
    this.#after(record, async () => {
      await this.#authority.event({ ...record.identity, event_type: "receipt", data: { provider_message_id: id, status: "delivered" } })
        .catch(() => { /* Advisory: an unrecorded receipt never changes what was sent. */ });
    });
  }
  /** The canonical chat for an SDK address: phone-number JID when known, with the LID as its alias. */
  #address(record: RecordState, jid: string, alt?: string): ChatAddress {
    if (jid.endsWith("@lid")) {
      const pn = alt?.endsWith("@s.whatsapp.net") ? alt : record.lidToPn.get(jid);
      if (pn) { record.lidToPn.set(jid, pn); record.addressing.set(pn, jid); return { jid: pn, alt: jid }; }
      record.addressing.set(jid, jid);
      return { jid };
    }
    const lid = alt?.endsWith("@lid") ? alt : undefined;
    if (lid) record.lidToPn.set(lid, jid);
    if (!record.addressing.has(jid)) record.addressing.set(jid, jid);
    return lid ? { jid, alt: lid } : { jid };
  }
  async #resolveLids(record: RecordState, jids: Iterable<string>) {
    const unknown = [...new Set([...jids].filter(jid => jid.endsWith("@lid") && !record.lidToPn.has(jid)))].slice(0, 500);
    const repository = record.socket?.signalRepository as { lidMapping?: { getPNsForLIDs?: (lids: string[]) => Promise<{ lid: string; pn: string }[] | null> } } | undefined;
    if (!unknown.length || !repository?.lidMapping?.getPNsForLIDs) return;
    try {
      for (const pair of (await repository.lidMapping.getPNsForLIDs(unknown)) ?? []) {
        const lid = canonicalJid(pair.lid), pn = canonicalJid(pair.pn);
        if (lid.endsWith("@lid") && pn.endsWith("@s.whatsapp.net")) record.lidToPn.set(lid, pn);
      }
    } catch { /* Unmapped LIDs stay their own chat address until WhatsApp shares the number. */ }
  }
  /**
   * Content path: ordered and never fatal to the session. Work waits for control events already
   * queued (so "open" binds the account before its first messages), then runs outside that queue.
   */
  #after(record: RecordState, work: () => Promise<void>, failed?: () => void) {
    const gate = record.tail;
    record.ingest = record.ingest.then(() => gate).then(async () => {
      if (record.state !== "connected" || !record.pump) { failed?.(); return; }
      await work();
    }).catch(() => { this.#log("sync_convert_failed", { session: this.#tag(record) }); failed?.(); });
  }
  #ingest(record: RecordState, origin: SyncOrigin, messages: readonly WAMessage[], after?: (rows: SyncMessage[]) => void) {
    this.#after(record, async () => {
      await this.#resolveLids(record, messages.map(message => canonicalJid(message.key?.remoteJid ?? "")));
      const rows: SyncMessage[] = [];
      const now = this.#clock();
      for (const message of messages) {
        const row = convertMessage(message, (jid, alt) => this.#address(record, jid, alt), now);
        if (row && !record.owner.has(row.chat_jid) && !(row.chat_alt_jid && record.owner.has(row.chat_alt_jid))) rows.push(row);
      }
      record.pump?.push(origin, rows);
      after?.(rows);
    }, () => after?.([]));
  }
  #title(row: Partial<Contact & Chat>): Pick<SyncChat, "title" | "title_source"> {
    const value = (row as Contact).name ? { title: (row as Contact).name, title_source: "contact" as const } :
      (row as Contact).verifiedName ? { title: (row as Contact).verifiedName, title_source: "verified" as const } :
      (row as Contact).notify ? { title: (row as Contact).notify, title_source: "push" as const } : {};
    return value.title ? { title: value.title.slice(0, 160), title_source: value.title_source } : {};
  }
  #emitChats(record: RecordState, rows: SyncChat[]) {
    for (const row of rows) {
      if (row.title && (record.chats.has(row.jid) || record.chats.size < 200)) {
        record.chats.set(row.jid, Object.freeze({ provider_chat_id: row.jid, title: row.title, kind: "contact" }));
      } else if (!record.chats.has(row.jid) && record.chats.size < 200) {
        record.chats.set(row.jid, Object.freeze({ provider_chat_id: row.jid, title: row.jid, kind: "contact" }));
      }
    }
    record.pump?.upsertChats(rows);
  }
  #canonical(record: RecordState, id: string | null | undefined, lid?: string | null, pn?: string | null): ChatAddress | null {
    const jid = canonicalJid(id ?? "");
    if (!individualJid(jid)) return null;
    const altLid = lid ? canonicalJid(lid) : undefined, altPn = pn ? canonicalJid(pn) : undefined;
    if (altLid?.endsWith("@lid") && jid.endsWith("@s.whatsapp.net")) record.lidToPn.set(altLid, jid);
    if (altPn?.endsWith("@s.whatsapp.net") && jid.endsWith("@lid")) record.lidToPn.set(jid, altPn);
    const address = this.#address(record, jid, jid.endsWith("@lid") ? altPn : altLid);
    return record.owner.has(address.jid) || (address.alt && record.owner.has(address.alt)) ? null : address;
  }
  /**
   * History chat lists name and create chats, with absolute unread counts and WhatsApp's last activity.
   * Live chat events only update existing chats: their positive unread counts are per-message increments
   * (live messages are counted where they are stored), 0 means read on the phone and -1 marked unread.
   */
  #chatRows(record: RecordState, chats: readonly Partial<Chat>[], live: boolean) {
    const rows: SyncChat[] = [];
    const now = this.#clock();
    for (const chat of chats.slice(0, 5000)) {
      const raw = chat as Partial<Chat> & { lidJid?: string | null; pnJid?: string | null; displayName?: string | null };
      const address = this.#canonical(record, raw.id, raw.lidJid, raw.pnJid);
      if (!address) continue;
      const name = raw.displayName || raw.name;
      const count = typeof raw.unreadCount === "number" ? raw.unreadCount : undefined;
      const unread = count === undefined || (live && count > 0) ? undefined : count < 0 ? 1 : count;
      const activity = live ? undefined : isoSeconds(raw.conversationTimestamp, now);
      if (live && !name && unread === undefined && typeof raw.archived !== "boolean") continue;
      rows.push({ jid: address.jid, ...(address.alt ? { alt_jid: address.alt } : {}),
        ...(name ? { title: name.slice(0, 160), title_source: "chat" as const } : {}),
        ...(unread !== undefined ? { unread_count: Math.min(unread, 1_000_000) } : {}),
        ...(typeof raw.archived === "boolean" ? { archived: raw.archived } : {}),
        ...(activity ? { last_activity_at: activity } : {}), ...(live ? { contact_only: true } : {}) });
    }
    this.#emitChats(record, rows);
  }
  #contactRows(record: RecordState, contacts: readonly Partial<Contact>[]) {
    const rows: SyncChat[] = [];
    for (const contact of contacts.slice(0, 5000)) {
      const raw = contact as Partial<Contact> & { phoneNumber?: string | null };
      const address = this.#canonical(record, raw.id, raw.lid, raw.phoneNumber);
      if (!address) continue;
      const title = this.#title(raw);
      if (!title.title || !TITLE_SOURCES.includes(title.title_source!)) continue;
      rows.push({ jid: address.jid, ...(address.alt ? { alt_jid: address.alt } : {}), ...title, contact_only: true });
    }
    this.#emitChats(record, rows);
  }
  async #sleep(record: RecordState, generation: number, ms: number): Promise<boolean> {
    await new Promise<void>(resolve => { const timer = setTimeout(resolve, ms); timer.unref(); });
    return record.generation === generation && record.state === "connected" && !this.#closed && Boolean(record.socket);
  }
  /** Older history, newest chats first: one on-demand page at a time from the phone, paced. */
  async #backfill(record: RecordState, generation: number) {
    const timing = this.#timing; let reports: BackfillReport[] = [];
    if (!this.#authority.backfill || !await this.#sleep(record, generation, timing.backfillStartMs)) return;
    while (record.generation === generation && record.state === "connected" && !this.#closed) {
      const busy = this.#clock() - record.historyAt < timing.backfillIdleMs || (record.pump?.buffered ?? 0) > 2000;
      if (busy) { if (!await this.#sleep(record, generation, 15_000)) return; continue; }
      const answer = await this.#authority.backfill({ ...record.identity, reports, limit: 5 });
      if (answer.status !== 200) { if (!await this.#sleep(record, generation, 60_000)) return; continue; }
      reports = [];
      const targets = this.#targets(answer.body);
      if (!targets.length) { if (!await this.#sleep(record, generation, timing.backfillRetryMs * 2)) return; continue; }
      for (const target of targets) {
        const outcome = await this.#older(record, generation, target);
        if (!outcome) return;
        reports.push({ jid: target.jid, outcome });
        if (outcome === "failed") { if (!await this.#sleep(record, generation, timing.backfillRetryMs)) return; break; }
        if (!await this.#sleep(record, generation, timing.backfillGapMs)) return;
      }
    }
  }
  #targets(body: unknown): BackfillTarget[] {
    const rows = (body as { targets?: unknown } | null)?.targets;
    if (!Array.isArray(rows)) return [];
    return rows.slice(0, 50).filter((row): row is BackfillTarget => Boolean(row) && typeof row === "object" &&
      individualJid((row as BackfillTarget).jid) && typeof (row as BackfillTarget).oldest_id === "string" &&
      (row as BackfillTarget).oldest_id.length <= 180 && typeof (row as BackfillTarget).oldest_from_me === "boolean" &&
      Number.isFinite(Date.parse((row as BackfillTarget).oldest_at)));
  }
  async #older(record: RecordState, generation: number, target: BackfillTarget): Promise<BackfillReport["outcome"] | null> {
    const socket = record.socket;
    if (!socket || record.generation !== generation) return null;
    const waiter = new Promise<number | null>(resolve => {
      const timer = setTimeout(() => { if (record.onDemand?.resolve === settle) record.onDemand = null; resolve(null); },
        this.#timing.backfillWaitMs);
      timer.unref();
      const settle = (count: number | null) => { clearTimeout(timer); resolve(count); };
      record.onDemand = { jid: target.jid, count: 0, resolve: settle };
    });
    try {
      await socket.fetchMessageHistory(50, { remoteJid: record.addressing.get(target.jid) ?? target.jid,
        id: target.oldest_id, fromMe: target.oldest_from_me }, Date.parse(target.oldest_at));
    } catch { record.onDemand?.resolve(null); }
    const count = await waiter;
    if (count === null) {
      // Some phones answer an exhausted chat with silence: two misses end that chat's backfill.
      const misses = (record.misses.get(target.jid) ?? 0) + 1; record.misses.set(target.jid, misses);
      return misses >= 2 ? "exhausted" : "failed";
    }
    record.misses.delete(target.jid);
    this.#log("backfill_page", { session: this.#tag(record), messages: count });
    return count === 0 ? "exhausted" : "requested";
  }
  /** One request per linked session that never received history (linked before full sync existed). */
  async #requestFullHistory(record: RecordState, generation: number) {
    if (!await this.#sleep(record, generation, this.#timing.fullHistoryAfterMs)) return;
    const creds = record.store?.state.creds as (AuthenticationCreds & { processedHistoryMessages?: unknown[] }) | undefined;
    if (record.historyAt > 0 || (creds?.processedHistoryMessages?.length ?? 0) > 0 || !record.socket) return;
    try {
      await record.socket.sendPeerDataOperationMessage({
        peerDataOperationRequestType: proto.Message.PeerDataOperationRequestType.FULL_HISTORY_SYNC_ON_DEMAND,
        fullHistorySyncOnDemandRequest: { requestMetadata: { requestId: randomUUID() }, historySyncConfig: FULL_HISTORY_CONFIG } });
      this.#log("full_history_requested", { session: this.#tag(record) });
    } catch { this.#log("full_history_request_failed", { session: this.#tag(record) }); }
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
    record.onDemand?.resolve(null); record.onDemand = null;
    if (record.timer) { clearTimeout(record.timer); record.timer = null; }
    if (record.authorityTimer) { clearInterval(record.authorityTimer); record.authorityTimer = null; }
    record.pump?.close(); record.pump = null;
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
    for (const row of this.#records.values()) { await row.ingest; await row.pump?.drain(); }
  }
  async close() { this.#closed = true; await Promise.all([...this.#records.values()].map(row => this.#stop(row, "disconnected"))); this.#records.clear(); this.#sends.clear(); }
}
