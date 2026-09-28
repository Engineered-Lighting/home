import assert from "node:assert/strict";
import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { HaLoginFlow } from "../src/ha-login-flow.mjs";
import { QualifiedHaAuth } from "../src/qualified-ha-auth.mjs";
import { HaRevocationOutbox } from "../src/ha-revocation-outbox.mjs";
import { FreshHaCeremony } from "../src/fresh-ha-ceremony.mjs";
import { EchoLinkCeremony } from "../src/echo-link-ceremony.mjs";
import { SessionStore, createVictoriaSessionStore } from "../src/bff.mjs";
import { VictoriaLinkCeremony } from "../src/victoria-link-ceremony.mjs";
import { EchoLinkReview } from "../src/echo-link-review.mjs";
import { SharedAuthProofJournal } from "../src/shared-auth-proof-journal.mjs";
import { SharedAuthProofClient } from "../src/shared-auth-proof-client.mjs";
import { SharedSessionRevocationClient } from "../src/shared-session-revocation-client.mjs";
import { GovernedFreshHaCeremony } from "../src/governed-fresh-ha-ceremony.mjs";
import { VictoriaSessionHandoff } from "../src/victoria-session-handoff.mjs";
import { VictoriaLinkAuthentication } from "../src/victoria-link-authentication.mjs";

const sessionCommitment = "a".repeat(64), challengeCommitment = "b".repeat(64);
const json = (value) => new Response(JSON.stringify(value), { headers: { "content-type": "application/json" } });
function sessionRevocation(site,key,now) {
  return {commitmentKey:key,client:new SharedSessionRevocationClient({endpoint:"https://revocation.internal/internal/shared-identity/v1/session-revocations",
    issuerId:`home-assistant:${site}`,credential:"d".repeat(64),now,fetchImpl:async(_,init)=>json({version:1,revocation:{
      ...JSON.parse(init.body).revocation,issuer_id:`home-assistant:${site}`,revoked_at:new Date(now()).toISOString()}})})};
}
function setup(t, beforeVerify = async () => {}, site = "victoria") {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "fresh-ceremony-"));
  const config = { origin: `https://${site}.ha.internal`, clientId: `https://${site}.home.internal/`,
    redirectUri: `https://${site}.home.internal/callback`, issuerId: `home-assistant:${site}`, siteId: site };
  const calls = [];
  const started = Date.now();
  let clock = started;
  const fetchImpl = async (target, options) => {
    const url = new URL(target);
    calls.push(url.pathname);
    if (url.pathname === "/auth/login_flow") return json({ type: "form", flow_id: "f".repeat(32),
      handler: ["homeassistant", null], step_id: "init", data_schema: [{ name: "username" }, { name: "password" }] });
    if (url.pathname.startsWith("/auth/login_flow/")) return json({ type: "create_entry", flow_id: "f".repeat(32),
      handler: ["homeassistant", null], result: "c".repeat(32) });
    if (url.pathname.endsWith("/whoami")) {
      await beforeVerify();
      return json({ user_id: "same-subject", is_active: true });
    }
    if (options.body.get("action") === "revoke") return new Response(null, { status: 200 });
    return json({ access_token: "access", refresh_token: "refresh", expires_in: 1800 });
  };
  const auth = new QualifiedHaAuth({ ...config, fetchImpl });
  const outbox = new HaRevocationOutbox({ databasePath: path.join(directory, "cleanup.sqlite"),
    encryptionKey: crypto.randomBytes(32), auth });
  const flow = new HaLoginFlow({ ...config, fetchImpl, wallClock: () => clock, monotonicClock: () => clock });
  const ceremony = new FreshHaCeremony({ flow, auth, outbox, wallClock: () => clock, monotonicClock: () => clock });
  t.after(() => { outbox.close(); fs.rmSync(directory, { recursive: true, force: true }); });
  return { ceremony, auth, outbox, calls, started, now: () => clock, advance: (ms) => { clock += ms; } };
}
const submit = (ceremony, handle) => ceremony.submit({ handle, sessionCommitment, input: { username: "owner", password: "fixture" } });

