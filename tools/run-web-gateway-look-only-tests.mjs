#!/usr/bin/env node
// The live den view's gateway rules: den-live's route (den-live-route.mjs) and the look-only tailnet view
// (look-only.mjs). Unit checks of the path rules, then the real gateway behind PROXY protocol lines, against a fake
// den-live and a fake lighting brain, on an authored content pack. No Tailscale, camera or host is contacted.
import childProcess from "node:child_process";
import fs from "node:fs";
import http from "node:http";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { DEN_LIVE_PREFIX, DEN_SITE, denLiveHeaders, denLiveRouteKind, denLiveWriteRefusal } from "../web-gateway/den-live-route.mjs";
import { isTailnetAddress, lookOnlyKind, lookOnlyPackIndex } from "../web-gateway/look-only.mjs";
import { REGISTRATION_ROUTE, initRegistration, readActiveBinding } from "../web-gateway/equipment-registration.mjs";
import { SITES } from "../web-gateway/home-location.mjs";
import { authoredDocument, installPack } from "./fixtures/apartment-home-shell-fixtures.mjs";

const REPO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const SERVER = path.join(REPO, "web-gateway", "server.mjs");
const USER = "owner";
const PASSWORD = "correct-horse-battery-staple";
const OTHER_SITE = SITES.find(site => site !== DEN_SITE);
const HOST = "home.test";
const TAILNET_CLIENT = "100.101.102.103";

let passes = 0;
let fails = 0;
function assert(name, cond, detail) {
  if (cond) { passes += 1; process.stdout.write(`  PASS  ${name}\n`); return; }
  fails += 1;
  process.stdout.write(`  FAIL  ${name}${detail === undefined ? "" : `\n        ${JSON.stringify(detail).slice(0, 400)}`}\n`);
}
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

