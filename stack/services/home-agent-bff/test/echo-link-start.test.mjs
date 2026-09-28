import assert from "node:assert/strict";
import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { SessionStore, createBff, COOKIE_NAME } from "../src/bff.mjs";
import { SharedSessionRevocationClient } from "../src/shared-session-revocation-client.mjs";
import { SharedLinkPairingJournal } from "../src/shared-link-pairing-journal.mjs";
import { SharedLinkIssuanceClient } from "../src/shared-link-issuance-client.mjs";
import { VictoriaHandoffClient } from "../src/victoria-handoff-client.mjs";
import { EchoLinkStart } from "../src/echo-link-start.mjs";
import { EchoLinkCeremony } from "../src/echo-link-ceremony.mjs";
import { HaLoginFlow } from "../src/ha-login-flow.mjs";
import { QualifiedHaAuth } from "../src/qualified-ha-auth.mjs";
import { HaRevocationOutbox } from "../src/ha-revocation-outbox.mjs";
import { FreshHaCeremony } from "../src/fresh-ha-ceremony.mjs";
import { GovernedFreshHaCeremony } from "../src/governed-fresh-ha-ceremony.mjs";
import { SharedAuthProofClient } from "../src/shared-auth-proof-client.mjs";
import { SharedAuthProofJournal } from "../src/shared-auth-proof-journal.mjs";
import { EchoLinkReview } from "../src/echo-link-review.mjs";

