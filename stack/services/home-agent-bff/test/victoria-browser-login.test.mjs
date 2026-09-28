import assert from "node:assert/strict";
import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import https from "node:https";
import http from "node:http";
import { fileURLToPath } from "node:url";
import test from "node:test";
import { COOKIE_NAME, createVictoriaSessionStore } from "../src/bff.mjs";
import { QualifiedHaAuth } from "../src/qualified-ha-auth.mjs";
import { SharedSessionRevocationClient } from "../src/shared-session-revocation-client.mjs";
import { createVictoriaBrowserLogin, VICTORIA_OAUTH_COOKIE_NAME } from "../src/victoria-browser-login.mjs";

const certificates = fileURLToPath(new URL("../../../../tools/shared-home/tests/fixtures/gateway_client/", import.meta.url));
const ca = fs.readFileSync(path.join(certificates, "ca.pem"));
const tls = { key: fs.readFileSync(path.join(certificates, "server-test-only.key")), cert: fs.readFileSync(path.join(certificates, "server.pem")) };
const responseJson = (res, value, status = 200) => { res.writeHead(status, { "content-type": "application/json" }); res.end(JSON.stringify(value)); };
const listen = (server) => new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
const stop = (server) => new Promise((resolve) => { server.close(resolve); server.closeIdleConnections(); });
const pause = () => { let resolve; const promise = new Promise((done) => { resolve = done; }); return { promise, resolve }; };
const waitFor = async (predicate) => {
  const until = Date.now() + 3000;
  while (!predicate()) { if (Date.now() >= until) assert.fail("fixture operation did not settle"); await new Promise((resolve) => setTimeout(resolve, 5)); }
};
function request(origin, target, { method = "GET", headers = {}, body = "", signal } = {}) {
  const url = new URL(origin);
  return new Promise((resolve, reject) => {
    const outgoing = https.request({ hostname: "127.0.0.1", servername: "localhost", port: url.port,
      ca, method, path: target, agent: false, signal,
      headers: Array.isArray(headers) ? ["Host", url.host, ...headers] : { host: url.host, ...headers } }, (res) => {
      const chunks = []; res.on("data", (chunk) => chunks.push(chunk));
      res.on("end", () => resolve({ status: res.statusCode, headers: res.headers, text: Buffer.concat(chunks).toString(),
        json() { return JSON.parse(this.text); } }));
    });
    outgoing.on("error", reject); outgoing.end(body);
  });
}
async function fixture(t) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "victoria-browser-login-"));
  const calls = []; let subject = "owner", offset = 0, hold = null, failRevocation = false, helper;
  const ha = https.createServer(tls, async (req, res) => {
    const parts = []; for await (const chunk of req) parts.push(chunk);
    const body = Buffer.concat(parts).toString(), values = new URLSearchParams(body);
    calls.push({ path: req.url, values, headers: req.headers });
    if (req.url === "/auth/token") {
      if (values.get("action") === "revoke") return responseJson(res, {}, failRevocation ? 503 : 200);
      if (hold) await hold.promise;
      return responseJson(res, { access_token: "fixture-access", refresh_token: "fixture-refresh", expires_in: 300 });
    }
    if (req.url === "/api/home_agent_edge/whoami") return responseJson(res, { user_id: subject, is_active: true, is_admin: false });
    if (req.url === "/internal/shared-identity/v1/session-revocations") return responseJson(res, { version: 1,
      revocation: { ...JSON.parse(body).revocation, issuer_id: "home-assistant:victoria", revoked_at: new Date(Date.now() + offset).toISOString() } });
    responseJson(res, {}, 404);
  });
  const browser = https.createServer(tls, async (req, res) => {
    if (!await helper.handle(req, res)) responseJson(res, { error: "not_found" }, 404);
  });
  await listen(ha); await listen(browser);
  const origin = `https://localhost:${browser.address().port}`, haOrigin = `https://localhost:${ha.address().port}`;
  const fetchImpl = async (url, options) => {
    const parsed = new URL(url);
    assert.equal(parsed.origin, haOrigin);
    const result = await request(parsed.origin, parsed.pathname, { method: options.method || "GET", signal: options.signal,
      headers: Object.fromEntries(new Headers(options.headers)), body: options.body?.toString() || "" });
    return new Response(result.text, { status: result.status, headers: result.headers });
  };
  const now = () => Date.now() + offset, key = crypto.randomBytes(32);
  const auth = new QualifiedHaAuth({ issuerId: "home-assistant:victoria", siteId: "victoria", origin: haOrigin,
    clientId: origin + "/", redirectUri: origin + "/auth/callback", fetchImpl });
  const client = new SharedSessionRevocationClient({ endpoint: haOrigin + "/internal/shared-identity/v1/session-revocations",
    issuerId: "home-assistant:victoria", credential: "a".repeat(64), fetchImpl, now });
  const options = { auth, browserOrigin: origin, echoOrigins: new Set(["https://echo.test"]),
    sessionDbPath: path.join(directory, "sessions.sqlite"), sessionEncryptionKey: crypto.randomBytes(32),
    idleTtlMs: 600_000, absoluteTtlMs: 1200_000, now, sharedSessionRevocation: { client, commitmentKey: key } };
  const { store, config } = createVictoriaSessionStore(options);
  helper = createVictoriaBrowserLogin({ store, config });
  t.after(async () => {
    hold?.resolve(); helper.close(); await stop(browser); await stop(ha); store.close();
    fs.rmSync(directory, { recursive: true, force: true });
  });
  const start = async () => {
    const result = await request(origin, "/api/agent/auth/start", { method: "POST", headers: { origin } });
    assert.equal(result.status, 200);
    return { result, state: new URL(result.json().authorize_url).searchParams.get("state"), cookie: result.headers["set-cookie"][0].split(";")[0] };
  };
  const callback = (started, extra = "", headers = {}) => request(origin,
    `/auth/callback?state=${started.state}&code=${"a".repeat(32)}${extra}`, { headers: { cookie: started.cookie, ...headers } });
  const login = async () => {
    const result = await callback(await start()); assert.equal(result.status, 303);
    const value = result.headers["set-cookie"].find((item) => item.startsWith(COOKIE_NAME + "=")).split(";")[0];
    return { cookie: value, id: value.split("=")[1], result };
  };
  return { origin, haOrigin, store, config, helper, key, calls, start, callback, login,
    advance: (milliseconds) => { offset += milliseconds; }, changeSubject: () => { subject = "someone-else"; },
    hold: () => { hold = pause(); return hold; }, failRevocation: () => { failRevocation = true; } };
}