test("Victoria pairing requires redeemed local session and completes fresh authentication without browser proof disclosure", async t => {
  const f=setup(t), directory=fs.mkdtempSync(path.join(os.tmpdir(),"victoria-pair-auth-")), key=crypto.randomBytes(32);
  const {store,config}=createVictoriaSessionStore({auth:f.auth,browserOrigin:"https://victoria.home.internal",
    echoOrigins:new Set(["https://echo.home.internal"]),sessionDbPath:path.join(directory,"sessions.sqlite"),
    sessionEncryptionKey:crypto.randomBytes(32),idleTtlMs:600000,absoluteTtlMs:3600000,now:f.now,
    sharedSessionRevocation:sessionRevocation("victoria",key,f.now)});
  const journal=new SharedAuthProofJournal({databasePath:path.join(directory,"proof.sqlite"),encryptionKey:crypto.randomBytes(32),issuerId:"home-assistant:victoria",now:f.now});
  const client=new SharedAuthProofClient({endpoint:"https://proof.internal/internal/shared-identity/v1/auth-proofs",
    issuerId:"home-assistant:victoria",credential:"c".repeat(64),now:f.now,fetchImpl:async(_,init)=>json({version:1,proof:{
      ...JSON.parse(init.body).proof,issuer_id:"home-assistant:victoria",issued_at:new Date(f.now()).toISOString(),expires_at:new Date(f.started+300000).toISOString()}})});
  const handoff=new VictoriaSessionHandoff({store,config,commitmentKey:key,monotonicNow:f.now});
  const ceremony=new VictoriaLinkCeremony({store,config,commitmentKey:key,
    ceremony:new GovernedFreshHaCeremony({ceremony:f.ceremony,client,journal,now:f.now})});
  const auth=new VictoriaLinkAuthentication({handoff,ceremony,store,config});
  t.after(()=>{handoff.close();journal.close();store.close();fs.rmSync(directory,{recursive:true,force:true});});
  const session=await store.authenticateLogin("a".repeat(32)), pairingId=crypto.randomUUID();
  const admission={pairing_id:pairingId,session_commitment:store.linkingContext(session.id,key).sessionCommitment,
    challenge_commitment:challengeCommitment,proof_id:crypto.randomUUID(),registration_revision:2,valid_until:f.started+300000};
  await assert.rejects(auth.admit(admission));
  const offer=await handoff.create({sessionId:session.id,pairingId});
  await handoff.redeem({token:offer.token,pairingId});
  await auth.admit(admission);
  await assert.rejects(auth.admit({...admission,proof_id:crypto.randomUUID()}),/conflict/);
  await assert.rejects(auth.authenticate("other-session","begin",{pairing_id:pairingId}));
  const form=await auth.authenticate(session.id,"begin",{pairing_id:pairingId});
  await assert.rejects(auth.authenticate(session.id,"begin",{pairing_id:pairingId}));
  const result=await auth.authenticate(session.id,"submit",{pairing_id:pairingId,handle:form.handle,input:{username:"owner",password:"fixture"}});
  assert.deepEqual(result,{version:1,status:"authenticated",site_id:"victoria",ceremony_id:pairingId});
  assert.equal((await auth.outcome(pairingId)).proof_id,admission.proof_id);
  assert.deepEqual(await auth.authenticate(session.id,"recover",{pairing_id:pairingId}),result);
  store.scheduleRevocation(session.id,store.get(session.id),"logout",f.now());
  await assert.rejects(auth.outcome(pairingId));
});

