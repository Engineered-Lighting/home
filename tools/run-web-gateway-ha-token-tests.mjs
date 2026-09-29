#!/usr/bin/env node
// Gateway-held HA token, long-lived login cookie and restart-stable asset
// version. Runs the real gateway against a fake HA (REST + websocket).
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
const HA_TOKEN = "fake-long-lived-ha-token-not-real.0123456789";
const MARKER = "__home_web_gateway_ha_token__";
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
const freePort = () => new Promise((resolve, reject) => {
  const probe = net.createServer();
  probe.once("error", reject);
  probe.listen(0, "127.0.0.1", () => { const { port } = probe.address(); probe.close(() => resolve(port)); });
});

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

// Server-side frame helpers for the fake HA.
function serverFrame(text) {
  const payload = Buffer.from(text, "utf8");
  if (payload.length >= 126) throw new Error("test frames stay small");
  return Buffer.concat([Buffer.from([0x81, payload.length]), payload]);
}
function takeMaskedFrames(state) {
  const frames = [];
  for (;;) {
    const b = state.buffer;
    if (b.length < 2) break;
    let length = b[1] & 0x7f;
    let offset = 2;
    if (length === 126) { if (b.length < 4) break; length = b.readUInt16BE(2); offset = 4; }
    else if (length === 127) { if (b.length < 10) break; length = Number(b.readBigUInt64BE(2)); offset = 10; }
    if (!(b[1] & 0x80)) throw new Error("upstream received an unmasked client frame");
    if (b.length < offset + 4 + length) break;
    const mask = b.subarray(offset, offset + 4);
    const payload = Buffer.alloc(length);
    for (let i = 0; i < length; i += 1) payload[i] = b[offset + 4 + i] ^ mask[i % 4];
    frames.push({ opcode: b[0] & 0x0f, text: payload.toString("utf8") });
    state.buffer = b.subarray(offset + 4 + length);
  }
  return frames;
}

// Browser-side masked frame with a chosen length encoding.
function clientFrame(text, { form } = {}) {
  const payload = Buffer.from(text, "utf8");
  const length = payload.length;
  let header;
  if (form === 127) {
    header = Buffer.alloc(10); header[0] = 0x81; header[1] = 0x80 | 127; header.writeBigUInt64BE(BigInt(length), 2);
  } else if (length >= 126 || form === 126) {
    header = Buffer.from([0x81, 0x80 | 126, length >> 8, length & 0xff]);
  } else {
    header = Buffer.from([0x81, 0x80 | length]);
  }
  const mask = crypto.randomBytes(4);
  const body = Buffer.alloc(length);
  for (let i = 0; i < length; i += 1) body[i] = payload[i] ^ mask[i % 4];
  return Buffer.concat([header, mask, body]);
}

function startFakeHa() {
  const seen = { rest: [], upgrades: [], messages: [], sockets: new Set() };
  const server = http.createServer((req, res) => {
    seen.rest.push({ url: req.url, authorization: req.headers.authorization || "" });
    res.writeHead(200, { "Content-Type": "application/json" });
    res.end("[]");
  });
  server.on("upgrade", (req, socket) => {
    seen.sockets.add(socket);
    seen.upgrades.push({ extensions: req.headers["sec-websocket-extensions"] || "", authorization: req.headers.authorization || "" });
    const accept = crypto.createHash("sha1")
      .update(`${req.headers["sec-websocket-key"]}258EAFA5-E914-47DA-95CA-C5AB0DC85B11`).digest("base64");
    socket.write(`HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: ${accept}\r\n\r\n`);
    socket.write(serverFrame(JSON.stringify({ type: "auth_required" })));
    const state = { buffer: Buffer.alloc(0) };
    socket.on("data", (chunk) => {
      state.buffer = Buffer.concat([state.buffer, chunk]);
      for (const frame of takeMaskedFrames(state)) {
        seen.messages.push(frame.text);
        let message = null;
        try { message = JSON.parse(frame.text); } catch { /* not JSON */ }
        if (message?.type === "auth") {
          socket.write(serverFrame(JSON.stringify({ type: message.access_token === HA_TOKEN ? "auth_ok" : "auth_invalid" })));
        } else if (message?.type === "ping") {
          socket.write(serverFrame(JSON.stringify({ type: "pong", id: message.id })));
        }
      }
    });
    socket.on("error", () => {});
  });
  return new Promise((resolve) => server.listen(0, "127.0.0.1", () => resolve({ server, port: server.address().port, seen })));
}

