import crypto from "node:crypto";
import { SharedSessionRevocationClient } from "./shared-session-revocation-client.mjs";

// Shares SessionStore's SQLite connection: arming precedes disclosure and
// scheduling participates in the same transaction as the local session state.
export class SharedSessionRevocationOutbox {
  #db; #client; #key; #issuer; #site; #now; #busy = false; #closed = false; #controller;
  static preflight(db, options, issuer, encryptionKey) {
    const exists = db.prepare("SELECT 1 FROM sqlite_master WHERE type='table' AND name='bff_shared_revocation_binding'").get();
    if (!options) {
      if (exists) throw new Error("shared session revocation configuration required");
      return;
    }
    if (!(options.client instanceof SharedSessionRevocationClient) || options.client.issuerId !== issuer ||
        !Buffer.isBuffer(options.commitmentKey) || options.commitmentKey.length !== 32 ||
        crypto.timingSafeEqual(options.commitmentKey, encryptionKey)) throw new Error("invalid shared session revocation binding");
    const fingerprint = crypto.createHmac("sha256", options.commitmentKey).update(JSON.stringify(["shared-session-outbox-v1", issuer])).digest("hex");
    if (exists) {
      const row = db.prepare("SELECT * FROM bff_shared_revocation_binding WHERE id=1").get();
      if (!row || row.issuer !== issuer || row.fingerprint !== fingerprint) throw new Error("shared session revocation binding changed");
    }
  }
  constructor(db, { client, commitmentKey }, issuer, site, now) {
    this.#db=db; this.#client=client; this.#key=Buffer.from(commitmentKey);
    this.#issuer=issuer; this.#site=site; this.#now=now;
    db.exec("BEGIN IMMEDIATE");
    try {
      db.exec(`CREATE TABLE IF NOT EXISTS bff_shared_revocation_binding (
        id INTEGER PRIMARY KEY CHECK(id=1),issuer TEXT NOT NULL,fingerprint TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS bff_shared_revocation (
        session_id TEXT PRIMARY KEY,session_commitment TEXT NOT NULL UNIQUE,revocation_id TEXT NOT NULL UNIQUE,
        state TEXT NOT NULL CHECK(state IN ('armed','pending','delivered')),
        attempts INTEGER NOT NULL DEFAULT 0,next_attempt INTEGER NOT NULL DEFAULT 0);`);
      const fingerprint=crypto.createHmac("sha256",this.#key).update(JSON.stringify(["shared-session-outbox-v1",issuer])).digest("hex");
      db.prepare("INSERT OR IGNORE INTO bff_shared_revocation_binding VALUES(1,?,?)").run(issuer,fingerprint);
      // Owner decision (single-owner install): linked sessions survive a BFF
      // restart instead of forcing a new sign-in after every deploy. Logout
      // moves a session to 'pending' in the same transaction as its local
      // state, so a completed logout is never lost; only a logout interrupted
      // mid-transaction could leave the Core commitment live until expiry.
      db.exec("COMMIT");
    } catch (error) { db.exec("ROLLBACK"); this.#key.fill(0); throw error; }
  }
  arm(id, key) {
    if (!Buffer.isBuffer(key) || key.length !== 32 || !crypto.timingSafeEqual(key,this.#key)) throw new Error("linking commitment key changed");
    const prior=this.#db.prepare("SELECT * FROM bff_shared_revocation WHERE session_id=?").get(id);
    if (prior) {
      if (prior.state !== "armed") throw new Error("linking session retired");
      return prior.session_commitment;
    }
    const commitment=crypto.createHmac("sha256",this.#key).update(JSON.stringify(["home-agent:shared-link:v1:bff-session",this.#issuer,this.#site,id])).digest("hex");
    this.#db.exec("BEGIN IMMEDIATE");
    try {
      this.#compact();
      if (this.#db.prepare("SELECT count(*) AS n FROM bff_shared_revocation").get().n >= 1024) throw new Error("shared revocation storage full");
      this.#db.prepare("INSERT INTO bff_shared_revocation(session_id,session_commitment,revocation_id,state) VALUES(?,?,?,'armed')").run(id,commitment,crypto.randomUUID());
      this.#db.exec("COMMIT");
    } catch (error) { this.#db.exec("ROLLBACK"); throw error; }
    return commitment;
  }
  armed(id) {
    const row=this.#db.prepare("SELECT state FROM bff_shared_revocation WHERE session_id=?").get(id);
    return !!row && row.state === "armed";
  }
  retired(id) {
    const row=this.#db.prepare("SELECT state FROM bff_shared_revocation WHERE session_id=?").get(id);
    return !!row && row.state !== "armed";
  }
  delivered(id) {
    if (this.#closed) return false;
    const row=this.#db.prepare("SELECT state FROM bff_shared_revocation WHERE session_id=?").get(id);
    return !row || row.state === "delivered";
  }
  schedule(id) {
    this.#db.prepare("UPDATE bff_shared_revocation SET state='pending' WHERE session_id=? AND state='armed'").run(id);
  }
  #compact() {
    // Only local delivery bookkeeping is removed. Core retains the authoritative
    // tombstone. A live session or an uncertain acknowledgement is never pruned.
    this.#db.exec(`DELETE FROM bff_shared_revocation WHERE state='delivered'
      AND NOT EXISTS (SELECT 1 FROM bff_session WHERE bff_session.id=bff_shared_revocation.session_id)`);
  }
  async drain() {
    if (this.#busy || this.#closed) return;
    this.#busy=true;
    this.#controller=new AbortController();
    try {
      const now=this.#now();
      if (!Number.isSafeInteger(now)) throw new Error("invalid revocation clock");
      const rows=this.#db.prepare("SELECT * FROM bff_shared_revocation WHERE state='pending' AND next_attempt<=? ORDER BY next_attempt,session_id LIMIT 16").all(now);
      for (const row of rows) {
        if (this.#closed) break;
        // Record attempts before network delivery. Unknown outcomes repeat the
        // exact original monotonic tombstone, never mint a replacement ID.
        const delay=Math.min(300_000,1000*2**Math.min(row.attempts,9));
        this.#db.prepare("UPDATE bff_shared_revocation SET attempts=attempts+1,next_attempt=? WHERE session_id=? AND state='pending'").run(now+delay,row.session_id);
        try {
          await this.#client.revoke({session_commitment:row.session_commitment,revocation_id:row.revocation_id},{signal:this.#controller.signal});
          if (this.#closed) break;
          this.#db.prepare("UPDATE bff_shared_revocation SET state='delivered' WHERE session_id=? AND revocation_id=? AND state='pending'").run(row.session_id,row.revocation_id);
        } catch { /* The retained row remains pending, including storage failure. */ }
      }
    } finally { this.#busy=false; this.#controller=undefined; }
  }
  close() {
    if (this.#closed) return;
    this.#closed=true;
    this.#controller?.abort();
    this.#key.fill(0);
  }
}
