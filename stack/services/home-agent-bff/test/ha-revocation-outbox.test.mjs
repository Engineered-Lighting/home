import assert from "node:assert/strict";
import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { DatabaseSync } from "node:sqlite";
import { spawnSync } from "node:child_process";
import test from "node:test";
import { QualifiedHaAuth } from "../src/qualified-ha-auth.mjs";
import { HaRevocationOutbox } from "../src/ha-revocation-outbox.mjs";
import { authenticateFreshHaCode } from "../src/fresh-ha-subject.mjs";

function setup(t) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "proof-revocation-"));
  t.after(() => fs.rmSync(directory, { recursive: true, force: true }));
  const encryptionKey = crypto.randomBytes(32);
  const databasePath = path.join(directory, "cleanup.sqlite");
  let calls = 0, fail = false, now = Date.parse("2026-09-26T12:00:00Z");
  const auth = new QualifiedHaAuth({ issuerId: "home-assistant:victoria", siteId: "victoria",
    origin: "https://victoria.ha.internal", clientId: "https://victoria.home.internal/",
    redirectUri: "https://victoria.home.internal/callback", fetchImpl: async (url, options) => {
      calls++;
      assert.equal(url, "https://victoria.ha.internal/auth/token");
      assert.equal(options.body.get("token"), "private-refresh-fixture");
      if (fail) throw new Error("fixture WAN loss");
      return new Response(null, { status: 200 });
    } });
  return { encryptionKey, databasePath, auth, now: () => now, advance: (ms) => { now += ms; },
    calls: () => calls, fail: (value) => { fail = value; } };
}

test("encrypted cleanup survives restart and retries a failed revocation", async (t) => {
  const f = setup(t);
  let store = new HaRevocationOutbox(f);
  const token = Buffer.from("private-refresh-fixture");
  const id = store.retain(token);
  token.fill(0);
  f.fail(true);
  assert.equal(await store.revoke(id), false);
  store.close();
  assert.equal(fs.readFileSync(f.databasePath).includes(Buffer.from("private-refresh-fixture")), false);
  store = new HaRevocationOutbox(f);
  try {
    f.fail(false);
    assert.deepEqual(await store.cleanup(), { attempted: 0, completed: 0 });
    f.advance(30_000);
    assert.deepEqual(await store.cleanup(), { attempted: 1, completed: 1 });
    assert.deepEqual(await store.cleanup(), { attempted: 0, completed: 0 });
    assert.equal(f.calls(), 2);
  } finally { store.close(); }
});

test("wrong encryption key or issuer endpoint binding fails closed", (t) => {
  const f = setup(t);
  const store = new HaRevocationOutbox(f);
  store.retain(Buffer.from("private-refresh-fixture"));
  store.close();
  assert.throws(() => new HaRevocationOutbox({ ...f, encryptionKey: crypto.randomBytes(32) }), /unavailable/);
  const other = new QualifiedHaAuth({ issuerId: "home-assistant:echo", siteId: "echo",
    origin: "https://echo.ha.internal", clientId: "https://echo.home.internal/",
    redirectUri: "https://echo.home.internal/callback" });
  assert.throws(() => new HaRevocationOutbox({ ...f, auth: other }), /unavailable/);
});

test("confirmed revocation survives failed local deletion without another HA call", async (t) => {
  const f = setup(t);
  let store = new HaRevocationOutbox(f);
  const id = store.retain(Buffer.from("private-refresh-fixture"));
  const db = new DatabaseSync(f.databasePath);
  db.exec("CREATE TRIGGER block_delete BEFORE DELETE ON revocations BEGIN SELECT RAISE(FAIL,'fixture'); END;");
  assert.equal(await store.revoke(id), false);
  assert.equal(f.calls(), 1);
  store.close();
  db.exec("DROP TRIGGER block_delete");
  db.close();
  store = new HaRevocationOutbox(f);
  try {
    f.advance(30_000);
    assert.deepEqual(await store.cleanup(), { attempted: 1, completed: 1 });
    assert.equal(f.calls(), 1);
  } finally { store.close(); }
});

