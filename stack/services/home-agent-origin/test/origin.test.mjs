import assert from "node:assert/strict";
import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import test from "node:test";

import {
  browserApiRouteAllowed,
  configFromEnv,
  createAgentOrigin,
  filteredAgentCookies,
  safeRequestTarget,
} from "../src/origin.mjs";
import {
  hasDefaultIpv4Route,
  isUnreachableErrorCode,
} from "../src/network-proof.mjs";

const PUBLIC_ORIGIN = "https://agent.test:8443";
const PUBLIC_HOST = "agent.test:8443";
const UUID = "018f6f42-3a8b-7c11-8123-123456789abc";

function makeAssets() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), "home-agent-origin-"));
  for (const [name, body] of [
    ["index.html", "<!doctype html><title>Agent</title>"],
    ["api.js", "globalThis.api = true;"],
    ["panel.js", "globalThis.panel = true;"],
    ["panel.css", "body { color: white; }"],
  ]) fs.writeFileSync(path.join(root, name), body);
  return root;
}

function listen(server) {
  return new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => resolve(server.address().port));
  });
}

function close(server) {
  return new Promise((resolve) => server.close(resolve));
}

function request(port, target, {
  method = "GET",
  host = PUBLIC_HOST,
  origin,
  headers = {},
  body = "",
} = {}) {
  return new Promise((resolve, reject) => {
    const requestHeaders = { Host: host, ...headers };
    if (origin !== undefined) requestHeaders.Origin = origin;
    if (body && requestHeaders["Content-Length"] === undefined) {
      requestHeaders["Content-Length"] = Buffer.byteLength(body);
    }
    const req = http.request({
      host: "127.0.0.1",
      port,
      path: target,
      method,
      headers: requestHeaders,
    }, (res) => {
      const chunks = [];
      res.on("data", (chunk) => chunks.push(chunk));
      res.on("end", () => resolve({
        status: res.statusCode,
        headers: res.headers,
        body: Buffer.concat(chunks).toString("utf8"),
      }));
    });
    req.on("error", reject);
    req.end(body);
  });
}

async function fixture(upstreamHandler) {
  const assets = makeAssets();
  const seen = [];
  const upstream = http.createServer((req, res) => upstreamHandler(req, res, seen));
  const upstreamPort = await listen(upstream);
  const config = configFromEnv({
    HOME_AGENT_WEB_PUBLIC_ORIGIN: PUBLIC_ORIGIN,
    HOME_AGENT_WEB_BFF_URL: `http://127.0.0.1:${upstreamPort}`,
    HOME_AGENT_WEB_ASSET_ROOT: assets,
    HOME_AGENT_WEB_HOST: "127.0.0.1",
    HOME_AGENT_WEB_PORT: "8096",
  });
  const origin = createAgentOrigin(config);
  const originPort = await listen(origin);
  return {
    assets,
    config,
    origin,
    originPort,
    seen,
    upstream,
    async cleanup() {
      await Promise.all([close(origin), close(upstream)]);
      fs.rmSync(assets, { recursive: true, force: true });
    },
  };
}

test("configuration fails closed for unsafe origins, service roots, and incomplete assets", () => {
  const assets = makeAssets();
  const base = {
    HOME_AGENT_WEB_PUBLIC_ORIGIN: PUBLIC_ORIGIN,
    HOME_AGENT_WEB_BFF_URL: "http://bff:8097",
    HOME_AGENT_WEB_ASSET_ROOT: assets,
  };
  assert.equal(configFromEnv(base).ready, true);
  for (const value of [
    "http://agent.test:8443",
    "https://user:pass@agent.test:8443",
    "https://agent.test:8443/path",
    "https://agent.test:8443?query",
  ]) assert.equal(configFromEnv({ ...base, HOME_AGENT_WEB_PUBLIC_ORIGIN: value }).ready, false);
  for (const value of [
    "https://bff:8097",
    "http://user:pass@bff:8097",
    "http://bff:8097/api/agent",
    "http://bff:8097?query",
  ]) assert.equal(configFromEnv({ ...base, HOME_AGENT_WEB_BFF_URL: value }).ready, false);
  fs.rmSync(path.join(assets, "panel.js"));
  assert.equal(configFromEnv(base).ready, false);
  fs.rmSync(assets, { recursive: true, force: true });
});