test("TLS OAuth login, forced session verification and issuer-bound logout use fixed endpoints", async (t) => {
  const f = await fixture(t), started = await f.start(), authorization = new URL(started.result.json().authorize_url);
  assert.equal(authorization.origin, f.haOrigin); assert.equal(authorization.pathname, "/auth/authorize");
  assert.equal(authorization.searchParams.get("client_id"), f.origin + "/");
  assert.equal(authorization.searchParams.get("redirect_uri"), f.origin + "/auth/callback");
  assert.match(started.state, /^[A-Za-z0-9_-]{43}$/);
  assert.match(started.result.headers["set-cookie"][0], /HttpOnly; Secure; SameSite=Lax; Max-Age=300/);
  const callback = await f.callback(started); assert.equal(callback.status, 303); assert.equal(callback.headers.location, "/");
  const sessionCookie = callback.headers["set-cookie"].find((value) => value.startsWith(COOKIE_NAME + "=")).split(";")[0];
  const sessionId = sessionCookie.split("=")[1];
  const result = await request(f.origin, "/api/agent/auth/session", { headers: { cookie: sessionCookie, "sec-fetch-site": "same-origin" } });
  assert.equal(result.status, 200); assert.equal(result.json().authority.site_id, "victoria"); assert.equal(result.json().user_id, "owner");
  assert.equal(f.calls.filter((call) => call.path.endsWith("whoami")).length, 2);
  const context = f.store.linkingContext(sessionId, f.key);
  const denied = await request(f.origin, "/api/agent/auth/logout", { method: "POST", headers: { origin: f.origin, cookie: sessionCookie, "x-csrf-token": "wrong" } });
  assert.equal(denied.status, 403); assert.ok(f.store.get(sessionId));
  const loggedOut = await request(f.origin, "/api/agent/auth/logout", { method: "POST",
    headers: { origin: f.origin, cookie: sessionCookie, "x-csrf-token": result.json().csrf_token } });
  assert.equal(loggedOut.status, 200); assert.equal(f.store.get(sessionId), null);
  assert.ok(f.calls.some((call) => call.values.get("action") === "revoke"));
  assert.equal(f.calls.filter((call) => call.path.includes("session-revocations")).length, 1);
  assert.match(context.sessionCommitment, /^[a-f0-9]{64}$/);
  assert.ok(f.calls.every((call) => !call.headers.cookie));
});

