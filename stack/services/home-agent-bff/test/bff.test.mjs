import assert from "node:assert/strict";
import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { DatabaseSync } from "node:sqlite";
import { afterEach, test } from "node:test";
import {
  COOKIE_NAME,
  MAX_CORE_RESPONSE_BYTES,
  OAUTH_COOKIE_NAME,
  SessionStore,
  configFromEnv,
  createBff,
  isAllowedOrigin,
  nativeRouteAllowed,
  parseCookies,
  randomToken,
  routeAllowed,
  safePostLoginRedirect,
  sessionCookie,
} from "../src/bff.mjs";

const servers = [];
afterEach(async () => {
  await Promise.all(servers.splice(0).map((server) => new Promise((resolve) => server.close(resolve))));
});

function configured(overrides = {}) {
  return {
    bindHost: "127.0.0.1",
    port: 0,
    allowedOrigins: new Set(["https://home.test"]),
    haUrl: "https://ha.test",
    clientId: "https://home.test",
    redirectUri: "https://home.test/api/agent/auth/callback",
    postLoginRedirect: "/",
    coreUrl: "http://core.internal:8096",
    coreToken: "internal-secret",
    sessionEncryptionKey: crypto.randomBytes(32),
    sessionDbPath: "",
    allowInMemorySessions: true,
    secureCookie: false,
    idleTtlMs: 30_000,
    absoluteTtlMs: 60_000,
    principalRevalidateMs: 5 * 60_000,
    sessionCleanupIntervalMs: 60_000,
    sessionCleanupBatchSize: 10,
    ready: true,
    ...overrides,
  };
}

async function listen(server) {
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  servers.push(server);
  return `http://127.0.0.1:${server.address().port}`;
}

test("configuration is fail closed", () => {
  const config = configFromEnv({});
  assert.equal(config.ready, false);
});

test("endpoint configuration accepts only exact secure OAuth and fixed service roots", () => {
  const base = {
    NODE_ENV: "test",
    HOME_AGENT_ALLOW_TEST_SESSION_KEY_ENV: "1",
    HOME_AGENT_ALLOW_IN_MEMORY_SESSIONS: "1",
    HOME_AGENT_SESSION_ENCRYPTION_KEY: crypto.randomBytes(32).toString("base64url"),
    HOME_AGENT_ALLOWED_ORIGINS: "https://agent.home.test",
    HOME_AGENT_HA_URL: "https://ha.test/",
    HOME_AGENT_OAUTH_CLIENT_ID: "https://agent.home.test",
    HOME_AGENT_OAUTH_REDIRECT_URI: "https://agent.home.test/api/agent/auth/callback",
    HOME_AGENT_CORE_URL: "http://core.internal:8096/",
    HOME_AGENT_CORE_TOKEN: "internal-secret",
  };
  const valid = configFromEnv(base);
  assert.equal(valid.ready, true);
  assert.equal(valid.haUrl, "https://ha.test");
  assert.equal(valid.coreUrl, "http://core.internal:8096");
  assert.equal(configFromEnv({ ...base, HOME_AGENT_HA_URL: "http://127.0.0.1:8123" }).ready, true);
  assert.equal(configFromEnv({ ...base, HOME_AGENT_HA_URL: "http://[::1]:8123" }).ready, true);

  for (const override of [
    { HOME_AGENT_ALLOWED_ORIGINS: "http://agent.home.test" },
    { HOME_AGENT_ALLOWED_ORIGINS: "https://agent.home.test/path" },
    { HOME_AGENT_ALLOWED_ORIGINS: "https://user:pass@agent.home.test" },
    { HOME_AGENT_ALLOWED_ORIGINS: "https://agent.home.test#fragment" },
    { HOME_AGENT_OAUTH_CLIENT_ID: "https://legacy.home.test" },
    { HOME_AGENT_OAUTH_CLIENT_ID: "https://agent.home.test/client" },
    { HOME_AGENT_OAUTH_REDIRECT_URI: "https://legacy.home.test/api/agent/auth/callback" },
    { HOME_AGENT_OAUTH_REDIRECT_URI: "https://user:pass@agent.home.test/api/agent/auth/callback" },
    { HOME_AGENT_OAUTH_REDIRECT_URI: "https://agent.home.test/api/agent/auth/callback?next=evil" },
    { HOME_AGENT_OAUTH_REDIRECT_URI: "https://agent.home.test/api/agent/auth/callback#fragment" },
    { HOME_AGENT_OAUTH_REDIRECT_URI: "https://agent.home.test/other" },
    { HOME_AGENT_HA_URL: "http://ha.internal:8123" },
    { HOME_AGENT_HA_URL: "https://ha.test/prefix" },
    { HOME_AGENT_HA_URL: "https://ha.test?query=1" },
    { HOME_AGENT_HA_URL: "https://ha.test#fragment" },
    { HOME_AGENT_HA_URL: "https://user:pass@ha.test" },
    { HOME_AGENT_CORE_URL: "http://core.internal:8096/prefix" },
    { HOME_AGENT_CORE_URL: "http://core.internal:8096?query=1" },
    { HOME_AGENT_CORE_URL: "http://core.internal:8096#fragment" },
    { HOME_AGENT_CORE_URL: "http://user:pass@core.internal:8096" },
    { HOME_AGENT_CORE_URL: "ftp://core.internal" },
  ]) assert.equal(configFromEnv({ ...base, ...override }).ready, false, JSON.stringify(override));

  assert.equal(configFromEnv({
    ...base,
    HOME_AGENT_ALLOWED_ORIGINS: "http://agent.home.test",
    HOME_AGENT_HA_URL: "http://ha.internal:8123",
    HOME_AGENT_OAUTH_CLIENT_ID: "http://agent.home.test",
    HOME_AGENT_OAUTH_REDIRECT_URI: "http://agent.home.test/api/agent/auth/callback",
    HOME_AGENT_ALLOW_INSECURE_TEST_URLS: "1",
  }).ready, true, "insecure URL escape is test-only and explicit");
  assert.equal(configFromEnv({
    ...base,
    NODE_ENV: "production",
    HOME_AGENT_ALLOWED_ORIGINS: "http://agent.home.test",
    HOME_AGENT_HA_URL: "http://ha.internal:8123",
    HOME_AGENT_OAUTH_CLIENT_ID: "http://agent.home.test",
    HOME_AGENT_OAUTH_REDIRECT_URI: "http://agent.home.test/api/agent/auth/callback",
    HOME_AGENT_ALLOW_INSECURE_TEST_URLS: "1",
  }).ready, false);

  for (const override of [
    { haUrl: "https://ha.test/prefix" },
    { coreUrl: "http://core.internal:8096/prefix" },
    { clientId: "https://legacy.home.test" },
    { redirectUri: "https://home.test/api/agent/auth/callback?next=evil" },
  ]) assert.throws(
    () => createBff(configured(override)),
    /unsafe endpoint configuration/i,
  );
});