function unitTests() {
  process.stdout.write("\nlook_only_rules\n");
  const allowed = {
    "/": "index", "/index.html?apartmentRedesign=1&worldZoom=off": "index", "/home-app.jsx?cb=1794442433544": "static",
    "/home-3d/apartment-people-layer.mjs": "static", "/vendor/three-0.180.0/three.module.js": "static",
    "/precompiled/manifest.json?cb=12": "static", "/favicon.svg": "static", "/auth/status": "auth-status",
    [`/home-app.jsx?v=${"0123456789abcdef".repeat(3)}-0123456789abcdef-chat-routing-20260914b-camera-context-20260926-den-20261015-r1-view-v3`]: "static",
    [`/content-packs/${DEN_SITE}/r1/manifest.json?cb=1794442433544`]: "pack",
    "/content-packs/index.json": "pack-index", [`/content-packs/${DEN_SITE}/r1/frame.json`]: "pack",
    [REGISTRATION_ROUTE]: "registration", [`${REGISTRATION_ROUTE}/revisions/3`]: "registration",
    [`${DEN_LIVE_PREFIX}/v1/homes/den/state`]: "den-live", [`${DEN_LIVE_PREFIX}/v1/homes/den/stream`]: "den-live",
    [`/api/lighting-brain/v1/homes/${DEN_SITE}/stream`]: "lighting-brain",
  };
  for (const [url, kind] of Object.entries(allowed)) assert(`look-only reads ${url}`, lookOnlyKind("GET", url) === kind, lookOnlyKind("GET", url));
  const refused = [
    `/content-packs/${OTHER_SITE}/r1/frame.json`, "/assets/apartment/points.ply", "/home-agent/index.js", "/api/world/v1/config",
    "/api/home/location", "/proxy/ha/api/states", "/api/perception-lab/v1/capabilities", `/api/lighting-brain/v1/homes/${OTHER_SITE}/state`,
    `${DEN_LIVE_PREFIX}/v1/homes/other/state`, `${DEN_LIVE_PREFIX}/v1/homes/den/state?x=1`, "/content-packs/index.json?x=1",
    `/home-app.jsx?v=${"a".repeat(201)}`, `/content-packs/${DEN_SITE}/r1/frame.json?token=1`, "/auth/status?cb=1",
    "/../web-gateway/server.js", "/a/.env.js", "/%2e%2e/x.js", "//evil.js", "/home-app.jsx?token=abc", `${REGISTRATION_ROUTE}/revisions/x`,
    "/den-ops/v1/status", "/data/x.js",
  ];
  for (const url of refused) assert(`look-only refuses ${url}`, lookOnlyKind("GET", url) === null, lookOnlyKind("GET", url));
  for (const method of ["POST", "PUT", "DELETE", "PATCH", "OPTIONS"]) {
    assert(`look-only never admits ${method}`, lookOnlyKind(method, `${DEN_LIVE_PREFIX}/v1/homes/den/state`) === null
      && lookOnlyKind(method, REGISTRATION_ROUTE) === null && lookOnlyKind(method, "/") === null);
  }
  assert("look-only HEAD of the registration is refused (it's GET only)", lookOnlyKind("HEAD", REGISTRATION_ROUTE) === null);
  for (const address of ["100.64.0.1", "100.127.255.254", "::ffff:100.64.0.9", "fd7a:115c:a1e0::5"]) {
    assert(`${address} is a tailnet address`, isTailnetAddress(address));
  }
  for (const address of ["100.63.255.255", "100.128.0.1", "10.20.30.40", "127.0.0.1", "", "fd7a:115c:a1e1::5", "not-an-ip"]) {
    assert(`${address || "(none)"} isn't a tailnet address`, !isTailnetAddress(address));
  }
  const index = { schemaVersion: 1, packs: [{ manifest: { siteId: DEN_SITE }, active: true }, { manifest: { siteId: OTHER_SITE }, active: true }] };
  const filtered = lookOnlyPackIndex(index);
  assert("the packs index keeps only the den's packs", filtered.packs.length === 1 && filtered.packs[0].manifest.siteId === DEN_SITE, filtered);
  assert("den-live reads state and stream only", denLiveRouteKind("GET", "/v1/homes/den/stream") === "read"
    && denLiveRouteKind("GET", "/v1/homes/den/events") === null && denLiveRouteKind("POST", "/v1/homes/den/state") === null
    && denLiveRouteKind("HEAD", "/v1/homes/den/stream") === null && denLiveRouteKind("GET", "/v1/homes/den/state?x") === null);
  assert("den-live's body path is its own kind, GET only", denLiveRouteKind("GET", "/v1/homes/den/body") === "body"
    && denLiveRouteKind("HEAD", "/v1/homes/den/body") === null && denLiveRouteKind("GET", "/v1/homes/den/body?x") === null
    && denLiveRouteKind("GET", "/v1/homes/den/body/x") === null);
  assert("look-only reads the markers but never the body", lookOnlyKind("GET", "/api/den-live/v1/homes/den/stream") === "den-live"
    && lookOnlyKind("GET", "/api/den-live/v1/homes/den/body") === null);
  // The lighting coordinator: its state for anyone who reads the markers, its four writes for a session only.
  assert("den-live's coordinator state is its own kind, GET only", denLiveRouteKind("GET", "/v1/homes/den/coordinator") === "coordinator"
    && denLiveRouteKind("HEAD", "/v1/homes/den/coordinator") === null && denLiveRouteKind("GET", "/v1/homes/den/coordinator?x") === null
    && denLiveRouteKind("POST", "/v1/homes/den/coordinator") === null);
  for (const write of ["switch", "settings", "learned/clear", "desk"]) {
    assert(`the coordinator's ${write} is a POST write`, denLiveRouteKind("POST", `/v1/homes/den/coordinator/${write}`) === "coordinator-write"
      && denLiveRouteKind("GET", `/v1/homes/den/coordinator/${write}`) === null && denLiveRouteKind("PUT", `/v1/homes/den/coordinator/${write}`) === null
      && denLiveRouteKind("POST", `/v1/homes/den/coordinator/${write}?x=1`) === null);
  }
  for (const suffix of ["/v1/homes/den/coordinator/learned", "/v1/homes/den/coordinator/other", "/v1/homes/other/coordinator/switch",
    "/v1/homes/den/coordinator/switch/", "/v1/homes/den/coordinator/%73witch", "/v1/homes/den/stream", "/v1/homes/den/state"]) {
    assert(`no POST reaches den-live at ${suffix}`, denLiveRouteKind("POST", suffix) === null);
  }
  assert("look-only reads the coordinator's state, and writes none of it", lookOnlyKind("GET", `${DEN_LIVE_PREFIX}/v1/homes/den/coordinator`) === "den-live"
    && lookOnlyKind("POST", `${DEN_LIVE_PREFIX}/v1/homes/den/coordinator/switch`) === null
    && lookOnlyKind("GET", `${DEN_LIVE_PREFIX}/v1/homes/den/coordinator/switch`) === null);
  const write = { "x-home-den-live-csrf": "1", "content-type": "application/json", "content-length": "11" };
  const refusal = (method, suffix, headers) => denLiveWriteRefusal(method, suffix, headers)?.error ?? null;
  assert("a coordinator write with the CSRF header, JSON and a length passes", refusal("POST", "/v1/homes/den/coordinator/switch", write) === null);
  assert("a coordinator write needs the CSRF header", refusal("POST", "/v1/homes/den/coordinator/switch", { ...write, "x-home-den-live-csrf": undefined })
    === "den_live_csrf_required" && refusal("POST", "/v1/homes/den/coordinator/switch", { ...write, "x-home-den-live-csrf": "0" }) === "den_live_csrf_required");
  assert("a coordinator write is JSON", refusal("POST", "/v1/homes/den/coordinator/settings", { ...write, "content-type": "text/plain" }) === "json_required"
    && refusal("POST", "/v1/homes/den/coordinator/settings", { ...write, "content-type": "application/json; charset=utf-8" }) === null);
  assert("a coordinator write has a length, not chunks", refusal("POST", "/v1/homes/den/coordinator/desk", { ...write, "content-length": undefined,
    "transfer-encoding": "chunked" }) === "length_required" && refusal("POST", "/v1/homes/den/coordinator/desk", { ...write, "content-length": "x" }) === "length_required");
  assert("a coordinator write is at most 64 KB", refusal("POST", "/v1/homes/den/coordinator/settings", { ...write, "content-length": "65537" }) === "too_large"
    && refusal("POST", "/v1/homes/den/coordinator/settings", { ...write, "content-length": "65536" }) === null);
  assert("any other write is refused", refusal("POST", "/v1/homes/den/state", write) === "den_live_write_not_allowed"
    && refusal("DELETE", "/v1/homes/den/coordinator/switch", write) === "den_live_write_not_allowed");
  const writeHeaders = denLiveHeaders({ cookie: "home_web_auth=x", authorization: "Basic abc", "content-type": "application/json",
    "content-length": "11", "x-home-den-live-csrf": "1", accept: "application/json" }, "http://127.0.0.1:8111/api/den-live", true);
  assert("a coordinator write reaches den-live with its body's headers and no credential", JSON.stringify(Object.keys(writeHeaders).sort())
    === JSON.stringify(["accept", "content-length", "content-type", "host"]), writeHeaders);
  const headers = denLiveHeaders({ cookie: "home_web_auth=x", authorization: "Bearer abc", accept: "text/event-stream",
    "last-event-id": "4", "x-forwarded-for": "1.2.3.4" }, "http://127.0.0.1:8111/api/den-live");
  assert("den-live gets no cookie, credential or forwarding header", JSON.stringify(Object.keys(headers).sort())
    === JSON.stringify(["accept", "host", "last-event-id"]) && headers.host === "127.0.0.1:8111", headers);
  assert("no site is written in the rules: the den's site comes from the registration route", REGISTRATION_ROUTE.includes(`/sites/${DEN_SITE}/`));
}

