import assert from "node:assert/strict";
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";
import { test } from "node:test";
import { COOKIE_NAME, SessionStore, createBff } from "../src/bff.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../../..");
const origin = "https://echo-agent.test";

async function fixture(t, upstream) {
  const config = {
    bindHost: "127.0.0.1", port: 0, allowedOrigins: new Set([origin]),
    haUrl: "https://echo-ha.test", clientId: origin,
    redirectUri: `${origin}/api/agent/auth/callback`, postLoginRedirect: "/",
    coreUrl: "http://core.fixture:8096", coreToken: "synthetic-internal-token",
    sessionEncryptionKey: crypto.randomBytes(32), sessionDbPath: "",
    allowInMemorySessions: true, secureCookie: false,
    idleTtlMs: 60_000, absoluteTtlMs: 120_000, principalRevalidateMs: 60_000,
    sessionCleanupIntervalMs: 60_000, sessionCleanupBatchSize: 10,
    nativeInstallations: null, nativePublicOrigin: "https://native.echo-agent.test",
    nativeAttestationConfigured: false, ready: true,
  };
  const store = new SessionStore(config);
  const session = store.createSession({ principal: { userId: "synthetic-owner", isAdmin: false },
    accessToken: "synthetic-ha-access", refreshToken: "synthetic-ha-refresh" });
  const server = createBff(config, { store, fetchImpl: upstream });
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  t.after(async () => {
    server.closeAllConnections();
    await new Promise(resolve => server.close(resolve));
  });
  const local = `http://127.0.0.1:${server.address().port}`;
  const calls = [];
  const navigations = [];
  let cookie = `${COOKIE_NAME}=${session.id}`;
  const browserFetch = async (input, init) => {
    const target = new URL(input, origin);
    assert.equal(target.origin, origin, "browser attempted an unprovisioned authority");
    assert.equal(init.credentials, "include");
    calls.push({ path: target.pathname, signal: init.signal });
    // Only this fixture maps the fixed browser HTTPS origin to its ephemeral
    // local HTTP server and supplies a synthetic browser cookie. Production
    // endpoint validation and TLS policy are not changed for the test.
    const response = await fetch(`${local}${target.pathname}${target.search}`, {
      ...init, headers: { ...init.headers, origin, ...(cookie ? { cookie } : {}) },
    });
    if ((response.headers.get("set-cookie") || "").includes(`${COOKIE_NAME}=`) &&
        /Max-Age=0/.test(response.headers.get("set-cookie") || "")) cookie = "";
    return response;
  };
  const window = { location: { origin, href: `${origin}/home-agent/index.html`, assign: url => navigations.push(url) },
    crypto, performance, AbortController, fetch: browserFetch };
  const context = vm.createContext({ window, globalThis: window, URL, AbortController,
    performance, fetch: browserFetch, setTimeout, clearTimeout });
  for (const source of ["app/src/home-connection-registry.js", "app/src/home-agent/api.js"]) {
    vm.runInContext(fs.readFileSync(path.join(root, source), "utf8"), context, { filename: source });
  }
  return { api: new window.HomeAgentApi(), calls, session, store, navigations };
}

test("actual browser connection accepts BFF-issued authority before private semantic reads", async t => {
  const upstream = [];
  const { api } = await fixture(t, async (url, init) => {
    upstream.push({ url, headers: init.headers });
    return Response.json({ rollout_mode: "shadow", fixture: true });
  });
  const session = await api.session();
  assert.deepEqual(JSON.parse(JSON.stringify(session.authority)), {
    version: 1, site_id: "echo", ha_issuer_id: "home-assistant:echo",
  });
  assert.equal(session.user_id, "synthetic-owner");
  assert.equal(api.csrf, session.csrf_token);
  const snapshot = await api.snapshot();
  assert.equal(snapshot.fixture, true);
  assert.equal(upstream.length, 1);
  assert.equal(upstream[0].url, "http://core.fixture:8096/v1/snapshot");
  assert.equal(upstream[0].headers["X-Authenticated-HA-Issuer"], "home-assistant:echo");
  assert.equal(upstream[0].headers["X-Authenticated-Home-Site"], "echo");
});

test("real browser logout cancels delayed private BFF response and cannot restore its session", async t => {
  let release, entered;
  const blocked = new Promise(resolve => { release = resolve; });
  const started = new Promise(resolve => { entered = resolve; });
  const { api, calls, store, session } = await fixture(t, async url => {
    if (url.endsWith("/v1/snapshot")) {
      entered();
      await blocked;
      return Response.json({ stale_private_fixture: true });
    }
    assert.equal(url, "https://echo-ha.test/auth/token");
    return Response.json({});
  });
  t.after(() => release());
  await api.session();
  const pending = api.snapshot().then(value => ({ value }), error => ({ error }));
  await started;
  await api.logout();
  release();
  const result = await pending;
  assert.ok(result.error, "delayed semantic response must not reach the caller");
  assert.equal(result.value, undefined);
  assert.equal(calls.find(call => call.path.endsWith("/snapshot")).signal.aborted, true);
  assert.equal(api.csrf, "");
  assert.equal(store.get(session.id), null);
  await assert.rejects(api.session());
  assert.equal(api.csrf, "");
});

test("BFF revocation failure clears the cookie while browser recovery requires explicit login", async t => {
  const { api, calls, store, session, navigations } = await fixture(t, async url => {
    assert.equal(url, "https://echo-ha.test/auth/token");
    throw new Error("synthetic HA unavailable");
  });
  await api.session();
  await assert.rejects(api.logout(), error => error.status === 503 && error.code === "logout_revocation_pending");
  assert.equal(store.getForRevocation(session.id).state, "revocation_pending");
  assert.equal(api.authority, null);
  const requestsBeforeRefresh = calls.length;
  await assert.rejects(api.session(), /logout_in_progress/);
  assert.equal(calls.length, requestsBeforeRefresh, "refresh must remain locally fenced");
  await assert.rejects(api.logout(), error => error.status === 401);
  assert.equal(api.logoutPending, true, "missing cookie is not proof of HA revocation");
  assert.equal(navigations.length, 0, "recovery must never authenticate automatically");
  await api.login();
  assert.equal(navigations.length, 1);
  const target = new URL(navigations[0]);
  assert.equal(target.origin, "https://echo-ha.test");
  assert.equal(target.pathname, "/auth/authorize");
  assert.equal(target.searchParams.get("client_id"), origin);
  assert.equal(api.authority, null, "starting fresh authentication does not install a principal");
  assert.equal(api.csrf, "");
  assert.equal(store.getForRevocation(session.id).state, "revocation_pending");
  await assert.rejects(api.snapshot(), /authenticated_session_required/);
});