test("request and route parsers reject generic, native, query-bearing, and ambiguous paths", () => {
  assert.equal(safeRequestTarget("https://evil.test/api/agent/v1/snapshot"), null);
  assert.equal(safeRequestTarget("//evil.test/api/agent/v1/snapshot"), null);
  assert.equal(safeRequestTarget("/home-agent\\..\\secret"), null);
  assert.equal(safeRequestTarget("/api/agent/v1/snapshot#hidden"), null);
  assert.equal(browserApiRouteAllowed("GET", new URL("http://x/api/agent/v1/snapshot")), true);
  assert.equal(browserApiRouteAllowed("GET", new URL("http://x/api/agent/v1/onboarding/status")), true);
  assert.equal(browserApiRouteAllowed("POST", new URL("http://x/api/agent/v1/onboarding/status")), false);
  assert.equal(browserApiRouteAllowed("GET", new URL("http://x/api/agent/v1/onboarding/status?debug=1")), false);
  assert.equal(browserApiRouteAllowed("GET", new URL("http://x/api/agent/v1/principal-binding-proposal")), true);
  assert.equal(browserApiRouteAllowed("POST", new URL("http://x/api/agent/v1/principal-binding-proposal")), false);
  assert.equal(browserApiRouteAllowed("POST", new URL("http://x/api/agent/v1/principal-binding-request")), true);
  assert.equal(browserApiRouteAllowed("GET", new URL("http://x/api/agent/v1/principal-binding-request")), false);
  assert.equal(browserApiRouteAllowed("POST", new URL("http://x/api/agent/v1/principal-binding-request/cancel")), true);
  assert.equal(browserApiRouteAllowed("POST", new URL("http://x/api/agent/v1/principal-binding-proposal/confirm")), true);
  assert.equal(browserApiRouteAllowed("POST", new URL("http://x/api/agent/v1/principal-bindings")), false);
  assert.equal(browserApiRouteAllowed("GET", new URL("http://x/api/agent/v1/people")), false);
  assert.equal(browserApiRouteAllowed("POST", new URL("http://x/api/agent/v1/relationships/parent-confirmations")), false);
  assert.equal(browserApiRouteAllowed("GET", new URL("http://x/api/agent/v1/principal-binding-proposal?person=x")), false);
  assert.equal(browserApiRouteAllowed("POST", new URL("http://x/api/agent/v1/principal-binding-request?person=x")), false);
  assert.equal(browserApiRouteAllowed("GET", new URL("http://x/api/agent/v1/snapshot?debug=1")), false);
  assert.equal(browserApiRouteAllowed("GET", new URL("http://x/api/agent/native/v1/snapshot")), false);
  assert.equal(browserApiRouteAllowed("POST", new URL(`http://x/api/agent/v1/memory-transactions/${UUID}/confirm`)), true);
  assert.equal(browserApiRouteAllowed("GET", new URL("http://x/api/agent/auth/callback?code=x&state=y")), true);
});

