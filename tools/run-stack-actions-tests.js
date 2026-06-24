#!/usr/bin/env node
/* ============================================================================
 * tools/run-stack-actions-tests.js
 *
 * Pure local tests for app/src/home-stack-actions.jsx.
 *
 * The module is browser-loaded and attaches window.HomeStackActions, so this
 * runner evaluates it in a VM with a fake window.tauriFetch.
 * ============================================================================ */

"use strict";

const fs = require("fs");
const path = require("path");
const vm = require("vm");

const REPO = path.resolve(__dirname, "..");
const SRC = path.join(REPO, "app", "src", "home-stack-actions.jsx");

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

function response(status, body, opts = {}) {
  return {
    ok: opts.ok === undefined ? status >= 200 && status < 300 : opts.ok,
    status,
    async json() {
      if (opts.throwJson) throw new Error("not json");
      return body;
    },
  };
}

function loadWith(fetchImpl) {
  const calls = [];
  const sandbox = {
    window: {
      tauriFetch: async (url, opts) => {
        calls.push({ url, opts });
        return fetchImpl(url, opts, calls.length);
      },
    },
  };
  const src = fs.readFileSync(SRC, "utf8");
  vm.runInNewContext(src, sandbox, { filename: SRC });
  return { H: sandbox.window.HomeStackActions, calls };
}

const base = "http://127.0.0.1:8093";
const token = "token-123";

