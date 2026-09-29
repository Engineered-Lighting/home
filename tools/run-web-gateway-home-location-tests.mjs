#!/usr/bin/env node
// Default-home location: the locator's signals and precedence (unit), and the
// real gateway's /api/home/location behind an optional PROXY protocol line
// (integration, against a fake HA). No Tailscale or HA is contacted.
import childProcess from "node:child_process";
import fs from "node:fs";
import http from "node:http";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { createHomeLocator, parseProxyLine, parseSiteList } from "../web-gateway/home-location.mjs";

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const SERVER = path.join(REPO, "web-gateway", "server.mjs");
const HA_TOKEN = "fake-long-lived-ha-token-not-real.0123456789";
const USER = "owner";
const PASSWORD = "correct-horse-battery-staple";

let passes = 0;
let fails = 0;
function assert(name, cond, detail) {
  if (cond) { passes += 1; process.stdout.write(`  PASS  ${name}\n`); return; }
  fails += 1;
  process.stdout.write(`  FAIL  ${name}${detail === undefined ? "" : `\n        ${JSON.stringify(detail)}`}\n`);
}
const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

const LANS = parseSiteList("echo=192.168.0.0/24,victoria=192.168.1.0/24");
const ZONES = parseSiteList("echo=home,victoria=Victoria");
const PEERS = {
  Peer: {
    laptop: { TailscaleIPs: ["100.64.0.1"], CurAddr: "192.168.0.23:41641" },
    desktop: { TailscaleIPs: ["100.64.0.2", "fd7a:115c:a1e0::2"], CurAddr: "192.168.1.91:41641" },
    phone: { TailscaleIPs: ["100.64.0.3"], CurAddr: "" },
    cafe: { TailscaleIPs: ["100.64.0.4"], CurAddr: "203.0.113.9:41641" },
  },
};

function fakeHa(stateRef) {
  return async (url, options) => {
    stateRef.calls.push({ url, authorization: options.headers.authorization });
    if (stateRef.fail) throw new Error("unreachable");
    return { ok: true, json: async () => ({ entity_id: "person.owner", state: stateRef.state,
      attributes: { latitude: 48.4, longitude: -123.3, gps_accuracy: 12 } }) };
  };
}

function locator({ phoneState = "not_home", statusImpl, fail = false, now, timeoutMs = 2_000 } = {}) {
  const ha = { state: phoneState, calls: [], fail };
  const statusCalls = [];
  const status = statusImpl || (async () => { statusCalls.push(1); return PEERS; });
  return {
    ha, statusCalls,
    locator: createHomeLocator({ lans: LANS, zones: ZONES, person: "person.owner", haTarget: "http://ha.test",
      haToken: HA_TOKEN, status, fetchImpl: fakeHa(ha), now: now || Date.now, timeoutMs }),
  };
}