test("binding workflow proxies only exact browser paths with same-origin writes", async () => {
  const f = await fixture((req, res, seen) => {
    const chunks = [];
    req.on("data", (chunk) => chunks.push(chunk));
    req.on("end", () => {
      seen.push({ method: req.method, url: req.url, headers: req.headers, body: Buffer.concat(chunks).toString() });
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end('{"ok":true}');
    });
  });
  try {
    const requests = [
      ["GET", "/api/agent/v1/principal-binding-proposal", ""],
      ["POST", "/api/agent/v1/principal-binding-request", "{}"],
      ["POST", "/api/agent/v1/principal-binding-request/cancel", "{}"],
      ["POST", "/api/agent/v1/principal-binding-proposal/confirm", JSON.stringify({
        proposal_digest: "a".repeat(64),
        confirmation_nonce: UUID,
      })],
    ];
    for (const [method, target, body] of requests) {
      const response = await request(f.originPort, target, {
        method,
        origin: method === "POST" ? PUBLIC_ORIGIN : undefined,
        headers: {
          Cookie: "legacy=drop; __Host-home_agent=session",
          "Content-Type": "application/json",
          "X-CSRF-Token": "csrf",
          "X-Authenticated-HA-User": "forged",
          "X-Home-Agent-Principal": "forged",
        },
        body,
      });
      assert.equal(response.status, 200);
    }
    assert.deepEqual(f.seen.map(({ method, url }) => ({ method, url })), requests.map(
      ([method, url]) => ({ method, url }),
    ));
    for (const item of f.seen) {
      assert.equal(item.headers.cookie, "__Host-home_agent=session");
      assert.equal(item.headers["x-authenticated-ha-user"], undefined);
      assert.equal(item.headers["x-home-agent-principal"], undefined);
    }

    const seenBeforeDenials = f.seen.length;
    for (const [method, target, origin] of [
      ["POST", "/api/agent/v1/principal-binding-proposal", PUBLIC_ORIGIN],
      ["GET", "/api/agent/v1/principal-binding-request", undefined],
      ["GET", "/api/agent/v1/principal-binding-proposal?person=forged", undefined],
      ["POST", "/api/agent/v1/principal-binding-request?person=forged", PUBLIC_ORIGIN],
      ["POST", "/api/agent/v1/principal-bindings", PUBLIC_ORIGIN],
      ["POST", "/api/agent/v1/relationships/parent-confirmations", PUBLIC_ORIGIN],
      ["GET", "/api/agent/v1/people", undefined],
    ]) {
      const response = await request(f.originPort, target, { method, origin, body: method === "POST" ? "{}" : "" });
      assert.equal(response.status, 404);
    }
    const missingOrigin = await request(f.originPort, "/api/agent/v1/principal-binding-request", {
      method: "POST", body: "{}",
    });
    assert.equal(missingOrigin.status, 403);
    assert.equal(f.seen.length, seenBeforeDenials);
  } finally {
    await f.cleanup();
  }
});

test("onboarding status proxies only the exact authenticated GET path", async () => {
  const f = await fixture((req, res, seen) => {
    seen.push({ method: req.method, url: req.url, cookie: req.headers.cookie });
    res.writeHead(401, { "Content-Type": "application/json" });
    res.end('{"error":"authentication_required"}');
  });
  try {
    const allowed = await request(f.originPort, "/api/agent/v1/onboarding/status", {
      headers: { Cookie: "legacy=drop; __Host-home_agent=session" },
    });
    assert.equal(allowed.status, 401);
    assert.deepEqual(f.seen, [{
      method: "GET",
      url: "/api/agent/v1/onboarding/status",
      cookie: "__Host-home_agent=session",
    }]);

    const wrongMethod = await request(
      f.originPort,
      "/api/agent/v1/onboarding/status",
      { method: "POST", origin: PUBLIC_ORIGIN },
    );
    const queryBearing = await request(
      f.originPort,
      "/api/agent/v1/onboarding/status?debug=1",
    );
    assert.equal(wrongMethod.status, 404);
    assert.equal(queryBearing.status, 404);
    assert.equal(f.seen.length, 1);
  } finally {
    await f.cleanup();
  }
});

test("cookie filtering forwards only unambiguous Home Agent cookies", () => {
  assert.equal(
    filteredAgentCookies("legacy=secret; __Host-home_agent=session; tracking=x; __Host-home_agent_oauth=nonce"),
    "__Host-home_agent=session; __Host-home_agent_oauth=nonce",
  );
  assert.equal(filteredAgentCookies("__Host-home_agent=one; __Host-home_agent=two"), null);
});

