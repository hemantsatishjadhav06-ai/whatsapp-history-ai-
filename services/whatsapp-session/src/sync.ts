/**
 * Ordered, bounded delivery of chat metadata and messages to the Python authority.
 *
 * Live messages go first. History, replay and backfill drain in batches under a byte
 * budget. Transient failures back off and retry the same batch; a batch Python
 * rejects as invalid is split until the single bad record is isolated and dropped,
 * so one malformed message can never stall the stream or kill the WhatsApp session.
 */
import type { Answer, Identity, SyncBatch, SyncChat, SyncMessage, SyncOrigin, SyncProgress } from "./protocol.ts";

export type PumpLog = (event: string, fields: Record<string, string | number | boolean>) => void;
export type PumpOptions = {
  identity: () => Identity;
  post: (batch: SyncBatch) => Promise<Answer>;
  /** Python refused the session identity or lease; the session decides whether to re-announce or stop. */
  onRefused?: (status: number) => void;
  log?: PumpLog;
  maxBuffered?: number;
  maxBatchMessages?: number;
  maxBatchBytes?: number;
  sleep?: (ms: number) => Promise<void>;
};
const ORDER: readonly SyncOrigin[] = ["live", "replay", "history", "backfill"];
const OVERHEAD = 220;

/** UTF-8 bytes on the wire: Indic scripts and emoji take up to four bytes per character. */
function size(row: SyncMessage | SyncChat): number {
  return OVERHEAD + Buffer.byteLength(JSON.stringify(row), "utf8");
}
export class SyncPump {
  #queues = new Map<SyncOrigin, SyncMessage[]>(ORDER.map(origin => [origin, []]));
  #chats = new Map<string, SyncChat>();
  #progress: SyncProgress | null = null;
  /** Built batches waiting at the front of the line: retries and halves of a split batch, in order. */
  #pending: SyncBatch[] = [];
  #options: Required<Omit<PumpOptions, "onRefused" | "log">> & Pick<PumpOptions, "onRefused" | "log">;
  #running: Promise<void> | null = null;
  #closed = false;
  #wake: (() => void) | null = null;
  readonly stats = { delivered: 0, dropped: 0, batches: 0, failures: 0 };
  constructor(options: PumpOptions) {
    this.#options = { maxBuffered: 250_000, maxBatchMessages: 300, maxBatchBytes: 900_000,
      sleep: ms => new Promise(resolve => { const timer = setTimeout(resolve, ms); timer.unref?.(); }), ...options };
  }
  get buffered(): number {
    let total = 0;
    for (const queue of this.#queues.values()) total += queue.length;
    for (const batch of this.#pending) total += batch.messages.length;
    return total;
  }
  push(origin: SyncOrigin, messages: readonly SyncMessage[]): void {
    if (this.#closed || !messages.length) return;
    const room = this.#options.maxBuffered - this.buffered;
    // Live messages are never shed; bulk history beyond the bound is left to on-demand backfill.
    const accepted = origin === "live" ? messages : messages.slice(0, Math.max(0, room));
    if (accepted.length < messages.length) {
      this.stats.dropped += messages.length - accepted.length;
      this.#options.log?.("sync_buffer_full", { origin, dropped: messages.length - accepted.length });
    }
    this.#queues.get(origin)!.push(...accepted);
    this.#kick();
  }
  upsertChats(chats: readonly SyncChat[]): void {
    if (this.#closed || !chats.length) return;
    for (const chat of chats) {
      const previous = this.#chats.get(chat.jid);
      const merged = previous ? { ...previous, ...chat } : chat;
      // A pending history row may still create its chat when a live update for it arrives first or later.
      if (previous && !(previous.contact_only && chat.contact_only)) delete merged.contact_only;
      this.#chats.set(chat.jid, merged);
    }
    this.#kick();
  }
  progress(value: SyncProgress): void { if (!this.#closed) { this.#progress = value; this.#kick(); } }
  /** Resolve once everything buffered now has been delivered or dropped (tests and shutdown). */
  async drain(): Promise<void> {
    while (!this.#closed && (this.buffered || this.#pending.length || this.#chats.size || this.#progress || this.#running)) {
      if (this.#running) await this.#running; else this.#kick();
    }
  }
  close(): void {
    this.#closed = true; this.#pending = []; this.#chats.clear(); this.#progress = null;
    for (const queue of this.#queues.values()) queue.length = 0;
    const wake = this.#wake; this.#wake = null; wake?.();
  }
  #kick(): void {
    if (this.#closed) return;
    const wake = this.#wake; this.#wake = null; wake?.();
    if (!this.#running) this.#running = this.#loop().finally(() => { this.#running = null; });
  }
  async #loop(): Promise<void> {
    let backoff = 0;
    while (!this.#closed) {
      const batch = this.#pending.shift() ?? this.#build();
      if (!batch) return;
      const outcome = await this.#deliver(batch);
      if (outcome === "done") { backoff = 0; continue; }
      if (this.#closed) return;
      this.#pending.unshift(batch);
      backoff = outcome === "paused" ? 60_000 : Math.min(60_000, Math.max(1000, backoff * 2));
      await new Promise<void>(resolve => { this.#wake = resolve; void this.#options.sleep(backoff).then(resolve); });
      this.#wake = null;
    }
  }
  #build(): SyncBatch | null {
    const origin = ORDER.find(name => this.#queues.get(name)!.length) ?? null;
    if (!origin && !this.#chats.size && !this.#progress) return null;
    const chats: SyncChat[] = []; const messages: SyncMessage[] = []; let bytes = 0;
    for (const [jid, chat] of this.#chats) {
      if (chats.length >= 500 || bytes + size(chat) > this.#options.maxBatchBytes / 4) break;
      chats.push(chat); bytes += size(chat); this.#chats.delete(jid);
    }
    if (origin) {
      const queue = this.#queues.get(origin)!;
      while (queue.length && messages.length < this.#options.maxBatchMessages &&
             (messages.length === 0 || bytes + size(queue[0]!) <= this.#options.maxBatchBytes)) {
        const row = queue.shift()!; messages.push(row); bytes += size(row);
      }
    }
    const progress = this.#progress; this.#progress = null;
    return { ...this.#options.identity(), origin: origin ?? "history", chats, messages, ...(progress ? { progress } : {}) };
  }
  /** "done" consumes the batch (delivered, dropped, or replaced by its halves); otherwise it is retried whole. */
  async #deliver(batch: SyncBatch): Promise<"done" | "retry" | "paused"> {
    const answer = await this.#options.post({ ...batch, ...this.#options.identity() });
    this.stats.batches += 1;
    if (answer.status >= 200 && answer.status < 300) { this.stats.delivered += batch.messages.length; return "done"; }
    this.stats.failures += 1;
    this.#options.log?.("sync_batch_failed", { origin: batch.origin, status: answer.status, messages: batch.messages.length });
    if (answer.status === 423) return "paused";
    if (answer.status === 401 || answer.status === 403 || answer.status === 409) {
      this.#options.onRefused?.(answer.status);
      return "retry";
    }
    if (![400, 413, 422].includes(answer.status)) return "retry";
    const rows = batch.messages.length ? batch.messages : batch.chats;
    if (rows.length <= 1) {
      this.stats.dropped += batch.messages.length;
      this.#options.log?.("sync_record_dropped", { origin: batch.origin, messages: batch.messages.length, chats: batch.chats.length });
      return "done";
    }
    // Invalid batch: halve it at the front of the line until the bad record is alone.
    const middle = Math.ceil(rows.length / 2);
    const { progress: _progress, ...rest } = batch;
    const halves: SyncBatch[] = batch.messages.length
      ? [{ ...batch, messages: batch.messages.slice(0, middle) }, { ...rest, chats: [], messages: batch.messages.slice(middle) }]
      : [{ ...batch, chats: batch.chats.slice(0, middle) }, { ...rest, chats: batch.chats.slice(middle) }];
    this.#pending.unshift(...halves);
    return "done";
  }
}