function freePort() {
  return new Promise(resolve => { const s = net.createServer(); s.listen(0, "127.0.0.1", () => { const { port } = s.address(); s.close(() => resolve(port)); }); });
}

// One request on a fresh connection, optionally behind a PROXY line naming `client`. For a stream it returns once the
// first event (or `until`) has arrived.
function exchange(port, { client = TAILNET_CLIENT, method = "GET", url, headers = {}, body = "", until = null, raw = null } = {}) {
  return new Promise(resolve => {
    const socket = net.connect(port, "127.0.0.1");
    let text = "";
    const done = () => { socket.destroy(); resolve(parse(text)); };
    const timer = setTimeout(done, 4000);
    socket.on("data", chunk => {
      text += chunk.toString("latin1");
      const head = text.indexOf("\r\n\r\n");
      if (head < 0) return;
      const length = /\r\ncontent-length: *(\d+)/i.exec(text.slice(0, head));
      if ((until && text.includes(until)) || (length && text.length >= head + 4 + Number(length))
        || (!until && !length && /\r\n0\r\n\r\n$/.test(text))) { clearTimeout(timer); done(); }
    });
    socket.on("error", () => { clearTimeout(timer); done(); });
    socket.on("end", () => { clearTimeout(timer); done(); });
    const lines = [`${method} ${url} HTTP/1.1`, `Host: ${HOST}`, "Connection: close", ...Object.entries(headers).map(([k, v]) => `${k}: ${v}`)];
    if (body) lines.push(`Content-Length: ${Buffer.byteLength(body)}`);
    socket.write(`${client ? `PROXY TCP4 ${client} 100.64.0.2 50000 443\r\n` : ""}${raw || `${lines.join("\r\n")}\r\n\r\n${body}`}`);
  });
}
// Holds a stream open on its own connection behind a tailnet PROXY line, until close().
function holdStream(port, url) {
  const socket = net.connect(port, "127.0.0.1");
  const opened = new Promise(resolve => {
    socket.on("data", chunk => { if (String(chunk).includes("200")) resolve(); });
    socket.on("error", resolve);
    setTimeout(resolve, 3000);
  });
  socket.write(`PROXY TCP4 ${TAILNET_CLIENT} 100.64.0.2 50000 443\r\nGET ${url} HTTP/1.1\r\nHost: ${HOST}\r\nAccept: text/event-stream\r\n\r\n`);
  return { opened, close: () => socket.destroy() };
}

