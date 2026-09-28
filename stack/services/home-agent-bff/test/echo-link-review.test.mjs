import assert from "node:assert/strict";
import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";
import { SessionStore, createBff, COOKIE_NAME } from "../src/bff.mjs";
import { EchoLinkReview } from "../src/echo-link-review.mjs";
import { SharedSessionRevocationClient } from "../src/shared-session-revocation-client.mjs";

const id = "00000000-0000-0000-0000-000000000001";
const gesture = "00000000-0000-0000-0000-000000000002";
const digest = "a".repeat(64);
const response = (data) => new Response(JSON.stringify(data), { headers: { "content-type": "application/json" } });
function config() {
  return { allowedOrigins: new Set(["https://home.test"]), haUrl: "https://ha.test",
    clientId: "https://home.test", redirectUri: "https://home.test/api/agent/auth/callback",
    postLoginRedirect: "/", coreUrl: "http://core.internal:8096", coreToken: "internal-secret",
    sessionEncryptionKey: crypto.randomBytes(32), sessionDbPath: "", allowInMemorySessions: true,
    secureCookie: false, idleTtlMs: 30_000, absoluteTtlMs: 60_000, principalRevalidateMs: 300_000,
    sessionCleanupIntervalMs: 60_000, sessionCleanupBatchSize: 10, ready: true };
}
function fixture(fetchImpl) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "echo-link-review-"));
  const commitmentKey = crypto.randomBytes(32);
  const client = new SharedSessionRevocationClient({
    endpoint: "https://link.internal/internal/shared-identity/v1/session-revocations",
    issuerId: "home-assistant:echo", credential: "d".repeat(64),
    fetchImpl: async (_url, init) => response({ version: 1, revocation: {
      ...JSON.parse(init.body).revocation, issuer_id: "home-assistant:echo",
      revoked_at: new Date().toISOString(),
    } }),
  });
  const configuration = { ...config(), sessionDbPath: path.join(directory, "sessions.sqlite"),
    allowInMemorySessions: false, sharedSessionRevocation: { client, commitmentKey } };
  const store = new SessionStore(configuration);
  const sessionId = store.retainLoginTokens({ accessToken: Buffer.from("access"), refreshToken: Buffer.from("refresh"), expiresIn: 300 });
  store.completeLogin(sessionId, { userId: "owner", isActive: true, haIssuerId: "home-assistant:echo", siteId: "echo" });
  const options = { store, commitmentKey, origin: "https://link.internal",
    credential: "e".repeat(64), fetchImpl };
  return { configuration, store, sessionId, options, linkReview: new EchoLinkReview(options),
    close() { store.close(); fs.rmSync(directory, { recursive: true, force: true }); } };
}
function review() {
  return { version: 1, ceremony_id: id, gesture_id: gesture, reviewed_digest: digest,
    expires_at: new Date(Date.now()+60_000).toISOString(), accounts: [
      { site_id: "echo", issuer_id: "home-assistant:echo", subject: "owner" },
      { site_id: "victoria", issuer_id: "home-assistant:victoria", subject: "owner" },
    ] };
}

test("link review rejects sessions without durable shared revocation", () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "echo-link-review-untracked-"));
  try {
    for (const sessionDbPath of ["", path.join(directory, "sessions.sqlite")]) {
      const store = new SessionStore({ ...config(), sessionDbPath });
      try {
        assert.equal(store.hasSharedSessionRevocation, false);
        assert.throws(() => new EchoLinkReview({ store, commitmentKey: crypto.randomBytes(32),
          origin: "https://link.internal", credential: "e".repeat(64) }), /configuration_rejected/);
      } finally { store.close(); }
    }
  } finally { fs.rmSync(directory, { recursive: true, force: true }); }
});

test("review transport binds the real session to one fixed private endpoint", async () => {
  let captured;
  const f = fixture(async (url, init) => { captured = { url, init }; return response(review()); });
  try {
    const result = await f.linkReview.request(f.sessionId, "review", { ceremony_id: id });
    assert.equal(result.accounts[1].site_id, "victoria");
    assert.equal(captured.url, "https://link.internal/internal/shared-identity/v1/link-review");
    const body = JSON.parse(captured.init.body);
    assert.equal(body.request.subject, "owner");
    assert.match(body.request.session_commitment, /^[a-f0-9]{64}$/);
    assert.equal(captured.init.body.includes(f.sessionId), false);
    assert.equal(captured.init.redirect, "error");
    assert.equal(captured.init.credentials, "omit");
  } finally { f.close(); }
});