// Open a websocket through the gateway and return a small client.
function openSocket(port, cookie) {
  return new Promise((resolve, reject) => {
    const socket = net.connect(port, "127.0.0.1");
    const client = { socket, received: [], closed: false, buffer: Buffer.alloc(0), upgraded: false };
    socket.on("close", () => { client.closed = true; });
    socket.on("error", () => {});
    socket.on("data", (chunk) => {
      client.buffer = Buffer.concat([client.buffer, chunk]);
      if (!client.upgraded) {
        const end = client.buffer.indexOf("\r\n\r\n");
        if (end < 0) return;
        const head = client.buffer.subarray(0, end).toString("utf8");
        client.buffer = client.buffer.subarray(end + 4);
        if (!head.startsWith("HTTP/1.1 101")) { reject(new Error(head.split("\r\n")[0])); return; }
        client.upgraded = true;
        resolve(client);
      }
      while (client.buffer.length >= 2 && client.buffer.length >= 2 + (client.buffer[1] & 0x7f)) {
        const length = client.buffer[1] & 0x7f;
        client.received.push(client.buffer.subarray(2, 2 + length).toString("utf8"));
        client.buffer = client.buffer.subarray(2 + length);
      }
    });
    socket.write([
      "GET /proxy/ha/api/websocket HTTP/1.1",
      `Host: 127.0.0.1:${port}`,
      "Upgrade: websocket",
      "Connection: Upgrade",
      `Sec-WebSocket-Key: ${crypto.randomBytes(16).toString("base64")}`,
      "Sec-WebSocket-Version: 13",
      "Sec-WebSocket-Extensions: permessage-deflate; client_max_window_bits",
      `Cookie: ${cookie}`,
      "", "",
    ].join("\r\n"));
  });
}

async function waitFor(check, ms = 3000) {
  for (let waited = 0; waited < ms; waited += 25) {
    if (check()) return true;
    await sleep(25);
  }
  return check();
}

async function startGateway(env) {
  const port = await freePort();
  const child = childProcess.spawn(process.execPath, [SERVER], {
    cwd: REPO, env: { ...process.env, HOME_WEB_HOST: "127.0.0.1", HOME_WEB_PORT: String(port), ...env },
    stdio: ["ignore", "ignore", "pipe"],
  });
  for (let i = 0; i < 80; i += 1) {
    if (child.exitCode != null) throw new Error(`gateway exited early with ${child.exitCode}`);
    try {
      const res = await request(port, "/healthz");
      if (res.status === 200) return { port, child, health: JSON.parse(res.body) };
    } catch { /* keep polling */ }
    await sleep(100);
  }
  throw new Error("gateway did not become ready");
}