test("origin serves only the four built Agent assets with hardened headers", async () => {
  const f = await fixture((_req, res) => res.end());
  try {
    const page = await request(f.originPort, "/home-agent/");
    assert.equal(page.status, 200);
    assert.match(page.body, /Agent/);
    assert.equal(page.headers["content-security-policy"].includes("frame-ancestors 'none'"), true);
    assert.equal(page.headers["strict-transport-security"], "max-age=31536000");
    assert.equal(page.headers["referrer-policy"], "no-referrer");
    assert.equal((await request(f.originPort, "/home-agent/panel.jsx")).status, 404);
    assert.equal((await request(f.originPort, "/home-agent/%2e%2e/index.html")).status, 404);
    assert.equal((await request(f.originPort, "/home-agent/index.html?v=1")).status, 404);
    assert.equal((await request(f.originPort, "/healthz")).status, 404);
    assert.equal((await request(f.originPort, "/proxy/ha/api/states")).status, 404);
    assert.equal((await request(f.originPort, "/")).status, 404);
    assert.equal((await request(f.originPort, "/home-agent/", { host: "legacy.test" })).status, 404);
    assert.equal((await request(f.originPort, "/home-agent/", { origin: "https://legacy.test" })).status, 403);
  } finally {
    await f.cleanup();
  }
});

test("deployment has pinned internal ingress and no host port or egress network", () => {
  const composePath = new URL(
    "../../../home-agent-deploy/agent-origin/compose.yml",
    import.meta.url,
  );
  const compose = fs.readFileSync(composePath, "utf8");
  assert.doesNotMatch(compose, /^\s+ports:/m);
  assert.match(compose, /core-api:\s*\n\s*ipv4_address:.*HOME_AGENT_WEB_INTERNAL_IP/);
  assert.doesNotMatch(compose, /origin-public|bff-public|default:/);
  assert.match(compose, /external:\s*true\s*\n\s*name:.*home-agent_api-net/);
  assert.match(compose, /additional_contexts:\s*\n\s*agent_assets:/);
  assert.doesNotMatch(compose, /^\s+volumes:/m);
  const dockerfile = fs.readFileSync(new URL("../Dockerfile", import.meta.url), "utf8");
  const assetCopies = dockerfile.match(/^COPY --from=agent_assets .+$/gm) || [];
  assert.equal(assetCopies.length, 4);
  assert.doesNotMatch(dockerfile, /^COPY\s+\.\s/m);
});