const json = value => new Response(JSON.stringify(value), { headers: { "content-type": "application/json" } });
function fixture(t, { unknown = false, revoke = false, authentication = false, revokeAtProof = false, reviewEnabled = false } = {}) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "echo-link-start-")), key = crypto.randomBytes(32), now = Date.now();
  const revocations = new SharedSessionRevocationClient({ endpoint: "https://core.test/internal/shared-identity/v1/session-revocations",
    issuerId: "home-assistant:echo", credential: "e".repeat(64), fetchImpl: async () => { throw new Error("unused"); } });
  const config = { allowedOrigins: new Set(["https://home.test"]), haUrl: "https://ha.test",
    clientId: "https://home.test", redirectUri: "https://home.test/api/agent/auth/callback",
    postLoginRedirect: "/", coreUrl: "http://core.internal:8096", coreToken: "internal-secret",
    secureCookie: false, principalRevalidateMs: 300000, sessionCleanupIntervalMs: 60000,
    sessionCleanupBatchSize: 10, ready: true,
    sessionDbPath: path.join(dir, "sessions.sqlite"), sessionEncryptionKey: crypto.randomBytes(32),
    allowInMemorySessions: false, idleTtlMs: 600000, absoluteTtlMs: 1200000,
    sharedSessionRevocation: { client: revocations, commitmentKey: key } };
  const store = new SessionStore(config), calls = [];
  const sessionId = store.retainLoginTokens({ accessToken: Buffer.from("access"), refreshToken: Buffer.from("refresh"), expiresIn: 600 });
  store.completeLogin(sessionId, { userId: "owner", isActive: true, haIssuerId: "home-assistant:echo", siteId: "echo" });
  store.revalidate = async (configuration, id, session, _fetch, _now, options) => {
    assert.equal(configuration, config); assert.equal(id, sessionId); assert.equal(typeof options.forcePrincipalCheck, "boolean");
  };
  const journal = new SharedLinkPairingJournal({ databasePath: path.join(dir, "pairings.sqlite"), encryptionKey: crypto.randomBytes(32) });
  let victoriaAdmission;
  const handoff = new VictoriaHandoffClient({ endpoint: "https://victoria.test/internal/shared-identity/v1/victoria-handoff", credential: "a".repeat(64),
    fetchImpl: async (_url, init) => {
      if(_url.endsWith("victoria-auth-admission")) {
        victoriaAdmission=JSON.parse(init.body).admission;
        return json({version:1,admission:{version:1,status:"authentication_required",pairing_id:victoriaAdmission.pairing_id}});
      }
      if(_url.endsWith("victoria-auth-outcome")) {
        const a=victoriaAdmission;
        return json({version:1,proof:{proof_id:a.proof_id,subject:"victoria-owner",issuer_id:"home-assistant:victoria",
          session_commitment:a.session_commitment,challenge_commitment:a.challenge_commitment,registration_revision:a.registration_revision,
          authenticated_at:new Date(now).toISOString(),issued_at:new Date(now).toISOString(),expires_at:new Date(now+300000).toISOString()}});
      }
      calls.push("handoff"); const input = JSON.parse(init.body);
      if (revoke) store.scheduleRevocation(sessionId, store.getForRevocation(sessionId), "logout");
      return json({ version: 1, handoff: { pairing_id: input.pairing_id, issuer_id: "home-assistant:victoria", site_id: "victoria",
        session_commitment: "b".repeat(64), valid_until: now + 59000 } });
    } });
  const issuance = new SharedLinkIssuanceClient({ endpoint: "https://core.test/internal/shared-identity/v1/link-issuance", credential: "c".repeat(64),
    fetchImpl: async (url, init) => {
      const recovery = url.endsWith("-outcome"), request = JSON.parse(init.body).request;
      calls.push(recovery ? "recover" : "issue");
      if (unknown && !recovery) throw new Error("unknown");
      assert.equal(request.subject, "owner");
      return json({ version: 1, issuance: { ceremony_id: request.context.ceremony_id, authorization_generation: 1, revision: 1,
        created_at: new Date(now).toISOString(), expires_at: new Date(now + 300000).toISOString(),
        echo_registration_revision: 1, victoria_registration_revision: 1 } });
    } });
  let ceremony, outbox, proofs;
  if (authentication) {
    const ha = { origin: "https://ha.test", clientId: "https://home.test/", redirectUri: "https://home.test/callback",
      issuerId: "home-assistant:echo", siteId: "echo", fetchImpl: async (target, init) => {
        const pathname = new URL(target).pathname;
        if (pathname === "/auth/login_flow") return json({ type: "form", flow_id: "f".repeat(32),
          handler: ["homeassistant", null], step_id: "init", data_schema: [{ name: "username" }, { name: "password" }] });
        if (pathname.startsWith("/auth/login_flow/")) return json({ type: "create_entry", flow_id: "f".repeat(32), handler: ["homeassistant", null], result: "c".repeat(32) });
        if (pathname.endsWith("/whoami")) return json({ user_id: "owner", is_active: true });
        if (init.body.get("action") === "revoke") return new Response(null, { status: 200 });
        return json({ access_token: "fresh-access", refresh_token: "fresh-refresh", expires_in: 1800 });
      } };
    const auth = new QualifiedHaAuth(ha);
    outbox = new HaRevocationOutbox({ databasePath: path.join(dir, "cleanup.sqlite"), encryptionKey: crypto.randomBytes(32), auth });
    proofs = new SharedAuthProofJournal({ databasePath: path.join(dir, "proofs.sqlite"), encryptionKey: crypto.randomBytes(32), issuerId: ha.issuerId });
    let savedProof;
    const client = new SharedAuthProofClient({ endpoint: "https://core.test/internal/shared-identity/v1/auth-proofs", issuerId: ha.issuerId,
      credential: "c".repeat(64), fetchImpl: async (_url, init) => {
        if (revokeAtProof) store.scheduleRevocation(sessionId, store.getForRevocation(sessionId), "logout");
        savedProof ??= { ...JSON.parse(init.body).proof, issuer_id: ha.issuerId,
          issued_at: new Date().toISOString(), expires_at: new Date(now + 300000).toISOString() };
        return json({ version: 1, proof: savedProof });
      } });
    ceremony = new EchoLinkCeremony({ store, commitmentKey: key, ceremony: new GovernedFreshHaCeremony({
      ceremony: new FreshHaCeremony({ flow: new HaLoginFlow(ha), auth, outbox }), client, journal: proofs }) });
  }
  const review=reviewEnabled ? new EchoLinkReview({store,commitmentKey:key,origin:"https://core.test",credential:"e".repeat(64),
    fetchImpl:async(url,init)=>{
      assert.equal(url,"https://core.test/internal/shared-identity/v1/link-review-prepare");
      const evidence=JSON.parse(init.body).request;
      assert.equal(evidence.echo.subject,"owner");assert.equal(evidence.victoria.subject,"victoria-owner");
      assert.equal(evidence.choice.session_commitment,evidence.context.echo_session_commitment);
      calls.push("review");
      return json({version:1,ceremony_id:evidence.context.ceremony_id,gesture_id:evidence.choice.gesture_id,
        reviewed_digest:"f".repeat(64),expires_at:new Date(now+300000).toISOString(),accounts:[
          {site_id:"echo",issuer_id:"home-assistant:echo",subject:"owner"},
          {site_id:"victoria",issuer_id:"home-assistant:victoria",subject:"victoria-owner"}]});
    }}) : undefined;
  const coordinator = new EchoLinkStart({ store, config, commitmentKey: key, journal, handoff, issuance, ceremony, review });
  t.after(() => { proofs?.close(); outbox?.close(); journal.close(); store.close(); fs.rmSync(dir, { recursive: true, force: true }); });
  return { coordinator, sessionId, calls, store, config };
}