test("post-login redirects are restricted to a query-free local absolute path", () => {
  assert.equal(safePostLoginRedirect("/home-agent/index.html"), "/home-agent/index.html");
  for (const value of [
    "https://attacker.test/", "//attacker.test/", "/\\attacker.test/",
    "/home-agent/index.html?next=https://attacker.test", "/home-agent#external",
    "javascript:alert(1)", "/safe\r\nLocation: https://attacker.test",
  ]) assert.equal(safePostLoginRedirect(value), null, value);

  const baseEnv = {
    NODE_ENV: "test",
    HOME_AGENT_ALLOW_TEST_SESSION_KEY_ENV: "1",
    HOME_AGENT_ALLOW_IN_MEMORY_SESSIONS: "1",
    HOME_AGENT_SESSION_ENCRYPTION_KEY: crypto.randomBytes(32).toString("base64url"),
    HOME_AGENT_ALLOWED_ORIGINS: "https://home.test",
    HOME_AGENT_HA_URL: "https://ha.test",
    HOME_AGENT_OAUTH_CLIENT_ID: "https://home.test",
    HOME_AGENT_OAUTH_REDIRECT_URI: "https://home.test/api/agent/auth/callback",
    HOME_AGENT_CORE_URL: "http://core.internal:8096",
    HOME_AGENT_CORE_TOKEN: "internal-secret",
  };
  assert.equal(configFromEnv(baseEnv).ready, true);
  assert.equal(configFromEnv({
    ...baseEnv,
    HOME_AGENT_POST_LOGIN_REDIRECT: "//attacker.test/",
  }).ready, false);
  assert.throws(
    () => createBff(configured({ postLoginRedirect: "https://attacker.test/" })),
    /unsafe post-login redirect/i,
  );
});

test("otherwise complete configuration fails closed without a session encryption key", () => {
  const env = {
    HOME_AGENT_ALLOWED_ORIGINS: "https://home.test",
    HOME_AGENT_HA_URL: "https://ha.test",
    HOME_AGENT_OAUTH_CLIENT_ID: "https://home.test",
    HOME_AGENT_OAUTH_REDIRECT_URI: "https://home.test/api/agent/auth/callback",
    HOME_AGENT_CORE_URL: "http://core.internal:8096",
    HOME_AGENT_CORE_TOKEN: "internal-secret",
    HOME_AGENT_SESSION_DB_PATH: "/tmp/home-agent-test/sessions.sqlite",
  };
  assert.equal(configFromEnv(env).ready, false);
  assert.equal(configFromEnv({
    ...env,
    HOME_AGENT_SESSION_ENCRYPTION_KEY: crypto.randomBytes(32).toString("base64url"),
  }).ready, false, "direct production environment secrets must be ignored");
  assert.equal(configFromEnv({
    ...env,
    HOME_AGENT_SESSION_ENCRYPTION_KEY_FILE: "C:/definitely/missing/session-key",
  }).ready, false);
  assert.throws(
    () => createBff(configured({ sessionEncryptionKey: null })),
    /cannot start ready without sealed persistent sessions/i,
  );
  assert.throws(
    () => createBff(configured({ allowInMemorySessions: false, sessionDbPath: "" })),
    /cannot start ready without sealed persistent sessions/i,
  );
});

test("production session encryption key is loaded only from a valid secret file", () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "home-agent-bff-"));
  const keyPath = path.join(directory, "session_encryption_key");
  try {
    const key = crypto.randomBytes(32);
    fs.writeFileSync(keyPath, `${key.toString("base64url")}\n`, { mode: 0o600 });
    const config = configFromEnv({
      HOME_AGENT_ALLOWED_ORIGINS: "https://home.test",
      HOME_AGENT_HA_URL: "https://ha.test",
      HOME_AGENT_OAUTH_CLIENT_ID: "https://home.test",
      HOME_AGENT_OAUTH_REDIRECT_URI: "https://home.test/api/agent/auth/callback",
      HOME_AGENT_CORE_URL: "http://core.internal:8096",
      HOME_AGENT_CORE_TOKEN: "internal-secret",
      HOME_AGENT_SESSION_ENCRYPTION_KEY_FILE: keyPath,
      HOME_AGENT_SESSION_DB_PATH: path.join(directory, "sessions.sqlite"),
    });
    assert.equal(config.ready, true);
    assert.deepEqual(config.sessionEncryptionKey, key);

    fs.writeFileSync(keyPath, "too-short\n", { mode: 0o600 });
    assert.equal(configFromEnv({
      HOME_AGENT_ALLOWED_ORIGINS: "https://home.test",
      HOME_AGENT_HA_URL: "https://ha.test",
      HOME_AGENT_OAUTH_CLIENT_ID: "https://home.test",
      HOME_AGENT_OAUTH_REDIRECT_URI: "https://home.test/api/agent/auth/callback",
      HOME_AGENT_CORE_URL: "http://core.internal:8096",
      HOME_AGENT_CORE_TOKEN: "internal-secret",
      HOME_AGENT_SESSION_ENCRYPTION_KEY_FILE: keyPath,
      HOME_AGENT_SESSION_DB_PATH: path.join(directory, "sessions.sqlite"),
    }).ready, false);
  } finally {
    fs.rmSync(directory, { recursive: true, force: true });
  }
});

test("tokens and cookies are random and strict", () => {
  assert.notEqual(randomToken(), randomToken());
  const cookie = sessionCookie("id", true);
  assert.match(cookie, new RegExp(`^${COOKIE_NAME}=`));
  assert.match(cookie, /HttpOnly/);
  assert.match(cookie, /SameSite=Strict/);
  assert.match(cookie, /Secure/);
});

