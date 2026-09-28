// Private durable proof delivery metadata. No tokens, browser route or runtime
// configuration enables this store. Erasure/restore admission remains required.
import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { DatabaseSync } from "node:sqlite";
import { SharedAuthProofClient, validateProofSubmission, validateProofReceipt,
  parseSharedIdentityInstant } from "./shared-auth-proof-client.mjs";

export class SharedAuthProofJournal {
  #db; #key; #issuer; #now; #active = 0;
  constructor({ databasePath, encryptionKey, issuerId, now = Date.now }) {
    if (typeof databasePath !== "string" || !path.isAbsolute(databasePath) ||
        fs.existsSync(databasePath) && fs.lstatSync(databasePath).isSymbolicLink() ||
        !Buffer.isBuffer(encryptionKey) || encryptionKey.length !== 32 ||
        !["home-assistant:echo", "home-assistant:victoria"].includes(issuerId) || typeof now !== "function") {
      throw new Error("proof_journal_configuration_rejected");
    }
    this.#key = Buffer.from(encryptionKey); this.#issuer = issuerId; this.#now = now;
    try {
      this.#db = new DatabaseSync(databasePath);
      const version = this.#db.prepare("PRAGMA user_version").get().user_version;
      if (![0, 1].includes(version) || version === 0 &&
          this.#db.prepare("SELECT count(*) AS n FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").get().n) throw new Error();
      if (version === 1) {
        const row = this.#db.prepare("SELECT * FROM admission WHERE id='admission'").get();
        if (!row || this.#open(row, "admission") !== "proof-journal-v1") throw new Error();
      }
      if (this.#db.prepare("PRAGMA page_size").get().page_size !== 4096) throw new Error();
      if (this.#db.prepare("PRAGMA journal_mode=DELETE").get().journal_mode !== "delete") throw new Error();
      this.#db.exec("PRAGMA synchronous=FULL; PRAGMA busy_timeout=2000;");
      if (this.#db.prepare("PRAGMA synchronous").get().synchronous !== 2 ||
          this.#db.prepare("PRAGMA max_page_count=4096").get().max_page_count !== 4096) throw new Error();
      this.#transaction(() => {
        const current = this.#db.prepare("PRAGMA user_version").get().user_version;
        if (current === 0) {
          this.#db.exec("CREATE TABLE admission(id TEXT PRIMARY KEY, iv BLOB NOT NULL, ciphertext BLOB NOT NULL, tag BLOB NOT NULL);");
          this.#db.exec("CREATE TABLE proofs(id TEXT PRIMARY KEY, state TEXT NOT NULL CHECK(state IN ('prepared','dispatching','completed')), iv BLOB NOT NULL, ciphertext BLOB NOT NULL, tag BLOB NOT NULL, receipt_iv BLOB, receipt_ciphertext BLOB, receipt_tag BLOB);");
          const sealed = this.#seal("proof-journal-v1", "admission", "admission");
          this.#db.prepare("INSERT INTO admission VALUES('admission',?,?,?)").run(...sealed);
          this.#db.exec("PRAGMA user_version=1");
        } else if (current !== 1) throw new Error();
        const row = this.#db.prepare("SELECT * FROM admission WHERE id='admission'").get();
        if (!row || this.#open(row, "admission") !== "proof-journal-v1") throw new Error();
      });
    } catch {
      this.#db?.close(); this.#key.fill(0);
      throw new Error("proof_journal_unavailable");
    }
  }
  get issuerId() { return this.#issuer; }
  #aad(id, purpose) { return Buffer.from(JSON.stringify(["shared-proof-journal-v1", this.#issuer, purpose, id])); }
  #seal(text, id, purpose) {
    const iv = crypto.randomBytes(12), cipher = crypto.createCipheriv("aes-256-gcm", this.#key, iv);
    cipher.setAAD(this.#aad(id, purpose));
    return [iv, Buffer.concat([cipher.update(text, "utf8"), cipher.final()]), cipher.getAuthTag()];
  }
  #open(row, purpose) {
    const decipher = crypto.createDecipheriv("aes-256-gcm", this.#key, row.iv);
    decipher.setAAD(this.#aad(row.id, purpose)); decipher.setAuthTag(row.tag);
    let partial, all;
    try {
      partial = decipher.update(row.ciphertext);
      all = Buffer.concat([partial, decipher.final()]);
      return new TextDecoder("utf-8", { fatal: true }).decode(all);
    } finally { partial?.fill(0); all?.fill(0); }
  }
  #transaction(work) {
    this.#db.exec("BEGIN IMMEDIATE");
    try { const result = work(); this.#db.exec("COMMIT"); return result; }
    catch (error) { this.#db.exec("ROLLBACK"); throw error; }
  }
  inspect(id) {
    if (typeof id !== "string" || !/^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/.test(id)) throw new Error("invalid_proof_id");
    const row = this.#db.prepare("SELECT * FROM proofs WHERE id=?").get(id);
    if (!row) return Object.freeze({ state: "not_found" });
    const submission = validateProofSubmission(JSON.parse(this.#open(row, "request")));
    if (submission.proof_id !== id) throw new Error("proof_journal_inconsistent");
    let receipt = null;
    if (row.state === "completed") {
      receipt = validateProofReceipt(JSON.parse(this.#open({ id, iv: row.receipt_iv,
        ciphertext: row.receipt_ciphertext, tag: row.receipt_tag }, "receipt")), submission, this.#issuer);
    } else if (!["prepared", "dispatching"].includes(row.state) ||
        [row.receipt_iv, row.receipt_ciphertext, row.receipt_tag].some((v) => v !== null)) {
      throw new Error("proof_journal_inconsistent");
    }
    return Object.freeze({ state: row.state === "dispatching" ? "indeterminate" : row.state, submission, receipt });
  }
  retain(submission) {
    const expected = validateProofSubmission(submission);
    return this.#transaction(() => {
      const current = this.inspect(expected.proof_id);
      if (current.state !== "not_found") {
        if (JSON.stringify(current.submission) !== JSON.stringify(expected)) throw new Error("proof_journal_conflict");
        return current.state;
      }
      if (this.#db.prepare("SELECT count(*) AS n FROM proofs").get().n >= 1024) throw new Error("proof_journal_full");
      const sealed = this.#seal(JSON.stringify(expected), expected.proof_id, "request");
      this.#db.prepare("INSERT INTO proofs(id,state,iv,ciphertext,tag) VALUES(?,'prepared',?,?,?)").run(expected.proof_id, ...sealed);
      return "prepared";
    });
  }
  async dispatch(id, client, options = {}) {
    if (!(client instanceof SharedAuthProofClient) || client.issuerId !== this.#issuer) throw new Error("proof_client_binding_rejected");
    const submission = this.#transaction(() => {
      const current = this.inspect(id), now = this.#now();
      if (current.state !== "prepared") throw new Error("proof_dispatch_unavailable");
      const authenticated = parseSharedIdentityInstant(current.submission.authenticated_at);
      if (!Number.isSafeInteger(now) || BigInt(now)*1000n < authenticated ||
          BigInt(now)*1000n >= authenticated+300_000_000n || options.signal?.aborted) throw new Error("proof_dispatch_expired");
      this.#db.prepare("UPDATE proofs SET state='dispatching' WHERE id=? AND state='prepared'").run(id);
      return current.submission;
    });
    this.#active++;
    try {
      const receipt = validateProofReceipt(await client.issue(submission, options), submission, this.#issuer);
      this.#complete(id, receipt);
      return receipt;
    } finally { this.#active--; }
  }
  async reconcile(id, client, options = {}) {
    if (!(client instanceof SharedAuthProofClient) || client.issuerId !== this.#issuer) throw new Error("proof_client_binding_rejected");
    const current = this.inspect(id);
    if (current.state !== "indeterminate") throw new Error("proof_reconciliation_unavailable");
    this.#active++;
    try {
      // Only the exact original request reaches the dedicated lookup. Unknown
      // results leave the reservation intact and never fall back to issuance.
      const receipt = validateProofReceipt(await client.inspect(current.submission, options), current.submission, this.#issuer);
      this.#complete(id, receipt);
      return receipt;
    } finally { this.#active--; }
  }
  #complete(id, receipt) {
    this.#transaction(() => {
      if (this.inspect(id).state !== "indeterminate") throw new Error("proof_journal_conflict");
      this.#requireFresh(receipt);
      const sealed = this.#seal(JSON.stringify(receipt), id, "receipt");
      this.#db.prepare("UPDATE proofs SET state='completed',receipt_iv=?,receipt_ciphertext=?,receipt_tag=? WHERE id=?").run(...sealed, id);
    });
    // Disk locking and commit can consume the final lease. A persisted receipt
    // remains historical evidence if it expires before delivery to the caller.
    this.#requireFresh(receipt);
  }
  #requireFresh(receipt) {
    const now = this.#now();
    if (!Number.isSafeInteger(now) || parseSharedIdentityInstant(receipt.expires_at) <= BigInt(now)*1000n ||
        parseSharedIdentityInstant(receipt.issued_at) > (BigInt(now)+1000n)*1000n) throw new Error("proof_delivery_expired");
  }
  close() {
    if (this.#active) throw new Error("proof_journal_busy");
    this.#db?.close(); this.#db = null; this.#key.fill(0);
  }
}
