import assert from "node:assert/strict";
import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { DatabaseSync } from "node:sqlite";
import test from "node:test";
import { SharedAuthProofJournal } from "../src/shared-auth-proof-journal.mjs";
import { SharedAuthProofClient } from "../src/shared-auth-proof-client.mjs";

function fixture(t) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "proof-journal-"));
  const now = Date.now();
  const options = { databasePath: path.join(directory, "proofs.sqlite"), encryptionKey: crypto.randomBytes(32),
    issuerId: "home-assistant:victoria", now: () => now };
  const value = { proof_id: crypto.randomUUID(), subject: "private-subject-canary", session_commitment: "a".repeat(64),
    challenge_commitment: "b".repeat(64), authenticated_at: new Date(now-1000).toISOString(), registration_revision: 3 };
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  return { options, value, now };
}
function client(options, fetchImpl) {
  return new SharedAuthProofClient({ endpoint: "https://proof.internal/internal/shared-identity/v1/auth-proofs",
    issuerId: options.issuerId, credential: "f".repeat(64), now: options.now, fetchImpl });
}
function receipt(f) {
  return { ...f.value, issuer_id: f.options.issuerId, issued_at: new Date(f.now).toISOString(),
    expires_at: new Date(f.now+299_000).toISOString() };
}
const response = (value) => new Response(JSON.stringify({ version: 1, proof: value }), { headers: { "content-type": "application/json" } });

test("journal persists encrypted original request before real client dispatch and reopens receipt", async (t) => {
  const f = fixture(t);
  let store = new SharedAuthProofJournal(f.options);
  store.retain(f.value); store.close();
  assert.equal(fs.readFileSync(f.options.databasePath).includes(Buffer.from(f.value.subject)), false);
  store = new SharedAuthProofJournal(f.options);
  const transport = client(f.options, async (_, init) => {
    const second = new SharedAuthProofJournal(f.options);
    try { assert.equal(second.inspect(f.value.proof_id).state, "indeterminate"); }
    finally { second.close(); }
    assert.deepEqual(JSON.parse(init.body).proof, f.value);
    return response(receipt(f));
  });
  assert.deepEqual(await store.dispatch(f.value.proof_id, transport), receipt(f));
  store.close(); store = new SharedAuthProofJournal(f.options);
  try {
    assert.deepEqual(store.inspect(f.value.proof_id).receipt, receipt(f));
    await assert.rejects(store.dispatch(f.value.proof_id, transport), /unavailable/);
  } finally { store.close(); }
});

test("journal wrong issuer or key cannot admit new work", (t) => {
  const f = fixture(t), store = new SharedAuthProofJournal(f.options);
  store.close();
  for (const change of [{ issuerId: "home-assistant:echo" }, { encryptionKey: crypto.randomBytes(32) }]) {
    assert.throws(() => new SharedAuthProofJournal({ ...f.options, ...change }), /unavailable/);
  }
});

for (const failure of ["network", "storage", "cancellation"]) {
  test(`journal keeps ${failure} outcome indeterminate across restart`, async (t) => {
    const f = fixture(t);
    let store = new SharedAuthProofJournal(f.options), calls = 0;
    store.retain(f.value);
    if (failure === "storage") {
      const db = new DatabaseSync(f.options.databasePath);
      db.exec("CREATE TRIGGER fail_completion BEFORE UPDATE OF receipt_iv ON proofs BEGIN SELECT RAISE(ABORT,'disk failure'); END");
      db.close();
    }
    const controller = new AbortController();
    const transport = client(f.options, async () => {
      calls++;
      if (failure === "network") throw new Error("lost");
      if (failure === "cancellation") controller.abort();
      return response(receipt(f));
    });
    await assert.rejects(store.dispatch(f.value.proof_id, transport, { signal: controller.signal }));
    store.close(); store = new SharedAuthProofJournal(f.options);
    try {
      assert.equal(store.inspect(f.value.proof_id).state, "indeterminate");
      assert.equal(store.retain(f.value), "indeterminate");
      await assert.rejects(store.dispatch(f.value.proof_id, transport), /unavailable/);
      assert.equal(calls, 1);
    } finally { store.close(); }
  });
}