(async function main() {
  process.stdout.write("\nstack_action_url_header_test\n");
  {
    const { H, calls } = loadWith(() => response(202, { task_id: "t-start", verb: "start" }));
    await H.start({ supervisorUrl: base, stackToken: token });
    assert("start posts to /api/stack/start", calls[0].url === `${base}/api/stack/start`, calls[0]);
    assert("start uses POST", calls[0].opts.method === "POST", calls[0].opts);
    assert("start sends bearer token", calls[0].opts.headers.Authorization === `Bearer ${token}`, calls[0].opts.headers);
    assert("start uses no-store cache", calls[0].opts.cache === "no-store", calls[0].opts);
  }

  {
    const { H, calls } = loadWith(() => response(202, { task_id: "t-start", verb: "start" }));
    await H.start({ supervisorUrl: `${base}/`, stackToken: token });
    assert("start trims trailing supervisor slash", calls[0].url === `${base}/api/stack/start`, calls[0]);
  }

  {
    const { H, calls } = loadWith(() => response(202, { task_id: "t-stop", verb: "stop" }));
    await H.stop({ supervisorUrl: base, stackToken: token });
    assert("stop posts to /api/stack/stop", calls[0].url === `${base}/api/stack/stop`, calls[0]);
    assert("stop sends required confirm header", calls[0].opts.headers["X-Confirm"] === "stop-ai-stack", calls[0].opts.headers);
  }

  {
    const { H, calls } = loadWith(() => response(202, { ok: true }));
    await H.freeGpu({ supervisorUrl: base, stackToken: token });
    assert("freeGpu posts to hyphenated supervisor route", calls[0].url === `${base}/api/stack/free-gpu`, calls[0]);
    assert("freeGpu sends required confirm header", calls[0].opts.headers["X-Confirm"] === "free-gpu", calls[0].opts.headers);
  }

  {
    const { H, calls } = loadWith(() => response(202, { ok: true }));
    await H.pausePerception({ supervisorUrl: `${base}/`, stackToken: token });
    assert(
      "pausePerception stops the vision-sidecar service",
      calls[0].url === `${base}/api/services/vision-sidecar/stop`,
      calls[0],
    );
    assert(
      "pausePerception sends vision-sidecar stop confirmation",
      calls[0].opts.headers["X-Confirm"] === "stop-vision-sidecar",
      calls[0].opts.headers,
    );
  }

  process.stdout.write("\nservice_action_url_header_test\n");
  {
    const { H, calls } = loadWith(() => response(200, { lines: ["a"] }));
    await H.serviceLogs({ supervisorUrl: `${base}/`, stackToken: token, service: "vision-sidecar", n: 12 });
    assert(
      "serviceLogs trims base and GETs encoded service logs route",
      calls[0].url === `${base}/api/services/vision-sidecar/logs?n=12`,
      calls[0],
    );
    assert("serviceLogs uses GET", calls[0].opts.method === "GET", calls[0].opts);
  }

  {
    const { H, calls } = loadWith(() => response(202, { ok: true }));
    await H.serviceStop({ supervisorUrl: base, stackToken: token, service: "wyoming-parakeet" });
    assert(
      "serviceStop posts to encoded stop route",
      calls[0].url === `${base}/api/services/wyoming-parakeet/stop`,
      calls[0],
    );
    assert(
      "serviceStop sends service-specific confirm header",
      calls[0].opts.headers["X-Confirm"] === "stop-wyoming-parakeet",
      calls[0].opts.headers,
    );
  }

  {
    const { H, calls } = loadWith(() => response(202, { ok: true }));
    await H.serviceRestart({ supervisorUrl: base, stackToken: token, service: "weird/name" });
    assert(
      "serviceRestart URL-encodes service names",
      calls[0].url === `${base}/api/services/weird%2Fname/restart`,
      calls[0],
    );
  }

  {
    const { H, calls } = loadWith(() => response(200, { overall: "ready" }));
    await H.status({ supervisorUrl: base, stackToken: token });
    assert("status GETs /api/stack/status", calls[0].url === `${base}/api/stack/status`, calls[0]);
  }

  process.stdout.write("\nresult_shape_test\n");
  {
    const { H } = loadWith(() => response(409, { detail: { task_id: "t-existing", verb: "restart" } }, { ok: false }));
    const result = await H.restart({ supervisorUrl: base, stackToken: token });
    assert("409 conflict is accepted as ok attachable task", result.ok === true && result.status === 409, result);
    assert("extractTaskId reads conflict detail.task_id", H.extractTaskId(result) === "t-existing", result);
    assert("extractVerb reads conflict detail.verb", H.extractVerb(result, "start") === "restart", result);
    assert("isConflict409 detects conflict", H.isConflict409(result) === true);
  }

  {
    const { H } = loadWith(() => response(202, { task_id: "t-new", verb: "start" }));
    const result = await H.start({ supervisorUrl: base, stackToken: token });
    assert("extractTaskId reads top-level task_id", H.extractTaskId(result) === "t-new", result);
    assert("extractVerb reads top-level verb", H.extractVerb(result, "fallback") === "start", result);
  }

  {
    const { H } = loadWith(() => response(200, null, { throwJson: true }));
    const result = await H.status({ supervisorUrl: base, stackToken: token });
    assert("empty/non-json status body is tolerated", result.ok === true && result.body === null, result);
  }

  process.stdout.write("\nerror_mapping_test\n");
  {
    const { H } = loadWith(() => response(401, { detail: "bad" }, { ok: false }));
    const result = await H.start({ supervisorUrl: base, stackToken: token });
    assert("401 maps to token hint", result.ok === false && /STACK_TOKEN/.test(result.error), result);
    assert("isAuth401 detects auth failure", H.isAuth401(result) === true);
  }

  {
    const { H } = loadWith(() => response(429, { detail: { retry_after_s: 7 } }, { ok: false }));
    const result = await H.stop({ supervisorUrl: base, stackToken: token });
    assert("429 maps retry_after_s", result.error.includes("7s"), result);
    assert("isRateLimit429 detects rate limit", H.isRateLimit429(result) === true);
  }

  {
    const { H } = loadWith(() => response(400, { detail: { error: "missing confirm" } }, { ok: false }));
    const result = await H.freeGpu({ supervisorUrl: base, stackToken: token });
    assert("400 maps detail.error", result.error === "missing confirm", result);
  }

  {
    const { H } = loadWith(() => response(503, { detail: "nope" }, { ok: false }));
    const result = await H.start({ supervisorUrl: base, stackToken: token });
    assert("other http failures include status", result.error === "http 503", result);
  }

  {
    const { H } = loadWith(() => { throw new Error("network down"); });
    const result = await H.start({ supervisorUrl: base, stackToken: token });
    assert("network failures become status 0", result.status === 0 && result.error === "network down", result);
  }

  process.stdout.write("\nurl_builder_test\n");
  {
    const { H } = loadWith(() => response(200, {}));
    assert(
      "logsStreamUrl URL-encodes task id",
      H.logsStreamUrl({ supervisorUrl: base, taskId: "task/1" }) ===
        `${base}/api/stack/logs/stream?task_id=task%2F1`,
    );
    assert(
      "logsStreamUrl trims trailing supervisor slash",
      H.logsStreamUrl({ supervisorUrl: `${base}/`, taskId: "task/1" }) ===
        `${base}/api/stack/logs/stream?task_id=task%2F1`,
    );
    assert(
      "logsStreamUrl can target current in-flight task without query",
      H.logsStreamUrl({ supervisorUrl: `${base}/` }) ===
        `${base}/api/stack/logs/stream`,
    );
    assert("extractTaskId returns null for empty result", H.extractTaskId(null) === null);
    assert("extractVerb falls back when body has no verb", H.extractVerb({ body: {} }, "start") === "start");
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