test("runtime logging is fixed text and cannot interpolate request targets", () => {
  const implementation = fs.readFileSync(new URL("../src/origin.mjs", import.meta.url), "utf8");
  const entrypoint = fs.readFileSync(new URL("../server.mjs", import.meta.url), "utf8");
  assert.doesNotMatch(implementation, /console\.(?:log|info|warn|error)/);
  const logCalls = entrypoint.match(/console\.(?:log|info|warn|error)\([^\n]+/g) || [];
  assert.equal(logCalls.length, 4);
  for (const call of logCalls) {
    assert.match(call, /^console\.(?:log|error)\("\[home-agent-origin\] [a-z_]+"\);$/);
  }
});

test("network proof rejects default routes and reachable or ambiguous failures", () => {
  const isolated = [
    "Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\tMTU\tWindow\tIRTT",
    "eth0\t000017AC\t00000000\t0001\t0\t0\t0\t0000FFFF\t0\t0\t0",
  ].join("\n");
  const routed = `${isolated}\neth0\t00000000\t010017AC\t0003\t0\t0\t0\t00000000\t0\t0\t0`;
  assert.equal(hasDefaultIpv4Route(isolated), false);
  assert.equal(hasDefaultIpv4Route(routed), true);
  assert.equal(isUnreachableErrorCode("ENETUNREACH"), true);
  assert.equal(isUnreachableErrorCode("EHOSTUNREACH"), true);
  for (const code of ["ECONNREFUSED", "ECONNRESET", "ETIMEDOUT", "CONNECTED", ""]) {
    assert.equal(isUnreachableErrorCode(code), false, code);
  }
});

test("OAuth callback remains functional without logging its code or state", async () => {
  const code = "canary-secret-authorization-code";
  const state = "canary-secret-oauth-state";
  const capturedLogs = [];
  const originalLog = console.log;
  const originalError = console.error;
  console.log = (...values) => capturedLogs.push(values.join(" "));
  console.error = (...values) => capturedLogs.push(values.join(" "));
  const f = await fixture((req, res, seen) => {
    seen.push(req.url);
    res.writeHead(302, {
      Location: "/home-agent/index.html",
      "Set-Cookie": [
        "__Host-home_agent=session; Path=/; Secure; HttpOnly; SameSite=Strict",
        "legacy=must-not-leave; Path=/",
      ],
    });
    res.end();
  });
  try {
    const result = await request(
      f.originPort,
      `/api/agent/auth/callback?code=${code}&state=${state}`,
      { headers: { Cookie: "__Host-home_agent_oauth=nonce; legacy=inbound" } },
    );
    assert.equal(result.status, 302);
    assert.equal(result.headers.location, "/home-agent/index.html");
    assert.deepEqual(result.headers["set-cookie"], [
      "__Host-home_agent=session; Path=/; Secure; HttpOnly; SameSite=Strict",
    ]);
    assert.equal(f.seen[0], `/api/agent/auth/callback?code=${code}&state=${state}`);
    const logs = capturedLogs.join("\n");
    assert.equal(logs.includes(code), false);
    assert.equal(logs.includes(state), false);
  } finally {
    console.log = originalLog;
    console.error = originalError;
    await f.cleanup();
  }
});

test("semantic proxy strips authority headers and rejects missing same-origin proof", async () => {
  const f = await fixture((req, res, seen) => {
    const chunks = [];
    req.on("data", (chunk) => chunks.push(chunk));
    req.on("end", () => {
      seen.push({ url: req.url, headers: req.headers, body: Buffer.concat(chunks).toString() });
      res.writeHead(200, { "Content-Type": "application/json", "X-Request-Id": "safe-id" });
      res.end('{"ok":true}');
    });
  });
  try {
    const body = '{"enabled":false}';
    const denied = await request(f.originPort, "/api/agent/v1/preferences/location_memory", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body,
    });
    assert.equal(denied.status, 403);
    assert.equal(f.seen.length, 0);

    const allowed = await request(f.originPort, "/api/agent/v1/preferences/location_memory", {
      method: "PUT",
      origin: PUBLIC_ORIGIN,
      headers: {
        Authorization: "Bearer must-not-forward",
        Cookie: "legacy=drop; __Host-home_agent=session",
        "Content-Type": "application/json",
        "X-CSRF-Token": "csrf",
        "X-Forwarded-For": "203.0.113.9",
        "X-Home-Agent-Principal": "attacker",
      },
      body,
    });
    assert.equal(allowed.status, 200);
    assert.equal(f.seen.length, 1);
    assert.equal(f.seen[0].headers.authorization, undefined);
    assert.equal(f.seen[0].headers["x-forwarded-for"], undefined);
    assert.equal(f.seen[0].headers["x-home-agent-principal"], undefined);
    assert.equal(f.seen[0].headers.cookie, "__Host-home_agent=session");
    assert.equal(f.seen[0].headers.origin, PUBLIC_ORIGIN);
    assert.equal(f.seen[0].headers["x-csrf-token"], "csrf");
    assert.equal(f.seen[0].body, body);
  } finally {
    await f.cleanup();
  }
});

test("unsafe upstream redirects are converted to a generic failure", async () => {
  const f = await fixture((_req, res) => {
    res.writeHead(302, { Location: "https://evil.test/collect" });
    res.end();
  });
  try {
    const result = await request(f.originPort, "/api/agent/auth/callback?code=x&state=y");
    assert.equal(result.status, 502);
    assert.equal(result.headers.location, undefined);
  } finally {
    await f.cleanup();
  }
});