for (const outcome of ["success", "logout_during_proof"]) {
  test(`Victoria authenticated session composes with fresh proof: ${outcome}`, async (t) => {
    const f = setup(t);
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), "victoria-link-"));
    const key = crypto.randomBytes(32);
    const { store, config } = createVictoriaSessionStore({ auth: f.auth,
      browserOrigin: "https://victoria.home.internal", echoOrigins: new Set(["https://echo.home.internal"]),
      sessionDbPath: path.join(directory, "sessions.sqlite"), sessionEncryptionKey: crypto.randomBytes(32),
      idleTtlMs: 600_000, absoluteTtlMs: 3_600_000, now: f.now, sharedSessionRevocation:sessionRevocation("victoria",key,f.now) });
    const journal = new SharedAuthProofJournal({ databasePath: path.join(directory, "proofs.sqlite"),
      encryptionKey: crypto.randomBytes(32), issuerId: "home-assistant:victoria", now: f.now });
    t.after(() => { journal.close(); store.close(); fs.rmSync(directory, { recursive: true, force: true }); });
    const echo = setup(t, async () => {}, "echo");
    assert.throws(() => new EchoLinkCeremony({ store, ceremony: echo.ceremony, commitmentKey: crypto.randomBytes(32) }), /configuration/);
    assert.throws(() => new EchoLinkReview({ store, commitmentKey: crypto.randomBytes(32),
      origin: "https://review.internal", credential: "e".repeat(64) }), /configuration/);
    const session = await store.authenticateLogin("a".repeat(32));
    const client = new SharedAuthProofClient({ endpoint: "https://proof.internal/internal/shared-identity/v1/auth-proofs",
      issuerId: "home-assistant:victoria", credential: "c".repeat(64), now: f.now, fetchImpl: async (_, init) => {
        if (outcome === "logout_during_proof") store.scheduleRevocation(session.id, store.get(session.id), "logout", f.now());
        return json({ version: 1, proof: { ...JSON.parse(init.body).proof, issuer_id: "home-assistant:victoria",
          issued_at: new Date(f.now()).toISOString(), expires_at: new Date(f.started+300_000).toISOString() } });
      } });
    const governed = new GovernedFreshHaCeremony({ ceremony: f.ceremony, client, journal, now: f.now });
    const adapter = new VictoriaLinkCeremony({ store, config, ceremony: governed, commitmentKey: key });
    const admission = { sessionId: session.id, challengeCommitment, proofId: crypto.randomUUID(),
      registrationRevision: 2, validUntil: f.started+300_000 };
    const form = await adapter.begin(admission);
    const operation = adapter.submit({ sessionId: session.id, handle: form.handle, input: { username: "owner", password: "fixture" } });
    if (outcome === "logout_during_proof") await assert.rejects(operation, /unavailable/);
    else {
      const result = await operation;
      assert.equal(result.proof.issuer_id, "home-assistant:victoria");
      assert.equal(result.proof.session_commitment, store.linkingContext(session.id, key).sessionCommitment);
      assert.deepEqual(await adapter.recover(admission), result);
      assert.equal(f.calls.filter((url) => url.endsWith("/whoami")).length, 5); // Login, begin, submit, fresh proof, recover.
    }
  });
}