function parse(text) {
  const head = text.indexOf("\r\n\r\n");
  const status = Number(/^HTTP\/1\.1 (\d{3})/.exec(text)?.[1] || 0);
  return { status, head: head < 0 ? text : text.slice(0, head), body: head < 0 ? "" : text.slice(head + 4) };
}
const json = response => { try { return JSON.parse(response.body.replace(/^[0-9a-f]+\r\n/, "").replace(/\r\n0\r\n\r\n$/, "")); } catch { return null; } };

function fakeUpstreams() {
  const seen = [];
  const server = http.createServer((req, res) => {
    let body = "";
    req.on("data", chunk => { body += chunk; });
    req.on("end", () => {
      seen.push({ method: req.method, url: req.url, headers: req.headers, body });
      if (req.url === `${DEN_LIVE_PREFIX}/v1/homes/den/stream`) {
        res.writeHead(200, { "Content-Type": "text/event-stream", "Cache-Control": "no-store" });
        res.write("retry: 2000\n\nid: 1\nevent: people\ndata: {\"schema\":\"den-live-people-1\",\"tracks\":[]}\n\n");
        setTimeout(() => res.end(), 1500);
        return;
      }
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ ok: true, url: req.url }));
    });
  });
  return { server, seen };
}

async function startGateway(port, env) {
  const child = childProcess.spawn(process.execPath, [SERVER], { cwd: REPO, env: { ...process.env, ...env }, stdio: ["ignore", "ignore", "pipe"] });
  let ready = false;
  for (let i = 0; i < 100 && !ready; i += 1) {
    try { ready = (await exchange(port, { client: null, url: "/healthz" })).status === 200; } catch { /* starting */ }
    if (!ready) await sleep(100);
  }
  return { child, ready };
}