test("retry backoff persists and failed first batch does not starve later records", async (t) => {
  const f = setup(t);
  let store = new HaRevocationOutbox(f);
  try {
    for (let i = 0; i < 11; i++) store.retain(Buffer.from("private-refresh-fixture"));
    f.fail(true);
    assert.deepEqual(await store.cleanup(), { attempted: 10, completed: 0 });
    f.fail(false);
    assert.deepEqual(await store.cleanup(), { attempted: 1, completed: 1 });
    f.advance(30_000);
    f.fail(true);
    assert.deepEqual(await store.cleanup(), { attempted: 10, completed: 0 });
    store.close();
    store = new HaRevocationOutbox(f);
    f.fail(false);
    f.advance(59_999);
    assert.deepEqual(await store.cleanup(), { attempted: 0, completed: 0 });
    f.advance(1);
    assert.deepEqual(await store.cleanup(), { attempted: 10, completed: 10 });
  } finally { store.close(); }
});

test("version one outbox upgrades without losing cleanup tokens", async (t) => {
  const f = setup(t);
  let store = new HaRevocationOutbox(f);
  store.retain(Buffer.from("private-refresh-fixture"));
  store.close();
  const db = new DatabaseSync(f.databasePath);
  db.exec(`DROP INDEX revocations_due;
    ALTER TABLE revocations DROP COLUMN attempts;
    ALTER TABLE revocations DROP COLUMN next_attempt;
    PRAGMA user_version=1;`);
  db.close();
  store = new HaRevocationOutbox(f);
  try { assert.deepEqual(await store.cleanup(), { attempted: 1, completed: 1 }); }
  finally { store.close(); }
});

test("cleanup timer is idempotent and stops on close", async (t) => {
  t.mock.timers.enable({ apis: ["setInterval"] });
  const f = setup(t);
  const store = new HaRevocationOutbox(f);
  store.retain(Buffer.from("private-refresh-fixture"));
  store.startCleanup();
  store.startCleanup();
  t.mock.timers.tick(60_000);
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(f.calls(), 1);
  store.close();
  t.mock.timers.tick(60_000);
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(f.calls(), 1);
});

test("verification lease excludes cleanup and fences late completion", async (t) => {
  const f = setup(t);
  let store = new HaRevocationOutbox(f);
  const id = store.retain(Buffer.from("private-refresh-fixture"), { verifying: true });
  assert.deepEqual(await store.cleanup(), { attempted: 0, completed: 0 });
  assert.equal(await store.revoke(id), false);
  f.advance(10_000);
  assert.equal(store.finishVerification(id), false);
  assert.deepEqual(await store.cleanup(), { attempted: 1, completed: 1 });
  store.retain(Buffer.from("private-refresh-fixture"), { verifying: true });
  store.close();
  store = new HaRevocationOutbox(f);
  try { assert.deepEqual(await store.cleanup(), { attempted: 1, completed: 1 }); }
  finally { store.close(); }
});

test("exclusive owner lock rejects another instance and another process", (t) => {
  const f = setup(t);
  const store = new HaRevocationOutbox(f);
  try {
    assert.throws(() => new HaRevocationOutbox(f), /unavailable/);
    const child = spawnSync(process.execPath, ["--input-type=module", "-e", `
      import { DatabaseSync } from 'node:sqlite';
      const db = new DatabaseSync(process.argv[1]);
      try { db.exec('PRAGMA busy_timeout=0; BEGIN EXCLUSIVE'); process.exit(2); }
      catch { process.exit(0); }
    `, `${fs.realpathSync(f.databasePath)}.owner.sqlite`], { windowsHide: true, timeout: 5000 });
    assert.equal(child.status, 0, child.stderr?.toString());
  } finally { store.close(); }
  const reopened = new HaRevocationOutbox(f);
  reopened.close();
  reopened.close();
  assert.throws(() => reopened.startCleanup(), /closed/);
});