for (const outcome of ["completed", "indeterminate", "wrong_session", "revoked", "clock_jump", "changed_receipt", "authority_denied"]) {
  test(`governed recovery checks original admission and current authority: ${outcome}`, async (t) => {
    const f = setup(t);
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), "governed-recover-"));
    const options = { databasePath: path.join(directory, "proof.sqlite"), encryptionKey: crypto.randomBytes(32),
      issuerId: "home-assistant:victoria", now: f.now };
    let journal = new SharedAuthProofJournal(options);
    const value = { proof_id: crypto.randomUUID(), subject: "same-subject", session_commitment: sessionCommitment,
      challenge_commitment: challengeCommitment, registration_revision: 2, authenticated_at: new Date(f.started).toISOString() };
    let phase = "issue", lookups = 0;
    let governed;
    const client = new SharedAuthProofClient({ endpoint: "https://proof.internal/internal/shared-identity/v1/auth-proofs",
      issuerId: options.issuerId, credential: "c".repeat(64), now: f.now, fetchImpl: async (url, init) => {
        assert.deepEqual(JSON.parse(init.body).proof, value);
        if (phase === "issue" && outcome === "indeterminate") throw new Error("lost");
        if (phase === "recover") {
          lookups++;
          assert.equal(new URL(url).pathname, "/internal/shared-identity/v1/auth-proof-outcome");
          if (outcome === "revoked") await governed.revokeSession(sessionCommitment);
          if (outcome === "clock_jump") f.advance(2000);
          if (outcome === "authority_denied") return new Response("{}", { status: 403 });
        }
        return json({ version: 1, proof: { ...value, issuer_id: options.issuerId,
          issued_at: new Date(f.started).toISOString(),
          expires_at: new Date(f.started+300_000-(phase === "recover" && outcome === "changed_receipt" ? 1000 : 0)).toISOString() } });
      } });
    journal.retain(value);
    if (outcome === "indeterminate") await assert.rejects(journal.dispatch(value.proof_id, client));
    else await journal.dispatch(value.proof_id, client);
    journal.close(); journal = new SharedAuthProofJournal(options);
    t.after(() => { journal.close(); fs.rmSync(directory, { recursive: true, force: true }); });
    governed = new GovernedFreshHaCeremony({ ceremony: f.ceremony, client, journal, now: f.now, monotonicClock: () => 0 });
    phase = "recover";
    const admission = { sessionCommitment: outcome === "wrong_session" ? "d".repeat(64) : sessionCommitment,
      challengeCommitment, proofId: value.proof_id, registrationRevision: 2,
      validUntil: f.started+300_000, expectedSubject: value.subject };
    if (["wrong_session", "revoked", "clock_jump", "changed_receipt", "authority_denied"].includes(outcome)) await assert.rejects(governed.recover(admission));
    else assert.equal((await governed.recover(admission)).proof.proof_id, value.proof_id);
    assert.equal(lookups, outcome === "wrong_session" ? 0 : 1);
    assert.deepEqual(f.calls, []); // Recovery never starts another HA login.
  });
}

test("governed ceremony connects fresh HA cleanup to durable proof dispatch", async (t) => {
  const f = setup(t);
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "governed-proof-"));
  const journal = new SharedAuthProofJournal({ databasePath: path.join(directory, "proof.sqlite"),
    encryptionKey: crypto.randomBytes(32), issuerId: "home-assistant:victoria", now: f.now });
  t.after(() => { journal.close(); fs.rmSync(directory, { recursive: true, force: true }); });
  const proofId = crypto.randomUUID();
  let calls = 0;
  const client = new SharedAuthProofClient({ endpoint: "https://proof.internal/internal/shared-identity/v1/auth-proofs",
    issuerId: "home-assistant:victoria", credential: "c".repeat(64), now: f.now, fetchImpl: async (_, init) => {
      calls++;
      assert.equal(f.calls.at(-1), "/auth/token"); // Fresh refresh token was revoked first.
      assert.equal(journal.inspect(proofId).state, "indeterminate");
      return json({ version: 1, proof: { ...JSON.parse(init.body).proof, issuer_id: "home-assistant:victoria",
        issued_at: new Date(f.now()).toISOString(), expires_at: new Date(f.started+300_000).toISOString() } });
    } });
  const governed = new GovernedFreshHaCeremony({ ceremony: f.ceremony, client, journal, now: f.now });
  const form = await governed.begin({ sessionCommitment, challengeCommitment, proofId, registrationRevision: 2,
    validUntil: f.started+300_000 });
  const result = await submit(governed, form.handle);
  assert.equal(result.status, "proved");
  assert.equal(result.proof.subject, "same-subject");
  assert.equal(result.proof.registration_revision, 2);
  assert.equal(journal.inspect(proofId).state, "completed");
  await assert.rejects(submit(governed, form.handle), /rejected/);
  assert.equal(calls, 1);
});