test("actual session -> Victoria handoff -> durable issuance without exposing subjects", async t => {
  const f = fixture(t), pair = await f.coordinator.start(f.sessionId);
  assert.deepEqual(Object.keys(pair).sort(), ["expires_at", "pairing_id"]);
  const result = await f.coordinator.redeem(f.sessionId, { pairing_id: pair.pairing_id, token: "d".repeat(64) });
  assert.deepEqual(f.calls, ["handoff", "issue"]);
  assert.equal(result.status, "authentication_required");
  assert.deepEqual(Object.keys(result).sort(), ["ceremony_id", "expires_at", "status", "version"]);
});

test("unknown issuance recovers by lookup and never resends handoff or issuance", async t => {
  const f = fixture(t, { unknown: true }), pair = await f.coordinator.start(f.sessionId);
  const input = { pairing_id: pair.pairing_id, token: "d".repeat(64) };
  await assert.rejects(f.coordinator.redeem(f.sessionId, input), /unknown/);
  await assert.rejects(f.coordinator.redeem(f.sessionId, input), /already_attempted/);
  assert.equal((await f.coordinator.recover(f.sessionId, pair.pairing_id)).status, "authentication_required");
  assert.deepEqual(f.calls, ["handoff", "issue", "recover"]);
});

test("logout during Victoria handoff suppresses downstream issuance", async t => {
  const f = fixture(t, { revoke: true }), pair = await f.coordinator.start(f.sessionId);
  await assert.rejects(f.coordinator.redeem(f.sessionId, { pairing_id: pair.pairing_id, token: "d".repeat(64) }));
  assert.deepEqual(f.calls, ["handoff"]);
});

test("browser-supplied account claims fail before exchange", async t => {
  const f = fixture(t), pair = await f.coordinator.start(f.sessionId);
  await assert.rejects(f.coordinator.redeem(f.sessionId, { pairing_id: pair.pairing_id, token: "d".repeat(64), subject: "owner" }));
  assert.deepEqual(f.calls, []);
});