test("origins require an exact configured origin", () => {
  const origins = new Set(["https://home.test"]);
  assert.equal(isAllowedOrigin("https://home.test", origins), true);
  assert.equal(isAllowedOrigin("https://home.test.evil", origins), false);
  assert.equal(isAllowedOrigin("https://home.test/", origins), false);
  assert.equal(isAllowedOrigin("", origins), false);
});

test("malformed cookies are ignored instead of crashing request handling", () => {
  assert.deepEqual(parseCookies(`${COOKIE_NAME}=%`), {});
});

test("semantic route allowlist excludes generic proxying", () => {
  const factId = "01900000-0000-7000-8000-000000000001";
  const transactionId = "01900000-0000-7000-8000-000000000002";

  assert.equal(routeAllowed("GET", "/api/agent/v1/snapshot"), true);
  assert.equal(routeAllowed("GET", "/api/agent/v1/initiatives"), false);
  assert.equal(routeAllowed("POST", "/api/agent/v1/memory-transactions"), true);
  assert.equal(routeAllowed("POST", "/api/agent/v1/places"), false);
  assert.equal(routeAllowed("POST", "/api/agent/v1/operator-rollout/authorizations/shadow"), false);
  assert.equal(routeAllowed("POST", "/api/agent/v1/memory-transactions/01900000-0000-7000-8000-000000000000/confirm"), true);
  assert.equal(routeAllowed("POST", `/api/agent/v1/facts/${factId}/correction-preview`), true);
  assert.equal(routeAllowed("POST", `/api/agent/v1/descriptor-corrections/${transactionId}/confirm`), true);
  assert.equal(routeAllowed("POST", `/api/agent/v1/facts/${factId}/retraction-preview`), true);
  assert.equal(routeAllowed("POST", `/api/agent/v1/descriptor-retractions/${transactionId}/confirm`), true);
  assert.equal(routeAllowed("POST", "/api/agent/v1/erasure-requests/01900000-0000-7000-8000-000000000000/confirm"), true);
  assert.equal(routeAllowed("GET", `/api/agent/v1/facts/${factId}/correction-preview`), false);
  assert.equal(routeAllowed("POST", "/api/agent/v1/facts/not-a-uuid/correction-preview"), false);
  assert.equal(routeAllowed("POST", `/api/agent/v1/facts/${factId}/correct`), false);
  assert.equal(routeAllowed("POST", `/api/agent/v1/descriptor-corrections/${transactionId}`), false);
  assert.equal(routeAllowed("POST", `/api/agent/v1/facts/${factId}/retract`), false);
  assert.equal(routeAllowed("POST", `/api/agent/v1/descriptor-retractions/${transactionId}`), false);
  assert.equal(routeAllowed("POST", "/api/agent/v1/initiatives/01900000-0000-7000-8000-000000000000/claim"), false);
  assert.equal(routeAllowed("POST", "/api/agent/v1/initiatives/01900000-0000-7000-8000-000000000000/dismiss"), false);
  assert.equal(routeAllowed("DELETE", "/api/agent/v1/memory-transactions/x"), false);
  assert.equal(routeAllowed("GET", "/api/frigate/events"), false);
  assert.equal(routeAllowed("POST", "/api/stack/stop"), false);
  assert.equal(nativeRouteAllowed("GET", "/api/agent/native/v1/snapshot"), true);
  assert.equal(nativeRouteAllowed("GET", "/api/agent/native/v1/initiatives"), true);
  assert.equal(nativeRouteAllowed("POST", `/api/agent/native/v1/initiatives/${factId}/claim`), true);
  assert.equal(nativeRouteAllowed("GET", `/api/agent/native/v1/places/${factId}/descriptor-relationship`), true);
  assert.equal(nativeRouteAllowed("GET", `/api/agent/native/v1/places/${factId}/parents/current-presence`), true);
  assert.equal(nativeRouteAllowed("POST", "/api/agent/native/v1/memory-transactions"), true);
  assert.equal(nativeRouteAllowed("POST", "/api/agent/native/v1/places"), false);
  assert.equal(nativeRouteAllowed("POST", "/api/agent/native/v1/relationships/parent-confirmations"), false);
  assert.equal(nativeRouteAllowed("POST", "/api/agent/native/v1/operator-rollout/authorizations/shadow"), false);
  assert.equal(nativeRouteAllowed("POST", "/api/agent/native/v1/forget-preview"), false);
  assert.equal(nativeRouteAllowed("GET", "/api/agent/native/v1/erasure-requests/01900000-0000-7000-8000-000000000000"), false);
  assert.equal(nativeRouteAllowed("GET", "/api/agent/native/v1/memory-transactions/------------------------------------"), false);
});

test("Core responses are byte-bounded before BFF Buffer copies", async () => {
  let coreCall = 0;
  let streamCancelled = false;
  const fetchImpl = async (url, init) => {
    assert.equal(init.redirect, "error");
    if (String(url).includes("/api/home_agent_edge/whoami")) {
      return new Response(JSON.stringify({
        user_id: "native-user", is_admin: false, is_active: true,
      }), { status: 200, headers: { "content-type": "application/json" } });
    }
    coreCall += 1;
    if (coreCall === 1) {
      return new Response("not-read", {
        status: 200,
        headers: { "content-length": String(MAX_CORE_RESPONSE_BYTES + 1) },
      });
    }
    const stream = new ReadableStream({
      start(controller) {
        controller.enqueue(new Uint8Array(MAX_CORE_RESPONSE_BYTES));
        controller.enqueue(new Uint8Array(1));
      },
      cancel() { streamCancelled = true; },
    });
    return new Response(stream, { status: 200 });
  };
  const base = await listen(createBff(configured(), { fetchImpl }));
  for (let attempt = 0; attempt < 2; attempt += 1) {
    const response = await fetch(`${base}/api/agent/native/v1/snapshot`, {
      headers: { Authorization: "Bearer native-access-token-test" },
    });
    assert.equal(response.status, 502);
    assert.equal((await response.json()).error, "agent_upstream_response_too_large");
  }
  assert.equal(coreCall, 2);
  assert.equal(streamCancelled, true);
});