for (const reason of ["revoked", "clock rollback"]) {
  test(`governed proof result is suppressed after ${reason} during transport`, async (t) => {
    const f = setup(t);
    const directory = fs.mkdtempSync(path.join(os.tmpdir(), "governed-proof-loss-"));
    const journal = new SharedAuthProofJournal({ databasePath: path.join(directory, "proof.sqlite"),
      encryptionKey: crypto.randomBytes(32), issuerId: "home-assistant:victoria", now: f.now });
    t.after(() => { journal.close(); fs.rmSync(directory, { recursive: true, force: true }); });
    let entered, release;
    const ready = new Promise((resolve) => { entered = resolve; });
    const gate = new Promise((resolve) => { release = resolve; });
    const proofId = crypto.randomUUID();
    const client = new SharedAuthProofClient({ endpoint: "https://proof.internal/internal/shared-identity/v1/auth-proofs",
      issuerId: "home-assistant:victoria", credential: "c".repeat(64), now: f.now, fetchImpl: async (_, init) => {
        entered(); await gate;
        return json({ version: 1, proof: { ...JSON.parse(init.body).proof, issuer_id: "home-assistant:victoria",
          issued_at: new Date(f.started).toISOString(), expires_at: new Date(f.started+300_000).toISOString() } });
      } });
    const governed = new GovernedFreshHaCeremony({ ceremony: f.ceremony, client, journal, now: f.now });
    const form = await governed.begin({ sessionCommitment, challengeCommitment, proofId, registrationRevision: 2,
      validUntil: f.started+300_000 });
    const pending = submit(governed, form.handle);
    const rejected = assert.rejects(pending, /outcome_unavailable/);
    await ready;
    if (reason === "revoked") await governed.revokeSession(sessionCommitment);
    else f.advance(-2000);
    release(); await rejected;
    // A rolled-back delivery clock now fails the transport's issued-at check
    // before the receipt can be accepted into the journal.
    assert.equal(journal.inspect(proofId).state, "indeterminate");
    await assert.rejects(submit(governed, form.handle), /rejected/);
  });
}

test("real ceremony modules preserve provenance and return no HA code or token", async (t) => {
  const f = setup(t);
  const form = await f.ceremony.begin({ sessionCommitment, challengeCommitment });
  const result = await submit(f.ceremony, form.handle);
  assert.deepEqual(result, { status: "verified", issuerId: "home-assistant:victoria", subject: "same-subject",
    sessionCommitment, challengeCommitment, authenticatedAt: new Date(f.started).toISOString(), expiresAt: f.started + 300_000 });
  assert.equal(f.calls.at(-1), "/auth/token");
  assert.deepEqual(await f.outbox.cleanup(), { attempted: 0, completed: 0 });
  await assert.rejects(submit(f.ceremony, form.handle), /rejected/);
});

for (const reason of ["revocation", "expiry"]) {
  test(`${reason} during token verification prevents late result and still cleans tokens`, async (t) => {
    let enter, release;
    const entered = new Promise((resolve) => { enter = resolve; });
    const gate = new Promise((resolve) => { release = resolve; });
    const f = setup(t, async () => { enter(); await gate; });
    const form = await f.ceremony.begin({ sessionCommitment, challengeCommitment });
    const pending = submit(f.ceremony, form.handle);
    await entered;
    if (reason === "revocation") await f.ceremony.revokeSession(sessionCommitment);
    else f.advance(300_000);
    release();
    await assert.rejects(pending, /fresh_ceremony_rejected/);
    assert.deepEqual(await f.outbox.cleanup(), { attempted: 0, completed: 0 });
  });
}

test("session and challenge stay exclusive while an emitted code is being verified", async (t) => {
  let enter, release;
  const entered = new Promise((resolve) => { enter = resolve; });
  const gate = new Promise((resolve) => { release = resolve; });
  const f = setup(t, async () => { enter(); await gate; });
  const form = await f.ceremony.begin({ sessionCommitment, challengeCommitment });
  const pending = submit(f.ceremony, form.handle);
  try {
    await entered;
    await assert.rejects(f.ceremony.begin({ sessionCommitment, challengeCommitment: "d".repeat(64) }), /busy/);
    await assert.rejects(f.ceremony.begin({ sessionCommitment: "e".repeat(64), challengeCommitment }), /busy/);
    await assert.rejects(submit(f.ceremony, form.handle), /busy/);
    assert.equal(f.calls.filter((value) => value === "/auth/login_flow").length, 1);
  } finally { release(); }
  assert.equal((await pending).status, "verified");
});

