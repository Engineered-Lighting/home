#!/usr/bin/env node
/* ============================================================================
 * run-resilience-tests.js — behavioral tests for the resilience PR:
 *   1. window.fetchWithRetry  (app/src/home-fetch-with-retry.js)
 *   2. service-worker install precache  (app/src/home-service-worker.js)
 *   3. gateway GET/HEAD /healthz  (web-gateway/server.mjs, spawned)
 *
 * These are runtime tests (not source substring checks): fetchWithRetry and the
 * SW install handler are evaluated in a vm sandbox with mocked fetch/caches, and
 * the gateway is spawned on a throwaway port and probed over HTTP.
 * ========================================================================== */
"use strict";

const childProcess = require("node:child_process");
const fs = require("node:fs");
const http = require("node:http");
const path = require("node:path");
const vm = require("node:vm");

const REPO = path.resolve(__dirname, "..");
const SRC = path.join(REPO, "app", "src");
const FETCH_FILE = path.join(SRC, "home-fetch-with-retry.js");
const SW_FILE = path.join(SRC, "home-service-worker.js");
const SERVER = path.join(REPO, "web-gateway", "server.mjs");

let passes = 0;
let fails = 0;
const failures = [];

function assert(name, cond, detail) {
  if (cond) {
    passes += 1;
    process.stdout.write("  PASS  " + name + "\n");
  } else {
    fails += 1;
    failures.push({ name, detail });
    process.stdout.write("  FAIL  " + name);
    if (detail !== undefined) {
      const dumped = typeof detail === "string" ? detail : JSON.stringify(detail, null, 2);
      process.stdout.write("\n        " + dumped.replace(/\n/g, "\n        "));
    }
    process.stdout.write("\n");
  }
}

/* ── fetchWithRetry harness ───────────────────────────────────────────── */

function loadFetchWithRetry(fetchStub) {
  const source = fs.readFileSync(FETCH_FILE, "utf8");
  const sandbox = {
    console: { log() {}, warn() {}, error() {}, info() {}, debug() {} },
    setTimeout,
    clearTimeout,
    Promise,
    Error,
    Date,
    Math,
    Object,
    JSON,
    AbortController,
    fetch: fetchStub,
  };
  sandbox.globalThis = sandbox;
  sandbox.window = sandbox;
  vm.runInNewContext(source, sandbox, { filename: FETCH_FILE });
  return sandbox.fetchWithRetry;
}

function makeResponse(status, opts = {}) {
  const headers = new Map();
  if (opts.retryAfter != null) headers.set("retry-after", String(opts.retryAfter));
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: (k) => (headers.has(String(k).toLowerCase()) ? headers.get(String(k).toLowerCase()) : null) },
    json: async () => ({}),
    text: async () => "",
  };
}

