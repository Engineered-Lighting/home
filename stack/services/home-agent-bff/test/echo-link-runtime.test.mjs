import assert from "node:assert/strict";
import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { createEchoLinkRuntime } from "../src/echo-link-runtime.mjs";
import { loadEchoLinkProvision } from "../src/echo-link-provision.mjs";

function fixture(t) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "echo-runtime-"));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  const config = { ready: true, secureCookie: true, allowInMemorySessions: false,
    allowedOrigins: new Set(["https://home.test"]), haUrl: "https://ha.test",
    clientId: "https://home.test", redirectUri: "https://home.test/api/agent/auth/callback",
    postLoginRedirect: "/", coreUrl: "http://core.internal:8096", coreToken: "test-only",
    principalRevalidateMs: 300000, sessionCleanupIntervalMs: 60000,
    sessionCleanupBatchSize: 10, idleTtlMs: 600000, absoluteTtlMs: 1200000,
    sessionDbPath: path.join(dir, "sessions.sqlite"), sessionEncryptionKey: crypto.randomBytes(32) };
  const endpoint = suffix => ({ endpoint: `https://core.test/internal/shared-identity/v1/${suffix}`,
    credential: "a".repeat(64) });
  const provision = { commitmentKey: crypto.randomBytes(32), journalKey: crypto.randomBytes(32),
    pairingDbPath: path.join(dir, "pairings.sqlite"), proofDbPath: path.join(dir, "proofs.sqlite"),
    revocationDbPath: path.join(dir, "revocations.sqlite"),
    sessionRevocation: endpoint("session-revocations"), proofIngress: endpoint("auth-proofs"),
    linkIssuance: endpoint("link-issuance"),
    victoriaHandoff: { endpoint: "https://victoria.test/internal/shared-identity/v1/victoria-handoff", credential: "b".repeat(64) },
    linkReview: { origin: "https://core.test", credential: "c".repeat(64) },
    victoriaBrowserOrigin: "https://victoria.test" };
  return { dir, config, provision };
}

test("runtime composes durable stores and can reopen them with the same keys", t => {
  const { config, provision } = fixture(t);
  for (let i = 0; i < 2; i++) {
    const runtime = createEchoLinkRuntime(config, provision);
    assert.equal(runtime.server.listening, false);
    runtime.server.close();
    runtime.close();
    runtime.close();
  }
});

test("runtime refuses insecure sessions, shared DB paths and insecure HA", t => {
  const { config, provision } = fixture(t);
  assert.throws(() => createEchoLinkRuntime({ ...config, secureCookie: false }, provision));
  assert.throws(() => createEchoLinkRuntime(config, { ...provision, proofDbPath: provision.pairingDbPath }));
  assert.throws(() => createEchoLinkRuntime({ ...config, haUrl: "http://ha.test" }, provision));
  assert.throws(() => createEchoLinkRuntime(config,{...provision,personalMemoryHomeOrigins:["http://home-ui.test"]}));
  assert.throws(() => createEchoLinkRuntime(config,{...provision,personalMemoryHomeOrigins:["https://home.test:8443"]}));
});

test("failed origin validation releases all stores for a corrected startup", t => {
  const { config, provision } = fixture(t);
  assert.throws(() => createEchoLinkRuntime(config, { ...provision, victoriaBrowserOrigin: "https://home.test:8443" }));
  const runtime = createEchoLinkRuntime(config, provision);
  runtime.server.close();
  runtime.close();
});

test("mounted provisioning composes real runtime and rejects injected transport or inline secrets", t => {
  const { dir, config, provision } = fixture(t);
  const write = (name, text) => {
    const file = path.join(dir, name);
    fs.writeFileSync(file, text);
    return file;
  };
  const value = { version: 1, pairingDbPath: provision.pairingDbPath,
    proofDbPath: provision.proofDbPath, revocationDbPath: provision.revocationDbPath,
    victoriaBrowserOrigin: provision.victoriaBrowserOrigin,
    commitmentKeyFile: write("commitment", provision.commitmentKey.toString("base64url")),
    journalKeyFile: write("journal", provision.journalKey.toString("base64url")) };
  for (const name of ["sessionRevocation", "proofIngress", "linkIssuance", "victoriaHandoff", "linkReview"]) {
    const { credential, ...endpoint } = provision[name];
    value[name] = { ...endpoint, credentialFile: write(name, credential) };
  }
  const profile = write("profile.json", JSON.stringify(value));
  value.personalMemory = {origin:"https://core.test",credentialFile:write("memory", "d".repeat(64))};
  value.personalMemoryHomeOrigins=["https://home-ui.test"];
  fs.writeFileSync(profile,JSON.stringify(value));
  const runtime = createEchoLinkRuntime(config, loadEchoLinkProvision(profile));
  runtime.server.close(); runtime.close();
  value.proofIngress.fetchImpl = "injected";
  fs.writeFileSync(profile, JSON.stringify(value));
  assert.throws(() => loadEchoLinkProvision(profile), /transport_rejected/);
  delete value.proofIngress.fetchImpl;
  value.journalKey = "inline-secret";
  fs.writeFileSync(profile, JSON.stringify(value));
  assert.throws(() => loadEchoLinkProvision(profile), /shape_rejected/);
  assert.throws(() => loadEchoLinkProvision("relative.json"), /absolute_file_required/);
});