test("wrong session cannot consume or cancel the legitimate ceremony", async (t) => {
  const f = setup(t);
  const form = await f.ceremony.begin({ sessionCommitment, challengeCommitment });
  await assert.rejects(f.ceremony.submit({ handle: form.handle, sessionCommitment: "e".repeat(64),
    input: { username: "owner", password: "fixture" } }), /rejected/);
  assert.equal((await submit(f.ceremony, form.handle)).status, "verified");
});

function echoFixture(t, beforeVerify) {
  const f = setup(t, beforeVerify, "echo");
  const directory=fs.mkdtempSync(path.join(os.tmpdir(),"echo-link-session-")),key=crypto.randomBytes(32);
  const store = new SessionStore({ sessionDbPath:path.join(directory,"sessions.sqlite"),allowedOrigins:new Set(["https://echo.home.internal"]),
    sessionEncryptionKey: crypto.randomBytes(32), now: f.now,
    sharedSessionRevocation:sessionRevocation("echo",key,f.now),
    idleTtlMs: 60_000, absoluteTtlMs: 300_000 });
  t.after(() => {store.close();fs.rmSync(directory,{recursive:true,force:true});});
  const newSession = (subject = "same-subject") => {
    const id = store.retainLoginTokens({ accessToken: Buffer.from("existing-access"),
      refreshToken: Buffer.from("existing-refresh"), expiresIn: 300 });
    store.completeLogin(id, { userId: subject, isActive: true, haIssuerId: "home-assistant:echo", siteId: "echo" });
    return id;
  };
  const adapter = new EchoLinkCeremony({ store, ceremony: f.ceremony, commitmentKey: key });
  return { ...f, store, key, adapter, newSession };
}

test("Echo adapter binds fresh HA authentication to the real existing session", async (t) => {
  const f = echoFixture(t);
  const sessionId = f.newSession();
  const expected = f.store.linkingContext(sessionId, f.key);
  const form = await f.adapter.begin({ sessionId, challengeCommitment });
  const result = await f.adapter.submit({ sessionId, handle: form.handle,
    input: { username: "owner", password: "fixture" } });
  assert.equal(result.subject, expected.subject);
  assert.equal(result.issuerId, expected.issuerId);
  assert.equal(result.sessionCommitment, expected.sessionCommitment);
  assert.equal(result.status, "verified");
  assert.equal(f.calls.at(-1), "/auth/token");
});

test("Echo session adapter completes the governed proof path for its initiating owner", async (t) => {
  const f = echoFixture(t);
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "echo-governed-proof-"));
  const journal = new SharedAuthProofJournal({ databasePath: path.join(directory, "proof.sqlite"),
    encryptionKey: crypto.randomBytes(32), issuerId: "home-assistant:echo", now: f.now });
  t.after(() => { journal.close(); fs.rmSync(directory, { recursive: true, force: true }); });
  const client = new SharedAuthProofClient({ endpoint: "https://proof.internal/internal/shared-identity/v1/auth-proofs",
    issuerId: "home-assistant:echo", credential: "c".repeat(64), now: f.now, fetchImpl: async (_, init) =>
      json({ version: 1, proof: { ...JSON.parse(init.body).proof, issuer_id: "home-assistant:echo",
        issued_at: new Date(f.now()).toISOString(), expires_at: new Date(f.started+300_000).toISOString() } }) });
  const governed = new GovernedFreshHaCeremony({ ceremony: f.ceremony, client, journal, now: f.now });
  const adapter = new EchoLinkCeremony({ store: f.store, ceremony: governed, commitmentKey: f.key });
  const sessionId = f.newSession();
  const form = await adapter.begin({ sessionId, challengeCommitment, proofId: crypto.randomUUID(),
    registrationRevision: 2, validUntil: f.started+300_000 });
  const result = await adapter.submit({ sessionId, handle: form.handle, input: { username: "owner", password: "fixture" } });
  assert.equal(result.status, "proved");
  assert.equal(result.proof.subject, "same-subject");
  assert.equal(result.proof.session_commitment, f.store.linkingContext(sessionId, f.key).sessionCommitment);
  const recovery = { sessionId, challengeCommitment, proofId: result.proof.proof_id,
    registrationRevision: 2, validUntil: f.started+300_000 };
  assert.deepEqual(await adapter.recover(recovery), result);
  await assert.rejects(adapter.recover({ ...recovery, sessionId: f.newSession() }), /rejected/);
  f.store.scheduleRevocation(sessionId, f.store.get(sessionId), "logout", f.now());
  await assert.rejects(adapter.recover(recovery), /rejected/);
});