async function testFetchWithRetry() {
  process.stdout.write("\nfetch_with_retry_test\n");

  // 1. 200 on the first try → exactly one fetch.
  {
    let calls = 0;
    const f = loadFetchWithRetry(async () => { calls += 1; return makeResponse(200); });
    const resp = await f({ url: "/x", backoffMs: 1 });
    assert("200 first try returns the response", resp && resp.status === 200, resp && resp.status);
    assert("200 first try makes exactly one fetch", calls === 1, calls);
  }

  // 2. 502, 502, 200 → three fetches, backoff grows between attempts.
  {
    let calls = 0;
    const delays = [];
    const f = loadFetchWithRetry(async () => {
      calls += 1;
      return makeResponse(calls < 3 ? 502 : 200);
    });
    const resp = await f({
      url: "/x", backoffMs: 10, backoffFactor: 2, backoffJitter: 0.3,
      onAttempt: ({ nextDelay }) => { if (nextDelay != null) delays.push(nextDelay); },
    });
    assert("502x2 then 200 eventually succeeds", resp && resp.status === 200, resp && resp.status);
    assert("502x2 then 200 makes three fetches", calls === 3, calls);
    assert("backoff is observed for two retries", delays.length === 2, delays);
    assert("backoff grows between attempts", delays.length === 2 && delays[1] > delays[0], delays);
  }

  // 3. Non-retryable 4xx → one fetch, throws reason "http".
  for (const status of [400, 401, 403, 404, 409]) {
    let calls = 0;
    const f = loadFetchWithRetry(async () => { calls += 1; return makeResponse(status); });
    let threw = null;
    try {
      await f({ url: "/x", backoffMs: 1 });
    } catch (e) { threw = e; }
    assert(`HTTP ${status} makes exactly one fetch`, calls === 1, calls);
    assert(`HTTP ${status} throws reason "http"`, threw && threw.reason === "http", threw && threw.reason);
    assert(`HTTP ${status} reports lastStatus`, threw && threw.lastStatus === status, threw && threw.lastStatus);
  }

  // 4. All attempts time out → throws reason "abort", attempts === maxAttempts.
  {
    let calls = 0;
    const f = loadFetchWithRetry((url, init) => new Promise((_resolve, reject) => {
      calls += 1;
      const sig = init && init.signal;
      if (sig) sig.addEventListener("abort", () => {
        const e = new Error("aborted"); e.name = "AbortError"; reject(e);
      });
    }));
    let threw = null;
    try {
      await f({ url: "/x", timeoutMs: 20, maxAttempts: 3, backoffMs: 1 });
    } catch (e) { threw = e; }
    assert("all-timeout throws reason \"abort\"", threw && threw.reason === "abort", threw && threw.reason);
    assert("all-timeout reports attempts === maxAttempts", threw && threw.attempts === 3, threw && threw.attempts);
    assert("all-timeout makes maxAttempts fetches", calls === 3, calls);
  }

  // 5. Retry-After: 2 → waits at least ~2s before the retry.
  {
    let calls = 0;
    const nextDelays = [];
    const f = loadFetchWithRetry(async () => {
      calls += 1;
      return calls === 1 ? makeResponse(503, { retryAfter: 2 }) : makeResponse(200);
    });
    const started = Date.now();
    const resp = await f({
      url: "/x", backoffMs: 5,
      onAttempt: ({ nextDelay }) => { if (nextDelay != null) nextDelays.push(nextDelay); },
    });
    const elapsed = Date.now() - started;
    assert("Retry-After eventually succeeds", resp && resp.status === 200, resp && resp.status);
    assert("Retry-After: 2 schedules a 2000ms delay", nextDelays[0] === 2000, nextDelays);
    assert("Retry-After: 2 actually waits >= ~2s", elapsed >= 1950, elapsed);
  }

  // 6. Jitter yields distinct delays across runs at the same base.
  {
    const firstDelays = [];
    for (let i = 0; i < 6; i += 1) {
      let calls = 0;
      const f = loadFetchWithRetry(async () => {
        calls += 1;
        return calls === 1 ? makeResponse(503) : makeResponse(200);
      });
      await f({
        url: "/x", backoffMs: 40, backoffFactor: 2, backoffJitter: 0.3,
        onAttempt: ({ nextDelay }) => { if (nextDelay != null && firstDelays.length < i + 1) firstDelays.push(nextDelay); },
      });
    }
    assert("jitter produces more than one distinct delay", new Set(firstDelays).size > 1, firstDelays);
  }
}

/* ── service-worker precache harness ──────────────────────────────────── */

function loadServiceWorkerInstall(opts = {}) {
  const source = fs.readFileSync(SW_FILE, "utf8");
  const listeners = {};
  const state = { openArg: null, addAllArgs: null, skipWaiting: false };
  const self = {
    location: { href: "https://home.example/home-service-worker.js" }, // no ?v → "dev"
    addEventListener: (type, fn) => { listeners[type] = fn; },
    skipWaiting: () => { state.skipWaiting = true; },
    clients: { claim: async () => {} },
  };
  const caches = {
    open: async (name) => {
      state.openArg = name;
      return {
        addAll: async (urls) => {
          state.addAllArgs = urls;
          if (opts.failAddAll) throw new Error("simulated offline precache failure");
        },
        put: async () => {},
        match: async () => null,
      };
    },
    keys: async () => [],
    delete: async () => {},
    match: async () => null,
  };
  const sandbox = {
    self,
    caches,
    console: { log() {}, warn() {}, error() {}, info() {}, debug() {} },
    setTimeout,
    clearTimeout,
    Promise,
    Error,
    Date,
    Math,
    Object,
    JSON,
    URL,
    AbortController,
    fetch: async () => makeResponse(200),
  };
  sandbox.globalThis = sandbox;
  vm.runInNewContext(source, sandbox, { filename: SW_FILE });
  return { listeners, state };
}

