#!/usr/bin/env node
// Den lighting brain route (/api/lighting-brain/v1/) and the den-ops status
// banner (/api/den-ops/v1/). Runs the real gateway against a fake brain.
import childProcess from "node:child_process";
import crypto from "node:crypto";
import fs from "node:fs";
import http from "node:http";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const SERVER = path.join(REPO, "web-gateway", "server.mjs");
const USER = "owner";
const PASSWORD = "correct-horse-battery-staple";
const PRODUCER_TOKEN = "fake-producer-token-not-real-0123456789";
const JETSON_MONITOR_TOKEN = "fake-jetson-monitor-token-not-real-0123456789";
const LA_MONITOR_TOKEN = "fake-la-monitor-token-not-real-0123456789abcd";
const BASE = "/api/lighting-brain/v1/homes/victoria";
const BASIC = `Basic ${Buffer.from(`${USER}:${PASSWORD}`).toString("base64")}`;

let passes = 0;
let fails = 0;
function assert(name, cond, detail) {
  if (cond) { passes += 1; process.stdout.write(`  PASS  ${name}\n`); return; }
  fails += 1;
  process.stdout.write(`  FAIL  ${name}${detail === undefined ? "" : `\n        ${JSON.stringify(detail)}`}\n`);
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const sha256 = (value) => crypto.createHash("sha256").update(value).digest("hex");
const freePort = () => new Promise((resolve, reject) => {
  const probe = net.createServer();
  probe.once("error", reject);
  probe.listen(0, "127.0.0.1", () => { const { port } = probe.address(); probe.close(() => resolve(port)); });
});

function request(port, urlPath, { method = "GET", headers = {}, body } = {}) {
  return new Promise((resolve, reject) => {
    const payload = body === undefined ? undefined : Buffer.from(typeof body === "string" ? body : JSON.stringify(body));
    const allHeaders = { ...headers };
    if (payload) {
      allHeaders["content-type"] = allHeaders["content-type"] || "application/json";
      allHeaders["content-length"] = String(payload.length);
    }
    const req = http.request({ host: "127.0.0.1", port, path: urlPath, method, headers: allHeaders }, (res) => {
      let text = "";
      res.setEncoding("utf8");
      res.on("data", (chunk) => { text += chunk; });
      res.on("end", () => {
        let json = null;
        try { json = JSON.parse(text); } catch { /* not JSON */ }
        resolve({ status: res.statusCode, headers: res.headers, body: text, json });
      });
    });
    req.once("error", reject);
    if (payload) req.write(payload);
    req.end();
  });
}

// Fake brain: records every request; /stream pushes a frame now and one later.
function startFakeBrain() {
  const seen = [];
  const streams = new Set();
  const server = http.createServer((req, res) => {
    const chunks = [];
    req.on("data", (chunk) => chunks.push(chunk));
    req.on("end", () => {
      const entry = { method: req.method, url: req.url, headers: req.headers, body: Buffer.concat(chunks).toString("utf8") };
      seen.push(entry);
      if (req.url.endsWith("/stream")) {
        res.writeHead(200, {
          "Content-Type": "text/event-stream; charset=utf-8",
          "Cache-Control": "no-store",
          "X-Accel-Buffering": "no",
        });
        res.write("retry: 3000\n\nid: 1\nevent: state\ndata: {\"n\":1}\n\n");
        const later = setTimeout(() => res.write("id: 2\nevent: state\ndata: {\"n\":2}\n\n"), 700);
        streams.add(res);
        res.on("close", () => { clearTimeout(later); streams.delete(res); entry.closed = true; });
        return;
      }
      res.writeHead(200, { "Content-Type": "application/json", "Cache-Control": "no-store" });
      res.end(JSON.stringify({ ok: true, schema: "activity-lighting-state-1", brain: "online" }));
    });
  });
  return new Promise((resolve) => server.listen(0, "127.0.0.1", () => resolve({ server, port: server.address().port, seen, streams })));
}

async function startGateway(env) {
  const port = await freePort();
  const child = childProcess.spawn(process.execPath, [SERVER], {
    cwd: REPO, env: { ...process.env, HOME_WEB_HOST: "127.0.0.1", HOME_WEB_PORT: String(port), ...env },
    stdio: ["ignore", "ignore", "pipe"],
  });
  for (let i = 0; i < 200; i += 1) {
    if (child.exitCode != null) throw new Error(`gateway exited early with ${child.exitCode}`);
    try {
      const res = await request(port, "/healthz");
      if (res.status === 200) return { port, child, health: res.json };
    } catch { /* keep polling */ }
    await sleep(100);
  }
  throw new Error("gateway did not become ready");
}

// Reads the SSE response through the gateway until `until(text)` or timeout.
function readStream(port, headers, { until, timeoutMs }) {
  return new Promise((resolve, reject) => {
    const started = Date.now();
    const marks = [];
    let text = "";
    const req = http.request({ host: "127.0.0.1", port, path: `${BASE}/stream`, headers }, (res) => {
      res.setEncoding("utf8");
      res.on("data", (chunk) => {
        text += chunk;
        marks.push({ at: Date.now() - started, text });
        if (until(text)) { req.destroy(); resolve({ status: res.statusCode, headers: res.headers, text, marks }); }
      });
      res.on("end", () => resolve({ status: res.statusCode, headers: res.headers, text, marks, ended: true }));
    });
    req.once("error", (error) => { if (!until(text)) reject(error); });
    req.end();
    setTimeout(() => { req.destroy(); resolve({ text, marks, timedOut: true }); }, timeoutMs);
  });
}

function status(host, level, extra = {}) {
  const steps = ["normal", "cadence_reduced", "deep_stack_paused", "loop_stopped"];
  return {
    schema: "den-ops-status-1", host, role: host.includes("jetson") ? "jetson" : "la", level, step: steps[level],
    latched: false, since: level ? 1000 : null, checked_at: 1060,
    message: level ? `Den lighting backed off: ${steps[level]}` : "Den lighting ops normal",
    reasons: level ? [{ signal: "temp.soc", level, detail: "SoC 83.0 °C ≥ 82 °C" }] : [],
    ...extra,
  };
}

async function main() {
  process.stdout.write("\nweb_gateway_lighting_brain_test\n");
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), "home-gateway-lighting-brain-"));
  const tokensFile = path.join(temp, "den-ops-monitors.json");
  fs.writeFileSync(tokensFile, JSON.stringify({
    schema: "den-ops-monitor-tokens-1",
    monitors: [
      { host: "victoria-jetson", token_sha256: sha256(JETSON_MONITOR_TOKEN) },
      { host: "la-host", token_sha256: sha256(LA_MONITOR_TOKEN) },
    ],
  }), { mode: 0o600 });
  const brain = await startFakeBrain();
  let gateway = null;
  try {
    gateway = await startGateway({
      HOME_WEB_AUTH_REQUIRED: "1",
      HOME_WEB_AUTH_FILE: path.join(temp, "missing-auth.json"),
      HOME_WEB_BASIC_AUTH: `${USER}:${PASSWORD}`,
      HOME_WEB_LIGHTING_BRAIN_TARGET: `http://127.0.0.1:${brain.port}/api/lighting-brain`,
      HOME_WEB_PROXY_RESPONSE_TIMEOUT_MS: "250",
      HOME_WEB_DEN_OPS_TOKENS_FILE: tokensFile,
      HOME_WEB_DEN_OPS_STALE_S: "1",
    });
    const { port, health } = gateway;
    const route = (health.routes || []).find((entry) => entry.prefix === "/api/lighting-brain");
    assert("healthz lists the lighting-brain route without websocket", route && route.ws === false, health.routes);
    assert("healthz reports den-ops status enabled with two monitors", health.denOpsStatus?.enabled === true && health.denOpsStatus.monitors === 2, health.denOpsStatus);

    // --- session reads ---------------------------------------------------
    brain.seen.length = 0;
    const anonymous = await request(port, `${BASE}/state`);
    assert("state without a session is 401", anonymous.status === 401, anonymous.status);
    assert("unauthenticated state never reaches the brain", brain.seen.length === 0, brain.seen);

    const basic = await request(port, `${BASE}/state`, { headers: { Authorization: BASIC } });
    assert("state with gateway Basic is proxied", basic.status === 200 && basic.json?.brain === "online", basic);
    const cookie = String(basic.headers["set-cookie"] || "").split(";")[0];
    assert("Basic login sets the session cookie", cookie.startsWith("home_web_auth="), basic.headers["set-cookie"]);
    assert("brain sees no gateway credential", !brain.seen[0]?.headers.authorization && !brain.seen[0]?.headers.cookie, brain.seen[0]?.headers);
    assert("brain sees the configured path", brain.seen[0]?.url === "/api/lighting-brain/v1/homes/victoria/state", brain.seen[0]?.url);

    brain.seen.length = 0;
    const withCookie = await request(port, `${BASE}/state`, {
      headers: { Cookie: `${cookie}; other=1`, Authorization: "Bearer browser-token-not-for-brain-000" },
    });
    assert("state with the session cookie is proxied", withCookie.status === 200, withCookie.status);
    assert("a browser bearer is not forwarded on session routes", !brain.seen[0]?.headers.authorization, brain.seen[0]?.headers);

    const bearerOnlyState = await request(port, `${BASE}/state`, { headers: { Authorization: `Bearer ${PRODUCER_TOKEN}` } });
    assert("a producer bearer cannot read state", bearerOnlyState.status === 401, bearerOnlyState.status);

    // --- SSE stream ------------------------------------------------------
    const stream = await readStream(port, { Cookie: cookie, Accept: "text/event-stream" }, {
      until: (text) => text.includes("\"n\":2"), timeoutMs: 4000,
    });
    assert("stream is proxied as text/event-stream", String(stream.headers?.["content-type"] || "").startsWith("text/event-stream"), stream.headers);
    assert("stream keeps X-Accel-Buffering: no", stream.headers?.["x-accel-buffering"] === "no", stream.headers);
    assert("first frame arrives before the second is sent (no buffering)",
      stream.marks?.[0] && stream.marks[0].text.includes("\"n\":1") && !stream.marks[0].text.includes("\"n\":2") && stream.marks[0].at < 600,
      stream.marks);
    assert("stream outlives the 250 ms response-header timeout", stream.text?.includes("\"n\":2") && !stream.timedOut && !stream.ended, stream);
    await sleep(150);
    assert("closing the client closes the upstream stream", brain.streams.size === 0, brain.streams.size);
    const anonymousStream = await request(port, `${BASE}/stream`);
    assert("stream without a session is 401", anonymousStream.status === 401, anonymousStream.status);

    // --- overrides -------------------------------------------------------
    const override = { schema: "activity-lighting-override-1", fixture_id: "victoria-den-mount-03-virtual-fixture", reason: "aim", at: 1000 };
    brain.seen.length = 0;
    const noCsrf = await request(port, `${BASE}/overrides`, { method: "POST", headers: { Cookie: cookie }, body: override });
    assert("override without the CSRF header is 403", noCsrf.status === 403 && noCsrf.json?.error === "lighting_brain_csrf_required", noCsrf);
    const wrongCsrf = await request(port, `${BASE}/overrides`, { method: "POST", headers: { Cookie: cookie, "X-Home-Lighting-Brain-CSRF": "yes" }, body: override });
    assert("override with a wrong CSRF value is 403", wrongCsrf.status === 403, wrongCsrf.status);
    const csrfNoSession = await request(port, `${BASE}/overrides`, { method: "POST", headers: { "X-Home-Lighting-Brain-CSRF": "1" }, body: override });
    assert("override with CSRF but no session is 401", csrfNoSession.status === 401, csrfNoSession.status);
    const bearerOverride = await request(port, `${BASE}/overrides`, {
      method: "POST", headers: { Authorization: `Bearer ${PRODUCER_TOKEN}`, "X-Home-Lighting-Brain-CSRF": "1" }, body: override,
    });
    assert("a producer bearer cannot post an override", bearerOverride.status === 401, bearerOverride.status);
    assert("refused overrides never reach the brain", brain.seen.length === 0, brain.seen);

    const posted = await request(port, `${BASE}/overrides`, {
      method: "POST", headers: { Cookie: cookie, "X-Home-Lighting-Brain-CSRF": "1" }, body: override,
    });
    assert("override with session and CSRF is proxied", posted.status === 200, posted);
    assert("override body reaches the brain intact", brain.seen[0]?.method === "POST" && JSON.parse(brain.seen[0].body).fixture_id === override.fixture_id, brain.seen[0]);
    assert("override reaches the brain without cookie or CSRF header",
      !brain.seen[0]?.headers.cookie && brain.seen[0]?.headers["x-home-lighting-brain-csrf"] === undefined, brain.seen[0]?.headers);

    brain.seen.length = 0;
    const cleared = await request(port, `${BASE}/overrides/victoria-den-mount-03-virtual-fixture`, {
      method: "DELETE", headers: { Cookie: cookie, "X-Home-Lighting-Brain-CSRF": "1" },
    });
    assert("DELETE override with session and CSRF is proxied", cleared.status === 200 && brain.seen[0]?.method === "DELETE", { cleared, seen: brain.seen });
    const clearedNoCsrf = await request(port, `${BASE}/overrides/victoria-den-mount-03-virtual-fixture`, { method: "DELETE", headers: { Cookie: cookie } });
    assert("DELETE override without CSRF is 403", clearedNoCsrf.status === 403, clearedNoCsrf.status);

    // --- producer events -------------------------------------------------
    const events = { schema: "activity-lighting-events-1", events: [{ type: "occupancy", home: "victoria", room: "den", source: "test", observed_at: 1, sent_at: 1, ttl_s: 90, present: true, people_count: 1 }] };
    brain.seen.length = 0;
    const produced = await request(port, `${BASE}/events`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${PRODUCER_TOKEN}`,
        Cookie: cookie,
        "X-Forwarded-For": "203.0.113.9",
        "X-Home-Principal": "spoofed",
        "User-Agent": "victoria-ha-bridge/1",
      },
      body: events,
    });
    assert("producer events with a bearer are proxied without a session", produced.status === 200, produced);
    const forwarded = brain.seen[0]?.headers || {};
    assert("the producer bearer reaches the brain", forwarded.authorization === `Bearer ${PRODUCER_TOKEN}`, forwarded);
    assert("producer ingress strips cookie, forwarding and principal headers",
      !forwarded.cookie && !forwarded["x-forwarded-for"] && !forwarded["x-home-principal"], forwarded);
    assert("producer ingress keeps the user agent and body", forwarded["user-agent"] === "victoria-ha-bridge/1" && JSON.parse(brain.seen[0].body).events.length === 1, forwarded);

    brain.seen.length = 0;
    const sessionEvents = await request(port, `${BASE}/events`, { method: "POST", headers: { Cookie: cookie }, body: events });
    assert("events with only a session are 401 (producers need a bearer)", sessionEvents.status === 401 && sessionEvents.json?.error === "lighting_brain_producer_token_required", sessionEvents);
    const basicEvents = await request(port, `${BASE}/events`, { method: "POST", headers: { Authorization: BASIC }, body: events });
    assert("events with gateway Basic are 401", basicEvents.status === 401, basicEvents.status);
    assert("events without a bearer never reach the brain", brain.seen.length === 0, brain.seen);

    // --- allow list ------------------------------------------------------
    for (const [method, urlPath] of [
      ["GET", "/api/lighting-brain/healthz"],
      ["GET", "/api/lighting-brain/v1/homes/victoria/state?debug=1"],
      ["PUT", `${BASE}/overrides`],
      ["GET", `${BASE}/overrides`],
      ["POST", `${BASE}/state`],
      ["GET", "/api/lighting-brain/v1/homes/Victoria/state"],
      ["DELETE", `${BASE}/overrides/a%2Fb`],
      ["GET", "/api/lighting-brain/v2/homes/victoria/state"],
    ]) {
      brain.seen.length = 0;
      const denied = await request(port, urlPath, { method, headers: { Cookie: cookie, "X-Home-Lighting-Brain-CSRF": "1" } });
      assert(`${method} ${urlPath} is not proxied`, denied.status === 403 && brain.seen.length === 0, { status: denied.status, seen: brain.seen.length });
    }
    const unknownBearer = await request(port, "/api/lighting-brain/v1/homes/victoria/admin", { method: "POST", headers: { Authorization: `Bearer ${PRODUCER_TOKEN}` } });
    assert("a bearer on an unlisted path is 401, not proxied", unknownBearer.status === 401, unknownBearer.status);

    // --- den-ops status banner ------------------------------------------
    const banner0 = await request(port, "/api/den-ops/v1/status", { headers: { Cookie: cookie } });
    assert("den-ops banner starts empty", banner0.status === 200 && banner0.json?.schema === "den-ops-banner-1" && banner0.json.level === 0 && banner0.json.banner === null, banner0.json);
    const anonymousBanner = await request(port, "/api/den-ops/v1/status");
    assert("den-ops banner needs a session or monitor token", anonymousBanner.status === 401, anonymousBanner.status);

    const jetsonPost = await request(port, "/api/den-ops/v1/hosts/victoria-jetson/status", {
      method: "POST", headers: { Authorization: `Bearer ${JETSON_MONITOR_TOKEN}` }, body: status("victoria-jetson", 2),
    });
    assert("a monitor posts its own status", jetsonPost.status === 200 && jetsonPost.json?.level === 2, jetsonPost);
    const laPost = await request(port, "/api/den-ops/v1/hosts/la-host/status", {
      method: "POST", headers: { Authorization: `Bearer ${LA_MONITOR_TOKEN}` }, body: status("la-host", 0),
    });
    assert("a second monitor posts its status", laPost.status === 200, laPost);
    const banner2 = await request(port, "/api/den-ops/v1/status", { headers: { Cookie: cookie } });
    assert("banner carries the worst level as an alert", banner2.json?.level === 2 && banner2.json.banner?.severity === "alert" && banner2.json.banner.host === "victoria-jetson", banner2.json);
    assert("banner lists both hosts", banner2.json?.hosts?.length === 2, banner2.json?.hosts);
    const monitorRead = await request(port, "/api/den-ops/v1/status", { headers: { Authorization: `Bearer ${LA_MONITOR_TOKEN}` } });
    assert("a monitor token can read the aggregate", monitorRead.status === 200 && monitorRead.json?.level === 2, monitorRead.status);

    const wrongHost = await request(port, "/api/den-ops/v1/hosts/la-host/status", {
      method: "POST", headers: { Authorization: `Bearer ${JETSON_MONITOR_TOKEN}` }, body: status("la-host", 3),
    });
    assert("a monitor cannot post for another host", wrongHost.status === 403, wrongHost.status);
    const sessionPost = await request(port, "/api/den-ops/v1/hosts/victoria-jetson/status", {
      method: "POST", headers: { Cookie: cookie }, body: status("victoria-jetson", 0),
    });
    assert("a browser session cannot post a monitor status", sessionPost.status === 401, sessionPost.status);
    const badStep = await request(port, "/api/den-ops/v1/hosts/victoria-jetson/status", {
      method: "POST", headers: { Authorization: `Bearer ${JETSON_MONITOR_TOKEN}` }, body: status("victoria-jetson", 1, { step: "loop_stopped" }),
    });
    assert("a status whose step disagrees with its level is 422", badStep.status === 422 && badStep.json?.error === "status_step", badStep.json);
    const oversized = await request(port, "/api/den-ops/v1/hosts/victoria-jetson/status", {
      method: "POST", headers: { Authorization: `Bearer ${JETSON_MONITOR_TOKEN}` }, body: status("victoria-jetson", 1, { message: "x".repeat(20000) }),
    });
    assert("an oversized status is 413", oversized.status === 413, oversized.status);
    const stillTwo = await request(port, "/api/den-ops/v1/status", { headers: { Cookie: cookie } });
    assert("refused posts leave the stored status unchanged", stillTwo.json?.level === 2, stillTwo.json?.level);

    await sleep(1300);
    const stale = await request(port, "/api/den-ops/v1/status", { headers: { Cookie: cookie } });
    assert("hosts that stop posting turn stale and drop out of the level", stale.json?.level === 0 && stale.json.hosts.every((h) => h.stale), stale.json);
    assert("stale hosts raise a 'monitor silent' warning", stale.json?.banner?.severity === "warn" && /silent/.test(stale.json.banner.text), stale.json?.banner);

    // --- upstream down ---------------------------------------------------
    await new Promise((resolve) => brain.server.close(resolve));
    for (const res of brain.streams) res.destroy();
    const down = await request(port, `${BASE}/state`, { headers: { Cookie: cookie } });
    assert("brain down gives a JSON 502", down.status === 502 && down.json?.ok === false, down);
  } finally {
    if (gateway?.child && gateway.child.exitCode == null) gateway.child.kill();
    brain.server.close();
    fs.rmSync(temp, { recursive: true, force: true });
  }

  // Den-ops is disabled (404) without a monitor token file.
  const plain = await startGateway({ HOME_WEB_AUTH_REQUIRED: "0" });
  try {
    const disabled = await request(plain.port, "/api/den-ops/v1/status");
    assert("den-ops status is a disabled capability without monitor tokens", disabled.status === 404 && disabled.json?.error === "capability_disabled", disabled);
  } finally {
    plain.child.kill();
  }

  process.stdout.write(`\n${passes} pass . ${fails} fail\n`);
  process.exit(fails ? 1 : 0);
}

main().catch((error) => {
  process.stdout.write(`  FAIL  harness error: ${error.stack || error}\n`);
  process.exit(1);
});