async function unitTests() {
  process.stdout.write("\nhome_location_unit\n");
  assert("PROXY TCP4 line names the client", parseProxyLine("PROXY TCP4 100.64.0.2 100.87.94.18 50000 443")?.address === "100.64.0.2");
  assert("PROXY TCP6 line names the client", parseProxyLine("PROXY TCP6 fd7a:115c:a1e0::2 fd7a::1 50000 443")?.address === "fd7a:115c:a1e0::2");
  assert("PROXY UNKNOWN carries no client", parseProxyLine("PROXY UNKNOWN")?.address === "");
  for (const bad of ["PROXY TCP4 not-an-ip 1.2.3.4 1 2", "PROXY TCP4 fd7a::2 1.2.3.4 1 2", "PROXY TCP4 1.2.3.4 1.2.3.4 1", "GET / HTTP/1.1"]) {
    assert(`malformed PROXY line is refused: ${bad}`, parseProxyLine(bad) === null);
  }
  let threw = false;
  try { parseSiteList("paris=10.0.0.0/8"); } catch { threw = true; }
  assert("unknown site names are refused", threw);

  let t = locator({ phoneState: "Victoria" });
  let result = await t.locator.locate("100.64.0.1");
  assert("an LA LAN path wins over the phone zone", result.site === "echo" && result.basis === "network", result);
  result = await t.locator.locate("100.64.0.2");
  assert("a Victoria LAN path means Victoria", result.site === "victoria" && result.basis === "network", result);
  result = await t.locator.locate("fd7a:115c:a1e0::2");
  assert("a device is found by its IPv6 tailnet address", result.site === "victoria" && result.basis === "network", result);
  result = await t.locator.locate("100.64.0.3");
  assert("a relayed device falls back to the phone zone", result.site === "victoria" && result.basis === "phone", result);
  result = await t.locator.locate("100.64.0.4");
  assert("a public path falls back to the phone zone", result.site === "victoria" && result.basis === "phone", result);
  assert("tailscale status is cached", t.statusCalls.length === 1, t.statusCalls.length);
  assert("HA is read with the gateway token for the configured person",
    t.ha.calls[0]?.url === "http://ha.test/api/states/person.owner" && t.ha.calls[0]?.authorization === `Bearer ${HA_TOKEN}`, t.ha.calls[0]);
  const text = JSON.stringify(result);
  assert("responses carry no coordinates, IPs or entity ids",
    !/48\.4|123\.3|100\.64|203\.0|192\.168|person\./.test(text) && Object.keys(result).sort().join() === "basis,label,site,version", text);

  t = locator({ phoneState: "home" });
  result = await t.locator.locate("");
  assert("with no client address the phone decides", result.site === "echo" && result.basis === "phone", result);
  t = locator({ phoneState: "not_home" });
  result = await t.locator.locate("100.64.0.4");
  assert("away from both homes is unknown", result.site === null && result.basis === null, result);
  t = locator({ phoneState: "Work" });
  result = await t.locator.locate("100.64.0.9");
  assert("other zones and unknown devices are unknown", result.site === null, result);
  t = locator({ fail: true, statusImpl: async () => { throw new Error("no tailscaled"); } });
  result = await t.locator.locate("100.64.0.1");
  assert("failing signals are unknown, never an error", result.site === null && result.version === 1, result);
  t = locator({ phoneState: "home", statusImpl: () => new Promise(() => {}), timeoutMs: 100 });
  const started = Date.now();
  result = await t.locator.locate("100.64.0.1");
  assert("a hung tailscale status times out to the phone zone", result.site === "echo" && result.basis === "phone" &&
    Date.now() - started < 1000, result);

  let clock = 0;
  t = locator({ phoneState: "Victoria", now: () => clock });
  await t.locator.locate("100.64.0.3");
  t.ha.state = "home";
  result = await t.locator.locate("100.64.0.3");
  assert("the phone zone is cached briefly", result.site === "victoria" && t.ha.calls.length === 1, t.ha.calls.length);
  clock = 11_000;
  result = await t.locator.locate("100.64.0.3");
  assert("the phone zone refreshes after its cache expires", result.site === "echo" && t.ha.calls.length === 2, result);
}

const freePort = () => new Promise((resolve, reject) => {
  const probe = net.createServer();
  probe.once("error", reject);
  probe.listen(0, "127.0.0.1", () => { const { port } = probe.address(); probe.close(() => resolve(port)); });
});

// Raw HTTP over one socket, optionally preceded by PROXY lines, in chosen writes.
function rawExchange(port, writes, { expect = 1 } = {}) {
  return new Promise((resolve) => {
    const socket = net.connect(port, "127.0.0.1");
    let text = "";
    const finish = () => { socket.destroy(); resolve(text); };
    socket.on("data", (chunk) => {
      text += chunk.toString("utf8");
      if ((text.match(/HTTP\/1\.1 \d{3}/g) || []).length >= expect && /\r\n\r\n[\s\S]*\}$/.test(text)) finish();
    });
    socket.on("error", finish);
    socket.on("close", finish);
    (async () => { for (const part of writes) { socket.write(part); await sleep(30); } })();
    setTimeout(finish, 3000);
  });
}

function request(port, urlPath, { method = "GET", headers = {}, body } = {}) {
  return new Promise((resolve, reject) => {
    const req = http.request({ host: "127.0.0.1", port, path: urlPath, method, headers }, (res) => {
      let text = "";
      res.setEncoding("utf8");
      res.on("data", (chunk) => { text += chunk; });
      res.on("end", () => resolve({ status: res.statusCode, headers: res.headers, body: text }));
    });
    req.once("error", reject);
    if (body) req.write(body);
    req.end();
  });
}