test("origin, host, body, duplicate headers/cookies and claimed identity are rejected before HA", async (t) => {
  const f = await fixture(t);
  for (const [headers, body] of [
    [{ origin: "https://evil.test" }, ""], [{ origin: f.origin, host: "evil.test" }, ""],
    [{ origin: f.origin, authorization: "Bearer forged" }, ""], [{ origin: f.origin, "x-authenticated-ha-user": "owner" }, ""],
    [{ origin: f.origin, "x-home-agent-channel": "private" }, ""], [{ origin: f.origin, "x-forwarded-host": "localhost" }, ""],
    [["Origin", f.origin, "Origin", f.origin], ""], [{ origin: f.origin }, "unexpected"],
    [{ origin: f.origin, cookie: `${COOKIE_NAME}=${"a".repeat(43)}; ${COOKIE_NAME}=${"b".repeat(43)}` }, ""],
  ]) {
    const response = await request(f.origin, "/api/agent/auth/start", { method: "POST", headers, body });
    assert.ok([400, 403].includes(response.status), `${response.status}: ${response.text}`);
  }
  assert.equal((await request(f.origin, "/api/agent/auth/session")).status, 403);
  assert.equal((await request(f.origin, "/api/agent/auth/start?next=https://evil.test", { method: "POST", headers: { origin: f.origin } })).status, 400);
  assert.equal((await request(f.origin, "/api/agent/auth/start", { headers: { origin: f.origin } })).status, 405);
  assert.equal((await request(f.origin, "/unrelated")).status, 404);
  assert.equal(f.calls.length, 0);
});

test("callback requires exact parameters and nonce, consumes state once and rejects clock discontinuity", async (t) => {
  const f = await fixture(t), started = await f.start();
  for (const extra of ["&next=/other", "&state=duplicate", "&code=duplicate"]) assert.equal((await f.callback(started, extra)).status, 400);
  assert.equal((await f.callback(started, "", { cookie: `${VICTORIA_OAUTH_COOKIE_NAME}=${"b".repeat(43)}` })).status, 400);
  assert.equal((await f.callback(started)).status, 400);
  const expired = await f.start(); f.advance(300_001); assert.equal((await f.callback(expired)).status, 400);
  const reversed = await f.start(); f.advance(-2000); assert.equal((await f.callback(reversed)).status, 400);
  assert.equal(f.calls.length, 0);
});

test("pending starts are capped, stale starts are pruned and close invalidates pending state", async (t) => {
  const f = await fixture(t);
  for (let index = 0; index < 32; index++) await f.start();
  assert.equal((await request(f.origin, "/api/agent/auth/start", { method: "POST", headers: { origin: f.origin } })).status, 429);
  f.advance(300_001); const started = await f.start(); f.helper.close();
  assert.equal((await f.callback(started)).status, 503); assert.equal(f.calls.length, 0);
});

test("two active login exchanges retain admission slots until transport settles", async (t) => {
  const f = await fixture(t), starts = await Promise.all([f.start(), f.start(), f.start()]), hold = f.hold();
  const first = f.callback(starts[0]), second = f.callback(starts[1]);
  await waitFor(() => f.calls.filter((call) => call.values.get("grant_type") === "authorization_code").length === 2);
  assert.equal((await f.callback(starts[2])).status, 429);
  hold.resolve(); assert.equal((await first).status, 303); assert.equal((await second).status, 303);
  assert.equal((await f.callback(starts[2])).status, 303);
});

test("disconnected callback revokes an eventual login instead of leaving undisclosed authority", async (t) => {
  const f = await fixture(t), started = await f.start(), hold = f.hold(), controller = new AbortController();
  const result = request(f.origin, `/auth/callback?state=${started.state}&code=${"a".repeat(32)}`,
    { headers: { cookie: started.cookie }, signal: controller.signal }).catch((error) => error);
  await waitFor(() => f.calls.some((call) => call.values.get("grant_type") === "authorization_code"));
  controller.abort(); await result; hold.resolve();
  await waitFor(() => f.calls.some((call) => call.values.get("action") === "revoke"));
  await waitFor(() => f.store.sessions.size === 0);
});

test("changed HA principal invalidates session and failed logout stays revocation-only", async (t) => {
  const f = await fixture(t), login = await f.login(); f.changeSubject();
  const response = await request(f.origin, "/api/agent/auth/session", { headers: { origin: f.origin, cookie: login.cookie } });
  assert.equal(response.status, 401); assert.equal(f.store.get(login.id), null);
  const next = await f.login(), session = f.store.get(next.id); f.failRevocation();
  const logout = await request(f.origin, "/api/agent/auth/logout", { method: "POST",
    headers: { origin: f.origin, cookie: next.cookie, "x-csrf-token": session.csrf } });
  assert.equal(logout.status, 503); assert.equal(logout.json().error, "logout_revocation_pending");
  assert.equal(f.store.get(next.id), null); assert.equal(f.store.getForRevocation(next.id).state, "revocation_pending");
});

test("configuration must be the exact qualified boundary and plain HTTP cannot enter login", async (t) => {
  const f = await fixture(t);
  assert.throws(() => createVictoriaBrowserLogin({ store: f.store, config: { ...f.config } }), /configuration/);
  const server = http.createServer(async (req, res) => { await f.helper.handle(req, res); }); await listen(server);
  try {
    const result = await fetch(`http://127.0.0.1:${server.address().port}/api/agent/auth/start`, { method: "POST",
      headers: { origin: f.origin, host: new URL(f.origin).host } });
    assert.equal(result.status, 403); assert.equal(f.calls.length, 0);
  } finally { await stop(server); }
});