test("journal rejects changed request, wrong client and expired unsent proof", async (t) => {
  const f = fixture(t);
  let store = new SharedAuthProofJournal(f.options);
  store.retain(f.value);
  assert.throws(() => store.retain({ ...f.value, subject: "different" }), /conflict/);
  let calls = 0;
  const transport = client({ ...f.options, issuerId: "home-assistant:echo" }, async () => { calls++; });
  await assert.rejects(store.dispatch(f.value.proof_id, transport), /binding/);
  store.close();
  store = new SharedAuthProofJournal({ ...f.options, now: () => f.now+300_000 });
  try {
    await assert.rejects(store.dispatch(f.value.proof_id, client(f.options, async () => { calls++; })), /expired/);
    assert.equal(calls, 0);
    assert.equal(store.inspect(f.value.proof_id).state, "prepared");
  } finally { store.close(); }
});

test("journal capacity and malformed storage prevent new delivery", (t) => {
  const f = fixture(t), store = new SharedAuthProofJournal(f.options);
  const db = new DatabaseSync(f.options.databasePath);
  try {
    const insert = db.prepare("INSERT INTO proofs(id,state,iv,ciphertext,tag) VALUES(?,'prepared',?,?,?)");
    db.exec("BEGIN");
    for (let i=0; i<1024; i++) insert.run(crypto.randomUUID(), Buffer.alloc(12), Buffer.alloc(1), Buffer.alloc(16));
    db.exec("COMMIT");
    assert.throws(() => store.retain(f.value), /full/);
    assert.equal(store.inspect(f.value.proof_id).state, "not_found");
  } finally { db.close(); store.close(); }
});

for (const outcome of ["found", "missing", "revoked", "expired", "substituted", "storage"]) {
  test(`proof recovery after restart is lookup only: ${outcome}`, async (t) => {
    const f = fixture(t);
    let store = new SharedAuthProofJournal(f.options);
    store.retain(f.value);
    let issues = 0, lookups = 0;
    await assert.rejects(store.dispatch(f.value.proof_id, client(f.options, async () => {
      issues++; throw new Error("lost commit acknowledgement");
    })));
    store.close(); store = new SharedAuthProofJournal(f.options);
    if (outcome === "storage") {
      const db = new DatabaseSync(f.options.databasePath);
      db.exec("CREATE TRIGGER fail_recovery BEFORE UPDATE OF receipt_iv ON proofs BEGIN SELECT RAISE(ABORT,'disk failure'); END");
      db.close();
    }
    const transport = client({ ...f.options, now: () => f.now+(outcome === "expired" ? 300_000 : 0) }, async (url, init) => {
      lookups++;
      assert.equal(url, "https://proof.internal/internal/shared-identity/v1/auth-proof-outcome");
      assert.deepEqual(JSON.parse(init.body), { version: 1, proof: f.value });
      assert.equal(init.redirect, "error"); assert.equal(init.credentials, "omit");
      if (outcome === "missing" || outcome === "revoked") return new Response("{}", { status: outcome === "missing" ? 503 : 403 });
      return response({ ...receipt(f), ...(outcome === "substituted" ? { subject: "other" } : {}) });
    });
    if (outcome === "found") assert.deepEqual(await store.reconcile(f.value.proof_id, transport), receipt(f));
    else await assert.rejects(store.reconcile(f.value.proof_id, transport));
    store.close(); store = new SharedAuthProofJournal(f.options);
    try {
      assert.equal(store.inspect(f.value.proof_id).state, outcome === "found" ? "completed" : "indeterminate");
      await assert.rejects(store.dispatch(f.value.proof_id, transport), /unavailable/);
      assert.equal(issues, 1); assert.equal(lookups, 1);
    } finally { store.close(); }
  });
}

test("proof lookup cannot dispatch a prepared or missing request", async (t) => {
  const f = fixture(t), store = new SharedAuthProofJournal(f.options);
  let calls = 0;
  const transport = client(f.options, async () => { calls++; return response(receipt(f)); });
  try {
    await assert.rejects(store.reconcile(f.value.proof_id, transport), /unavailable/);
    store.retain(f.value);
    await assert.rejects(store.reconcile(f.value.proof_id, transport), /unavailable/);
    assert.equal(calls, 0);
    assert.equal(store.inspect(f.value.proof_id).state, "prepared");
  } finally { store.close(); }
});

test("proof recovery rechecks freshness after waiting for transport", async (t) => {
  const f = fixture(t);
  let journalNow = f.now;
  const store = new SharedAuthProofJournal({ ...f.options, now: () => journalNow });
  store.retain(f.value);
  try {
    await assert.rejects(store.dispatch(f.value.proof_id, client(f.options, async () => { throw new Error("lost"); })));
    await assert.rejects(store.reconcile(f.value.proof_id, client(f.options, async () => {
      journalNow += 300_000;
      return response(receipt(f));
    })), /expired/);
    assert.equal(store.inspect(f.value.proof_id).state, "indeterminate");
  } finally { store.close(); }
});