test("owner process exit releases lock without deleting lock file", (t) => {
  const f = setup(t);
  const store = new HaRevocationOutbox(f);
  store.close();
  const lockPath = `${fs.realpathSync(f.databasePath)}.owner.sqlite`;
  const child = spawnSync(process.execPath, ["--input-type=module", "-e", `
    import { DatabaseSync } from 'node:sqlite';
    const db = new DatabaseSync(process.argv[1]);
    db.exec('PRAGMA busy_timeout=0; BEGIN EXCLUSIVE');
    process.exit(23);
  `, lockPath], { windowsHide: true, timeout: 5000 });
  assert.equal(child.status, 23, child.stderr?.toString());
  assert.equal(fs.existsSync(lockPath), true);
  const recovered = new HaRevocationOutbox(f);
  recovered.close();
});

test("failed retry reservation prevents network dispatch and keeps token recoverable", async (t) => {
  const f = setup(t);
  const store = new HaRevocationOutbox(f);
  const id = store.retain(Buffer.from("private-refresh-fixture"));
  const db = new DatabaseSync(f.databasePath);
  try {
    db.exec("CREATE TRIGGER block_reservation BEFORE UPDATE OF attempts ON revocations BEGIN SELECT RAISE(FAIL,'fixture'); END;");
    assert.equal(await store.revoke(id), false);
    assert.equal(f.calls(), 0);
    assert.equal(db.prepare("SELECT count(*) AS n FROM revocations").get().n, 1);
    db.exec("DROP TRIGGER block_reservation");
    assert.equal(await store.revoke(id), true);
    assert.equal(f.calls(), 1);
  } finally { db.close(); store.close(); }
});

for (const verificationFails of [false, true]) {
  test(`fresh exchange persists before verification and ${verificationFails ? "retains failed cleanup" : "revokes before returning subject"}`, async (t) => {
    const f = setup(t);
    const calls = [];
    const auth = new QualifiedHaAuth({ issuerId: "home-assistant:victoria", siteId: "victoria",
      origin: "https://victoria.ha.internal", clientId: "https://victoria.home.internal/",
      redirectUri: "https://victoria.home.internal/callback", fetchImpl: async (url, options) => {
        if (url.endsWith("/whoami")) {
          calls.push("verify");
          const db = new DatabaseSync(f.databasePath);
          try { assert.equal(db.prepare("SELECT count(*) AS n FROM revocations").get().n, 1); }
          finally { db.close(); }
          assert.deepEqual(await outbox.cleanup(), { attempted: 0, completed: 0 });
          if (verificationFails) throw new Error("fixture unavailable");
          return new Response(JSON.stringify({ user_id: "same-id", is_active: true }),
            { headers: { "content-type": "application/json" } });
        }
        if (options.body.get("action") === "revoke") {
          calls.push("revoke");
          return new Response(null, { status: 200 });
        }
        calls.push("exchange");
        return new Response(JSON.stringify({ access_token: "access", refresh_token: "refresh", expires_in: 1800 }),
          { headers: { "content-type": "application/json" } });
      } });
    const outbox = new HaRevocationOutbox({ ...f, auth });
    try {
      const operation = authenticateFreshHaCode({ auth, outbox, code: "a".repeat(32) });
      if (verificationFails) {
        await assert.rejects(operation, /fresh_auth_subject_unavailable/);
        assert.deepEqual(calls, ["exchange", "verify"]);
        assert.deepEqual(await outbox.cleanup(), { attempted: 1, completed: 1 });
      } else {
        assert.equal((await operation).haIssuerId, "home-assistant:victoria");
        assert.deepEqual(calls, ["exchange", "verify", "revoke"]);
        assert.deepEqual(await outbox.cleanup(), { attempted: 0, completed: 0 });
      }
    } finally { outbox.close(); }
  });
}