test("session expiry is enforced", () => {
  let now = 1_000;
  const store = new SessionStore({
    idleTtlMs: 100,
    absoluteTtlMs: 1_000,
    sessionEncryptionKey: crypto.randomBytes(32),
    allowInMemorySessions: true,
    now: () => now,
  });
  const created = store.createSession({ principal: { userId: "u" }, accessToken: "a", refreshToken: "r" });
  assert.ok(store.get(created.id));
  now += 101;
  assert.equal(store.get(created.id), null);
});

test("expiry cleanup is bounded, revokes at HA, and never serves expired sessions", async () => {
  let now = 1_000;
  const config = configured({ sessionCleanupBatchSize: 2 });
  const store = new SessionStore({
    ...config,
    idleTtlMs: 10_000,
    absoluteTtlMs: 100,
    now: () => now,
  });
  const created = [];
  for (let index = 0; index < 3; index += 1) {
    created.push(store.createSession({
      principal: { userId: `expired-${index}` },
      accessToken: `expired-access-${index}`,
      refreshToken: `expired-refresh-${index}`,
      expiresIn: 3600,
    }));
  }
  now += 101;
  assert.equal(store.get(created[2].id), null);
  let revokeCalls = 0;
  const result = await store.cleanupExpired(config, async (url, init) => {
    revokeCalls += 1;
    assert.equal(String(url), "https://ha.test/auth/token");
    assert.equal(init.body.get("action"), "revoke");
    return new Response(null, { status: 200 });
  }, { now, batchSize: 2 });
  assert.deepEqual(result, { attempted: 2, completed: 2 });
  assert.equal(revokeCalls, 2);
  assert.equal(store.sessions.size, 1, "one record remains for the next bounded cycle");
  assert.equal(store.get([...store.sessions.keys()][0]), null);
});

test("session Map contains only AES-GCM sealed HA tokens", () => {
  const accessToken = "ha-access-plaintext-canary";
  const refreshToken = "ha-refresh-plaintext-canary";
  const store = new SessionStore({
    idleTtlMs: 30_000,
    absoluteTtlMs: 60_000,
    sessionEncryptionKey: crypto.randomBytes(32),
    allowInMemorySessions: true,
  });
  const created = store.createSession({
    principal: { userId: "u" }, accessToken, refreshToken, expiresIn: 3600,
  });
  const stored = store.sessions.get(created.id);
  assert.equal(stored.accessToken, undefined);
  assert.equal(stored.refreshToken, undefined);
  assert.equal(stored.sealedTokens.version, 1);
  assert.equal(stored.sealedTokens.iv.length, 12);
  assert.equal(stored.sealedTokens.tag.length, 16);
  const inspected = JSON.stringify(stored);
  assert.equal(inspected.includes(accessToken), false);
  assert.equal(inspected.includes(refreshToken), false);
});

test("sealed token envelopes reject ciphertext tampering and cross-session swaps", async () => {
  const store = new SessionStore({
    idleTtlMs: 30_000,
    absoluteTtlMs: 60_000,
    sessionEncryptionKey: crypto.randomBytes(32),
    allowInMemorySessions: true,
  });
  const first = store.createSession({
    principal: { userId: "first" }, accessToken: "first-access", refreshToken: "first-refresh",
  });
  const second = store.createSession({
    principal: { userId: "second" }, accessToken: "second-access", refreshToken: "second-refresh",
  });
  const firstRecord = store.sessions.get(first.id);
  const secondRecord = store.sessions.get(second.id);
  const originalFirstEnvelope = firstRecord.sealedTokens;
  const config = configured({ principalRevalidateMs: 0 });
  const unexpectedFetch = async () => {
    throw new Error("tampered token material must fail before an HA call");
  };

  firstRecord.sealedTokens = secondRecord.sealedTokens;
  await assert.rejects(
    store.revalidate(config, first.id, firstRecord, unexpectedFetch),
    /authenticate data|unable to authenticate/i,
  );
  firstRecord.sealedTokens = originalFirstEnvelope;
  firstRecord.sealedTokens.ciphertext[0] ^= 1;
  await assert.rejects(
    store.revalidate(config, first.id, firstRecord, unexpectedFetch),
    /authenticate data|unable to authenticate/i,
  );
});

test("sealed sessions survive restart while token canaries and preauth state do not", async () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "home-agent-bff-restart-"));
  const dbPath = path.join(directory, "sessions.sqlite");
  const key = crypto.randomBytes(32);
  const config = configured({
    sessionEncryptionKey: key,
    sessionDbPath: dbPath,
    allowInMemorySessions: false,
    principalRevalidateMs: 0,
  });
  const accessCanary = "restart-access-plaintext-canary";
  const refreshCanary = "restart-refresh-plaintext-canary";
  let store;
  try {
    store = new SessionStore(config);
    const session = store.createSession({
      principal: { userId: "restart-user", isAdmin: false, isActive: true },
      accessToken: accessCanary,
      refreshToken: refreshCanary,
      expiresIn: 3600,
    });
    const preauth = store.createPreauth();
    store.close();
    store = null;

    for (const filename of fs.readdirSync(directory).filter((name) => name.startsWith("sessions.sqlite"))) {
      const bytes = fs.readFileSync(path.join(directory, filename));
      assert.equal(bytes.includes(Buffer.from(accessCanary)), false);
      assert.equal(bytes.includes(Buffer.from(refreshCanary)), false);
    }

    store = new SessionStore(config);
    const reloaded = store.get(session.id);
    assert.ok(reloaded);
    assert.equal(reloaded.csrf, session.csrf);
    assert.equal(
      store.consumePreauth(preauth.state, preauth.browserNonce),
      null,
      "in-flight OAuth state is deliberately invalidated by restart",
    );
    await store.revalidate(config, session.id, reloaded, async (url, init) => {
      assert.equal(String(url), "https://ha.test/api/home_agent_edge/whoami");
      assert.equal(init.headers.get("authorization"), `Bearer ${accessCanary}`);
      return new Response(JSON.stringify({
        user_id: "restart-user", is_admin: false, is_active: true,
      }), { status: 200, headers: { "content-type": "application/json" } });
    });
  } finally {
    store?.close();
    fs.rmSync(directory, { recursive: true, force: true });
  }
});