async function integrationTests() {
  process.stdout.write("\nhome_location_gateway\n");
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), "home-gateway-location-"));
  const tokenFile = path.join(temp, "ha-token");
  fs.writeFileSync(tokenFile, `${HA_TOKEN}\n`, { mode: 0o600 });
  const seen = [];
  const ha = http.createServer((req, res) => {
    seen.push({ url: req.url, authorization: req.headers.authorization || "" });
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ entity_id: "person.owner", state: "Victoria", attributes: { latitude: 48.4 } }));
  });
  ha.on("upgrade", (req, socket) => {
    seen.push({ url: req.url, authorization: req.headers.authorization || "" });
    socket.end("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\r\n");
  });
  await new Promise((resolve) => ha.listen(0, "127.0.0.1", resolve));
  const port = await freePort();
  const child = childProcess.spawn(process.execPath, [SERVER], {
    cwd: REPO,
    env: { ...process.env, HOME_WEB_HOST: "127.0.0.1", HOME_WEB_PORT: String(port), HOME_WEB_AUTH_REQUIRED: "1",
      HOME_WEB_AUTH_FILE: path.join(temp, "missing-auth.json"), HOME_WEB_BASIC_AUTH: `${USER}:${PASSWORD}`,
      HOME_WEB_ENABLE_LEGACY_HA_PROXY: "1", HOME_WEB_HA_TARGET: `http://127.0.0.1:${ha.address().port}`,
      HOME_WEB_HA_TOKEN_FILE: tokenFile, HOME_WEB_LOCATION_PERSON: "person.owner",
      HOME_WEB_SITE_ZONES: "echo=home,victoria=Victoria", HOME_WEB_ASSET_VERSION: "" },
    stdio: ["ignore", "ignore", "pipe"],
  });
  try {
    let ready = false;
    for (let i = 0; i < 80 && !ready; i += 1) {
      try { ready = (await request(port, "/healthz")).status === 200; } catch { await sleep(100); }
    }
    assert("gateway starts", ready);
    const unauthenticated = await request(port, "/api/home/location");
    assert("location requires sign-in", unauthenticated.status === 401, unauthenticated.status);
    const login = await request(port, "/auth/login", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: USER, password: PASSWORD }) });
    const cookie = String(login.headers["set-cookie"] || "").split(";")[0];
    const located = await request(port, "/api/home/location", { headers: { Cookie: cookie } });
    const body = JSON.parse(located.body || "{}");
    assert("signed-in location reports the phone zone", located.status === 200 && body.site === "victoria" && body.basis === "phone", located.body);
    assert("location is not cached by the browser", located.headers["cache-control"] === "no-store", located.headers["cache-control"]);
    assert("location never echoes HA coordinates", !located.body.includes("48.4"), located.body);
    assert("HA is read with the gateway token", seen.at(-1)?.authorization === `Bearer ${HA_TOKEN}` && seen.at(-1)?.url === "/api/states/person.owner", seen.at(-1));
    const post = await request(port, "/api/home/location", { method: "POST", headers: { Cookie: cookie } });
    assert("location is GET only", post.status === 405, post.status);

    const get = `GET /api/home/location HTTP/1.1\r\nHost: home.test\r\nCookie: ${cookie}\r\n\r\n`;
    let text = await rawExchange(port, [`PROXY TCP4 100.64.0.2 100.87.94.18 50000 443\r\n${get}`]);
    assert("a PROXY line and request in one write is served", /^HTTP\/1\.1 200/.test(text) && text.includes('"site":"victoria"'), text.slice(0, 120));
    text = await rawExchange(port, ["PRO", "XY TCP4 100.64.0.2 100.87.94.18 50000 443\r", `\n${get.slice(0, 10)}`, get.slice(10)]);
    assert("a PROXY line split across writes is served", /^HTTP\/1\.1 200/.test(text), text.slice(0, 120));
    text = await rawExchange(port, [`PROXY TCP4 100.64.0.2 100.87.94.18 50000 443\r\n${get}${get}`], { expect: 2 });
    assert("keep-alive requests after a PROXY line are served", (text.match(/HTTP\/1\.1 200/g) || []).length === 2, text.slice(0, 200));
    text = await rawExchange(port, [get]);
    assert("connections without a PROXY line still work", /^HTTP\/1\.1 200/.test(text), text.slice(0, 120));
    const upgrade = ["GET /proxy/ha/api/websocket HTTP/1.1", "Host: home.test", "Upgrade: websocket", "Connection: Upgrade",
      "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==", "Sec-WebSocket-Version: 13", `Cookie: ${cookie}`, "", ""].join("\r\n");
    text = await new Promise((resolve) => {
      const socket = net.connect(port, "127.0.0.1");
      let received = "";
      socket.on("data", (chunk) => { received += chunk; if (received.includes("\r\n\r\n")) { socket.destroy(); resolve(received); } });
      socket.on("error", () => resolve(received));
      socket.write(`PROXY TCP4 100.64.0.2 100.87.94.18 50000 443\r\n${upgrade}`);
      setTimeout(() => { socket.destroy(); resolve(received); }, 3000);
    });
    assert("websocket upgrades after a PROXY line reach HA", /^HTTP\/1\.1 101/.test(text), text.slice(0, 120));
    text = await rawExchange(port, [`PROXY TCP4 bogus\r\n${get}`]);
    assert("a malformed PROXY line closes the connection", text === "", text.slice(0, 120));
    const health = await request(port, "/healthz");
    assert("gateway is healthy afterwards", health.status === 200, health.status);
  } finally {
    child.kill();
    ha.close();
    fs.rmSync(temp, { recursive: true, force: true });
  }
}

await unitTests();
await integrationTests();
process.stdout.write(`\n${passes} passed, ${fails} failed\n`);
process.exit(fails ? 1 : 0);