test("browser linking routes require same session, origin and CSRF before transport", async t => {
  const f = fixture(t);
  const server = createBff(f.config, { store: f.store, linkStart: f.coordinator });
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  try {
    const base = `http://127.0.0.1:${server.address().port}/api/agent/shared-identity/`;
    const headers = { cookie: `${COOKIE_NAME}=${f.sessionId}`, origin: "https://home.test",
      "x-csrf-token": f.store.get(f.sessionId).csrf, "content-type": "application/json" };
    for (const route of ["start", "handoff", "issuance-outcome"]) {
      for (const changed of [{ cookie: "" }, { origin: "https://evil.test" }, { "x-csrf-token": "wrong" }]) {
        const res = await fetch(base + route, { method: "POST", headers: { ...headers, ...changed }, body: "{}" });
        assert.ok([401, 403].includes(res.status));
      }
    }
    assert.deepEqual(f.calls, []);
    for (const body of ['{"subject":"owner"}', '[]', 'null', '{"x":1,"x":2}']) {
      assert.equal((await fetch(base + "start", { method: "POST", headers, body })).status, 422);
    }
    const started = await fetch(base + "start", { method: "POST", headers, body: "{}" });
    assert.equal(started.status, 200);
    const pairing = await started.json();
    const handed = await fetch(base + "handoff", { method: "POST", headers,
      body: JSON.stringify({ pairing_id: pairing.pairing_id, token: "d".repeat(64) }) });
    assert.equal(handed.status, 200);
    assert.equal((await handed.json()).status, "authentication_required");
    assert.deepEqual(f.calls, ["handoff", "issue"]);
  } finally { await new Promise(resolve => server.close(resolve)); }
});

test("link start routes stay unavailable without explicit composition", async t => {
  const f = fixture(t), server = createBff(f.config, { store: f.store });
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  try {
    for (const route of ["start", "handoff", "issuance-outcome"]) {
      const result = await fetch(`http://127.0.0.1:${server.address().port}/api/agent/shared-identity/${route}`, {
        method: "POST", headers: { cookie: `${COOKIE_NAME}=${f.sessionId}` }, body: "{}" });
      assert.equal(result.status, 404);
    }
  } finally { await new Promise(resolve => server.close(resolve)); }
});

test("issued pairing drives fresh Echo authentication without exposing proof or accepting caller authority", async t => {
  const f = fixture(t, { authentication: true }), pair = await f.coordinator.start(f.sessionId);
  await assert.rejects(f.coordinator.authenticate(f.sessionId, "begin", { pairing_id: pair.pairing_id }));
  await f.coordinator.redeem(f.sessionId, { pairing_id: pair.pairing_id, token: "d".repeat(64) });
  await assert.rejects(f.coordinator.authenticate(f.sessionId, "begin", { pairing_id: pair.pairing_id, registrationRevision: 5 }));
  const form = await f.coordinator.authenticate(f.sessionId, "begin", { pairing_id: pair.pairing_id });
  assert.equal(form.status, "form");
  await assert.rejects(f.coordinator.authenticate(f.sessionId, "begin", { pairing_id: pair.pairing_id }), /already_attempted/);
  const input = { pairing_id: pair.pairing_id, handle: form.handle, input: { username: "owner", password: "fixture" } };
  await assert.rejects(f.coordinator.authenticate(f.sessionId, "submit", { ...input, handle: "e".repeat(64) }));
  const result = await f.coordinator.authenticate(f.sessionId, "submit", input);
  assert.deepEqual(result, { version: 1, status: "authenticated", site_id: "echo", ceremony_id: pair.pairing_id });
  await assert.rejects(f.coordinator.authenticate(f.sessionId, "submit", input));
  assert.deepEqual(await f.coordinator.authenticate(f.sessionId, "recover", { pairing_id: pair.pairing_id }), result);
});

test("logout during fresh proof suppresses account-link authentication result", async t => {
  const f = fixture(t, { authentication: true, revokeAtProof: true }), pair = await f.coordinator.start(f.sessionId);
  await f.coordinator.redeem(f.sessionId, { pairing_id: pair.pairing_id, token: "d".repeat(64) });
  const form = await f.coordinator.authenticate(f.sessionId, "begin", { pairing_id: pair.pairing_id });
  await assert.rejects(f.coordinator.authenticate(f.sessionId, "submit", { pairing_id: pair.pairing_id,
    handle: form.handle, input: { username: "owner", password: "fixture" } }));
});