test("revocation-pending sessions survive a crash boundary and retry after restart", async () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "home-agent-bff-revoke-"));
  const dbPath = path.join(directory, "sessions.sqlite");
  const config = configured({
    sessionEncryptionKey: crypto.randomBytes(32),
    sessionDbPath: dbPath,
    allowInMemorySessions: false,
  });
  const refreshCanary = "crash-retry-refresh-plaintext-canary";
  let store;
  try {
    store = new SessionStore(config);
    const session = store.createSession({
      principal: { userId: "crash-user", isAdmin: false, isActive: true },
      accessToken: "crash-retry-access-plaintext-canary",
      refreshToken: refreshCanary,
      expiresIn: 3600,
    });
    assert.equal(await store.revoke(config, session.id, store.sessions.get(session.id), async () => {
      throw new Error("simulated crash-boundary outage");
    }), false);
    const retryAt = store.sessions.get(session.id).nextRevocationAt;
    store.close();
    store = null;

    store = new SessionStore(config);
    assert.equal(store.get(session.id), null, "pending records never regain authority after restart");
    const result = await store.cleanupExpired(config, async (url, init) => {
      assert.equal(String(url), "https://ha.test/auth/token");
      assert.equal(init.body.get("action"), "revoke");
      assert.equal(init.body.get("token"), refreshCanary);
      return new Response(null, { status: 200 });
    }, { now: retryAt, batchSize: 1 });
    assert.deepEqual(result, { attempted: 1, completed: 1 });
    assert.equal(store.sessions.has(session.id), false);
    store.close();
    store = null;

    store = new SessionStore(config);
    assert.equal(store.sessions.size, 0, "authority-confirmed deletion is durable");
  } finally {
    store?.close();
    fs.rmSync(directory, { recursive: true, force: true });
  }
});

test("persisted ciphertext tampering fails closed after restart without deleting the retry record", async () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "home-agent-bff-tamper-"));
  const dbPath = path.join(directory, "sessions.sqlite");
  const config = configured({
    sessionEncryptionKey: crypto.randomBytes(32),
    sessionDbPath: dbPath,
    allowInMemorySessions: false,
    principalRevalidateMs: 0,
  });
  let store;
  try {
    store = new SessionStore(config);
    const session = store.createSession({
      principal: { userId: "tamper-user", isAdmin: false, isActive: true },
      accessToken: "tamper-access-plaintext-canary",
      refreshToken: "tamper-refresh-plaintext-canary",
      expiresIn: 3600,
    });
    store.close();
    store = null;

    const db = new DatabaseSync(dbPath);
    const row = db.prepare("SELECT ciphertext FROM bff_session WHERE id = ?").get(session.id);
    const ciphertext = Buffer.from(row.ciphertext);
    ciphertext[0] ^= 1;
    db.prepare("UPDATE bff_session SET ciphertext = ? WHERE id = ?").run(ciphertext, session.id);
    db.close();

    store = new SessionStore(config);
    const tamperServer = createBff(config, {
      store,
      fetchImpl: async () => {
        throw new Error("tampered tokens must fail before network access");
      },
    });
    await new Promise((resolve) => tamperServer.listen(0, "127.0.0.1", resolve));
    try {
      const response = await fetch(
        `http://127.0.0.1:${tamperServer.address().port}/api/agent/v1/snapshot`,
        { headers: { cookie: `${COOKIE_NAME}=${session.id}` } },
      );
      assert.equal(response.status, 401);
      const body = await response.json();
      assert.deepEqual(body, { error: "authentication_revoked" });
      assert.equal(JSON.stringify(body).includes("ciphertext"), false);
      assert.equal(JSON.stringify(body).includes("tamper-"), false);
    } finally {
      await new Promise((resolve) => tamperServer.close(resolve));
    }
    assert.equal(store.sessions.get(session.id).state, "revocation_pending");
    assert.equal(store.sessions.has(session.id), true, "unrevoked authority token is not forgotten");
  } finally {
    store?.close();
    fs.rmSync(directory, { recursive: true, force: true });
  }
});

test("unconfigured server exposes health but denies private routes", async () => {
  const config = configured({ ready: false });
  const base = await listen(createBff(config));
  const health = await fetch(`${base}/healthz`);
  assert.equal(health.status, 503);
  const denied = await fetch(`${base}/api/agent/v1/snapshot`);
  assert.equal(denied.status, 503);
  assert.equal((await denied.json()).error, "bff_unconfigured");
});

test("OAuth start is origin-checked and binds state to an HttpOnly browser nonce without fake PKCE", async () => {
  const config = configured();
  const base = await listen(createBff(config));
  const denied = await fetch(`${base}/api/agent/auth/start`, { method: "POST" });
  assert.equal(denied.status, 403);

  const started = await fetch(`${base}/api/agent/auth/start`, {
    method: "POST",
    headers: { origin: "https://home.test" },
  });
  assert.equal(started.status, 200);
  const payload = await started.json();
  const authorize = new URL(payload.authorize_url);
  assert.equal(authorize.pathname, "/auth/authorize");
  assert.equal(authorize.searchParams.get("redirect_uri"), config.redirectUri);
  assert.ok(authorize.searchParams.get("state"));
  assert.equal(authorize.searchParams.has("code_challenge"), false);
  assert.equal(authorize.searchParams.has("code_challenge_method"), false);
  assert.match(started.headers.get("set-cookie") || "", new RegExp(`${OAUTH_COOKIE_NAME}=`));
  assert.match(started.headers.get("set-cookie") || "", /HttpOnly/);
  assert.match(started.headers.get("set-cookie") || "", /SameSite=Lax/);
});