test("Echo adapter rejects another account after revoking fresh HA tokens", async (t) => {
  const f = echoFixture(t);
  const sessionId = f.newSession("different-subject");
  const form = await f.adapter.begin({ sessionId, challengeCommitment });
  await assert.rejects(f.adapter.submit({ sessionId, handle: form.handle,
    input: { username: "owner", password: "fixture" } }), /fresh_ceremony_rejected/);
  assert.equal(f.calls.at(-1), "/auth/token");
  assert.deepEqual(await f.outbox.cleanup(), { attempted: 0, completed: 0 });
});

for (const reason of ["logout", "expiry"]) {
  test(`Echo adapter checks ${reason} during asynchronous HA authentication`, async (t) => {
    let f, sessionId;
    f = echoFixture(t, async () => {
      if (reason === "logout") f.store.scheduleRevocation(sessionId, f.store.get(sessionId), "logout", f.now());
      else f.advance(60_000);
    });
    sessionId = f.newSession();
    const form = await f.adapter.begin({ sessionId, challengeCommitment });
    await assert.rejects(f.adapter.submit({ sessionId, handle: form.handle,
      input: { username: "owner", password: "fixture" } }), /echo_link_session_rejected/);
    assert.deepEqual(await f.outbox.cleanup(), { attempted: 0, completed: 0 });
  });
}

test("Echo adapter refuses wrong-session handles without consuming the owner flow", async (t) => {
  const f = echoFixture(t);
  const sessionId = f.newSession();
  const form = await f.adapter.begin({ sessionId, challengeCommitment });
  await assert.rejects(f.adapter.submit({ sessionId: f.newSession(), handle: form.handle,
    input: { username: "owner", password: "fixture" } }), /rejected/);
  assert.equal((await f.adapter.submit({ sessionId, handle: form.handle,
    input: { username: "owner", password: "fixture" } })).status, "verified");
});

test("Echo adapter rejects missing sessions before HA traffic and Victoria configuration", async (t) => {
  const f = echoFixture(t);
  await assert.rejects(f.adapter.begin({ sessionId: "missing", challengeCommitment }), /rejected/);
  assert.equal(f.calls.length, 0);
  const victoria = setup(t);
  assert.throws(() => new EchoLinkCeremony({ store: f.store, ceremony: victoria.ceremony,
    commitmentKey: f.key }), /configuration_rejected/);
});

test("Echo ceremony freezes its initiating subject across later session changes", async (t) => {
  const f = echoFixture(t);
  const sessionId = f.newSession("original-subject");
  const form = await f.adapter.begin({ sessionId, challengeCommitment });
  // Simulate a revalidation bug or an internal mutation between begin and submit.
  // The fresh HA response and current session agree, but not the initiating user.
  f.store.get(sessionId).principal.userId = "same-subject";
  await assert.rejects(f.adapter.submit({ sessionId, handle: form.handle,
    input: { username: "owner", password: "fixture" } }), /fresh_ceremony_rejected/);
  assert.deepEqual(await f.outbox.cleanup(), { attempted: 0, completed: 0 });
});

test("fresh ceremony rejects malformed expected subjects before HA traffic", async (t) => {
  const f = setup(t);
  for (const expectedSubject of [null, 7, "", " a", "a\nb", "a".repeat(65)]) {
    await assert.rejects(f.ceremony.begin({ sessionCommitment, challengeCommitment, expectedSubject }), /rejected/);
  }
  assert.equal(f.calls.length, 0);
});
