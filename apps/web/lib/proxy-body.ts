/** Bound bytes and time before the browser proxy opens an upstream request. */
export class UploadTimeoutError extends Error {}

export async function boundedProxyBody(request: Request, maxBytes: number, timeoutMs = 10_000): Promise<ArrayBuffer | undefined> {
  const declared = request.headers.get('content-length');
  if (declared && (!/^\d+$/.test(declared) || Number(declared) > maxBytes)) throw new RangeError('Upload is too large');
  if (!request.body) return undefined;
  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  let rejectDeadline: (reason: Error) => void = () => {};
  const deadline = new Promise<never>((_, reject) => { rejectDeadline = reject; });
  const stop = (reason: Error) => {
    rejectDeadline(reason);
    // A broken stream's cancellation callback must not keep this request alive.
    void reader.cancel(reason).catch(() => {});
  };
  const timer = setTimeout(() => stop(new UploadTimeoutError('Upload deadline exceeded')), timeoutMs);
  const aborted = () => stop(new Error('Upload was canceled'));
  request.signal.addEventListener('abort', aborted, {once:true});
  if (request.signal.aborted) aborted();
  try {
    while (true) {
      const chunk = await Promise.race([reader.read(), deadline]);
      if (chunk.done) break;
      size += chunk.value.byteLength;
      if (size > maxBytes) { stop(new RangeError('Upload is too large')); throw new RangeError('Upload is too large'); }
      chunks.push(chunk.value);
    }
  } finally {
    clearTimeout(timer);
    request.signal.removeEventListener('abort', aborted);
    reader.releaseLock();
  }
  const result = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) { result.set(chunk, offset); offset += chunk.byteLength; }
  return result.buffer;
}