test("OAuth callback requires the initiating browser nonce and creates a bound session", async () => {
  const config = configured();
  let exchangeCalls = 0;
  const fetchImpl = async (url, init) => {
    assert.equal(init.redirect, "error");
    if (String(url).endsWith("/auth/token")) {
      exchangeCalls += 1;
      assert.equal(init.body.get("grant_type"), "authorization_code");
      assert.equal(init.body.get("client_id"), config.clientId);
      assert.equal(init.body.get("redirect_uri"), config.redirectUri);
      assert.equal(init.body.get("code"), "code");
      assert.equal(init.body.has("code_verifier"), false);
      return new Response(JSON.stringify({
        access_token: "ha-access", refresh_token: "ha-refresh", expires_in: 1800,
      }), { status: 200, headers: { "content-type": "application/json" } });
    }
    if (String(url).endsWith("/api/home_agent_edge/whoami")) {
      return new Response(JSON.stringify({
        user_id: "ha-user", is_admin: false, is_active: true,
      }), { status: 200, headers: { "content-type": "application/json" } });
    }
    throw new Error(`unexpected upstream ${url}`);
  };
  const base = await listen(createBff(config, { fetchImpl }));
  const started = await fetch(`${base}/api/agent/auth/start`, {
    method: "POST", headers: { origin: "https://home.test" },
  });
  const startBody = await started.json();
  const state = new URL(startBody.authorize_url).searchParams.get("state");

  const swapped = await fetch(
    `${base}/api/agent/auth/callback?state=${encodeURIComponent(state)}&code=code`,
    { redirect: "manual" },
  );
  assert.equal(swapped.status, 400);

  const restarted = await fetch(`${base}/api/agent/auth/start`, {
    method: "POST", headers: { origin: "https://home.test" },
  });
  const restartBody = await restarted.json();
  const restartState = new URL(restartBody.authorize_url).searchParams.get("state");
  const nonceCookie = (restarted.headers.get("set-cookie") || "").split(";")[0];
  const callback = await fetch(
    `${base}/api/agent/auth/callback?state=${encodeURIComponent(restartState)}&code=code`,
    { headers: { cookie: nonceCookie }, redirect: "manual" },
  );
  assert.equal(callback.status, 302);
  assert.equal(callback.headers.get("location"), "/");
  const cookies = callback.headers.get("set-cookie") || "";
  assert.match(cookies, new RegExp(`${COOKIE_NAME}=`));
  assert.match(cookies, new RegExp(`${OAUTH_COOKIE_NAME}=;`));
  assert.equal(callback.headers.get("referrer-policy"), "no-referrer");
  assert.equal(exchangeCalls, 1);

  const replay = await fetch(
    `${base}/api/agent/auth/callback?state=${encodeURIComponent(restartState)}&code=code`,
    { headers: { cookie: nonceCookie }, redirect: "manual" },
  );
  assert.equal(replay.status, 400);
  assert.equal((await replay.json()).error, "oauth_state_invalid");
  assert.equal(exchangeCalls, 1, "a consumed callback must never exchange twice");
});

test("OAuth callback rejects duplicate or extra parameters before token exchange", async () => {
  const config = configured();
  let upstreamCalls = 0;
  const base = await listen(createBff(config, {
    fetchImpl: async () => {
      upstreamCalls += 1;
      throw new Error("malformed callback must not reach HA");
    },
  }));
  const started = await fetch(`${base}/api/agent/auth/start`, {
    method: "POST", headers: { origin: "https://home.test" },
  });
  const startBody = await started.json();
  const state = new URL(startBody.authorize_url).searchParams.get("state");
  const nonceCookie = (started.headers.get("set-cookie") || "").split(";")[0];

  for (const query of [
    `state=${encodeURIComponent(state)}&code=one&code=two`,
    `state=${encodeURIComponent(state)}&code=one&iss=https%3A%2F%2Fha.test`,
    `state=${encodeURIComponent(state)}&code=one%20two`,
    `state=${encodeURIComponent(state)}&code=`,
  ]) {
    const response = await fetch(`${base}/api/agent/auth/callback?${query}`, {
      headers: { cookie: nonceCookie }, redirect: "manual",
    });
    assert.equal(response.status, 400);
    assert.equal((await response.json()).error, "oauth_callback_invalid");
  }
  assert.equal(upstreamCalls, 0);
});

test("logout revokes the HA refresh token before deleting the sealed session", async () => {
  const config = configured();
  const store = new SessionStore(config);
  const refreshCanary = "logout-refresh-plaintext-canary";
  const session = store.createSession({
    principal: { userId: "ha-user", isAdmin: false, isActive: true },
    accessToken: "logout-access-plaintext-canary",
    refreshToken: refreshCanary,
    expiresIn: 3600,
  });
  let observedBody;
  const fetchImpl = async (url, init) => {
    assert.equal(String(url), "https://ha.test/auth/token");
    assert.equal(init.method, "POST");
    assert.equal(init.redirect, "error");
    assert.equal(init.body.get("action"), "revoke");
    assert.equal(init.body.get("token"), refreshCanary);
    assert.equal(store.sessions.has(session.id), true, "authority revocation must happen first");
    assert.equal(store.sessions.get(session.id).state, "revocation_pending");
    observedBody = init.body;
    return new Response(null, { status: 200 });
  };
  const base = await listen(createBff(config, { fetchImpl, store }));
  const response = await fetch(`${base}/api/agent/auth/logout`, {
    method: "POST",
    headers: {
      cookie: `${COOKIE_NAME}=${session.id}`,
      origin: "https://home.test",
      "x-csrf-token": session.csrf,
    },
  });
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), { ok: true });
  assert.equal(store.sessions.has(session.id), false);
  assert.equal(observedBody.get("token"), null, "mutable form token is cleared after fetch");
  assert.match(response.headers.get("set-cookie") || "", /Max-Age=0/);
});