test("fresh-auth HTTP routes enforce the existing browser boundary and return only form or status", async t => {
  const f = fixture(t, { authentication: true }), pair = await f.coordinator.start(f.sessionId);
  await f.coordinator.redeem(f.sessionId, { pairing_id: pair.pairing_id, token: "d".repeat(64) });
  const server = createBff(f.config, { store: f.store, linkStart: f.coordinator });
  await new Promise(resolve => server.listen(0, "127.0.0.1", resolve));
  try {
    const base = `http://127.0.0.1:${server.address().port}/api/agent/shared-identity/`;
    const headers = { cookie: `${COOKIE_NAME}=${f.sessionId}`, origin: "https://home.test",
      "x-csrf-token": f.store.get(f.sessionId).csrf, "content-type": "application/json" };
    for (const route of ["auth-begin", "auth-submit", "auth-outcome"]) {
      for (const change of [{ cookie: "" }, { origin: "https://evil.test" }, { "x-csrf-token": "wrong" }]) {
        const denied = await fetch(base + route, { method: "POST", headers: { ...headers, ...change }, body: "{}" });
        assert.ok([401, 403].includes(denied.status));
      }
    }
    const beginning = await fetch(base + "auth-begin", { method: "POST", headers, body: JSON.stringify({ pairing_id: pair.pairing_id }) });
    assert.equal(beginning.status, 200);
    const form = await beginning.json();
    assert.equal(form.status, "form");
    const submitted = await fetch(base + "auth-submit", { method: "POST", headers, body: JSON.stringify({
      pairing_id: pair.pairing_id, handle: form.handle, input: { username: "owner", password: "fixture" } }) });
    assert.equal(submitted.status, 200);
    assert.deepEqual(await submitted.json(), { version: 1, status: "authenticated", site_id: "echo", ceremony_id: pair.pairing_id });
  } finally { await new Promise(resolve => server.close(resolve)); }
});

test("coordinator joins separately authenticated home proofs into owner review without creating approval",async t=>{
  const f=fixture(t,{authentication:true,reviewEnabled:true}),pair=await f.coordinator.start(f.sessionId);
  await f.coordinator.redeem(f.sessionId,{pairing_id:pair.pairing_id,token:"d".repeat(64)});
  await assert.rejects(f.coordinator.prepareReview(f.sessionId,{pairing_id:pair.pairing_id}));
  const form=await f.coordinator.authenticate(f.sessionId,"begin",{pairing_id:pair.pairing_id});
  await f.coordinator.authenticate(f.sessionId,"submit",{pairing_id:pair.pairing_id,handle:form.handle,input:{username:"owner",password:"fixture"}});
  await f.coordinator.victoriaAuthentication(f.sessionId,"admit",{pairing_id:pair.pairing_id});
  await assert.rejects(f.coordinator.prepareReview(f.sessionId,{pairing_id:pair.pairing_id}));
  const result=await f.coordinator.victoriaAuthentication(f.sessionId,"outcome",{pairing_id:pair.pairing_id});
  assert.deepEqual(result,{version:1,status:"authenticated",site_id:"victoria",ceremony_id:pair.pairing_id});
  const reviewed=await f.coordinator.prepareReview(f.sessionId,{pairing_id:pair.pairing_id});
  assert.equal(reviewed.accounts[1].subject,"victoria-owner");
  assert(!JSON.stringify(reviewed).includes("proof_id"));
  assert.deepEqual(await f.coordinator.prepareReview(f.sessionId,{pairing_id:pair.pairing_id}),reviewed);
  assert.deepEqual(f.calls,["handoff","issue","review","review"]);
});