async function testServiceWorkerPrecache() {
  process.stdout.write("\nservice_worker_precache_test\n");
  const EXPECTED = [
    "home-tokens.css",
    "favicon.svg",
    "favicon-32.png",
    "apple-touch-icon.png",
    "site.webmanifest",
  ];

  // Happy path: install precaches the shell list into the versioned cache.
  {
    const { listeners, state } = loadServiceWorkerInstall();
    assert("install handler is registered", typeof listeners.install === "function");
    let waited = null;
    listeners.install({ waitUntil: (p) => { waited = p; } });
    assert("install skips waiting", state.skipWaiting === true);
    assert("install passes a promise to waitUntil", waited && typeof waited.then === "function");
    await waited;
    assert("install opens the per-deploy cache", state.openArg === "home-web-static-dev", state.openArg);
    assert("install addAll receives the expected shell list",
      JSON.stringify(state.addAllArgs) === JSON.stringify(EXPECTED), state.addAllArgs);
    assert("precache list does not include the service worker itself",
      Array.isArray(state.addAllArgs) && !state.addAllArgs.some((u) => /home-service-worker\.js/.test(u)),
      state.addAllArgs);
    assert("precache list excludes heavy apartment assets",
      Array.isArray(state.addAllArgs) && !state.addAllArgs.some((u) => /^\/?assets\/apartment\//.test(u)),
      state.addAllArgs);
    assert("precache list excludes executable app code",
      Array.isArray(state.addAllArgs) && !state.addAllArgs.some((u) => /\.(js|jsx)$/i.test(u) || u === "/"),
      state.addAllArgs);
  }

  // Failure path: a rejecting addAll must NOT reject install (best-effort).
  {
    const { listeners } = loadServiceWorkerInstall({ failAddAll: true });
    let waited = null;
    listeners.install({ waitUntil: (p) => { waited = p; } });
    let rejected = false;
    try {
      await waited;
    } catch (_) {
      rejected = true;
    }
    assert("addAll failure is swallowed and install does not reject", rejected === false);
  }
}

/* ── gateway /healthz harness ─────────────────────────────────────────── */

function freePort() {
  return new Promise((resolve, reject) => {
    const probe = http.createServer();
    probe.once("error", reject);
    probe.listen(0, "127.0.0.1", () => {
      const port = probe.address().port;
      probe.close(() => resolve(port));
    });
  });
}

function request(port, urlPath, method = "GET") {
  return new Promise((resolve, reject) => {
    const req = http.request({ host: "127.0.0.1", port, path: urlPath, method }, (res) => {
      let body = "";
      res.setEncoding("utf8");
      res.on("data", (c) => { body += c; });
      res.on("end", () => resolve({ status: res.statusCode, headers: res.headers, body }));
    });
    req.once("error", reject);
    req.end();
  });
}

async function waitForGateway(port, child) {
  for (let i = 0; i < 50; i += 1) {
    if (child.exitCode != null) throw new Error(`gateway exited early with ${child.exitCode}`);
    try {
      const res = await request(port, "/healthz");
      if (res.status === 200) return;
    } catch { /* keep polling */ }
    await new Promise((r) => setTimeout(r, 100));
  }
  throw new Error("gateway did not become ready");
}

async function testHealthz() {
  process.stdout.write("\ngateway_healthz_test\n");
  const port = await freePort();
  const child = childProcess.spawn(process.execPath, [SERVER], {
    cwd: REPO,
    env: { ...process.env, HOME_WEB_HOST: "127.0.0.1", HOME_WEB_PORT: String(port), HOME_WEB_AUTH_REQUIRED: "0" },
    stdio: ["ignore", "ignore", "pipe"],
  });
  try {
    await waitForGateway(port, child);

    const get = await request(port, "/healthz", "GET");
    assert("GET /healthz returns 200", get.status === 200, get.status);
    assert("GET /healthz sets Cache-Control: no-store",
      /no-store/.test(String(get.headers["cache-control"] || "")), get.headers["cache-control"]);
    assert("GET /healthz is application/json",
      /application\/json/.test(String(get.headers["content-type"] || "")), get.headers["content-type"]);
    let parsed = null;
    try { parsed = JSON.parse(get.body); } catch { /* leave null */ }
    assert("GET /healthz body has status: ok", parsed && parsed.status === "ok", parsed);
    assert("GET /healthz body has a non-empty commit string",
      parsed && typeof parsed.commit === "string" && parsed.commit.length > 0, parsed && parsed.commit);
    assert("GET /healthz body has a numeric uptime",
      parsed && typeof parsed.uptime === "number", parsed && parsed.uptime);

    const head = await request(port, "/healthz", "HEAD");
    assert("HEAD /healthz returns 200", head.status === 200, head.status);
    assert("HEAD /healthz sets Cache-Control: no-store",
      /no-store/.test(String(head.headers["cache-control"] || "")), head.headers["cache-control"]);
    assert("HEAD /healthz has an empty body", head.body === "", JSON.stringify(head.body));
  } finally {
    if (child.exitCode == null) child.kill();
  }
}

(async function main() {
  await testFetchWithRetry();
  await testServiceWorkerPrecache();
  await testHealthz();

  process.stdout.write(`\n${passes} pass . ${fails} fail\n`);
  if (fails) {
    for (const failure of failures) process.stdout.write(`  FAIL: ${failure.name}\n`);
    process.exit(1);
  }
})().catch((err) => { console.error(err); process.exit(1); });
