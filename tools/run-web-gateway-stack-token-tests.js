#!/usr/bin/env node
"use strict";

const childProcess = require("node:child_process");
const fs = require("node:fs");
const http = require("node:http");
const os = require("node:os");
const path = require("node:path");

const REPO = path.resolve(__dirname, "..");
const SERVER = path.join(REPO, "web-gateway", "server.mjs");
const FAKE_TOKEN = "test-stack-token-not-real";

let passes = 0;
let fails = 0;

function assert(name, cond, detail) {
  if (cond) {
    passes += 1;
    process.stdout.write(`  PASS  ${name}\n`);
  } else {
    fails += 1;
    process.stdout.write(`  FAIL  ${name}`);
    if (detail !== undefined) {
      const dumped = typeof detail === "string" ? detail : JSON.stringify(detail, null, 2);
      process.stdout.write(`\n        ${dumped.replace(/\n/g, "\n        ")}`);
    }
    process.stdout.write("\n");
  }
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
  return new Promise((resolve) => server.close(() => resolve()));
}

function request(port, urlPath, headers = {}) {
  return new Promise((resolve, reject) => {
    const req = http.request({
      host: "127.0.0.1",
      port,
      path: urlPath,
      method: "GET",
      headers,
    }, (res) => {
      let body = "";
      res.setEncoding("utf8");
      res.on("data", (chunk) => { body += chunk; });
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
      if (res.status === 200) return JSON.parse(res.body);
    } catch {
      // keep polling
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error("gateway did not become ready");
}

(async function main() {
  process.stdout.write("\nweb_gateway_stack_token_proxy_test\n");

  const upstreamRequests = [];
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), "home-gateway-token-"));
  const tokenFile = path.join(tempDir, ".env");
  fs.writeFileSync(tokenFile, `# fake test file\nSTACK_TOKEN='${FAKE_TOKEN}'\n`, "utf8");
  const upstream = http.createServer((req, res) => {
    upstreamRequests.push({
      url: req.url,
      authorization: req.headers.authorization || "",
    });
    res.writeHead(200, {
      "Content-Type": "application/json; charset=utf-8",
      "Cache-Control": "no-store",
    });
    res.end(JSON.stringify({ ok: true }));
  });

  let child = null;
  try {
    const upstreamPort = await listen(upstream);
    const gatewayPort = await new Promise((resolve, reject) => {
      const probe = http.createServer();
      probe.once("error", reject);
      probe.listen(0, "127.0.0.1", () => {
        const port = probe.address().port;
        probe.close(() => resolve(port));
      });
    });

    child = childProcess.spawn(process.execPath, [SERVER], {
      cwd: REPO,
      env: {
        ...process.env,
        HOME_WEB_HOST: "127.0.0.1",
        HOME_WEB_PORT: String(gatewayPort),
        HOME_WEB_AUTH_REQUIRED: "0",
        HOME_WEB_SUPERVISOR_TARGET: `http://127.0.0.1:${upstreamPort}`,
        HOME_WEB_STACK_TOKEN: "",
        STACK_TOKEN: "",
        HOME_WEB_STACK_TOKEN_FILE: tokenFile,
      },
      stdio: ["ignore", "ignore", "pipe"],
    });

    const health = await waitForGateway(gatewayPort, child);
    assert("health reports token proxy enabled", health.stackTokenProxy?.enabled === true, health.stackTokenProxy);
    assert("health reports token source without token value", health.stackTokenProxy?.source === "file", health.stackTokenProxy);

    const html = await request(gatewayPort, "/");
    assert("index serves successfully", html.status === 200, html.status);
    assert("index exposes gateway proxy marker", html.body.includes("window.__HOME_WEB_STACK_TOKEN_PROXY=true"), html.body.slice(0, 300));
    assert("index does not expose real token", !html.body.includes(FAKE_TOKEN));

    upstreamRequests.length = 0;
    const stack = await request(gatewayPort, "/proxy/supervisor/api/stack/status", {
      Authorization: "Bearer browser-supplied-token",
    });
    assert("stack API request succeeds through proxy", stack.status === 200, stack.status);
    assert("stack API receives gateway token", upstreamRequests[0]?.authorization === `Bearer ${FAKE_TOKEN}`, upstreamRequests[0]);

    upstreamRequests.length = 0;
    const healthz = await request(gatewayPort, "/proxy/supervisor/healthz", {
      Authorization: "Bearer browser-supplied-token",
    });
    assert("supervisor health request succeeds through proxy", healthz.status === 200, healthz.status);
    assert("supervisor health receives no browser token", upstreamRequests[0]?.authorization === "", upstreamRequests[0]);
  } finally {
    if (child && child.exitCode == null) child.kill();
    await close(upstream);
    fs.rmSync(tempDir, { recursive: true, force: true });
  }

  if (fails) {
    process.stdout.write(`\n${passes} pass . ${fails} fail\n`);
    process.exit(1);
  }
  process.stdout.write(`\n${passes} pass . 0 fail\n`);
})();
