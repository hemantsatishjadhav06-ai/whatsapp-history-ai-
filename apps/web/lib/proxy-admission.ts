export type ProxyLease = { release(): void };
const TOTAL_LIMIT = 32;
const CONTROL_LIMIT = 4;
const NORMAL_LIMIT = TOTAL_LIMIT - CONTROL_LIMIT;
const IMPORT_LIMIT = 2;
let normal = 0;
let control = 0;
let imports = 0;

/** Process-local resource admission, never an identity or authorization decision. */
export function acquireProxyLease(isControl: boolean, isImport: boolean): ProxyLease | null {
  if (normal + control >= TOTAL_LIMIT || (isControl ? control >= CONTROL_LIMIT : normal >= NORMAL_LIMIT)
      || (isImport && imports >= IMPORT_LIMIT)) return null;
  if (isControl) control++; else normal++;
  if (isImport) imports++;
  let released = false;
  return { release() {
    if (released) return;
    released = true;
    if (isControl) control--; else normal--;
    if (isImport) imports--;
  } };
}

/** Keep admission while a proxied response is consumed, canceled, or expires. */
export function leaseProxyBody(body: ReadableStream<Uint8Array>, lease: ProxyLease, signal: AbortSignal): ReadableStream<Uint8Array> {
  const reader = body.getReader();
  let ended = false;
  let output: ReadableStreamDefaultController<Uint8Array>;
  const finish = () => {
    if (ended) return;
    ended = true;
    signal.removeEventListener('abort', interrupt);
    lease.release();
  };
  const interrupt = () => {
    if (ended) return;
    finish();
    void reader.cancel(signal.reason).catch(() => {});
    output.error(signal.reason || new Error('Proxy response expired'));
  };
  return new ReadableStream<Uint8Array>({
    start(controller) {
      output = controller;
      signal.addEventListener('abort', interrupt, {once:true});
      if (signal.aborted) interrupt();
    },
    async pull(controller) {
      if (ended) return;
      try {
        const chunk = await reader.read();
        if (ended) return;
        if (chunk.done) { finish(); controller.close(); }
        else controller.enqueue(chunk.value);
      } catch (failure) {
        if (!ended) { finish(); controller.error(failure); }
      }
    },
    cancel(reason) {
      finish();
      // Broken cancellation callbacks cannot retain a capacity reservation.
      void reader.cancel(reason).catch(() => {});
    },
  });
}
