import assert from "node:assert/strict";
import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { DatabaseSync } from "node:sqlite";
import { SharedLinkPairingJournal } from "../src/shared-link-pairing-journal.mjs";

function fixture(t) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "pairing-journal-"));
  let now = Date.now(), journal;
  const options = { databasePath: path.join(dir, "pairing.sqlite"), encryptionKey: crypto.randomBytes(32), now: () => now };
  journal = new SharedLinkPairingJournal(options);
  const context = { subject: "owner-secret", issuerId: "home-assistant:echo", siteId: "echo", sessionCommitment: "a".repeat(64), validUntil: now + 300000 };
  t.after(() => { journal.close(); fs.rmSync(dir, { recursive: true, force: true }); });
  return { get journal() { return journal; }, options, context, advance: ms => now += ms,
    restart() { journal.close(); journal = new SharedLinkPairingJournal(options); },
    handoff(id) { return { pairing_id: id, issuer_id: "home-assistant:victoria", site_id: "victoria", session_commitment: "b".repeat(64), valid_until: now + 59000 }; } };
}

test("handoff and issuance are durably claimed once; restart recovers exact original request", t => {
  const f = fixture(t), id = f.journal.create(f.context).pairing_id;
  f.journal.claimHandoff(id, f.context);
  assert.throws(() => f.journal.claimHandoff(id, f.context), /already_attempted/);
  const retained = f.journal.retainHandoff(id, f.context, f.handoff(id));
  const dispatched = f.journal.claimIssuance(id, f.context);
  assert.deepEqual(dispatched, retained);
  assert.throws(() => f.journal.claimIssuance(id, f.context), /unavailable/);
  f.restart(); assert.deepEqual(f.journal.recoverIssuance(id, f.context), retained);
  assert(!fs.readFileSync(f.options.databasePath).includes(Buffer.from("owner-secret")));
  assert(!fs.readFileSync(f.options.databasePath).includes(Buffer.from("a".repeat(64))));
});

test("uncertain handoff never becomes a fresh handoff or issuance on restart", t => {
  const f = fixture(t), id = f.journal.create(f.context).pairing_id;
  f.journal.claimHandoff(id, f.context); f.restart();
  assert.throws(() => f.journal.claimHandoff(id, f.context));
  assert.throws(() => f.journal.recoverIssuance(id, f.context));
});

for (const patch of [{ subject: "another" }, { sessionCommitment: "c".repeat(64) },
  { siteId: "victoria", issuerId: "home-assistant:victoria" }]) {
  test("same pairing cannot be consumed or recovered by another owner/session/issuer", t => {
    const f = fixture(t), id = f.journal.create(f.context).pairing_id;
    assert.throws(() => f.journal.claimHandoff(id, { ...f.context, ...patch }));
    f.journal.claimHandoff(id, f.context);
  });
}

test("pairing expires after sixty seconds and recovery never renews five-minute context", t => {
  const f = fixture(t), id = f.journal.create(f.context).pairing_id;
  f.journal.claimHandoff(id, f.context); f.journal.retainHandoff(id, f.context, f.handoff(id)); f.journal.claimIssuance(id, f.context);
  const second = f.journal.create(f.context).pairing_id;
  f.advance(60000); assert.throws(() => f.journal.claimHandoff(second, f.context));
  f.advance(240000); assert.throws(() => f.journal.recoverIssuance(id, { ...f.context, validUntil: f.context.validUntil + 300000 }));
});

test("wrong key or modified durable phase cannot authorize dispatch", t => {
  const f = fixture(t), id = f.journal.create(f.context).pairing_id;
  assert.throws(() => new SharedLinkPairingJournal({ ...f.options, encryptionKey: crypto.randomBytes(32) }), /unavailable/);
  const db = new DatabaseSync(f.options.databasePath); db.prepare("UPDATE pairings SET state='ready' WHERE id=?").run(id); db.close();
  assert.throws(() => f.journal.claimIssuance(id, f.context));
});

test("clock rollback and mismatched Victoria response fail closed", t => {
  const f = fixture(t), id = f.journal.create(f.context).pairing_id;
  f.journal.claimHandoff(id, f.context);
  assert.throws(() => f.journal.retainHandoff(id, f.context, { ...f.handoff(id), site_id: "echo" }));
  f.advance(-1); assert.throws(() => f.journal.inspect(id, f.context));
});

test("recovered receipts compare content rather than JSON field order", t => {
  const f = fixture(t), id = f.journal.create(f.context).pairing_id;
  f.journal.claimHandoff(id, f.context);
  f.journal.retainHandoff(id, f.context, f.handoff(id));
  f.journal.claimIssuance(id, f.context);
  const receipt = { ceremony_id: id, authorization_generation: 1,
    expires_at: new Date(f.context.validUntil).toISOString() };
  f.journal.recordIssuance(id, f.context, receipt);
  f.restart();
  f.journal.recordIssuance(id, f.context, Object.fromEntries(Object.entries(receipt).reverse()));
  assert.throws(() => f.journal.recordIssuance(id, f.context, { ...receipt, authorization_generation: 2 }), /conflict/);
});
