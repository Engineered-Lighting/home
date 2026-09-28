// Limits apply to decoded bytes delivered by fetch, including chunked bodies.
// Callers must also supply an HTTP deadline covering response body consumption.
export async function readHaJson(response, maxBytes) {
  let reader;
  let joined;
  const chunks = [];
  try {
    if (!Number.isSafeInteger(maxBytes) || maxBytes < 1 ||
        !/^application\/json(?:;|$)/i.test(response.headers.get("content-type") || "")) {
      throw new Error();
    }
    const length = response.headers.get("content-length");
    if (length !== null && (!/^\d+$/.test(length) ||
        !Number.isSafeInteger(Number(length)) || Number(length) > maxBytes)) throw new Error();
    reader = response.body?.getReader();
    if (!reader) throw new Error();
    let total = 0;
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      if (!(value instanceof Uint8Array) || value.byteLength > maxBytes - total) throw new Error();
      total += value.byteLength;
      chunks.push(Buffer.from(value));
    }
    joined = Buffer.concat(chunks, total);
    const result = JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(joined));
    if (!result || typeof result !== "object" || Array.isArray(result)) throw new Error();
    return result;
  } catch {
    // Never include provider content, tokens, or parser snippets in exceptions.
    throw new Error("ha_response_rejected");
  } finally {
    for (const chunk of chunks) chunk.fill(0);
    joined?.fill(0);
    if (reader) {
      await reader.cancel().catch(() => {});
      reader.releaseLock();
    } else {
      await response.body?.cancel().catch(() => {});
    }
  }
}