for (const change of [
  (v) => { v.ceremony_id = gesture; },
  (v) => { v.accounts[0].subject = "guest"; },
  (v) => { v.accounts[1].issuer_id = "home-assistant:echo"; },
  (v) => { v.expires_at = new Date(Date.now()-1).toISOString(); },
  (v) => { v.proof_id = id; },
]) {
  test("review transport rejects substituted, stale or excessive response data", async () => {
    const f = fixture(async () => { const data = review(); change(data); return response(data); });
    try { await assert.rejects(f.linkReview.request(f.sessionId, "review", { ceremony_id: id }), /unknown/); }
    finally { f.close(); }
  });
}

test("review transport rejects caller identity and arbitrary endpoint configuration", async () => {
  let calls = 0;
  const f = fixture(async () => { calls++; return response(review()); });
  try {
    for (const origin of ["http://link.internal", "https://link.internal/other", "https://user@link.internal", "https://link.internal/?url=other"]) {
      assert.throws(() => new EchoLinkReview({ ...f.options, origin }));
    }
    for (const extra of [{ subject: "guest" }, { session_commitment: digest }, { issuer_id: "home-assistant:victoria" }]) {
      await assert.rejects(f.linkReview.request(f.sessionId, "review", { ceremony_id: id, ...extra }), /rejected/);
    }
    assert.equal(calls, 0);
  } finally { f.close(); }
});

test("logout during transport suppresses late review delivery", async () => {
  let resolve;
  const f = fixture(() => new Promise((done) => { resolve = done; }));
  try {
    const pending = f.linkReview.request(f.sessionId, "review", { ceremony_id: id });
    f.store.scheduleRevocation(f.sessionId, f.store.get(f.sessionId), "logout");
    resolve(response(review()));
    await assert.rejects(pending, /unknown/);
  } finally { f.close(); }
});

test("cancelled transport retains physical concurrency charge and never retries", async () => {
  const resolvers = [];
  const f = fixture(() => new Promise((done) => resolvers.push(done)));
  const controller = new AbortController();
  try {
    const first = f.linkReview.request(f.sessionId, "review", { ceremony_id: id }, { signal: controller.signal });
    const second = f.linkReview.request(f.sessionId, "review", { ceremony_id: id }, { signal: controller.signal });
    controller.abort();
    await assert.rejects(first, /unknown/); await assert.rejects(second, /unknown/);
    await assert.rejects(f.linkReview.request(f.sessionId, "review", { ceremony_id: id }), /busy/);
    assert.equal(resolvers.length, 2);
  } finally {
    resolvers.forEach((done) => done(response(review())));
    await new Promise((resolve) => setImmediate(resolve));
    f.close();
  }
});

test("BFF optional routes enforce cookie origin CSRF and fresh principal before fixed transport", async () => {
  let privateCalls = 0, haCalls = 0;
  const f = fixture(async () => { privateCalls++; return response(review()); });
  const server = createBff(f.configuration, { store: f.store, linkReview: f.linkReview,
    fetchImpl: async () => { haCalls++; return response({ user_id: "owner", is_active: true }); } });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const url = `http://127.0.0.1:${server.address().port}/api/agent/shared-identity/review`;
  const headers = { cookie: `${COOKIE_NAME}=${f.sessionId}`, origin: "https://home.test",
    "x-csrf-token": f.store.get(f.sessionId).csrf, "content-type": "application/json" };
  try {
    for (const operation of ["review", "confirm", "outcome"]) {
      for (const changed of [{ cookie: "" }, { origin: "https://evil.test" }, { "x-csrf-token": "wrong" }]) {
        const res = await fetch(url.replace(/review$/, operation), { method: "POST", headers: { ...headers, ...changed }, body: JSON.stringify({ ceremony_id: id }) });
        assert.ok([401, 403].includes(res.status));
      }
    }
    assert.equal(privateCalls, 0); assert.equal(haCalls, 0);
    const result = await fetch(url, { method: "POST", headers, body: JSON.stringify({ ceremony_id: id }) });
    assert.equal(result.status, 200);
    assert.equal((await result.json()).accounts[0].subject, "owner");
    assert.equal(privateCalls, 1); assert.equal(haCalls, 1);
    assert.equal((await fetch(url, { method: "POST", headers, body: `{"ceremony_id":"${id}","ceremony_id":"${id}"}` })).status, 422);
    assert.equal(privateCalls, 1);
    const sessionResponse = await fetch(url.replace(/shared-identity\/review$/, "auth/session"), { headers: { cookie: headers.cookie } });
    assert.equal((await sessionResponse.json()).shared_link_review_enabled, true);
  } finally {
    await new Promise((resolve) => server.close(resolve));
    f.close();
  }
});