test("logout network failure keeps only a sealed retry record and returns a contained error", async () => {
  const config = configured();
  const store = new SessionStore(config);
  const accessCanary = "failed-logout-access-plaintext-canary";
  const refreshCanary = "failed-logout-refresh-plaintext-canary";
  const session = store.createSession({
    principal: { userId: "ha-user", isAdmin: false, isActive: true },
    accessToken: accessCanary,
    refreshToken: refreshCanary,
    expiresIn: 3600,
  });
  let calls = 0;
  const unavailable = async () => {
    calls += 1;
    throw new Error("simulated HA network failure");
  };
  const base = await listen(createBff(config, { fetchImpl: unavailable, store }));
  const response = await fetch(`${base}/api/agent/auth/logout`, {
    method: "POST",
    headers: {
      cookie: `${COOKIE_NAME}=${session.id}`,
      origin: "https://home.test",
      "x-csrf-token": session.csrf,
    },
  });
  assert.equal(response.status, 503);
  const payload = await response.json();
  assert.deepEqual(payload, { error: "logout_revocation_pending", retryable: true });
  assert.equal(JSON.stringify(payload).includes(refreshCanary), false);
  assert.match(response.headers.get("set-cookie") || "", /Max-Age=0/);
  assert.equal(calls, 1);
  const pending = store.sessions.get(session.id);
  assert.equal(pending.state, "revocation_pending");
  assert.equal(pending.revocationAttempts, 1);
  const inspected = JSON.stringify(pending);
  assert.equal(inspected.includes(accessCanary), false);
  assert.equal(inspected.includes(refreshCanary), false);

  const denied = await fetch(`${base}/api/agent/v1/snapshot`, {
    headers: { cookie: `${COOKIE_NAME}=${session.id}` },
  });
  assert.equal(denied.status, 401, "logout-pending sessions are never served");

  const retried = await store.cleanupExpired(
    config,
    async (url, init) => {
      assert.equal(String(url), "https://ha.test/auth/token");
      assert.equal(init.body.get("token"), refreshCanary);
      return new Response(null, { status: 200 });
    },
    { now: pending.nextRevocationAt, batchSize: 1 },
  );
  assert.deepEqual(retried, { attempted: 1, completed: 1 });
  assert.equal(store.sessions.has(session.id), false);
});