async function main() {
  process.stdout.write("\nweb_gateway_ha_token_proxy_test\n");
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), "home-gateway-ha-token-"));
  const tokenFile = path.join(temp, "ha-token");
  fs.writeFileSync(tokenFile, `${HA_TOKEN}\n`, { mode: 0o600 });
  const ha = await startFakeHa();
  const baseEnv = {
    HOME_WEB_AUTH_REQUIRED: "1",
    HOME_WEB_AUTH_FILE: path.join(temp, "missing-auth.json"),
    HOME_WEB_BASIC_AUTH: `${USER}:${PASSWORD}`,
    HOME_WEB_ENABLE_LEGACY_HA_PROXY: "1",
    HOME_WEB_HA_TARGET: `http://127.0.0.1:${ha.port}`,
    HOME_WEB_HA_TOKEN_FILE: tokenFile,
    HOME_WEB_ASSET_VERSION: "",
  };
  const children = [];
  const probe = path.join(REPO, "app", "src", `.asset-version-probe-${process.pid}`);
  try {
    const first = await startGateway(baseEnv);
    children.push(first.child);
    assert("health reports the HA token proxy without its value",
      first.health.haTokenProxy?.enabled === true && first.health.haTokenProxy?.source === "file" &&
      !JSON.stringify(first.health).includes(HA_TOKEN), first.health.haTokenProxy);

    const login = await request(first.port, "/auth/login", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: USER, password: PASSWORD }),
    });
    const setCookie = String(login.headers["set-cookie"] || "");
    assert("login succeeds", login.status === 200, login.status);
    assert("login cookie lasts 400 days", /; Max-Age=34560000(?:;|$)/.test(setCookie) && /HttpOnly/.test(setCookie) && /Secure/.test(setCookie), setCookie);
    const cookie = setCookie.split(";")[0];

    const index = await request(first.port, "/", { headers: { Cookie: cookie } });
    assert("index announces the gateway-held HA token", index.body.includes(`window.__HOME_WEB_HA_TOKEN_PROXY=true;window.__HOME_WEB_HA_TOKEN="${MARKER}"`), index.body.slice(0, 400));
    assert("index never contains the real HA token", !index.body.includes(HA_TOKEN));

    ha.seen.rest.length = 0;
    const states = await request(first.port, "/proxy/ha/api/states", { headers: { Cookie: cookie, Authorization: `Bearer ${MARKER}` } });
    assert("REST through /proxy/ha succeeds", states.status === 200, states.status);
    assert("REST reaches HA with the gateway's token", ha.seen.rest[0]?.authorization === `Bearer ${HA_TOKEN}`, ha.seen.rest[0]);

    for (const [name, frame, chunked] of [
      ["7-bit auth frame", clientFrame(JSON.stringify({ type: "auth", access_token: MARKER })), false],
      ["16-bit auth frame", clientFrame(JSON.stringify({ type: "auth", access_token: MARKER, note: "x".repeat(300) })), false],
      ["64-bit-length auth frame", clientFrame(JSON.stringify({ type: "auth", access_token: MARKER }), { form: 127 }), false],
      ["auth frame split across TCP chunks", clientFrame(JSON.stringify({ type: "auth", access_token: MARKER })), true],
    ]) {
      ha.seen.messages.length = 0;
      const ws = await openSocket(first.port, cookie);
      await waitFor(() => ws.received.some((text) => text.includes("auth_required")));
      if (chunked) {
        for (const byte of frame) { ws.socket.write(Buffer.from([byte])); await sleep(1); }
      } else {
        ws.socket.write(frame);
      }
      ws.socket.write(clientFrame(JSON.stringify({ id: 1, type: "ping" })));
      await waitFor(() => ws.received.some((text) => text.includes("pong")));
      const auth = ha.seen.messages.map((text) => { try { return JSON.parse(text); } catch { return null; } }).find((m) => m?.type === "auth");
      assert(`${name}: HA receives the real token`, auth?.access_token === HA_TOKEN, auth);
      assert(`${name}: browser sees auth_ok and later frames pass through`,
        ws.received.some((text) => text.includes("auth_ok")) && ws.received.some((text) => text.includes("pong")), ws.received);
      ws.socket.destroy();
    }
    assert("websocket compression is not negotiated through the rewrite", ha.seen.upgrades.every((u) => u.extensions === ""), ha.seen.upgrades);

    ha.seen.messages.length = 0;
    const plain = await openSocket(first.port, cookie);
    await waitFor(() => plain.received.length > 0);
    plain.socket.write(clientFrame(JSON.stringify({ id: 7, type: "ping" })));
    await waitFor(() => plain.received.some((text) => text.includes("pong")));
    assert("a non-auth first frame is forwarded unchanged", ha.seen.messages[0] === JSON.stringify({ id: 7, type: "ping" }), ha.seen.messages);
    plain.socket.destroy();

    const oversized = await openSocket(first.port, cookie);
    await waitFor(() => oversized.received.length > 0);
    oversized.socket.write(clientFrame(JSON.stringify({ type: "auth", access_token: MARKER, pad: "x".repeat(20000) })));
    assert("an oversized first frame closes the connection", await waitFor(() => oversized.closed), oversized.closed);

    const second = await startGateway(baseEnv);
    children.push(second.child);
    assert("asset version is stable across restarts", second.health.assetVersion === first.health.assetVersion,
      [first.health.assetVersion, second.health.assetVersion]);
    let probeWritten = false;
    try { fs.writeFileSync(probe, "changed shell content\n"); probeWritten = true; }
    catch (error) {
      // The hosted gate mounts the checkout read-only; this check runs locally.
      if (!["EROFS", "EACCES", "EPERM"].includes(error.code)) throw error;
      process.stdout.write("  SKIP  asset version changes when shell content changes (read-only checkout)\n");
    }
    if (probeWritten) {
      const third = await startGateway(baseEnv);
      children.push(third.child);
      assert("asset version changes when shell content changes", third.health.assetVersion !== first.health.assetVersion,
        [first.health.assetVersion, third.health.assetVersion]);
    }

    if (process.platform !== "win32") {
      fs.chmodSync(tokenFile, 0o644);
      const loose = await startGateway(baseEnv);
      children.push(loose.child);
      assert("a group- or world-readable token file disables the proxy", loose.health.haTokenProxy?.enabled === false, loose.health.haTokenProxy);
    }
    const none = await startGateway({ ...baseEnv, HOME_WEB_HA_TOKEN_FILE: "" });
    children.push(none.child);
    const plainIndex = await request(none.port, "/", { headers: { Cookie: cookie } });
    assert("without a token file the page is told there is no proxy", plainIndex.body.includes("window.__HOME_WEB_HA_TOKEN_PROXY=false;") && !plainIndex.body.includes(MARKER));
  } finally {
    for (const child of children) if (child.exitCode == null) child.kill();
    fs.rmSync(probe, { force: true });
    for (const socket of ha.seen.sockets) socket.destroy();
    ha.server.closeAllConnections();
    await new Promise((resolve) => ha.server.close(() => resolve()));
    fs.rmSync(temp, { recursive: true, force: true });
  }
  process.stdout.write(`\n${passes} pass . ${fails} fail\n`);
  process.exitCode = fails ? 1 : 0;
}

main().catch((error) => { console.error(error); process.exitCode = 1; });