test("confirmation transport requires the matching ceremony and valid outcome", async () => {
  const f = fixture(async (url) => {
    assert.equal(url, "https://link.internal/internal/shared-identity/v1/link-confirmation");
    return response({ version: 1, status: "confirmed", ceremony_id: id, link_id: gesture,
      authorization_generation: 2, revision: 1, confirmed_at: new Date().toISOString() });
  });
  try {
    const result = await f.linkReview.request(f.sessionId, "confirm", { ceremony_id: id, gesture_id: gesture, reviewed_digest: digest });
    assert.equal(result.status, "confirmed");
  } finally { f.close(); }
});

test("optional linking handler cannot use a different session store", () => {
  const f = fixture(async () => response(review()));
  const other = new SessionStore(config());
  try {
    assert.throws(() => createBff(f.configuration, { store: other, linkReview: f.linkReview }), /same authenticated/);
  } finally { f.close(); other.close(); }
});

test("default BFF does not expose optional linking routes", async () => {
  const f = fixture(async () => { throw new Error("must not dispatch"); });
  const server = createBff(f.configuration, { store: f.store });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  try {
    const result = await fetch(`http://127.0.0.1:${server.address().port}/api/agent/shared-identity/review`, {
      method: "POST", headers: { cookie: `${COOKIE_NAME}=${f.sessionId}` },
    });
    assert.equal(result.status, 404);
    const sessionResponse = await fetch(`http://127.0.0.1:${server.address().port}/api/agent/auth/session`, {
      headers: { cookie: `${COOKIE_NAME}=${f.sessionId}` },
    });
    assert.equal((await sessionResponse.json()).shared_link_review_enabled, undefined);
  } finally { await new Promise((resolve) => server.close(resolve)); f.close(); }
});

test("private response size limit and wrong confirmation ceremony fail closed", async () => {
  for (const payload of [ { padding: "x".repeat(4096) },
    { version: 1, status: "confirmed", ceremony_id: gesture, link_id: gesture,
      authorization_generation: 2, revision: 1, confirmed_at: new Date().toISOString() } ]) {
    const f = fixture(async () => response(payload));
    try {
      await assert.rejects(f.linkReview.request(f.sessionId, "confirm", {
        ceremony_id: id, gesture_id: gesture, reviewed_digest: digest }), /unknown/);
    } finally { f.close(); }
  }
});

test("outcome transport looks up the original ceremony without approval fields", async () => {
  let calls = 0;
  const stamp = new Date(Date.now()-600_000).toISOString();
  const f = fixture(async (url, init) => {
    calls++;
    assert.equal(url, "https://link.internal/internal/shared-identity/v1/link-outcome");
    assert.deepEqual(Object.keys(JSON.parse(init.body).request).sort(), ["ceremony_id", "session_commitment", "subject"]);
    return response({ version: 1, status: "confirmed", ceremony_id: id, link_id: gesture,
      authorization_generation: 2, revision: 1, confirmed_at: stamp });
  });
  try {
    assert.equal((await f.linkReview.request(f.sessionId, "outcome", { ceremony_id: id })).confirmed_at, stamp);
    await assert.rejects(f.linkReview.request(f.sessionId, "outcome", { ceremony_id: id, gesture_id: gesture }), /rejected/);
    assert.equal(calls, 1);
  } finally { f.close(); }
});
