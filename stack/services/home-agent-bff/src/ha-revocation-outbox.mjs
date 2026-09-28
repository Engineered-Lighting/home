// Dedicated refresh-token cleanup storage. These records can never be sessions.
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { performance } from "node:perf_hooks";
import { DatabaseSync } from "node:sqlite";
import { QualifiedHaAuth } from "./qualified-ha-auth.mjs";

export class HaRevocationOutbox {
  #db; #owner; #closed = false; #key; #auth; #binding; #now; #timer; #cleaning = false; #inflight = new Set(); #leases = new Map();
  constructor({ databasePath, encryptionKey, auth, now = Date.now }) {
    if (!(auth instanceof QualifiedHaAuth) || !path.isAbsolute(databasePath) ||
        !Buffer.isBuffer(encryptionKey) || encryptionKey.length !== 32 || typeof now !== "function") {
      throw new Error("invalid_revocation_outbox_configuration");
    }
    this.#key = Buffer.from(encryptionKey);
    this.#auth = auth;
    this.#now = now;
    this.#binding = auth.binding;
    try {
      this.#db = new DatabaseSync(databasePath);
      // A separate SQLite file supplies an OS-lifetime exclusive lock without
      // holding the credential database's write lock across network operations.
      // A crashed process releases it; never delete or replace this lock file.
      this.#owner = new DatabaseSync(`${fs.realpathSync(databasePath)}.owner.sqlite`);
      this.#owner.exec("PRAGMA busy_timeout=0; BEGIN EXCLUSIVE;");
      const version = this.#db.prepare("PRAGMA user_version").get().user_version;
      const tables = this.#db.prepare("SELECT name FROM sqlite_master WHERE type='table'").all();
      if (![1, 2].includes(version) && (version !== 0 || tables.length)) throw new Error();
      this.#db.exec("PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL; PRAGMA secure_delete=ON; PRAGMA busy_timeout=2000;");
      this.#db.exec("BEGIN IMMEDIATE");
      try {
        if (version === 0) {
          this.#db.exec(`CREATE TABLE binding(id INTEGER PRIMARY KEY CHECK(id=1), value TEXT NOT NULL);
            CREATE TABLE revocations(id TEXT PRIMARY KEY, iv BLOB NOT NULL, ciphertext BLOB NOT NULL,
              tag BLOB NOT NULL, state TEXT NOT NULL CHECK(state IN ('pending','revoked')));
            PRAGMA user_version=1;`);
          this.#db.prepare("INSERT INTO binding VALUES(1,?)").run(this.#binding);
        }
        if (this.#db.prepare("SELECT value FROM binding WHERE id=1").get()?.value !== this.#binding) throw new Error();
        if (version < 2) {
          this.#db.exec(`ALTER TABLE revocations ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts>=0);
            ALTER TABLE revocations ADD COLUMN next_attempt INTEGER NOT NULL DEFAULT 0 CHECK(next_attempt>=0);
            CREATE INDEX revocations_due ON revocations(next_attempt);
            PRAGMA user_version=2;`);
        }
        // Detect a wrong key before admitting more credentials. AAD also binds
        // each encrypted token to the issuer endpoints and its immutable ID.
        for (const row of this.#db.prepare("SELECT * FROM revocations").all()) this.#open(row).fill(0);
        this.#db.exec("COMMIT");
      } catch (error) { this.#db.exec("ROLLBACK"); throw error; }
      fs.chmodSync(databasePath, 0o600);
    } catch {
      this.#owner?.close(); this.#db?.close(); this.#key.fill(0);
      throw new Error("revocation_outbox_unavailable");
    }
  }

  #aad(id) { return Buffer.from(JSON.stringify([this.#binding, id])); }
  get binding() { return this.#binding; }
  #open(row) {
    const decipher = crypto.createDecipheriv("aes-256-gcm", this.#key, row.iv);
    decipher.setAAD(this.#aad(row.id));
    decipher.setAuthTag(row.tag);
    let partial;
    try {
      partial = decipher.update(row.ciphertext);
      return Buffer.concat([partial, decipher.final()]);
    } finally { partial?.fill(0); }
  }

  retain(refreshToken, { verifying = false } = {}) {
    if (this.#closed) throw new Error("revocation_outbox_closed");
    if (!Buffer.isBuffer(refreshToken) || !refreshToken.length || refreshToken.length > 65536) {
      throw new Error("invalid_revocation_token");
    }
    const lease = verifying ? { wall: this.#now(), mono: performance.now() } : null;
    if (lease && (!Number.isSafeInteger(lease.wall) || lease.wall < 0)) throw new Error("invalid_cleanup_clock");
    const id = crypto.randomUUID(), iv = crypto.randomBytes(12);
    const cipher = crypto.createCipheriv("aes-256-gcm", this.#key, iv);
    cipher.setAAD(this.#aad(id));
    const ciphertext = Buffer.concat([cipher.update(refreshToken), cipher.final()]);
    let transaction = false;
    try {
      this.#db.exec("BEGIN IMMEDIATE");
      transaction = true;
      if (this.#db.prepare("SELECT count(*) AS n FROM revocations").get().n >= 1000) throw new Error();
      this.#db.prepare("INSERT INTO revocations(id,iv,ciphertext,tag,state) VALUES(?,?,?,?,'pending')")
        .run(id, iv, ciphertext, cipher.getAuthTag());
      this.#db.exec("COMMIT");
      transaction = false;
      if (lease) this.#leases.set(id, lease);
      return id;
    } catch {
      if (transaction) this.#db.exec("ROLLBACK");
      throw new Error("revocation_retention_failed");
    } finally { ciphertext.fill(0); }
  }

  #verifying(id) {
    const lease = this.#leases.get(id);
    if (!lease) return false;
    const wallAge = this.#now() - lease.wall, monoAge = performance.now() - lease.mono;
    return Number.isFinite(wallAge) && wallAge >= 0 && wallAge < 10_000 &&
      monoAge >= 0 && monoAge < 10_000 && Math.abs(wallAge - monoAge) <= 1000;
  }

  finishVerification(id) {
    const valid = !this.#closed && this.#verifying(id);
    this.#leases.delete(id);
    return valid;
  }

  abandonVerification(id) { this.#leases.delete(id); }

  async revoke(id) {
    if (this.#closed) return false;
    if (this.#verifying(id) || this.#inflight.has(id) || this.#inflight.size >= 2) return false;
    this.#leases.delete(id);
    const now = this.#now();
    if (!Number.isSafeInteger(now) || now < 0) return false;
    const row = this.#db.prepare("SELECT * FROM revocations WHERE id=?").get(id);
    if (!row) return true;
    if (row.next_attempt > now) return false;
    this.#inflight.add(id);
    try {
      // Reserve a retry time durably before presenting a token to HA. Crashes
      // and failures cannot create an immediate retry loop after restart.
      const attempts = Math.min(row.attempts + 1, 32);
      const delay = Math.min(3_600_000, 30_000 * 2 ** Math.min(attempts - 1, 7));
      this.#db.prepare("UPDATE revocations SET attempts=?,next_attempt=? WHERE id=?")
        .run(attempts, now + delay, id);
      if (row.state !== "revoked") {
        const token = this.#open(row);
        try { await this.#auth.revoke(token); }
        finally { token.fill(0); }
        // If deletion fails after this write, restart finishes locally without
        // presenting the already revoked refresh token to HA again.
        this.#db.prepare("UPDATE revocations SET state='revoked' WHERE id=?").run(id);
      }
      this.#db.prepare("DELETE FROM revocations WHERE id=?").run(id);
      return true;
    } catch { return false; }
    finally { this.#inflight.delete(id); }
  }

  async cleanup() {
    if (this.#closed) throw new Error("revocation_outbox_closed");
    if (this.#cleaning) return { attempted: 0, completed: 0 };
    const now = this.#now();
    if (!Number.isSafeInteger(now) || now < 0) throw new Error("invalid_cleanup_clock");
    this.#cleaning = true;
    try {
      const rows = this.#db.prepare("SELECT id FROM revocations WHERE next_attempt<=? ORDER BY next_attempt,rowid LIMIT 1000")
        .all(now).filter(({ id }) => !this.#verifying(id)).slice(0, 10);
      let completed = 0;
      for (const { id } of rows) if (await this.revoke(id)) completed++;
      return { attempted: rows.length, completed };
    } finally { this.#cleaning = false; }
  }

  startCleanup() {
    if (this.#closed) throw new Error("revocation_outbox_closed");
    if (this.#timer) return;
    this.#timer = setInterval(() => { this.cleanup().catch(() => {}); }, 60_000);
    this.#timer.unref();
  }

  stopCleanup() {
    clearInterval(this.#timer);
    this.#timer = undefined;
  }

  close() {
    if (this.#closed) return;
    this.stopCleanup();
    if (this.#inflight.size || this.#cleaning) throw new Error("revocation_outbox_busy");
    this.#db.close(); this.#owner.close(); this.#key.fill(0);
    this.#leases.clear(); this.#closed = true;
  }
}
