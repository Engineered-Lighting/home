#!/usr/bin/env node
"use strict";

const childProcess = require("node:child_process");
const fs = require("node:fs");
const http = require("node:http");
const os = require("node:os");
const path = require("node:path");

const REPO = path.resolve(__dirname, "..");
const SERVER = path.join(REPO, "web-gateway", "server.mjs");
const BASIC = `Basic ${Buffer.from("operator:correct horse battery staple").toString("base64")}`;
const BEARER = "Bearer native-ha-access-test-token";
const AGENT_ORIGIN = "https://agent.home.test";
const AGENT_HOST = "agent.home.test";
const NATIVE_ORIGIN = "https://native-agent.home.test";
const NATIVE_HOST = "native-agent.home.test";
let passes = 0;
let fails = 0;

function assert(name, condition, detail) {
  if (condition) {
    passes += 1;
    process.stdout.write(`  PASS  ${name}\n`);
    return;
  }
  fails += 1;
  process.stdout.write(`  FAIL  ${name}${detail === undefined ? "" : `\n        ${JSON.stringify(detail)}`}\n`);
}

function listen(server) {
  return new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      server.off("error", reject);
      resolve(server.address().port);
    });
  });
}

function close(server) {
  return new Promise((resolve) => server.close(resolve));
}

function reservePort() {
  const server = http.createServer();
  return listen(server).then((port) => close(server).then(() => port));
}

function request(port, pathname, { method = "GET", headers = {}, body } = {}) {
  return new Promise((resolve, reject) => {
    const requestHeaders = pathname.startsWith("/api/agent/native/") && headers.Host === undefined
      ? { ...headers, Host: NATIVE_HOST }
      : headers;
    const req = http.request({
      host: "127.0.0.1", port, path: pathname, method, headers: requestHeaders,
    }, (res) => {
      let value = "";
      res.setEncoding("utf8");
      res.on("data", (chunk) => { value += chunk; });
      res.on("end", () => resolve({ status: res.statusCode, headers: res.headers, body: value }));
    });
    req.once("error", reject);
    if (body !== undefined) req.end(body);
    else req.end();
  });
}

function websocketUpgrade(port, pathname, { headers = {} } = {}) {
  return new Promise((resolve, reject) => {
    const req = http.request({
      host: "127.0.0.1",
      port,
      path: pathname,
      method: "GET",
      headers: {
        Connection: "Upgrade",
        Upgrade: "websocket",
        "Sec-WebSocket-Key": Buffer.alloc(16).toString("base64"),
        "Sec-WebSocket-Version": "13",
        ...headers,
      },
    });
    req.once("upgrade", (res, socket) => {
      socket.destroy();
      resolve({ status: res.statusCode, headers: res.headers });
    });
    req.once("response", (res) => {
      res.resume();
      res.once("end", () => resolve({ status: res.statusCode, headers: res.headers }));
    });
    req.once("error", reject);
    req.setTimeout(2_000, () => req.destroy(new Error("websocket upgrade probe timed out")));
    req.end();
  });
}

