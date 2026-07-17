#!/usr/bin/env node
"use strict";

const childProcess = require("node:child_process");
const fs = require("node:fs");
const http = require("node:http");
const os = require("node:os");
const path = require("node:path");

const REPO = path.resolve(__dirname, "..");
const SERVER = path.join(REPO, "web-gateway", "server.mjs");
const WEB_RUNTIME = path.join(REPO, "app", "src", "home-web-runtime.js");
const USERNAME = "vision-test";
const PASSWORD = "test-password-not-real";
const BASIC_AUTH = `Basic ${Buffer.from(`${USERNAME}:${PASSWORD}`).toString("base64")}`;

let passes = 0;
let fails = 0;

function assert(name, condition, detail) {
  if (condition) {
    passes += 1;
    process.stdout.write(`  PASS  ${name}\n`);
    return;
  }
  fails += 1;
  process.stdout.write(`  FAIL  ${name}`);
  if (detail !== undefined) {
    const dumped = typeof detail === "string" ? detail : JSON.stringify(detail, null, 2);
    process.stdout.write(`\n        ${dumped.replace(/\n/g, "\n        ")}`);
  }
  process.stdout.write("\n");
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

function reservePort() {
  return new Promise((resolve, reject) => {
    const probe = http.createServer();
    probe.once("error", reject);
    probe.listen(0, "127.0.0.1", () => {
      const port = probe.address().port;
      probe.close(() => resolve(port));
    });
  });
}

function request(port, urlPath, { method = "GET", headers = {}, body = null } = {}) {
  return new Promise((resolve, reject) => {
    const req = http.request({
      host: "127.0.0.1",
      port,
      path: urlPath,
      method,
      headers,
    }, (res) => {
      const chunks = [];
      res.on("data", (chunk) => chunks.push(Buffer.from(chunk)));
      res.on("end", () => resolve({
        status: res.statusCode,
        headers: res.headers,
        body: Buffer.concat(chunks),
      }));
    });
    req.once("error", reject);
    if (body) req.write(body);
    req.end();
  });
}

async function waitForGateway(port, child) {
  for (let i = 0; i < 50; i += 1) {
    if (child.exitCode != null) throw new Error(`gateway exited early with ${child.exitCode}`);
    try {
      const res = await request(port, "/healthz");
      if (res.status === 200) return JSON.parse(res.body.toString("utf8"));
    } catch {
      // Keep polling while the short-lived test process starts.
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
  }
  throw new Error("gateway did not become ready");
}

(async function main() {
  process.stdout.write("\nweb_gateway_grounded_vision_test\n");

  const upstreamRequests = [];
  const tempDir = fs.mkdtempSync(path.join(os.tmpdir(), "home-grounded-vision-"));
  const authFile = path.join(tempDir, "missing-auth.json");
  const upstream = http.createServer((req, res) => {
    upstreamRequests.push({
      method: req.method,
      url: req.url,
      authorization: req.headers.authorization || "",
      cookie: req.headers.cookie || "",
    });
    if (req.url === "/reason/latest" || req.url === "/describe/latest") {
      res.writeHead(200, {
        "Content-Type": "text/html; charset=utf-8",
        "Cache-Control": "public, max-age=3600",
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Credentials": "true",
        "Set-Cookie": "vision_adapter_cookie=must-not-escape; Path=/",
        "WWW-Authenticate": "Basic realm=vision-adapter",
      });
      res.end(JSON.stringify({
        camera: "living_room",
        answer: "A cup is on the table.",
        annotated_url: "/reason/living_room/annotated.jpg",
      }));
      return;
    }
    res.writeHead(200, {
      "Content-Type": "image/jpeg",
      "Cache-Control": "no-store",
      "Access-Control-Allow-Origin": "*",
    });
    if (req.method === "HEAD") res.end();
    else res.end(Buffer.from([0xff, 0xd8, 0xff, 0xd9]));
  });

  let child = null;
  try {
    const upstreamPort = await listen(upstream);
    const gatewayPort = await reservePort();
    const stderr = [];
    child = childProcess.spawn(process.execPath, [SERVER], {
      cwd: REPO,
      env: {
        ...process.env,
        HOME_WEB_HOST: "127.0.0.1",
        HOME_WEB_PORT: String(gatewayPort),
        HOME_WEB_AUTH_REQUIRED: "1",
        HOME_WEB_AUTH_FILE: authFile,
        HOME_WEB_BASIC_AUTH: `${USERNAME}:${PASSWORD}`,
        HOME_WEB_VISION_TARGET: `http://127.0.0.1:${upstreamPort}`,
      },
      stdio: ["ignore", "ignore", "pipe"],
    });
    child.stderr.on("data", (chunk) => stderr.push(String(chunk)));

    const health = await waitForGateway(gatewayPort, child);
    const routePrefixes = (health.routes || []).map((route) => route.prefix);
    assert("health advertises the narrow result route", routePrefixes.includes("/proxy/vision-results"), routePrefixes);
    assert("health keeps the compatibility vision route during migration", routePrefixes.includes("/proxy/vision"), routePrefixes);

    const unauthenticated = await request(gatewayPort, "/proxy/vision-results/reason/latest");
    assert("result metadata requires gateway authentication", unauthenticated.status === 401, unauthenticated.status);
    assert("unauthenticated metadata does not reach vision", upstreamRequests.length === 0, upstreamRequests);

    const latest = await request(gatewayPort, "/proxy/vision-results/reason/latest", {
      headers: { Authorization: BASIC_AUTH },
    });
    assert("authenticated result metadata is proxied", latest.status === 200, latest.status);
    assert("metadata is never cached", latest.headers["cache-control"] === "no-store", latest.headers);
    assert("metadata content type is pinned to JSON", latest.headers["content-type"] === "application/json; charset=utf-8", latest.headers);
    assert("result responses are same-origin resources", latest.headers["cross-origin-resource-policy"] === "same-origin", latest.headers);
    assert("upstream CORS headers are removed", !latest.headers["access-control-allow-origin"] && !latest.headers["access-control-allow-credentials"], latest.headers);
    assert("upstream auth challenges and cookies are removed", !latest.headers["www-authenticate"] && latest.headers["set-cookie"]?.length === 1, latest.headers);
    assert("browser credentials are stripped upstream", upstreamRequests[0]?.authorization === "" && upstreamRequests[0]?.cookie === "", upstreamRequests[0]);
    const sessionCookie = String(latest.headers["set-cookie"]?.[0] || "").split(";", 1)[0];
    assert("successful gateway authentication issues a session cookie", sessionCookie.startsWith("home_web_auth="), latest.headers["set-cookie"]);

    const annotated = await request(
      gatewayPort,
      "/proxy/vision-results/reason/living_room/annotated.jpg?cb=1720000000000",
      { headers: { Cookie: sessionCookie } },
    );
    assert("annotated grounded-look image is proxied", annotated.status === 200 && annotated.headers["content-type"] === "image/jpeg", {
      status: annotated.status,
      headers: annotated.headers,
    });
    assert("cache-busted image is briefly immutable", annotated.headers["cache-control"] === "private, max-age=60, immutable", annotated.headers);
    assert("session cookie is stripped upstream", upstreamRequests[1]?.cookie === "", upstreamRequests[1]);

    const bearer = await request(
      gatewayPort,
      "/proxy/vision-results/reason/living_room/annotated.jpg?cb=1720000000002",
      { headers: { Cookie: sessionCookie, Authorization: "Bearer browser-secret-must-not-leak" } },
    );
    assert("authenticated image can be read with an unrelated browser bearer present", bearer.status === 200, bearer.status);
    assert("all browser authorization is stripped upstream", upstreamRequests[2]?.authorization === "", upstreamRequests[2]);

    const overview = await request(
      gatewayPort,
      "/proxy/vision-results/reason_zoom/living_room/overview.jpg?cb=1720000000001",
      { method: "HEAD", headers: { Cookie: sessionCookie } },
    );
    assert("HEAD is allowed for zoom result images", overview.status === 200 && overview.body.length === 0, {
      status: overview.status,
      bodyLength: overview.body.length,
    });
    assert("HEAD is preserved upstream", upstreamRequests[3]?.method === "HEAD", upstreamRequests[3]);

    const describe = await request(
      gatewayPort,
      "/proxy/vision-results/describe/latest",
      { headers: { Cookie: sessionCookie } },
    );
    assert("describe metadata is available read-only", describe.status === 200, describe.status);

    const snapshot = await request(
      gatewayPort,
      "/proxy/vision-results/snapshot/living_room/latest.jpg?cb=1720000000003",
      { headers: { Cookie: sessionCookie } },
    );
    assert("camera-scoped describe snapshot is available read-only", snapshot.status === 200, snapshot.status);

    const beforeForbidden = upstreamRequests.length;
    const forbidden = [
      ["POST", "/proxy/vision-results/reason/latest"],
      ["GET", "/proxy/vision-results/reason/latest?within_ms=999999"],
      ["GET", "/proxy/vision-results/reason"],
      ["POST", "/proxy/vision-results/reason_zoom"],
      ["GET", "/proxy/vision-results/api/status"],
      ["GET", "/proxy/vision-results/reason/living_room/annotated.jpg?cb=not-a-number"],
      ["GET", "/proxy/vision-results/reason/living_room/annotated.jpg?cb=1&extra=1"],
      ["GET", "/proxy/vision-results/snapshot/living_room/current.jpg"],
    ];
    for (const [method, urlPath] of forbidden) {
      const response = await request(gatewayPort, urlPath, {
        method,
        headers: { Cookie: sessionCookie },
      });
      assert(`${method} ${urlPath} is denied`, response.status === 403, response.status);
    }
    assert("denied narrow-route requests never reach vision", upstreamRequests.length === beforeForbidden, upstreamRequests.slice(beforeForbidden));

    const generic = await request(gatewayPort, "/proxy/vision/reason/latest", {
      headers: { Cookie: sessionCookie },
    });
    assert("existing natural-look compatibility route still works", generic.status === 200, generic.status);
    assert("compatibility route strips vision-adapter cookies", !generic.headers["set-cookie"], generic.headers);
    assert("compatibility request reaches vision only after authentication", upstreamRequests.length === beforeForbidden + 1, upstreamRequests.slice(beforeForbidden));

    const groundedPost = await request(gatewayPort, "/proxy/vision/reason_zoom", {
      method: "POST",
      headers: {
        Cookie: sessionCookie,
        Authorization: "Bearer browser-secret-must-not-leak",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ camera: "living_room", question: "what is on the coffee table" }),
    });
    assert("natural-look reason_zoom POST remains available", groundedPost.status === 200, groundedPost.status);
    assert("compatibility vision POST strips browser authorization", upstreamRequests.at(-1)?.authorization === "", upstreamRequests.at(-1));

    const blockedCompatibilityApi = await request(gatewayPort, "/proxy/vision/api/status", {
      headers: { Cookie: sessionCookie },
    });
    assert("compatibility route no longer exposes arbitrary vision API paths", blockedCompatibilityApi.status === 403, blockedCompatibilityApi.status);

    const blockedCompatibilityDelete = await request(gatewayPort, "/proxy/vision/reason/latest", {
      method: "DELETE",
      headers: { Cookie: sessionCookie },
    });
    assert("compatibility route rejects mutation methods", blockedCompatibilityDelete.status === 403, blockedCompatibilityDelete.status);

    const runtime = fs.readFileSync(WEB_RUNTIME, "utf8");
    assert(
      "browser runtime publishes the dedicated grounded result base",
      runtime.includes('HG_DEFAULT_GROUNDED_VISION_BASE: "/proxy/vision-results"'),
    );

    if (stderr.length) {
      assert("gateway emitted no stderr", false, stderr.join(""));
    }
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
})().catch((error) => {
  process.stderr.write(`${error.stack || error}\n`);
  process.exit(1);
});
