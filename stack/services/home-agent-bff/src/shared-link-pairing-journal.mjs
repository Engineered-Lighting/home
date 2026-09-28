import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { DatabaseSync } from "node:sqlite";
import { isDeepStrictEqual } from "node:util";
import { validateLinkIssuanceRequest } from "./shared-link-issuance-client.mjs";
import { validateProofReceipt } from "./shared-auth-proof-client.mjs";

const uuid = v => typeof v === "string" && /^[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}$/.test(v);
const digest = v => typeof v === "string" && /^[a-f0-9]{64}$/.test(v);
function echoContext(value) {
  if (!value || value.issuerId !== "home-assistant:echo" || value.siteId !== "echo" ||
      !digest(value.sessionCommitment) || typeof value.subject !== "string" || !value.subject.length ||
      [...value.subject].length > 64 || value.subject.trim() !== value.subject || /[\x00-\x1f\x7f]/.test(value.subject) ||
      !Number.isSafeInteger(value.validUntil)) throw new Error("pairing_owner_unavailable");
  return { subject: value.subject, session: value.sessionCommitment };
}

// Durable delivery accounting, not an owner-link database. Only the governed
// Core confirmation creates a link. Handoff tokens and HA credentials are never
// persisted here. A new/restored process must still validate the live session.
export class SharedLinkPairingJournal {
  #db; #key; #now;
  constructor({ databasePath, encryptionKey, now = Date.now }) {
    if (typeof databasePath !== "string" || !path.isAbsolute(databasePath) ||
        fs.existsSync(databasePath) && fs.lstatSync(databasePath).isSymbolicLink() ||
        !Buffer.isBuffer(encryptionKey) || encryptionKey.length !== 32 || typeof now !== "function") {
      throw new Error("pairing_journal_configuration_rejected");
    }
    this.#key = Buffer.from(encryptionKey); this.#now = now;
    try {
      this.#db = new DatabaseSync(databasePath);
      const version = this.#db.prepare("PRAGMA user_version").get().user_version;
      if (![0, 1].includes(version) || version === 0 &&
          this.#db.prepare("SELECT count(*) AS n FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").get().n) throw new Error();
      if (this.#db.prepare("PRAGMA journal_mode=DELETE").get().journal_mode !== "delete" ||
          this.#db.prepare("PRAGMA page_size").get().page_size !== 4096) throw new Error();
      this.#db.exec("PRAGMA synchronous=FULL; PRAGMA busy_timeout=2000; PRAGMA secure_delete=ON;");
      if (this.#db.prepare("PRAGMA max_page_count=4096").get().max_page_count !== 4096) throw new Error();
      this.#transaction(() => {
        if (version === 0) {
          this.#db.exec("CREATE TABLE pairings(id TEXT PRIMARY KEY, state TEXT NOT NULL, iv BLOB NOT NULL, ciphertext BLOB NOT NULL, tag BLOB NOT NULL); PRAGMA user_version=1;");
          this.#write("admission", "admission", { contract: "shared-link-pairing-v1" });
        }
        if (this.#load("admission").data.contract !== "shared-link-pairing-v1") throw new Error();
      });
    } catch {
      this.#db?.close(); this.#key.fill(0); throw new Error("pairing_journal_unavailable");
    }
  }
  #transaction(work) {
    this.#db.exec("BEGIN IMMEDIATE");
    try { const value = work(); this.#db.exec("COMMIT"); return value; }
    catch (error) { this.#db.exec("ROLLBACK"); throw error; }
  }
  #aad(id, state) { return Buffer.from(JSON.stringify(["shared-link-pairing-v1", id, state])); }
  #write(id, state, value) {
    const iv = crypto.randomBytes(12), cipher = crypto.createCipheriv("aes-256-gcm", this.#key, iv);
    cipher.setAAD(this.#aad(id, state));
    const body = Buffer.from(JSON.stringify(value));
    try {
      const sealed = Buffer.concat([cipher.update(body), cipher.final()]);
      this.#db.prepare("INSERT INTO pairings VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET state=excluded.state,iv=excluded.iv,ciphertext=excluded.ciphertext,tag=excluded.tag")
        .run(id, state, iv, sealed, cipher.getAuthTag());
    } finally { body.fill(0); }
  }
  #load(id) {
    const row = this.#db.prepare("SELECT * FROM pairings WHERE id=?").get(id);
    if (!row) throw new Error("pairing_unavailable");
    const decipher = crypto.createDecipheriv("aes-256-gcm", this.#key, row.iv);
    decipher.setAAD(this.#aad(id, row.state)); decipher.setAuthTag(row.tag);
    let part, body;
    try {
      part = decipher.update(row.ciphertext); body = Buffer.concat([part, decipher.final()]);
      return { state: row.state, data: JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(body)) };
    } finally { part?.fill(0); body?.fill(0); }
  }
  #owned(id, context, { pairingWindow = false } = {}) {
    if (!uuid(id)) throw new Error("pairing_unavailable");
    const owner = echoContext(context), current = this.#load(id), now = this.#now();
    if (!Number.isSafeInteger(now) || now < current.data.created || now >= context.validUntil ||
        now >= current.data.expires || pairingWindow && now >= current.data.pairingExpires ||
        current.data.owner.subject !== owner.subject || current.data.owner.session !== owner.session) throw new Error("pairing_unavailable");
    return current;
  }
  create(context) {
    const owner = echoContext(context), now = this.#now();
    if (!Number.isSafeInteger(now) || context.validUntil <= now) throw new Error("pairing_unavailable");
    return this.#transaction(() => {
      if (this.#db.prepare("SELECT count(*) AS n FROM pairings").get().n >= 257) throw new Error("pairing_journal_full");
      const id = crypto.randomUUID(), expiry = Math.min(now + 60_000, context.validUntil);
      this.#write(id, "created", { owner, created: now, pairingExpires: expiry, expires: Math.min(now + 300_000, context.validUntil) });
      return Object.freeze({ pairing_id: id, expires_at: expiry });
    });
  }
  claimHandoff(id, context) {
    this.#transaction(() => {
      const current = this.#owned(id, context, { pairingWindow: true });
      if (current.state !== "created") throw new Error("handoff_already_attempted");
      this.#write(id, "handoff", current.data);
    });
  }
  retainHandoff(id, context, handoff) {
    return this.#transaction(() => {
      const current = this.#owned(id, context, { pairingWindow: true }), now = this.#now();
      if (current.state !== "handoff" || handoff?.pairing_id !== id || handoff.issuer_id !== "home-assistant:victoria" ||
          handoff.site_id !== "victoria" || !digest(handoff.session_commitment) || !Number.isSafeInteger(handoff.valid_until) ||
          handoff.valid_until <= now || handoff.valid_until > now + 60_000) throw new Error("handoff_unavailable");
      const request = validateLinkIssuanceRequest({ subject: current.data.owner.subject, context: {
        ceremony_id: id, echo_session_commitment: current.data.owner.session,
        victoria_session_commitment: handoff.session_commitment,
        echo_challenge_id: crypto.randomUUID(), victoria_challenge_id: crypto.randomUUID(),
        echo_challenge_commitment: crypto.randomBytes(32).toString("hex"),
        victoria_challenge_commitment: crypto.randomBytes(32).toString("hex"),
      } });
      this.#write(id, "ready", { ...current.data, request, handoffDeadline: handoff.valid_until });
      return request;
    });
  }
  claimIssuance(id, context) {
    return this.#transaction(() => {
      const current = this.#owned(id, context);
      if (current.state !== "ready" || this.#now() >= current.data.handoffDeadline) throw new Error("issuance_unavailable");
      const request = validateLinkIssuanceRequest(current.data.request);
      this.#write(id, "issuing", current.data); return request;
    });
  }
  recoverIssuance(id, context) {
    const current = this.#owned(id, context);
    if (!["issuing", "issued"].includes(current.state)) throw new Error("issuance_recovery_unavailable");
    return validateLinkIssuanceRequest(current.data.request);
  }
  recordIssuance(id, context, receipt) {
    this.#transaction(() => {
      const current = this.#owned(id, context);
      if (!["issuing", "issued"].includes(current.state) || receipt?.ceremony_id !== id ||
          !Number.isSafeInteger(receipt.authorization_generation) || receipt.authorization_generation < 1 ||
          !Number.isFinite(Date.parse(receipt.expires_at)) || Date.parse(receipt.expires_at) <= this.#now()) throw new Error("issuance_unavailable");
      if (current.state === "issued" && !isDeepStrictEqual(receipt, current.data.receipt)) throw new Error("issuance_conflict");
      this.#write(id, "issued", { ...current.data, receipt });
    });
  }
  inspect(id, context) { const current = this.#owned(id, context); return Object.freeze({ state: current.state, expires_at: current.data.expires }); }
  authentication(id, context, { claim = false } = {}) {
    return this.#transaction(() => {
      const current = this.#owned(id, context), data = current.data;
      if (current.state !== "issued" || Date.parse(data.receipt.expires_at) <= this.#now()) throw new Error("authentication_unavailable");
      if (claim) {
        if (data.echoAuthentication) throw new Error("authentication_already_attempted");
        data.echoAuthentication = { proofId: crypto.randomUUID() };
        this.#write(id, current.state, data);
      }
      if (!data.echoAuthentication) throw new Error("authentication_unavailable");
      return Object.freeze({ proofId: data.echoAuthentication.proofId,
        challengeCommitment: data.request.context.echo_challenge_commitment,
        registrationRevision: data.receipt.echo_registration_revision,
        validUntil: Math.min(data.expires, Date.parse(data.receipt.expires_at)) });
    });
  }
  retainAuthenticationHandle(id, context, handle) {
    this.#transaction(() => {
      const current = this.#owned(id, context), auth = current.data.echoAuthentication;
      if (current.state !== "issued" || !auth || auth.handle || typeof handle !== "string" || !/^[a-f0-9]{64}$/.test(handle)) throw new Error("authentication_unavailable");
      auth.handle = handle; this.#write(id, current.state, current.data);
    });
  }
  checkAuthenticationHandle(id, context, handle) {
    const current = this.#owned(id, context);
    if (current.state !== "issued" || !handle || current.data.echoAuthentication?.handle !== handle ||
        current.data.echoAuthentication?.proof) throw new Error("authentication_unavailable");
  }
  retainAuthenticationProof(id, context, proof) {
    this.#transaction(() => {
      const current = this.#owned(id, context), data = current.data, auth = data.echoAuthentication;
      if (current.state !== "issued" || !auth) throw new Error("authentication_unavailable");
      const validated = validateProofReceipt(proof, { proof_id: auth.proofId, subject: data.owner.subject,
        session_commitment: data.owner.session, challenge_commitment: data.request.context.echo_challenge_commitment,
        registration_revision: data.receipt.echo_registration_revision, authenticated_at: proof?.authenticated_at }, "home-assistant:echo");
      if (Date.parse(validated.authenticated_at) < Date.parse(data.receipt.created_at) ||
          Date.parse(validated.expires_at) <= this.#now() || Date.parse(validated.expires_at) > Date.parse(data.receipt.expires_at) ||
          auth.proof && !isDeepStrictEqual(auth.proof, validated)) throw new Error("authentication_unavailable");
      auth.proof = validated; this.#write(id, current.state, data);
    });
  }
  victoriaAuthentication(id,context) {
    return this.#transaction(()=>{
      const current=this.#owned(id,context),data=current.data;
      if(current.state!=="issued" || Date.parse(data.receipt.expires_at)<=this.#now()) throw new Error("authentication_unavailable");
      if(!data.victoriaAuthentication){
        data.victoriaAuthentication={proofId:crypto.randomUUID()};
        this.#write(id,current.state,data);
      }
      return Object.freeze({pairing_id:id,session_commitment:data.request.context.victoria_session_commitment,
        challenge_commitment:data.request.context.victoria_challenge_commitment,proof_id:data.victoriaAuthentication.proofId,
        registration_revision:data.receipt.victoria_registration_revision,
        valid_until:Math.min(data.expires,Date.parse(data.receipt.expires_at))});
    });
  }
  retainVictoriaProof(id,context,proof) {
    this.#transaction(()=>{
      const current=this.#owned(id,context),data=current.data,auth=data.victoriaAuthentication;
      if(current.state!=="issued" || !auth) throw new Error("authentication_unavailable");
      const validated=validateProofReceipt(proof,{proof_id:auth.proofId,subject:proof?.subject,
        session_commitment:data.request.context.victoria_session_commitment,
        challenge_commitment:data.request.context.victoria_challenge_commitment,
        registration_revision:data.receipt.victoria_registration_revision,authenticated_at:proof?.authenticated_at},"home-assistant:victoria");
      if(Date.parse(validated.authenticated_at)<Date.parse(data.receipt.created_at) ||
        Date.parse(validated.expires_at)<=this.#now() || Date.parse(validated.expires_at)>Date.parse(data.receipt.expires_at) ||
        auth.proof && !isDeepStrictEqual(auth.proof,validated)) throw new Error("authentication_unavailable");
      auth.proof=validated;this.#write(id,current.state,data);
    });
  }
  reviewEvidence(id,context) {
    return this.#transaction(()=>{
      const current=this.#owned(id,context),data=current.data;
      if(current.state!=="issued" || !data.echoAuthentication?.proof || !data.victoriaAuthentication?.proof ||
          [data.echoAuthentication.proof,data.victoriaAuthentication.proof].some(p=>Date.parse(p.expires_at)<=this.#now())) throw new Error("review_evidence_unavailable");
      if(!data.choice){
        data.choice={session_commitment:data.owner.session};
        for(const field of ["gesture_id","proposal_id","receipt_id","link_id","echo_binding_id","victoria_binding_id"]) data.choice[field]=crypto.randomUUID();
        this.#write(id,current.state,data);
      }
      return Object.freeze({subject:data.owner.subject,context:data.request.context,
        echo:data.echoAuthentication.proof,victoria:data.victoriaAuthentication.proof,choice:data.choice});
    });
  }
  close() { this.#db?.close(); this.#db = null; this.#key.fill(0); }
}