async function waitForGateway(port, child) {
  for (let attempt = 0; attempt < 60; attempt += 1) {
    if (child.exitCode != null) throw new Error(`gateway exited ${child.exitCode}`);
    try {
      const response = await request(port, "/healthz", { headers: { Authorization: BASIC } });
      if (response.status === 200) return;
    } catch {}
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error("gateway did not start");
}

(async function main() {
  process.stdout.write("\nnative_agent_gateway_security_test\n");
  const gatewaySource = fs.readFileSync(SERVER, "utf8");
  assert(
    "gateway bounds native bodies and global header/socket resources",
    [
      "NATIVE_AGENT_MAX_BODY_BYTES",
      "NATIVE_AGENT_REQUEST_TIMEOUT_MS",
      "headersTimeout: GATEWAY_HEADER_TIMEOUT_MS",
      "maxHeaderSize: 16 * 1024",
      "server.maxHeadersCount = 64",
      "server.maxRequestsPerSocket = 100",
    ].every((token) => gatewaySource.includes(token)),
  );
  const upstreamRequests = [];
  const upstreamUpgrades = [];
  const upstream = http.createServer((req, res) => {
    upstreamRequests.push({
      method: req.method,
      url: req.url,
      authorization: req.headers.authorization || "",
      dpop: req.headers.dpop || "",
      cookie: req.headers.cookie || "",
      actor: req.headers["x-actor-id"] || "",
      principal: req.headers["x-principal-id"] || "",
      haUser: req.headers["x-authenticated-ha-user"] || "",
      forwardedUser: req.headers["x-forwarded-user"] || "",
      channel: req.headers["x-home-agent-channel"] || "",
      installation: req.headers["x-home-agent-installation"] || "",
    });
    const native = req.url.startsWith("/api/agent/native/");
    const authorized = req.headers.authorization === BEARER;
    if (req.url.includes("/preferences/travel_greetings")) {
      res.writeHead(302, {
        Location: "https://attacker.invalid/redirect",
        "Set-Cookie": "attacker=1",
      });
      res.end();
      return;
    }
    res.writeHead(native && !authorized ? 401 : 200, {
      "Content-Type": "application/json",
      "Set-Cookie": "upstream=must-not-cross-native-boundary",
    });
    res.end(JSON.stringify({ ok: !native || authorized }));
  });
  upstream.on("upgrade", (req, socket) => {
    upstreamUpgrades.push({
      url: req.url,
      authorization: req.headers.authorization || "",
    });
    socket.write("HTTP/1.1 101 Switching Protocols\r\nConnection: Upgrade\r\nUpgrade: websocket\r\n\r\n");
    socket.destroy();
  });
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), "home-native-gateway-"));
  let child;
  try {
    const upstreamPort = await listen(upstream);
    const gatewayPort = await reservePort();
    child = childProcess.spawn(process.execPath, [SERVER], {
      cwd: REPO,
      env: {
        ...process.env,
        HOME_WEB_HOST: "127.0.0.1",
        HOME_WEB_PORT: String(gatewayPort),
        HOME_WEB_AUTH_REQUIRED: "1",
        HOME_WEB_BASIC_AUTH: "operator:correct horse battery staple",
        HOME_WEB_AGENT_ORIGINS: AGENT_ORIGIN,
        HOME_WEB_NATIVE_AGENT_ORIGIN: NATIVE_ORIGIN,
        HOME_WEB_AUTH_FILE: path.join(tempDir, "missing-auth.json"),
        HOME_WEB_AGENT_BFF_TARGET: `http://127.0.0.1:${upstreamPort}/api/agent`,
        // A stale target must not reactivate the quarantined broad HA proxy.
        HOME_WEB_HA_TARGET: `http://127.0.0.1:${upstreamPort}`,
        HOME_WEB_ENABLE_LEGACY_HA_PROXY: "0",
        HOME_WEB_TRACKER_TARGET: `http://127.0.0.1:${upstreamPort}`,
        HOME_WEB_ENABLE_LEGACY_TRACKER_PROXY: "1",
      },
      stdio: ["ignore", "ignore", "pipe"],
    });
    await waitForGateway(gatewayPort, child);

    upstreamRequests.length = 0;
    const disabledHaProxy = await request(gatewayPort, "/proxy/ha/api/states", {
      headers: { Authorization: BASIC },
    });
    assert("stale HA target cannot enable the quarantined broad proxy",
      disabledHaProxy.status === 404 && JSON.parse(disabledHaProxy.body).error === "capability_disabled" && upstreamRequests.length === 0,
      { status: disabledHaProxy.status, body: disabledHaProxy.body, upstreamRequests });

    const quarantinedLegacyUi = await request(gatewayPort, "/", {
      headers: { Authorization: BASIC },
    });
    assert("legacy Home UI explains that its HA proxy is quarantined",
      quarantinedLegacyUi.status === 503 && quarantinedLegacyUi.body.includes("Legacy Home web UI is quarantined"),
      { status: quarantinedLegacyUi.status, body: quarantinedLegacyUi.body.slice(0, 160) });

    const metadata = await request(gatewayPort, "/native-oauth-client", {
      headers: { Host: NATIVE_HOST },
    });
    assert("OAuth client metadata is public and small", metadata.status === 200 && Buffer.byteLength(metadata.body) < 10 * 1024, metadata.status);
    assert("metadata contains the exact locked redirect link", metadata.body.includes('<link rel="redirect_uri" href="http://127.0.0.1:43821/oauth/callback">'));
    assert("metadata has a deny-all CSP", String(metadata.headers["content-security-policy"]).includes("default-src 'none'"));

    const legacyMetadata = await request(gatewayPort, "/native-oauth-client");
    assert("OAuth metadata is absent from the legacy origin", legacyMetadata.status === 404, legacyMetadata.status);

    const agentPage = await request(gatewayPort, "/home-agent/index.html", {
      headers: { Host: AGENT_HOST },
    });
    assert("dedicated Agent origin serves only its local app without gateway Basic", agentPage.status === 200 && !agentPage.body.includes("../"), agentPage.status);

    upstreamRequests.length = 0;
    const agentSession = await request(gatewayPort, "/api/agent/auth/session", {
      headers: { Host: AGENT_HOST, Origin: AGENT_ORIGIN },
    });
    assert("browser Agent session route is available on the exact Agent origin", agentSession.status === 200 && upstreamRequests.length === 1, { status: agentSession.status, upstreamRequests });

    upstreamRequests.length = 0;
    const authStart = await request(gatewayPort, "/api/agent/auth/start", {
      method: "POST",
      headers: {
        Host: AGENT_HOST,
        Origin: AGENT_ORIGIN,
        "Content-Type": "application/json",
      },
      body: "{}",
    });
    assert("OAuth start is routed only through the exact Agent origin", authStart.status === 200 && upstreamRequests[0]?.url === "/api/agent/auth/start", { status: authStart.status, upstreamRequests });

    upstreamRequests.length = 0;
    const authCallback = await request(gatewayPort, "/api/agent/auth/callback?state=opaque&code=authorization-code", {
      headers: { Host: AGENT_HOST },
    });
    assert("OAuth callback remains reachable on the Agent origin", authCallback.status === 200 && upstreamRequests[0]?.url === "/api/agent/auth/callback?state=opaque&code=authorization-code", { status: authCallback.status, upstreamRequests });

    upstreamRequests.length = 0;
    const crossOriginAgent = await request(gatewayPort, "/api/agent/v1/memory-transactions", {
      method: "POST",
      headers: {
        Host: AGENT_HOST,
        Origin: "https://legacy.home.test",
        "Content-Type": "application/json",
      },
      body: "{}",
    });
    assert("legacy-origin XSS is rejected before BFF even on the Agent host", crossOriginAgent.status === 403 && upstreamRequests.length === 0, { status: crossOriginAgent.status, upstreamRequests });

    for (const denied of ["/", "/auth", "/healthz", "/native-oauth-client", "/proxy/ha/api/states"]) {
      upstreamRequests.length = 0;
      const response = await request(gatewayPort, denied, { headers: { Host: AGENT_HOST } });
      assert(`Agent origin denies legacy surface: ${denied}`, response.status === 404 && upstreamRequests.length === 0, { status: response.status, upstreamRequests });
    }

    for (const denied of ["/", "/home-agent/index.html", "/api/agent/auth/session"]) {
      upstreamRequests.length = 0;
      const response = await request(gatewayPort, denied, { headers: { Host: NATIVE_HOST } });
      assert(`native origin denies browser/legacy surface: ${denied}`, response.status === 404 && upstreamRequests.length === 0, { status: response.status, upstreamRequests });
    }

    for (const [name, host] of [["browser", AGENT_HOST], ["native", NATIVE_HOST]]) {
      upstreamUpgrades.length = 0;
      const response = await websocketUpgrade(gatewayPort, "/proxy/tracker/ws/tracks", {
        headers: { Host: host, Authorization: BASIC },
      });
      assert(
        `${name} Agent origin rejects websocket upgrades before legacy proxying`,
        response.status === 403 && upstreamUpgrades.length === 0,
        { status: response.status, upstreamUpgrades },
      );
    }

    for (const denied of ["/home-agent/index.html", "/api/agent/auth/session", "/api/agent/v1/snapshot"]) {
      upstreamRequests.length = 0;
      const response = await request(gatewayPort, denied, { headers: { Authorization: BASIC } });
      assert(`legacy origin denies browser Agent surface: ${denied}`, response.status === 404 && upstreamRequests.length === 0, { status: response.status, upstreamRequests });
    }

    upstreamRequests.length = 0;
    const nativeNoBearer = await request(gatewayPort, "/api/agent/native/v1/snapshot");
    assert("exact native route bypasses Basic but BFF rejects missing HA bearer", nativeNoBearer.status === 401, nativeNoBearer.status);
    assert("missing-bearer request reaches only the BFF", upstreamRequests.length === 1, upstreamRequests);

    upstreamRequests.length = 0;
    const nativeBearer = await request(gatewayPort, "/api/agent/native/v1/snapshot", { headers: {
      Authorization: BEARER,
      DPoP: "attestation-proof",
      Cookie: "home_web_auth=forged; private=forged",
      "X-Actor-ID": "forged-actor",
      "X-Principal-ID": "forged-principal",
      "X-Authenticated-HA-User": "forged-ha-user",
      "X-Forwarded-User": "forged-forwarded-user",
      "X-Home-Agent-Channel": "private_tauri_attested_v1",
      "X-Home-Agent-Installation": "018f6f42-3a8b-4c11-8123-123456789abc",
    } });
    assert("exact native route accepts HA bearer", nativeBearer.status === 200, nativeBearer.status);
    assert("gateway preserves native HA bearer", upstreamRequests[0]?.authorization === BEARER, upstreamRequests[0]);
    assert("gateway preserves only the native proof header needed by the BFF", upstreamRequests[0]?.dpop === "attestation-proof", upstreamRequests[0]);
    assert("native ingress strips cookies and all client identity assertions", upstreamRequests[0]?.cookie === "" && upstreamRequests[0]?.actor === "" && upstreamRequests[0]?.principal === "" && upstreamRequests[0]?.haUser === "" && upstreamRequests[0]?.forwardedUser === "" && upstreamRequests[0]?.channel === "" && upstreamRequests[0]?.installation === "", upstreamRequests[0]);
    assert("native responses cannot set browser cookies", nativeBearer.headers["set-cookie"] === undefined, nativeBearer.headers);

    for (const allowed of [
      ["POST", "/api/agent/native/v1/attestation/challenge"],
      ["GET", "/api/agent/native/v1/places/01900000-0000-7000-8000-000000000002/descriptor-relationship"],
      ["GET", "/api/agent/native/v1/places/01900000-0000-7000-8000-000000000002/parents/current-presence"],
    ]) {
      upstreamRequests.length = 0;
      const response = await request(gatewayPort, allowed[1], {
        method: allowed[0],
        headers: { Authorization: BEARER, "Content-Type": "application/json" },
        body: allowed[0] === "POST" ? "{}" : undefined,
      });
      assert(`exact private native route is allowed: ${allowed[1]}`,
        response.status === 200 && upstreamRequests[0]?.url === allowed[1],
        { status: response.status, upstream: upstreamRequests[0] });
    }

    upstreamRequests.length = 0;
    const nativeOnAgentHost = await request(gatewayPort, "/api/agent/native/v1/snapshot", {
      headers: { Host: AGENT_HOST, Authorization: BEARER },
    });
    assert("browser Agent host cannot relay a native proof", nativeOnAgentHost.status === 404 && upstreamRequests.length === 0, { status: nativeOnAgentHost.status, upstream: upstreamRequests[0] });

    upstreamRequests.length = 0;
    const nativeOnAlias = await request(gatewayPort, "/api/agent/native/v1/snapshot", {
      headers: { Host: "alias.home.test", Authorization: BEARER },
    });
    assert("native proof cannot traverse a noncanonical gateway alias", nativeOnAlias.status === 404 && upstreamRequests.length === 0, { status: nativeOnAlias.status, upstreamRequests });

    upstreamRequests.length = 0;
    const oversizedNativeBody = await request(
      gatewayPort,
      "/api/agent/native/v1/attestation/challenge",
      {
        method: "POST",
        headers: { Authorization: BEARER, "Content-Type": "application/json" },
        body: "x".repeat((64 * 1024) + 1),
      },
    );
    assert("native ingress rejects an oversized body before the BFF", oversizedNativeBody.status === 413 && upstreamRequests.length === 0, { status: oversizedNativeBody.status, upstreamRequests });

    upstreamRequests.length = 0;
    const nativeBasic = await request(gatewayPort, "/api/agent/native/v1/snapshot", { headers: {
      Authorization: BASIC,
      Cookie: "private=forged",
    } });
    assert("gateway Basic is never forwarded through native ingress", nativeBasic.status === 401 && upstreamRequests[0]?.authorization === "" && upstreamRequests[0]?.cookie === "", { status: nativeBasic.status, upstream: upstreamRequests[0] });

    upstreamRequests.length = 0;
    const nativeRedirect = await request(gatewayPort, "/api/agent/native/v1/preferences/travel_greetings", {
      method: "PUT",
      headers: { Authorization: BEARER, "Content-Type": "application/json" },
      body: "{}",
    });
    assert("native upstream redirects are converted to a contained 502", nativeRedirect.status === 502 && !nativeRedirect.headers.location && !nativeRedirect.headers["set-cookie"] && JSON.parse(nativeRedirect.body).error === "native_upstream_redirect_denied", { status: nativeRedirect.status, headers: nativeRedirect.headers, body: nativeRedirect.body });

    upstreamRequests.length = 0;
    const normalBearer = await request(gatewayPort, "/api/agent/v1/snapshot", { headers: { Authorization: BEARER } });
    assert("legacy origin cannot reach browser Agent routes with an HA bearer", normalBearer.status === 404, normalBearer.status);
    assert("legacy-origin browser route never reaches BFF", upstreamRequests.length === 0, upstreamRequests);

    upstreamRequests.length = 0;
    const agentBrowser = await request(gatewayPort, "/api/agent/v1/snapshot", {
      headers: { Host: AGENT_HOST, Origin: AGENT_ORIGIN, Authorization: BASIC },
    });
    assert("browser Agent route reaches BFF only on the Agent origin", agentBrowser.status === 200, agentBrowser.status);
    assert("gateway Basic credential is stripped at the Agent boundary", upstreamRequests[0]?.authorization === "", upstreamRequests[0]);

    for (const forbidden of [
      "/api/agent/native/v1/initiatives/claim",
      "/api/agent/native/v1/initiatives",
      "/api/agent/native/v1/initiatives/01900000-0000-7000-8000-000000000001/claim",
      "/api/agent/native/v1/places",
      "/api/agent/native/v1/parent-confirmations",
      "/api/agent/native/v1/relationships/parent-confirmations",
      "/api/agent/native/v1/operator-rollout/phase3-readiness",
      "/api/agent/native/v1/operator-rollout/phase3-readiness?debug=1",
      "/api/agent/native/v1/snapshot?url=https://attacker.invalid",
      "/api/agent/native/v1/memory-transactions/not-a-uuid",
    ]) {
      upstreamRequests.length = 0;
      const response = await request(gatewayPort, forbidden, { headers: { Authorization: BEARER } });
      assert(`non-allowlisted native path is denied: ${forbidden}`, response.status === 404 && upstreamRequests.length === 0, { status: response.status, upstreamRequests });
    }
  } finally {
    if (child && child.exitCode == null) child.kill();
    await close(upstream);
    fs.rmSync(tempDir, { recursive: true, force: true });
  }

  process.stdout.write(`\n${passes} pass · ${fails} fail\n`);
  if (fails) process.exit(1);
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