async function integrationTests() {
  process.stdout.write("\nlook_only_gateway\n");
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), "home-gateway-look-only-"));
  const packs = path.join(temp, "packs"), privateRoot = path.join(temp, "registration");
  fs.mkdirSync(packs); fs.mkdirSync(privateRoot);
  const den = installPack(packs, DEN_SITE), other = installPack(packs, OTHER_SITE);
  fs.writeFileSync(path.join(packs, "index.json"), JSON.stringify({ schemaVersion: 1, packs: [den, other].map(item =>
    ({ manifest: item.manifest, active: true, baseUrl: item.base })) }));
  const Core = (await import(pathToFileURL(path.join(REPO, "app/src/home-equipment-registration.js")).href)).default;
  initRegistration(privateRoot, authoredDocument(readActiveBinding(packs), Core), { contentPacksDir: packs });
  const upstream = fakeUpstreams();
  await new Promise(resolve => upstream.server.listen(0, "127.0.0.1", resolve));
  const fake = `http://127.0.0.1:${upstream.server.address().port}`;
  const base = { NODE_ENV: "test", HOME_WEB_HOST: "127.0.0.1", HOME_WEB_AUTH_REQUIRED: "1",
    HOME_WEB_AUTH_FILE: path.join(temp, "missing-auth.json"), HOME_WEB_BASIC_AUTH: `${USER}:${PASSWORD}`,
    HOME_WEB_ENABLE_LEGACY_HA_PROXY: "1", HOME_WEB_ASSET_VERSION: "", HOME_CONTENT_PACKS_DIR: packs,
    HOME_WEB_EQUIPMENT_REGISTRATION_ENABLED: "1", HOME_WEB_EQUIPMENT_REGISTRATION_ROOT: privateRoot,
    HOME_WEB_EQUIPMENT_REGISTRATION_ORIGIN: `http://${HOST}`, HOME_WEB_DEN_LIVE_TARGET: `${fake}${DEN_LIVE_PREFIX}`,
    HOME_WEB_LIGHTING_BRAIN_TARGET: `${fake}/api/lighting-brain` };
  const port = await freePort(), servePort = await freePort();
  const gateway = await startGateway(port, { ...base, HOME_WEB_PORT: String(port), HOME_WEB_SERVE_PORT: String(servePort),
    HOME_WEB_DEN_LIVE: "1", HOME_WEB_LOOK_ONLY: "1" });
  try {
    assert("gateway starts with den-live and look-only on", gateway.ready);
    let r = await exchange(servePort, { url: "/" });
    assert("a tailnet device with no session gets Home's shell", r.status === 200 && /text\/html/.test(r.head), r.head);
    assert("the shell says look-only and where the den's markers come from", r.body.includes("window.HomeLookOnly=Object.freeze({active:true,site:")
      && r.body.includes("window.HomeDenLive=Object.freeze(") && r.body.includes("window.HG_DISABLE_SERVICE_WORKER=true"));
    r = await exchange(servePort, { url: "/auth/status" });
    let body = json(r);
    assert("/auth/status says look-only for the den", r.status === 200 && body?.authenticated === false && body?.lookOnly === true
      && body?.lookOnlySite === DEN_SITE, body);
    r = await exchange(servePort, { url: "/home-3d/apartment-people-layer.mjs?cb=1" });
    assert("look-only reads Home's code", r.status === 200, r.status);
    r = await exchange(servePort, { url: "/content-packs/index.json" });
    body = json(r);
    assert("the packs index lists only the den's packs", r.status === 200 && body?.packs?.length === 1 && body.packs[0].manifest.siteId === DEN_SITE, body);
    r = await exchange(servePort, { url: `${den.base}frame.json` });
    assert("look-only reads the den's pack", r.status === 200, r.status);
    r = await exchange(servePort, { url: `${other.base}frame.json` });
    assert("look-only can't read the other site's pack", r.status === 401, r.status);
    r = await exchange(servePort, { url: REGISTRATION_ROUTE, headers: { Accept: "application/json" } });
    assert("look-only reads the den's registration", r.status === 200 && json(r)?.registration, r.status);
    r = await exchange(servePort, { method: "PUT", url: REGISTRATION_ROUTE, headers: { "Content-Type": "application/json" }, body: "{}" });
    assert("look-only can't write the registration", r.status === 401, r.status);
    upstream.seen.length = 0;
    r = await exchange(servePort, { url: `${DEN_LIVE_PREFIX}/v1/homes/den/state`, headers: { Cookie: "home_web_auth=forged", Authorization: "Bearer forged-token-0123456789" } });
    assert("look-only reads den-live's state", r.status === 200, r.status);
    const reached = upstream.seen.at(-1);
    assert("den-live gets no cookie or credential from a look-only device", reached && !reached.headers.cookie && !reached.headers.authorization
      && reached.headers.host === new URL(fake).host, reached?.headers);
    r = await exchange(servePort, { url: `${DEN_LIVE_PREFIX}/v1/homes/den/stream`, headers: { Accept: "text/event-stream" }, until: "event: people" });
    assert("look-only reads den-live's stream", r.status === 200 && r.body.includes("event: people"), r.head);
    r = await exchange(servePort, { url: `/api/lighting-brain/v1/homes/${DEN_SITE}/state` });
    assert("look-only reads the lighting brain's den state", r.status === 200, r.status);
    r = await exchange(servePort, { url: `/api/lighting-brain/v1/homes/${OTHER_SITE}/state`, headers: { Accept: "application/json" } });
    assert("look-only can't read the other home's lighting brain", r.status === 401, r.status);
    for (const [method, url] of [["POST", `${DEN_LIVE_PREFIX}/v1/homes/den/stream`], ["GET", "/api/world/v1/config"], ["GET", "/api/home/location"],
      ["GET", "/assets/apartment/points.ply"], ["POST", `/api/lighting-brain/v1/homes/${DEN_SITE}/overrides`], ["GET", "/api/perception-lab/v1/capabilities"]]) {
      r = await exchange(servePort, { method, url, headers: { Accept: "application/json", "Content-Type": "application/json" }, body: method === "POST" ? "{}" : "" });
      assert(`look-only ${method} ${url} needs sign-in`, r.status === 401, r.status);
    }
    upstream.seen.length = 0;
    r = await exchange(servePort, { method: "POST", url: `/api/lighting-brain/v1/homes/${DEN_SITE}/events`,
      headers: { Authorization: "Bearer producer-token-0123456789abcdef", "Content-Type": "application/json" }, body: "{\"type\":\"probe\"}" });
    assert("a producer posting with its own token is untouched by look-only", r.status === 200 && upstream.seen.at(-1)?.method === "POST"
      && upstream.seen.at(-1)?.headers.authorization === "Bearer producer-token-0123456789abcdef", r.status);
    r = await exchange(servePort, { client: "203.0.113.9", url: "/content-packs/index.json", headers: { Accept: "application/json" } });
    assert("an address off the tailnet needs sign-in", r.status === 401, r.status);
    r = await exchange(servePort, { client: null, url: "/content-packs/index.json", headers: { Accept: "application/json" } });
    assert("the Serve listener refuses a connection with no PROXY line", r.status === 0, r.status);
    r = await exchange(port, { client: null, url: "/content-packs/index.json", headers: { Accept: "application/json" } });
    assert("on the ordinary port, no PROXY line needs sign-in", r.status === 401, r.status);
    r = await exchange(port, { url: "/content-packs/index.json", headers: { Accept: "application/json" } });
    assert("on the ordinary port, a client's own PROXY line never gives look-only", r.status === 401, r.status);
    r = await exchange(port, { url: "/auth/status" });
    assert("on the ordinary port /auth/status isn't look-only", json(r)?.lookOnly === false, json(r));
    r = await exchange(servePort, { url: "/home-site-registry.js" });
    assert("the site registry reaches a look-only device redacted: no address, no coordinates", r.status === 200
      && /address:\s*""/.test(r.body) && /latitude:\s*NaN/.test(r.body) && !/\baddress:\s*"[^"]/.test(r.body)
      && !/\b(?:latitude|longitude):\s*-?\d/.test(r.body), r.body.slice(0, 200));
    r = await exchange(servePort, { url: "/home-site-registry.js", headers: { Authorization: `Basic ${Buffer.from(`${USER}:${PASSWORD}`).toString("base64")}` } });
    assert("a signed-in device gets the registry as it is", r.status === 200 && /\b(?:latitude|longitude):\s*-?\d/.test(r.body));
    for (const url of ["/spatial-spike/fixtures.js", "/home-3d/world-surface.test.mjs", "/home-agent/panel.js"]) {
      r = await exchange(servePort, { url, headers: { Accept: "application/json" } });
      // /home-agent/ is a 404 before sign-in is even asked (its own origin only); the rest need sign-in.
      assert(`look-only isn't served ${url}`, r.status === 401 || url.startsWith("/home-agent/") && r.status === 404, r.status);
    }
    r = await exchange(servePort, { url: `${DEN_LIVE_PREFIX}/v1/homes/den/state`, body: `GET ${DEN_LIVE_PREFIX}/v1/homes/den/other HTTP/1.1\r\nHost: x\r\n\r\n` });
    assert("a look-only GET with a body is refused", r.status === 400, r.status);
    r = await exchange(port, { url: `${DEN_LIVE_PREFIX}/v1/homes/den/state`, headers: { Authorization: `Basic ${Buffer.from(`${USER}:${PASSWORD}`).toString("base64")}` }, body: "x" });
    assert("a signed-in den-live GET with a body is refused", r.status === 400, r.status);
    const held = [1, 2].map(() => holdStream(servePort, `${DEN_LIVE_PREFIX}/v1/homes/den/stream`));
    await Promise.all(held.map(h => h.opened));
    r = await exchange(servePort, { url: `${DEN_LIVE_PREFIX}/v1/homes/den/stream`, headers: { Accept: "text/event-stream" } });
    assert("a look-only device holds at most two den-live streams", r.status === 429, r.status);
    held.forEach(h => h.close());
    await sleep(200);
    r = await exchange(servePort, { url: `${DEN_LIVE_PREFIX}/v1/homes/den/stream`, headers: { Accept: "text/event-stream" }, until: "event: people" });
    assert("a closed stream frees its place", r.status === 200, r.status);
    r = await exchange(servePort, { raw: ["GET /proxy/ha/api/websocket HTTP/1.1", `Host: ${HOST}`, "Upgrade: websocket", "Connection: Upgrade",
      "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==", "Sec-WebSocket-Version: 13", "", ""].join("\r\n") });
    assert("a look-only WebSocket upgrade needs sign-in", r.status === 401, r.head);

    const basic = `Basic ${Buffer.from(`${USER}:${PASSWORD}`).toString("base64")}`;
    r = await exchange(servePort, { url: "/auth/status", headers: { Authorization: basic } });
    assert("a signed-in tailnet device isn't look-only", json(r)?.authenticated === true && json(r)?.lookOnly === false, json(r));
    r = await exchange(servePort, { url: "/", headers: { Authorization: basic } });
    assert("a signed-in shell names den-live but isn't look-only", r.status === 200 && r.body.includes("window.HomeDenLive=")
      && !r.body.includes("window.HomeLookOnly="));
    r = await exchange(servePort, { url: "/content-packs/index.json", headers: { Authorization: basic } });
    assert("a signed-in device still sees every pack", json(r)?.packs?.length === 2, json(r));
    upstream.seen.length = 0;
    r = await exchange(servePort, { url: `${DEN_LIVE_PREFIX}/v1/homes/den/state`, headers: { Authorization: basic } });
    assert("a signed-in device reads den-live's state, with no credential passed on", r.status === 200
      && !upstream.seen.at(-1)?.headers.authorization && !upstream.seen.at(-1)?.headers.cookie, upstream.seen.at(-1)?.headers);
    r = await exchange(servePort, { method: "POST", url: `${DEN_LIVE_PREFIX}/v1/homes/den/state`, headers: { Authorization: basic }, body: "{}" });
    assert("nobody writes to den-live's state through the gateway", r.status === 403, r.status);

    // The lighting coordinator through the gateway.
    const COORDINATOR = `${DEN_LIVE_PREFIX}/v1/homes/den/coordinator`;
    const csrf = { "X-Home-Den-Live-CSRF": "1", "Content-Type": "application/json", Accept: "application/json" };
    upstream.seen.length = 0;
    r = await exchange(servePort, { method: "POST", url: `${COORDINATOR}/switch`, headers: { ...csrf, Authorization: basic, Cookie: "home_web_auth=x" }, body: "{\"on\":true}" });
    let reached2 = upstream.seen.at(-1);
    assert("a signed-in device turns the coordinator on through the gateway", r.status === 200 && reached2?.method === "POST"
      && reached2.url === `${COORDINATOR}/switch` && reached2.body === "{\"on\":true}" && reached2.headers["content-type"] === "application/json", reached2);
    assert("den-live gets the write with no credential, cookie or CSRF header", reached2 && !reached2.headers.authorization && !reached2.headers.cookie
      && !reached2.headers["x-home-den-live-csrf"] && reached2.headers.host === new URL(fake).host, reached2?.headers);
    for (const [path, body] of [["settings", "{\"move_style\":\"dim\"}"], ["learned/clear", "{}"], ["desk", "{\"on\":true}"]]) {
      upstream.seen.length = 0;
      r = await exchange(servePort, { method: "POST", url: `${COORDINATOR}/${path}`, headers: { ...csrf, Authorization: basic }, body });
      assert(`a signed-in device posts the coordinator's ${path}`, r.status === 200 && upstream.seen.at(-1)?.body === body, r.status);
    }
    upstream.seen.length = 0;
    r = await exchange(servePort, { method: "POST", url: `${COORDINATOR}/switch`, headers: { "Content-Type": "application/json", Authorization: basic }, body: "{\"on\":false}" });
    assert("a signed-in write with no CSRF header is refused before den-live", r.status === 403 && json(r)?.error === "den_live_csrf_required"
      && upstream.seen.length === 0, r.status);
    r = await exchange(servePort, { method: "POST", url: `${COORDINATOR}/switch`, headers: { ...csrf, "Content-Type": "text/plain", Authorization: basic }, body: "{\"on\":false}" });
    assert("a signed-in write that isn't JSON is refused before den-live", r.status === 415 && upstream.seen.length === 0, r.status);
    r = await exchange(servePort, { method: "POST", url: `${COORDINATOR}/other`, headers: { ...csrf, Authorization: basic }, body: "{}" });
    assert("a signed-in write to another coordinator path is refused", r.status === 403 && upstream.seen.length === 0, r.status);
    r = await exchange(servePort, { method: "POST", url: `${COORDINATOR}/switch`, headers: csrf, body: "{\"on\":false}" });
    assert("a look-only device can't flip the coordinator", r.status === 401 && upstream.seen.length === 0, r.status);
    r = await exchange(port, { client: null, method: "POST", url: `${COORDINATOR}/switch`, headers: csrf, body: "{\"on\":false}" });
    assert("no session, no write, on the ordinary port too", r.status === 401 && upstream.seen.length === 0, r.status);
    r = await exchange(servePort, { url: COORDINATOR, headers: { Accept: "application/json" } });
    assert("a look-only device reads the coordinator's state", r.status === 200 && upstream.seen.at(-1)?.url === COORDINATOR
      && !upstream.seen.at(-1)?.headers.cookie, r.status);
    r = await exchange(servePort, { url: COORDINATOR, headers: { Accept: "application/json", Authorization: basic } });
    assert("a signed-in device reads the coordinator's state", r.status === 200, r.status);
  } finally {
    gateway.child.kill();
  }

  const offPort = await freePort();
  const off = await startGateway(offPort, { ...base, HOME_WEB_PORT: String(offPort), HOME_WEB_LOOK_ONLY: "1" });
  try {
    assert("gateway starts with both off", off.ready);
    let r = await exchange(offPort, { url: "/", headers: { Accept: "text/html" } });
    assert("with look-only off, a tailnet device with no session gets the sign-in page", r.status === 401 && !r.body.includes("window.HomeLookOnly="), r.status);
    r = await exchange(offPort, { url: "/auth/status" });
    assert("with look-only off, /auth/status isn't look-only", json(r)?.lookOnly === false, json(r));
    const basic = `Basic ${Buffer.from(`${USER}:${PASSWORD}`).toString("base64")}`;
    r = await exchange(offPort, { url: "/", headers: { Authorization: basic } });
    assert("with den-live off, the shell doesn't name it", r.status === 200 && !r.body.includes("window.HomeDenLive="));
    r = await exchange(offPort, { url: `${DEN_LIVE_PREFIX}/v1/homes/den/state`, headers: { Authorization: basic } });
    assert("with den-live off, its route is a disabled capability", r.status === 404 && json(r)?.error === "capability_disabled", r.status);
    r = await exchange(offPort, { method: "POST", url: `${DEN_LIVE_PREFIX}/v1/homes/den/coordinator/switch`,
      headers: { Authorization: basic, "X-Home-Den-Live-CSRF": "1", "Content-Type": "application/json" }, body: "{\"on\":true}" });
    assert("with den-live off, a coordinator write is a disabled capability too", r.status === 404 && json(r)?.error === "capability_disabled", r.status);
  } finally {
    off.child.kill();
    upstream.server.close();
    fs.rmSync(temp, { recursive: true, force: true });
  }
}

unitTests();
await integrationTests();
process.stdout.write(`\n${passes} passed, ${fails} failed\n`);
process.exit(fails ? 1 : 0);
