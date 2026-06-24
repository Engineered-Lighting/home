#!/usr/bin/env node
/* ============================================================================
 * tools/run-sse-fetch-tests.js
 *
 * Pure local tests for app/src/home-sse-fetch.js.
 * ============================================================================ */

"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const REPO = path.resolve(__dirname, "..");
const SRC = path.join(REPO, "app", "src", "home-sse-fetch.js");

let passes = 0;
let fails = 0;
const failures = [];

function assert(name, cond, detail) {
  if (cond) {
    passes++;
    process.stdout.write("  \x1b[32mPASS\x1b[0m  " + name + "\n");
  } else {
    fails++;
    failures.push({ name, detail });
    process.stdout.write("  \x1b[31mFAIL\x1b[0m  " + name);
    if (detail !== undefined) {
      const dumped = typeof detail === "string"
        ? detail
        : JSON.stringify(detail, null, 2).replace(/\n/g, "\n        ");
      process.stdout.write("\n        " + dumped);
    }
    process.stdout.write("\n");
  }
}

function makeReader(frames, opts) {
  const encoder = new TextEncoder();
  const chunks = frames.map((s) => encoder.encode(s));
  let idx = 0;
  const options = opts || {};
  return {
    async read() {
      if (options.throwAt === idx) {
        throw new Error(options.throwMessage || "reader exploded");
      }
      if (idx >= chunks.length) return { done: true };
      return { done: false, value: chunks[idx++] };
    },
  };
}

function loadWith(fetchImpl) {
  const calls = [];
  const sandbox = {
    window: {},
    fetch: async (url, opts) => {
      calls.push({ url, opts });
      return fetchImpl(url, opts, calls.length);
    },
    AbortController,
    TextDecoder,
    JSON,
    Error,
  };
  const src = fs.readFileSync(SRC, "utf8");
  vm.runInNewContext(src, sandbox, { filename: SRC });
  return { streamSse: sandbox.window.streamSse, calls };
}

function waitForClose(run) {
  return new Promise((resolve) => {
    run(resolve);
  });
}

(async function main() {
  process.stdout.write("\nshape_test\n");
  {
    const { streamSse } = loadWith(() => ({ ok: false, status: 500, body: null }));
    assert("streamSse exported to window", typeof streamSse === "function");
  }

  process.stdout.write("\nframe_parse_test\n");
  {
    const lines = [];
    const events = [];
    let closed = false;
    const { streamSse, calls } = loadWith(() => ({
      ok: true,
      status: 200,
      body: {
        getReader() {
          return makeReader([
            ": ping\n\n",
            "data: {\"line\":\"booting\"}\n\n",
            "event: done\ndata: {\"exit_code\":0}\n\n",
          ]);
        },
      },
    }));
    await waitForClose((resolve) => {
      streamSse("http://stack/api/stream", { Authorization: "Bearer t" }, {
        onLine: (line) => lines.push(line),
        onEvent: (event, data) => events.push({ event, data }),
        onClose: () => { closed = true; resolve(); },
      });
    });

    assert("fetch receives URL", calls[0].url === "http://stack/api/stream", calls[0]);
    assert("fetch sends supplied auth header", calls[0].opts.headers.Authorization === "Bearer t", calls[0].opts);
    assert("fetch uses no-store cache", calls[0].opts.cache === "no-store", calls[0].opts);
    assert("fetch receives abort signal", !!calls[0].opts.signal, calls[0].opts);
    assert("heartbeat frame skipped", lines.length === 1, lines);
    assert("message line callback extracts parsed line", lines[0] === "booting", lines);
    assert("message event emitted", events[0].event === "message" && events[0].data.line === "booting", events);
    assert("done event emitted with JSON data", events[1].event === "done" && events[1].data.exit_code === 0, events);
    assert("onClose called after EOF", closed === true);
  }

  process.stdout.write("\nchunk_boundary_and_raw_test\n");
  {
    const events = [];
    const { streamSse } = loadWith(() => ({
      ok: true,
      status: 200,
      body: {
        getReader() {
          return makeReader([
            "data: {\"li",
            "ne\":\"split\"}\n\n",
            "event: note\ndata: plain\n",
            "data: text\n\n",
          ]);
        },
      },
    }));
    await waitForClose((resolve) => {
      streamSse("u", {}, {
        onEvent: (event, data) => events.push({ event, data }),
        onClose: resolve,
      });
    });
    assert("frame split across chunks parses", events[0].data.line === "split", events);
    assert("raw multiline data falls back to {raw}", events[1].event === "note" && events[1].data.raw === "plain\ntext", events);
  }

  process.stdout.write("\nhttp_and_reader_error_test\n");
  {
    const errors = [];
    let closed = false;
    const { streamSse } = loadWith(() => ({ ok: false, status: 503, body: null }));
    await waitForClose((resolve) => {
      streamSse("u", {}, {
        onError: (err) => errors.push(err.message),
        onClose: () => { closed = true; resolve(); },
      });
    });
    assert("http failure calls onError", errors[0] === "http 503", errors);
    assert("http failure still closes", closed === true);
  }

  {
    const errors = [];
    const { streamSse } = loadWith(() => ({
      ok: true,
      status: 200,
      body: { getReader: () => makeReader([], { throwAt: 0, throwMessage: "read failed" }) },
    }));
    await waitForClose((resolve) => {
      streamSse("u", {}, {
        onError: (err) => errors.push(err.message),
        onClose: resolve,
      });
    });
    assert("reader error calls onError", errors[0] === "read failed", errors);
  }

  process.stdout.write("\nabort_test\n");
  {
    const errors = [];
    let closed = false;
    let ctrl;
    const { streamSse } = loadWith((_url, opts) => ({
      ok: true,
      status: 200,
      body: {
        getReader() {
          return {
            async read() {
              if (opts.signal.aborted) throw new Error("aborted read");
              ctrl.abort();
              throw new Error("aborted read");
            },
          };
        },
      },
    }));
    await waitForClose((resolve) => {
      ctrl = streamSse("u", {}, {
        onError: (err) => errors.push(err.message),
        onClose: () => { closed = true; resolve(); },
      });
    });
    assert("abort suppresses onError for reader abort", errors.length === 0, errors);
    assert("abort still calls onClose", closed === true);
  }

  process.stdout.write("\n");
  if (fails === 0) {
    process.stdout.write("\x1b[32m" + passes + " pass . 0 fail\x1b[0m\n");
    process.exit(0);
  }
  for (const f of failures) {
    process.stdout.write("FAIL " + f.name + "\n");
    if (f.detail !== undefined) process.stdout.write(String(f.detail) + "\n");
  }
  process.stdout.write("\x1b[31m" + passes + " pass . " + fails + " fail\x1b[0m\n");
  process.exit(1);
})().catch((e) => {
  process.stderr.write((e && e.stack) ? e.stack : String(e));
  process.exit(2);
});