test("state changes require both exact origin and csrf", async () => {
  const config = configured();
  const store = new SessionStore(config);
  const session = store.createSession({
    principal: { userId: "ha-user", isAdmin: false },
    accessToken: "ha-token",
    refreshToken: "refresh",
  });
  const fetchImpl = async () => new Response(JSON.stringify({ ok: true }), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
  const base = await listen(createBff(config, { fetchImpl, store }));
  const cookie = `${COOKIE_NAME}=${session.id}`;

  const noOrigin = await fetch(`${base}/api/agent/v1/memory-transactions`, {
    method: "POST", headers: { cookie, "x-csrf-token": session.csrf }, body: "{}",
  });
  assert.equal(noOrigin.status, 403);

  const noCsrf = await fetch(`${base}/api/agent/v1/memory-transactions`, {
    method: "POST", headers: { cookie, origin: "https://home.test" }, body: "{}",
  });
  assert.equal(noCsrf.status, 403);

  const accepted = await fetch(`${base}/api/agent/v1/memory-transactions`, {
    method: "POST",
    headers: {
      cookie,
      origin: "https://home.test",
      "x-csrf-token": session.csrf,
      authorization: "Bearer attacker-controlled",
      "x-home-agent-principal": "forged",
      "content-type": "application/json",
    },
    body: "{}",
  });
  assert.equal(accepted.status, 200);
});

test("typed descriptor lifecycle routes proxy to their exact Core paths", async () => {
  const config = configured();
  const store = new SessionStore(config);
  const session = store.createSession({
    principal: { userId: "ha-user", isAdmin: false },
    accessToken: "ha-token",
    refreshToken: "refresh",
  });
  const observed = [];
  const fetchImpl = async (url, init) => {
    observed.push({ url: String(url), method: init.method });
    return new Response("{}", {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  };
  const base = await listen(createBff(config, { fetchImpl, store }));
  const factId = "01900000-0000-7000-8000-000000000001";
  const transactionId = "01900000-0000-7000-8000-000000000002";
  const paths = [
    `/v1/facts/${factId}/correction-preview`,
    `/v1/descriptor-corrections/${transactionId}/confirm`,
    `/v1/facts/${factId}/retraction-preview`,
    `/v1/descriptor-retractions/${transactionId}/confirm`,
  ];

  for (const path of paths) {
    const response = await fetch(`${base}/api/agent${path}`, {
      method: "POST",
      headers: {
        cookie: `${COOKIE_NAME}=${session.id}`,
        origin: "https://home.test",
        "x-csrf-token": session.csrf,
        "content-type": "application/json",
      },
      body: "{}",
    });
    assert.equal(response.status, 200);
  }

  assert.deepEqual(
    observed,
    paths.map((path) => ({ url: `http://core.internal:8096${path}`, method: "POST" })),
  );
});

test("BFF constructs trusted upstream headers instead of forwarding actor headers", async () => {
  const config = configured();
  const store = new SessionStore(config);
  const session = store.createSession({
    principal: { userId: "real-ha-user", isAdmin: true },
    accessToken: "ha-token",
    refreshToken: "refresh",
  });
  let observed;
  const fetchImpl = async (url, init) => {
    observed = { url, init };
    return new Response("{}", { status: 200, headers: { "content-type": "application/json" } });
  };
  const base = await listen(createBff(config, { fetchImpl, store }));
  await fetch(`${base}/api/agent/v1/snapshot`, {
    headers: {
      cookie: `${COOKIE_NAME}=${session.id}`,
      authorization: "Bearer forged",
      "x-home-agent-principal": "forged",
    },
  });
  assert.equal(observed.url, "http://core.internal:8096/v1/snapshot");
  assert.equal(observed.init.headers.Authorization, "Bearer internal-secret");
  assert.equal(observed.init.headers["X-Authenticated-HA-User"], "real-ha-user");
  assert.equal(observed.init.headers["X-Home-Agent-Principal"], undefined);
  assert.equal(observed.init.headers["X-Home-Agent-Admin"], undefined);
  assert.equal(observed.init.headers["X-Home-Agent-Channel"], undefined);
});

test("native Agent routes authenticate the HA bearer and expose only typed private queries", async () => {
  const config = configured();
  const calls = [];
  const fetchImpl = async (url, init = {}) => {
    assert.equal(init.redirect, "error");
    calls.push({ url: String(url), init });
    if (String(url).endsWith("/api/home_agent_edge/whoami")) {
      assert.equal(init.headers.get("authorization"), "Bearer native-ha-access-canary");
      return new Response(JSON.stringify({
        user_id: "native-ha-user", is_admin: false, is_active: true,
      }), { status: 200, headers: { "content-type": "application/json" } });
    }
    assert.ok([
      "http://core.internal:8096/v1/snapshot",
      "http://core.internal:8096/v1/initiatives",
    ].includes(String(url)));
    assert.equal(init.headers.Authorization, "Bearer internal-secret");
    assert.equal(init.headers["X-Authenticated-HA-User"], "native-ha-user");
    assert.equal(init.headers["X-Home-Agent-Channel"], "private_tauri");
    assert.equal(init.headers["X-Home-Agent-Principal"], undefined);
    return new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: { "content-type": "application/json" },
    });
  };
  const base = await listen(createBff(config, { fetchImpl }));
  const unauthenticated = await fetch(`${base}/api/agent/native/v1/snapshot`);
  assert.equal(unauthenticated.status, 401);
  assert.equal((await unauthenticated.json()).error, "native_authentication_required");

  for (const blockedPath of ["places", "supervisor/config"]) {
    const blocked = await fetch(`${base}/api/agent/native/v1/${blockedPath}`, {
      headers: { authorization: "Bearer native-ha-access-canary" },
    });
    assert.equal(blocked.status, 404);
  }

  const accepted = await fetch(`${base}/api/agent/native/v1/snapshot`, {
    headers: {
      authorization: "Bearer native-ha-access-canary",
      "x-home-agent-principal": "forged",
      "x-home-agent-channel": "private_web",
    },
  });
  assert.equal(accepted.status, 200);
  assert.deepEqual(await accepted.json(), { ok: true });
  const initiative = await fetch(`${base}/api/agent/native/v1/initiatives`, {
    headers: { authorization: "Bearer native-ha-access-canary" },
  });
  assert.equal(initiative.status, 200);
  assert.equal(calls.length, 4, "blocked routes must not reach HA or Core");
});

test("expired HA tokens are decrypted only for refresh and resealed in the session Map", async () => {
  const config = configured({ principalRevalidateMs: 5 * 60_000 });
  const store = new SessionStore(config);
  const session = store.createSession({
    principal: { userId: "ha-user", isAdmin: false, isActive: true },
    accessToken: "old-access-plaintext-canary",
    refreshToken: "old-refresh-plaintext-canary",
    expiresIn: 1,
  });
  let refreshCalls = 0;
  let whoamiCalls = 0;
  let coreCalls = 0;
  const fetchImpl = async (url, init = {}) => {
    assert.equal(init.redirect, "error");
    if (String(url).endsWith("/auth/token")) {
      refreshCalls += 1;
      const expectedRefresh = refreshCalls === 1
        ? "old-refresh-plaintext-canary"
        : "new-refresh-plaintext-canary";
      assert.equal(init.body.get("refresh_token"), expectedRefresh);
      return new Response(JSON.stringify({
        access_token: refreshCalls === 1
          ? "new-access-plaintext-canary"
          : "newest-access-plaintext-canary",
        refresh_token: refreshCalls === 1
          ? "new-refresh-plaintext-canary"
          : "newest-refresh-plaintext-canary",
        expires_in: 3600,
      }), { status: 200, headers: { "content-type": "application/json" } });
    }
    if (String(url).endsWith("/api/home_agent_edge/whoami")) {
      whoamiCalls += 1;
      const expectedAccess = whoamiCalls === 1
        ? "new-access-plaintext-canary"
        : "newest-access-plaintext-canary";
      assert.equal(init.headers.get("authorization"), `Bearer ${expectedAccess}`);
      return new Response(JSON.stringify({
        user_id: "ha-user", is_admin: false, is_active: true,
      }), { status: 200, headers: { "content-type": "application/json" } });
    }
    coreCalls += 1;
    return new Response("{}", { status: 200, headers: { "content-type": "application/json" } });
  };
  const base = await listen(createBff(config, { fetchImpl, store }));
  const response = await fetch(`${base}/api/agent/v1/snapshot`, {
    headers: { cookie: `${COOKIE_NAME}=${session.id}` },
  });
  assert.equal(response.status, 200);
  assert.equal(refreshCalls, 1);
  assert.equal(whoamiCalls, 1);
  assert.equal(coreCalls, 1);

  const stored = store.sessions.get(session.id);
  const inspected = JSON.stringify(stored);
  for (const plaintext of ["old-access", "old-refresh", "new-access", "new-refresh"]) {
    assert.equal(inspected.includes(plaintext), false);
  }

  // Force a second refresh. Its request must use the first refresh response's
  // token, proving that response was resealed and later reopened rather than
  // retained as a plaintext session field.
  stored.accessExpiresAt = 0;
  const secondResponse = await fetch(`${base}/api/agent/v1/snapshot`, {
    headers: { cookie: `${COOKIE_NAME}=${session.id}` },
  });
  assert.equal(secondResponse.status, 200);
  assert.equal(refreshCalls, 2);
  assert.equal(whoamiCalls, 2);
  assert.equal(coreCalls, 2);
  const reinspected = JSON.stringify(store.sessions.get(session.id));
  for (const plaintext of ["newest-access", "newest-refresh"]) {
    assert.equal(reinspected.includes(plaintext), false);
  }
});

test("revoked HA users lose their BFF session before Core access", async () => {
  const config = configured({ principalRevalidateMs: 0 });
  const store = new SessionStore(config);
  const session = store.createSession({
    principal: { userId: "revoked-user", isAdmin: false, isActive: true },
    accessToken: "ha-token",
    refreshToken: "refresh",
    expiresIn: 3600,
  });
  let coreCalled = false;
  const fetchImpl = async (url) => {
    if (String(url).includes("/api/home_agent_edge/whoami")) {
      return new Response(JSON.stringify({
        user_id: "revoked-user", is_admin: false, is_active: false,
      }), { status: 200, headers: { "content-type": "application/json" } });
    }
    coreCalled = true;
    return new Response("{}", { status: 200 });
  };
  const base = await listen(createBff(config, { fetchImpl, store }));
  const denied = await fetch(`${base}/api/agent/v1/snapshot`, {
    headers: { cookie: `${COOKIE_NAME}=${session.id}` },
  });
  assert.equal(denied.status, 401);
  assert.equal((await denied.json()).error, "authentication_revoked");
  assert.equal(coreCalled, false);
  assert.equal(store.get(session.id), null);
});
