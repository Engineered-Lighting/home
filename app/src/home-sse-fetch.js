/* Home — SSE-via-fetch helper.
 *
 * EventSource in browsers can't send custom headers (only cookies).
 * Several of our endpoints — the supervisor's /api/stack/logs/stream
 * being the first — require Authorization: Bearer, so EventSource is
 * out. This helper uses fetch() + ReadableStream to parse SSE frames
 * manually, which DOES support custom headers.
 *
 * Usage:
 *   const ctrl = streamSse(url, { Authorization: `Bearer ${token}` }, {
 *     onLine:  (s)         => append a log line,
 *     onEvent: (event, d)  => handle 'done', etc.,
 *     onError: (err)       => optional,
 *     onClose: ()          => optional cleanup,
 *   });
 *   // ... later, to cancel:
 *   ctrl.abort();
 *
 * SSE frame format (per the spec):
 *
 *     event: done              <- optional; defaults to "message"
 *     data: {"exit_code": 0}
 *
 *     data: {"line": "..."}    <- common case (just a "data:" line)
 *
 *     : ping                   <- comment / heartbeat; skipped here
 *
 * Frames are separated by an empty line ("\n\n"). Multi-line data is
 * concatenated with newlines per the spec; we then attempt JSON.parse
 * and fall back to raw text under a { raw } field.
 */
function streamSse(url, headers, callbacks) {
  const ctrl = new AbortController();
  const { onLine, onEvent, onError, onOpen, onClose } = callbacks || {};

  (async () => {
    let closed = false;
    try {
      const fetcher = (typeof window !== "undefined" && window.tauriFetch) || fetch;
      const r = await fetcher(url, { headers, signal: ctrl.signal, cache: "no-store" });
      if (!r.ok || !r.body) {
        if (onError) onError(new Error(`http ${r.status}`));
        return;
      }
      if (onOpen) onOpen();
      const reader = r.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      // Read until EOF, the server sends a terminal "event: done" frame,
      // or AbortController is triggered (which makes reader.read() throw).
      while (true) {
        let chunk;
        try {
          chunk = await reader.read();
        } catch (e) {
          if (ctrl.signal.aborted) return;       // expected on user cancel
          if (onError) onError(e);
          return;
        }
        if (chunk.done) return;
        buf += decoder.decode(chunk.value, { stream: true });
        let sep;
        while ((sep = buf.indexOf("\n\n")) >= 0) {
          const frame = buf.slice(0, sep);
          buf = buf.slice(sep + 2);
          if (!frame || frame.startsWith(":")) continue;  // heartbeat / comment
          let eventName = "message";
          const dataLines = [];
          for (const ln of frame.split("\n")) {
            if (ln.startsWith("event:")) {
              eventName = ln.slice(6).trim();
            } else if (ln.startsWith("data:")) {
              // SSE spec says leading single space after "data:" is stripped.
              const v = ln.slice(5);
              dataLines.push(v.startsWith(" ") ? v.slice(1) : v);
            }
          }
          if (dataLines.length === 0) continue;
          let parsed;
          try { parsed = JSON.parse(dataLines.join("\n")); }
          catch { parsed = { raw: dataLines.join("\n") }; }
          // Convenience callback for the common case.
          if (eventName === "message" && typeof parsed.line === "string" && onLine) {
            onLine(parsed.line);
          }
          if (onEvent) onEvent(eventName, parsed);
        }
      }
    } catch (e) {
      if (!ctrl.signal.aborted && onError) onError(e);
    } finally {
      if (!closed) {
        closed = true;
        if (onClose) onClose();
      }
    }
  })();

  return ctrl;
}

Object.assign(window, { streamSse });
